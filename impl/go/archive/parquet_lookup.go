package archive

import (
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/parquet-go/parquet-go"
	"github.com/parquet-go/parquet-go/compress"

	"parquetprofile/common"
)

// ParquetLookup: Parquet chuẩn, chỉnh để tra theo key, đọc thẳng bằng parquet-go (không DuckDB).
//
// Ghi như go-parquet-duckdb (row group 128 MB, dictionary, ZSTD), thêm bloom filter trên reference và
// page 64 KB để page index chia file thành đơn vị nhỏ. Đọc: với mỗi row group, bloom filter loại key
// không có; page index (min/max từng page của reference) chọn page có thể chứa key; quét cột reference
// của các page đó để có số dòng; rồi chỉ đọc đầy đủ đúng các dòng đó.
//
// Settings: PARQUET_COMPRESSION (ZSTD), PARQUET_ROW_GROUP_BYTES (128 MiB), PARQUET_PAGE_BYTES (64 KiB),
// PARQUET_WRITE_BATCH (10000).
type ParquetLookup struct{}

func init() { Register("parquet-lookup", func() common.Archive { return ParquetLookup{} }) }

func (ParquetLookup) Extension() string { return "parquet" }

var lookupCodecs = map[string]compress.Codec{
	"ZSTD":         &parquet.Zstd,
	"SNAPPY":       &parquet.Snappy,
	"LZ4_RAW":      &parquet.Lz4Raw,
	"GZIP":         &parquet.Gzip,
	"UNCOMPRESSED": &parquet.Uncompressed,
}

func lookupSchema(textColumns int) *parquet.Schema {
	group := parquet.Group{
		"id":         parquet.Int(64),
		"reference":  parquet.String(),
		"created_at": parquet.TimestampAdjusted(parquet.Microsecond, false),
	}
	for i := 1; i <= textColumns; i++ {
		group[fmt.Sprintf("text_%02d", i)] = parquet.Optional(parquet.String())
	}
	return parquet.NewSchema("archive", group)
}

// lookupColumns maps each leaf column of a file to its place in a Row.
type lookupColumns struct {
	id, reference, createdAt int
	text                     map[int]int // leaf column -> text index, 0-based
}

func lookupColumnsOf(s *parquet.Schema) (lookupColumns, error) {
	c := lookupColumns{text: map[int]int{}}
	for name, target := range map[string]*int{"id": &c.id, "reference": &c.reference, "created_at": &c.createdAt} {
		leaf, ok := s.Lookup(name)
		if !ok {
			return c, fmt.Errorf("column %s missing", name)
		}
		*target = leaf.ColumnIndex
	}
	for i := 1; ; i++ {
		leaf, ok := s.Lookup(fmt.Sprintf("text_%02d", i))
		if !ok {
			break
		}
		c.text[leaf.ColumnIndex] = i - 1
	}
	return c, nil
}

// ---- WRITE ----

func (ParquetLookup) Dump(rows common.RowCursor, out string) (int64, error) {
	codecName := common.String("PARQUET_COMPRESSION", "ZSTD")
	codec, ok := lookupCodecs[codecName]
	if !ok {
		return 0, fmt.Errorf("unknown PARQUET_COMPRESSION %s", codecName)
	}
	s := lookupSchema(rows.TextColumns())
	cols, err := lookupColumnsOf(s)
	if err != nil {
		return 0, err
	}
	f, err := os.Create(out)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	options := []parquet.WriterOption{
		s, // schema cũng là một WriterOption
		parquet.Compression(codec),
		// Như mặc định của parquet-java: dictionary cho chuỗi, quá 1 MB thì chuyển PLAIN.
		parquet.DefaultEncodingFor(parquet.ByteArray, &parquet.RLEDictionary),
		parquet.DictionaryMaxBytes(1 << 20),
		// Reference dài hơn 16 byte mặc định; giữ nguyên min/max để bỏ qua row group chính xác.
		parquet.ColumnIndexSizeLimit(func([]string) int { return 64 }),
		// Tính năng chuẩn cho tra theo key: bloom filter trên reference, page nhỏ cho page index.
		parquet.BloomFilters(parquet.SplitBlockFilter(10, "reference")),
		parquet.PageBufferSize(int(common.Int("PARQUET_PAGE_BYTES", 64<<10))),
	}
	if os.Getenv("PARQUET_ROW_GROUP_ROWS") != "" {
		options = append(options, parquet.MaxRowsPerRowGroup(common.Int("PARQUET_ROW_GROUP_ROWS", 0)))
	}
	writer := parquet.NewWriter(f, options...)
	rowGroupBytes := common.Int("PARQUET_ROW_GROUP_BYTES", 128<<20)
	rowGroupStart := int64(0) // Size() tính từ đầu file; phần vượt mốc này là row group đang ghi

	batchSize := int(common.Int("PARQUET_WRITE_BATCH", 10_000))
	batch := make([]parquet.Row, 0, batchSize)
	columnCount := len(s.Columns())
	var count int64
	flush := func() error {
		if len(batch) == 0 {
			return nil
		}
		if _, err := writer.WriteRows(batch); err != nil {
			return err
		}
		batch = batch[:0]
		if writer.Size()-rowGroupStart >= rowGroupBytes {
			if err := writer.Flush(); err != nil {
				return err
			}
			rowGroupStart = writer.Size()
		}
		return nil
	}
	for {
		more, err := rows.Next()
		if err != nil {
			return count, err
		}
		if !more {
			break
		}
		// Mỗi dòng một Row mới: lô giữ nhiều dòng cùng lúc, không được dùng lại slice.
		row := make(parquet.Row, columnCount)
		row[cols.id] = parquet.Int64Value(rows.ID()).Level(0, 0, cols.id)
		row[cols.reference] = parquet.ByteArrayValue([]byte(rows.Reference())).Level(0, 0, cols.reference)
		row[cols.createdAt] = parquet.Int64Value(rows.CreatedAt().UnixMicro()).Level(0, 0, cols.createdAt)
		for col, i := range cols.text {
			if text, ok := rows.Text(i + 1); ok {
				row[col] = parquet.ByteArrayValue([]byte(text)).Level(0, 1, col)
			} else {
				row[col] = parquet.NullValue().Level(0, 0, col)
			}
		}
		batch = append(batch, row)
		count++
		if len(batch) == batchSize {
			if err := flush(); err != nil {
				return count, err
			}
		}
	}
	if err := flush(); err != nil {
		return count, err
	}
	if err := writer.Close(); err != nil {
		return count, err
	}
	return count, f.Close()
}

// ---- FIND ----

func (ParquetLookup) Find(file string, references []string) ([]common.Row, error) {
	f, err := os.Open(file)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	pf, err := parquet.OpenFile(f, info.Size())
	if err != nil {
		return nil, err
	}
	cols, err := lookupColumnsOf(pf.Schema())
	if err != nil {
		return nil, err
	}
	textColumns := len(cols.text)
	keys := append([]string(nil), references...)
	sort.Strings(keys)

	var found []common.Row
	for _, rg := range pf.RowGroups() {
		chunk := rg.ColumnChunks()[cols.reference]
		// Bloom filter: bỏ key chắc chắn không có trong row group này.
		candidates := keys
		if bloom := chunk.BloomFilter(); bloom != nil {
			candidates = candidates[:0:0]
			for _, k := range keys {
				if ok, err := bloom.Check(parquet.ByteArrayValue([]byte(k))); err != nil {
					return nil, err
				} else if ok {
					candidates = append(candidates, k)
				}
			}
		}
		if len(candidates) == 0 {
			continue
		}
		wanted := make(map[string]bool, len(candidates))
		for _, k := range candidates {
			wanted[k] = true
		}
		matches, err := lookupMatchingRows(chunk, candidates, wanted)
		if err != nil {
			return nil, err
		}
		if len(matches) == 0 {
			continue
		}
		rows := rg.Rows()
		buf := make([]parquet.Row, 1)
		for _, index := range matches {
			if err := rows.SeekToRow(index); err != nil {
				rows.Close()
				return nil, err
			}
			if n, err := rows.ReadRows(buf); n != 1 {
				rows.Close()
				return nil, fmt.Errorf("đọc dòng %d: %v", index, err)
			}
			found = append(found, lookupToRow(buf[0], cols, textColumns))
		}
		rows.Close()
	}
	return found, nil
}

// lookupMatchingRows trả số thứ tự dòng (trong row group) có reference cần tìm, dùng page index để chỉ
// quét các page có min/max bao quanh một key.
func lookupMatchingRows(chunk parquet.ColumnChunk, sorted []string, wanted map[string]bool) ([]int64, error) {
	pages := chunk.Pages()
	defer pages.Close()
	index, err := chunk.ColumnIndex()
	if errors.Is(err, parquet.ErrMissingColumnIndex) {
		return lookupScanPages(pages, 0, -1, wanted) // không có page index: quét cả chunk
	}
	if err != nil {
		return nil, err
	}
	offsets, err := chunk.OffsetIndex()
	if err != nil {
		return nil, err
	}
	var matches []int64
	for p := 0; p < index.NumPages(); p++ {
		low, high := string(index.MinValue(p).ByteArray()), string(index.MaxValue(p).ByteArray())
		i := sort.SearchStrings(sorted, low)
		if i == len(sorted) || sorted[i] > high {
			continue
		}
		first := offsets.FirstRowIndex(p)
		if err := pages.SeekToRow(first); err != nil {
			return nil, err
		}
		found, err := lookupScanPages(pages, first, 1, wanted)
		if err != nil {
			return nil, err
		}
		matches = append(matches, found...)
	}
	return matches, nil
}

// lookupScanPages đọc tối đa limit page (tất cả nếu limit < 0) bắt đầu từ dòng first.
func lookupScanPages(pages parquet.Pages, first int64, limit int, wanted map[string]bool) ([]int64, error) {
	var matches []int64
	values := make([]parquet.Value, 1024)
	position := first
	for read := 0; limit < 0 || read < limit; read++ {
		page, err := pages.ReadPage()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
		reader := page.Values()
		for {
			n, err := reader.ReadValues(values)
			for _, v := range values[:n] {
				if wanted[string(v.ByteArray())] {
					matches = append(matches, position)
				}
				position++
			}
			if err == io.EOF {
				break
			}
			if err != nil {
				parquet.Release(page)
				return nil, err
			}
		}
		parquet.Release(page)
	}
	return matches, nil
}

func lookupToRow(values parquet.Row, cols lookupColumns, textColumns int) common.Row {
	row := common.Row{Texts: make([]*string, textColumns)}
	for _, v := range values {
		switch col := v.Column(); col {
		case cols.id:
			row.ID = v.Int64()
		case cols.reference:
			row.Reference = strings.Clone(string(v.ByteArray()))
		case cols.createdAt:
			row.CreatedAt = time.UnixMicro(v.Int64()).UTC()
		default:
			if i, ok := cols.text[col]; ok && !v.IsNull() {
				text := string(v.ByteArray())
				row.Texts[i] = &text
			}
		}
	}
	return row
}
