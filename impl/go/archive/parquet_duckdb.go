package archive

import (
	"database/sql"
	"fmt"
	"os"
	"strings"

	"github.com/parquet-go/parquet-go"
	"github.com/parquet-go/parquet-go/compress"

	"parquetprofile/common"

	_ "github.com/duckdb/duckdb-go/v2"
)

// ParquetDuckdb ghi Parquet bằng parquet-go và đọc bằng DuckDB nhúng (duckdb-go, cần cgo).
//
// Ghi theo cùng cấu hình mặc định của bản Java (parquet-java): dictionary encoding cho cột chuỗi,
// tối đa 1 MB dictionary mỗi cột rồi chuyển sang PLAIN; row group 128 MB. parquet-go không tự chia
// row group theo byte, nên Dump gọi Flush khi Writer.Size() báo row group đang ghi đạt 128 MB,
// đúng cách tài liệu parquet-go hướng dẫn. Dòng được ghi theo lô PARQUET_WRITE_BATCH.
//
// Settings: PARQUET_COMPRESSION (ZSTD | SNAPPY | LZ4_RAW | GZIP | UNCOMPRESSED),
// PARQUET_ROW_GROUP_BYTES (128 MiB), PARQUET_ROW_GROUP_ROWS (không giới hạn nếu không đặt),
// PARQUET_WRITE_BATCH (10000), DUCKDB_THREADS (4),
// DUCKDB_EXTERNAL_FILE_CACHE (false).
type ParquetDuckdb struct{}

func init() { Register("parquet-duckdb", func() common.Archive { return ParquetDuckdb{} }) }

func (ParquetDuckdb) Extension() string { return "parquet" }

var codecs = map[string]compress.Codec{
	"ZSTD":         &parquet.Zstd,
	"SNAPPY":       &parquet.Snappy,
	"LZ4_RAW":      &parquet.Lz4Raw,
	"GZIP":         &parquet.Gzip,
	"UNCOMPRESSED": &parquet.Uncompressed,
}

func schema(textColumns int) *parquet.Schema {
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

// columns maps each leaf column of a file to its place in a Row.
type columns struct {
	id, reference, createdAt int
	text                     map[int]int // leaf column -> text index, 0-based
}

func columnsOf(s *parquet.Schema) (columns, error) {
	c := columns{text: map[int]int{}}
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

func (ParquetDuckdb) Dump(rows common.RowCursor, out string) (int64, error) {
	codecName := common.String("PARQUET_COMPRESSION", "ZSTD")
	codec, ok := codecs[codecName]
	if !ok {
		return 0, fmt.Errorf("unknown PARQUET_COMPRESSION %s", codecName)
	}
	s := schema(rows.TextColumns())
	cols, err := columnsOf(s)
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

// Find chạy một truy vấn DuckDB cho mọi reference, cùng câu truy vấn với java-parquet-duckdb
// (SELECT *). Cột được ánh xạ theo tên, vì parquet-go xếp cột theo bảng chữ cái.
func (ParquetDuckdb) Find(file string, references []string) ([]common.Row, error) {
	db, err := sql.Open("duckdb", "")
	if err != nil {
		return nil, err
	}
	defer db.Close()
	db.SetMaxOpenConns(1) // SET chỉ có hiệu lực trên một kết nối
	if _, err := db.Exec(fmt.Sprintf("SET threads = %d", common.Int("DUCKDB_THREADS", 4))); err != nil {
		return nil, err
	}
	if _, err := db.Exec(fmt.Sprintf("SET enable_external_file_cache = %t", common.Bool("DUCKDB_EXTERNAL_FILE_CACHE", false))); err != nil {
		return nil, err
	}

	args := []any{file}
	marks := make([]string, len(references))
	for i, r := range references {
		marks[i] = "?"
		args = append(args, r)
	}
	rows, err := db.Query("SELECT * FROM read_parquet(?) WHERE reference IN ("+strings.Join(marks, ",")+")", args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	names, err := rows.Columns()
	if err != nil {
		return nil, err
	}

	textColumns := 0
	for _, name := range names {
		if strings.HasPrefix(name, "text_") {
			textColumns++
		}
	}
	var row common.Row
	texts := make([]sql.NullString, textColumns)
	dest := make([]any, len(names))
	textIndex := make([]int, len(names)) // vị trí cột trong kết quả -> chỉ số text, -1 nếu không phải text
	for i, name := range names {
		textIndex[i] = -1
		switch name {
		case "id":
			dest[i] = &row.ID
		case "reference":
			dest[i] = &row.Reference
		case "created_at":
			dest[i] = &row.CreatedAt
		default:
			var n int
			if _, err := fmt.Sscanf(name, "text_%d", &n); err != nil || n < 1 || n > textColumns {
				return nil, fmt.Errorf("unexpected column %s", name)
			}
			dest[i] = &texts[n-1]
			textIndex[i] = n - 1
		}
	}

	var found []common.Row
	for rows.Next() {
		if err := rows.Scan(dest...); err != nil {
			return nil, err
		}
		out := common.Row{ID: row.ID, Reference: row.Reference, CreatedAt: row.CreatedAt.UTC(), Texts: make([]*string, textColumns)}
		for i, t := range texts {
			if t.Valid {
				value := t.String
				out.Texts[i] = &value
			}
		}
		found = append(found, out)
	}
	return found, rows.Err()
}
