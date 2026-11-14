package archive

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"sort"
	"time"

	"github.com/apache/arrow-go/v18/arrow"
	"github.com/apache/arrow-go/v18/arrow/array"
	"github.com/apache/arrow-go/v18/arrow/ipc"
	"github.com/apache/arrow-go/v18/arrow/memory"

	"parquetprofile/common"
)

// ArrowZstd lưu theo cột bằng Apache Arrow IPC (file format), mỗi buffer nén ZSTD. Các dòng (đã sắp
// theo reference) chia thành lô ARROW_BATCH_ROWS dòng. Arrow không có thống kê min/max, nên sau
// phần Arrow là một index nhỏ ghi reference đầu tiên của từng lô; FIND tìm nhị phân trong index rồi
// chỉ giải nén lô chứa key. Định dạng giống hệt bản Java (xem README).
//
// Settings: ARROW_BATCH_ROWS (1024).
type ArrowZstd struct{}

const arrowMagic = "ARCHARW1"

func (ArrowZstd) Extension() string { return "arrow" }

func arrowSchema(textColumns int) *arrow.Schema {
	fields := []arrow.Field{
		{Name: "id", Type: arrow.PrimitiveTypes.Int64},
		{Name: "reference", Type: arrow.BinaryTypes.String},
		{Name: "created_at", Type: &arrow.TimestampType{Unit: arrow.Microsecond}},
	}
	for i := 1; i <= textColumns; i++ {
		fields = append(fields, arrow.Field{Name: fmt.Sprintf("text_%02d", i), Type: arrow.BinaryTypes.String, Nullable: true})
	}
	return arrow.NewSchema(fields, nil)
}

// countingWriter đếm số byte phần Arrow, để biết index bắt đầu ở đâu.
type countingWriter struct {
	w io.Writer
	n uint64
}

func (c *countingWriter) Write(p []byte) (int, error) {
	n, err := c.w.Write(p)
	c.n += uint64(n)
	return n, err
}

// ---- WRITE ----

func (ArrowZstd) Dump(rows common.RowCursor, out string) (int64, error) {
	batchRows := int(common.Int("ARROW_BATCH_ROWS", 1024))
	schema := arrowSchema(rows.TextColumns())
	f, err := os.Create(out)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	cw := &countingWriter{w: f}
	writer, err := ipc.NewFileWriter(cw, ipc.WithSchema(schema), ipc.WithZstd())
	if err != nil {
		return 0, err
	}
	builder := array.NewRecordBuilder(memory.DefaultAllocator, schema)
	defer builder.Release()
	ids := builder.Field(0).(*array.Int64Builder)
	refs := builder.Field(1).(*array.StringBuilder)
	times := builder.Field(2).(*array.TimestampBuilder)
	texts := make([]*array.StringBuilder, rows.TextColumns())
	for i := range texts {
		texts[i] = builder.Field(3 + i).(*array.StringBuilder)
	}

	var firsts []string
	var count int64
	inBatch := 0
	flush := func() error {
		if inBatch == 0 {
			return nil
		}
		rec := builder.NewRecordBatch()
		defer rec.Release()
		inBatch = 0
		return writer.Write(rec)
	}
	for {
		more, err := rows.Next()
		if err != nil {
			return count, err
		}
		if !more {
			break
		}
		if inBatch == 0 {
			firsts = append(firsts, rows.Reference())
		}
		ids.Append(rows.ID())
		refs.Append(rows.Reference())
		times.Append(arrow.Timestamp(rows.CreatedAt().UnixMicro()))
		for i, b := range texts {
			if text, ok := rows.Text(i + 1); ok {
				b.Append(text)
			} else {
				b.AppendNull()
			}
		}
		count++
		if inBatch++; inBatch == batchRows {
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
	if err := writeBatchIndex(f, firsts, cw.n); err != nil {
		return count, err
	}
	return count, f.Close()
}

// writeBatchIndex ghi sau phần Arrow: số lô, reference đầu của từng lô, rồi độ dài phần Arrow + magic.
func writeBatchIndex(w io.Writer, firsts []string, arrowLength uint64) error {
	idx := binary.AppendUvarint(nil, uint64(len(firsts)))
	for _, first := range firsts {
		idx = binary.AppendUvarint(idx, uint64(len(first)))
		idx = append(idx, first...)
	}
	idx = binary.LittleEndian.AppendUint64(idx, arrowLength)
	idx = append(idx, arrowMagic...)
	_, err := w.Write(idx)
	return err
}

// ---- FIND ----

func (ArrowZstd) Find(file string, references []string) ([]common.Row, error) {
	f, err := os.Open(file)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	firsts, arrowLength, err := readBatchIndex(f)
	if err != nil {
		return nil, err
	}
	wanted := map[int]map[string]bool{}
	for _, ref := range references {
		b := sort.SearchStrings(firsts, ref)
		if b == len(firsts) || firsts[b] != ref {
			b-- // lô cuối cùng có reference đầu <= key
		}
		if b < 0 {
			continue
		}
		if wanted[b] == nil {
			wanted[b] = map[string]bool{}
		}
		wanted[b][ref] = true
	}
	batches := make([]int, 0, len(wanted))
	for b := range wanted {
		batches = append(batches, b)
	}
	sort.Ints(batches)

	reader, err := ipc.NewFileReader(io.NewSectionReader(f, 0, int64(arrowLength)))
	if err != nil {
		return nil, err
	}
	defer reader.Close()
	schema := reader.Schema()
	textColumns := schema.NumFields() - 3
	var found []common.Row
	for _, b := range batches {
		rec, err := reader.RecordBatch(b)
		if err != nil {
			return nil, err
		}
		ids := rec.Column(0).(*array.Int64)
		refs := rec.Column(1).(*array.String)
		times := rec.Column(2).(*array.Timestamp)
		for i := 0; i < int(rec.NumRows()); i++ {
			if !wanted[b][refs.Value(i)] {
				continue
			}
			row := common.Row{
				ID:        ids.Value(i),
				Reference: refs.Value(i),
				CreatedAt: time.UnixMicro(int64(times.Value(i))).UTC(),
				Texts:     make([]*string, textColumns),
			}
			for c := 0; c < textColumns; c++ {
				col := rec.Column(3 + c).(*array.String)
				if col.IsValid(i) {
					text := col.Value(i)
					row.Texts[c] = &text
				}
			}
			found = append(found, row)
		}
	}
	return found, nil
}

func readBatchIndex(f *os.File) ([]string, uint64, error) {
	info, err := f.Stat()
	if err != nil {
		return nil, 0, err
	}
	trailer := make([]byte, 16)
	if _, err := f.ReadAt(trailer, info.Size()-16); err != nil {
		return nil, 0, err
	}
	if string(trailer[8:]) != arrowMagic {
		return nil, 0, fmt.Errorf("not an indexed arrow file")
	}
	arrowLength := binary.LittleEndian.Uint64(trailer)
	data := make([]byte, uint64(info.Size())-16-arrowLength)
	if _, err := f.ReadAt(data, int64(arrowLength)); err != nil {
		return nil, 0, err
	}
	r := bytes.NewReader(data)
	count, _ := binary.ReadUvarint(r)
	firsts := make([]string, count)
	for i := range firsts {
		n, err := binary.ReadUvarint(r)
		if err != nil {
			return nil, 0, err
		}
		b := make([]byte, n)
		if _, err := io.ReadFull(r, b); err != nil {
			return nil, 0, err
		}
		firsts[i] = string(b)
	}
	return firsts, arrowLength, nil
}
