package archive

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/linxGnu/grocksdb"

	"parquetprofile/common"
)

// Rocksdb: RocksDB qua grocksdb (cgo, liên kết librocksdb), cùng thiết kế với bản Java rocksdbjni.
//
// Mỗi ngày là một DB, ghi một lần rồi chỉ đọc. Ghi: các dòng đã sắp theo reference được ghi tuần tự
// bằng SSTFileWriter ra một file SST rồi nạp vào DB bằng IngestExternalFile, không qua WAL, memtable
// hay compaction. Key là REFERENCE; value là bản ghi JSON theo định dạng chuẩn của repo (giống hệt bản
// Java, nên hai bên đọc được DB của nhau và `ldb scan` xem được). Block nén ZSTD, bloom filter
// 10 bit/key, block 64 KB.
//
// Đọc: mở DB chỉ đọc, MultiGet. Index và bloom filter nằm trong block cache có giới hạn.
//
// Settings: ROCKSDB_BLOCK_BYTES (65536), ROCKSDB_CACHE_BYTES (64 MiB).
type Rocksdb struct{}

func init() { Register("rocksdb", func() common.Archive { return Rocksdb{} }) }

// Extension: một ngày là một thư mục DB YYYY-MM-DD.rocksdb/.
func (Rocksdb) Extension() string { return "rocksdb" }

// rocksdbOptions trả options và hàm giải phóng mọi đối tượng native (Go không tự dọn chúng).
func rocksdbOptions() (*grocksdb.Options, func()) {
	cache := grocksdb.NewLRUCache(uint64(common.Int("ROCKSDB_CACHE_BYTES", 64<<20)))
	table := grocksdb.NewDefaultBlockBasedTableOptions()
	table.SetBlockSize(int(common.Int("ROCKSDB_BLOCK_BYTES", 64<<10)))
	table.SetFilterPolicy(grocksdb.NewBloomFilter(10))
	table.SetCacheIndexAndFilterBlocks(true)
	table.SetPinL0FilterAndIndexBlocksInCache(true)
	table.SetBlockCache(cache)
	opts := grocksdb.NewDefaultOptions()
	opts.SetCompression(grocksdb.ZSTDCompression)
	opts.SetBottommostCompression(grocksdb.ZSTDCompression)
	opts.SetBlockBasedTableFactory(table)
	return opts, func() {
		opts.Destroy()
		table.Destroy()
		cache.Destroy()
	}
}

// ---- WRITE ----

func (Rocksdb) Dump(rows common.RowCursor, out string) (int64, error) {
	if err := os.MkdirAll(out, 0o755); err != nil {
		return 0, err
	}
	sst := out + ".sst"
	defer os.Remove(sst)
	opts, release := rocksdbOptions()
	defer release()

	env := grocksdb.NewDefaultEnvOptions()
	defer env.Destroy()
	writer := grocksdb.NewSSTFileWriter(env, opts)
	defer writer.Destroy()
	if err := writer.Open(sst); err != nil {
		return 0, err
	}
	var count int64
	var value bytes.Buffer
	w := bufio.NewWriter(&value)
	for {
		more, err := rows.Next()
		if err != nil {
			return count, err
		}
		if !more {
			break
		}
		row := common.Row{ID: rows.ID(), Reference: rows.Reference(), CreatedAt: rows.CreatedAt(),
			Texts: make([]*string, rows.TextColumns())}
		for i := range row.Texts {
			if text, ok := rows.Text(i + 1); ok {
				row.Texts[i] = &text
			}
		}
		value.Reset()
		w.Reset(&value)
		common.WriteJSONL(w, row)
		if err := w.Flush(); err != nil {
			return count, err
		}
		line := bytes.TrimSuffix(value.Bytes(), []byte("\n"))
		if err := writer.Put([]byte(row.Reference), line); err != nil {
			return count, err
		}
		count++
	}
	if err := writer.Finish(); err != nil {
		return count, err
	}

	opts.SetCreateIfMissing(true)
	db, err := grocksdb.OpenDb(opts, out)
	if err != nil {
		return count, err
	}
	defer db.Close()
	ingest := grocksdb.NewDefaultIngestExternalFileOptions()
	defer ingest.Destroy()
	ingest.SetMoveFiles(true)
	return count, db.IngestExternalFile([]string{sst}, ingest)
}

// ---- FIND ----

func (Rocksdb) Find(dir string, references []string) ([]common.Row, error) {
	opts, release := rocksdbOptions()
	defer release()
	db, err := grocksdb.OpenDbForReadOnly(opts, dir, false)
	if err != nil {
		return nil, err
	}
	defer db.Close()
	read := grocksdb.NewDefaultReadOptions()
	defer read.Destroy()

	keys := make([][]byte, len(references))
	for i, r := range references {
		keys[i] = []byte(r)
	}
	values, err := db.MultiGet(read, keys...)
	if err != nil {
		return nil, err
	}
	defer values.Destroy()
	var found []common.Row
	for _, v := range values {
		if !v.Exists() {
			continue
		}
		row, err := rocksdbParse(v.Data())
		if err != nil {
			return nil, err
		}
		found = append(found, row)
	}
	return found, nil
}

// rocksdbParse đọc lại bản ghi JSON: id, reference, created_at, text_01..text_NN (null là NULL).
func rocksdbParse(data []byte) (common.Row, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber() // id có thể tới int64 lớn nhất, không đi qua float64
	var fields map[string]any
	if err := decoder.Decode(&fields); err != nil {
		return common.Row{}, err
	}
	var row common.Row
	textColumns := 0
	for name := range fields {
		if strings.HasPrefix(name, "text_") {
			textColumns++
		}
	}
	row.Texts = make([]*string, textColumns)
	for name, value := range fields {
		switch name {
		case "id":
			id, err := value.(json.Number).Int64()
			if err != nil {
				return row, err
			}
			row.ID = id
		case "reference":
			row.Reference = value.(string)
		case "created_at":
			t, err := time.Parse("2006-01-02 15:04:05.000000", value.(string))
			if err != nil {
				return row, err
			}
			row.CreatedAt = t
		default:
			n, err := strconv.Atoi(strings.TrimPrefix(name, "text_"))
			if err != nil || n < 1 || n > textColumns {
				return row, fmt.Errorf("unexpected field %s", name)
			}
			if s, ok := value.(string); ok {
				row.Texts[n-1] = &s
			}
		}
	}
	return row, nil
}
