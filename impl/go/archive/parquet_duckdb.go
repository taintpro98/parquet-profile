// Package archive holds the Go storage options, one file per option.
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
// Settings: PARQUET_COMPRESSION (ZSTD | SNAPPY | LZ4_RAW | GZIP | UNCOMPRESSED),
// PARQUET_ROW_GROUP_ROWS (250000), DUCKDB_THREADS (4), DUCKDB_EXTERNAL_FILE_CACHE (false).
type ParquetDuckdb struct{}

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
	writer := parquet.NewWriter(f, s,
		parquet.Compression(codec),
		parquet.MaxRowsPerRowGroup(common.Int("PARQUET_ROW_GROUP_ROWS", 250_000)),
		// References are longer than the default 16 bytes; untruncated bounds keep pruning exact.
		parquet.ColumnIndexSizeLimit(func([]string) int { return 64 }))

	row := make(parquet.Row, len(s.Columns()))
	batch := []parquet.Row{row}
	var count int64
	for {
		more, err := rows.Next()
		if err != nil {
			return count, err
		}
		if !more {
			break
		}
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
		if _, err := writer.WriteRows(batch); err != nil {
			return count, err
		}
		count++
	}
	if err := writer.Close(); err != nil {
		return count, err
	}
	return count, f.Close()
}

// ---- FIND ----

// Find chạy một truy vấn DuckDB cho mọi reference, giống java-parquet-duckdb. Cột được chọn theo tên,
// nên đọc được cả file do phương án khác ghi.
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
	rows, err := db.Query(`SELECT id, reference, created_at, COLUMNS('^text_\d+$') FROM read_parquet(?)
		WHERE reference IN (`+strings.Join(marks, ",")+`)`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	names, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	texts := make([]sql.NullString, len(names)-3)
	var found []common.Row
	for rows.Next() {
		var row common.Row
		dest := []any{&row.ID, &row.Reference, &row.CreatedAt}
		for i := range texts {
			dest = append(dest, &texts[i])
		}
		if err := rows.Scan(dest...); err != nil {
			return nil, err
		}
		row.CreatedAt = row.CreatedAt.UTC()
		row.Texts = make([]*string, len(texts))
		for i, t := range texts {
			if t.Valid {
				value := t.String
				row.Texts[i] = &value
			}
		}
		found = append(found, row)
	}
	return found, rows.Err()
}
