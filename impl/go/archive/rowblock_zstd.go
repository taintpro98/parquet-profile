package archive

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"sort"
	"time"

	"github.com/klauspost/compress/zstd"

	"parquetprofile/common"
)

// RowblockZstd lưu theo dòng: các dòng (đã sắp theo reference) gom thành block, mỗi block nén ZSTD
// riêng; cuối file có index thưa ghi reference đầu tiên và vị trí của từng block. FIND tìm nhị phân
// trong index rồi chỉ giải nén đúng block chứa key. Định dạng giống hệt bản Java (xem README).
//
// Settings: BLOCK_BYTES (1048576, kích thước block trước khi nén), BLOCK_ZSTD_LEVEL (3).
type RowblockZstd struct{}

const rowblockMagic = "ARCHBLK1"

func (RowblockZstd) Extension() string { return "rbk" }

type blockEntry struct {
	first        string
	offset, size uint64 // vị trí và kích thước đã nén
	rawSize      uint64
}

// ---- WRITE ----

func (RowblockZstd) Dump(rows common.RowCursor, out string) (int64, error) {
	limit := int(common.Int("BLOCK_BYTES", 1<<20))
	encoder, err := zstd.NewWriter(nil, zstd.WithEncoderLevel(zstd.EncoderLevelFromZstd(int(common.Int("BLOCK_ZSTD_LEVEL", 3)))))
	if err != nil {
		return 0, err
	}
	defer encoder.Close()
	f, err := os.Create(out)
	if err != nil {
		return 0, err
	}
	defer f.Close()

	var (
		index      []blockEntry
		block      []byte
		compressed []byte
		first      string
		offset     uint64
		count      int64
		scratch    [8]byte
	)
	flush := func() error {
		if len(block) == 0 {
			return nil
		}
		compressed = encoder.EncodeAll(block, compressed[:0])
		if _, err := f.Write(compressed); err != nil {
			return err
		}
		index = append(index, blockEntry{first, offset, uint64(len(compressed)), uint64(len(block))})
		offset += uint64(len(compressed))
		block = block[:0]
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
		if len(block) == 0 {
			first = rows.Reference()
		}
		binary.LittleEndian.PutUint64(scratch[:], uint64(rows.ID()))
		block = append(block, scratch[:]...)
		binary.LittleEndian.PutUint64(scratch[:], uint64(rows.CreatedAt().UnixMicro()))
		block = append(block, scratch[:]...)
		block = binary.AppendUvarint(block, uint64(len(rows.Reference())))
		block = append(block, rows.Reference()...)
		for c := 1; c <= rows.TextColumns(); c++ {
			if text, ok := rows.Text(c); ok {
				block = binary.AppendUvarint(block, uint64(len(text))+1)
				block = append(block, text...)
			} else {
				block = binary.AppendUvarint(block, 0)
			}
		}
		count++
		if len(block) >= limit {
			if err := flush(); err != nil {
				return count, err
			}
		}
	}
	if err := flush(); err != nil {
		return count, err
	}

	// index: số block, số cột text, rồi mỗi block: reference đầu, offset, kích thước nén, kích thước gốc
	idx := binary.AppendUvarint(nil, uint64(len(index)))
	idx = binary.AppendUvarint(idx, uint64(rows.TextColumns()))
	for _, e := range index {
		idx = binary.AppendUvarint(idx, uint64(len(e.first)))
		idx = append(idx, e.first...)
		idx = binary.AppendUvarint(idx, e.offset)
		idx = binary.AppendUvarint(idx, e.size)
		idx = binary.AppendUvarint(idx, e.rawSize)
	}
	idx = binary.LittleEndian.AppendUint64(idx, offset) // vị trí bắt đầu index
	idx = append(idx, rowblockMagic...)
	if _, err := f.Write(idx); err != nil {
		return count, err
	}
	return count, f.Close()
}

// ---- FIND ----

func (RowblockZstd) Find(file string, references []string) ([]common.Row, error) {
	f, err := os.Open(file)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	index, textColumns, err := readBlockIndex(f)
	if err != nil {
		return nil, err
	}
	// Mỗi key thuộc block cuối cùng có reference đầu <= key.
	wanted := map[int]map[string]bool{}
	for _, ref := range references {
		b := sort.Search(len(index), func(i int) bool { return index[i].first > ref }) - 1
		if b < 0 {
			continue
		}
		if wanted[b] == nil {
			wanted[b] = map[string]bool{}
		}
		wanted[b][ref] = true
	}
	blocks := make([]int, 0, len(wanted))
	for b := range wanted {
		blocks = append(blocks, b)
	}
	sort.Ints(blocks)

	decoder, err := zstd.NewReader(nil, zstd.WithDecoderConcurrency(1))
	if err != nil {
		return nil, err
	}
	defer decoder.Close()
	var found []common.Row
	var compressed, raw []byte
	for _, b := range blocks {
		e := index[b]
		compressed = grow(compressed, int(e.size))
		if _, err := f.ReadAt(compressed, int64(e.offset)); err != nil {
			return nil, err
		}
		raw, err = decoder.DecodeAll(compressed, grow(raw, int(e.rawSize))[:0])
		if err != nil {
			return nil, err
		}
		rows, err := scanBlock(raw, textColumns, wanted[b])
		if err != nil {
			return nil, fmt.Errorf("block %d: %w", b, err)
		}
		found = append(found, rows...)
	}
	return found, nil
}

func grow(b []byte, n int) []byte {
	if cap(b) < n {
		return make([]byte, n)
	}
	return b[:n]
}

func readBlockIndex(f *os.File) ([]blockEntry, int, error) {
	info, err := f.Stat()
	if err != nil {
		return nil, 0, err
	}
	trailer := make([]byte, 16)
	if _, err := f.ReadAt(trailer, info.Size()-16); err != nil {
		return nil, 0, err
	}
	if string(trailer[8:]) != rowblockMagic {
		return nil, 0, fmt.Errorf("not a rowblock file")
	}
	start := int64(binary.LittleEndian.Uint64(trailer))
	data := make([]byte, info.Size()-16-start)
	if _, err := f.ReadAt(data, start); err != nil {
		return nil, 0, err
	}
	r := bytes.NewReader(data)
	count, _ := binary.ReadUvarint(r)
	textColumns, _ := binary.ReadUvarint(r)
	index := make([]blockEntry, count)
	for i := range index {
		n, _ := binary.ReadUvarint(r)
		first := make([]byte, n)
		if _, err := io.ReadFull(r, first); err != nil {
			return nil, 0, err
		}
		index[i].first = string(first)
		index[i].offset, _ = binary.ReadUvarint(r)
		index[i].size, _ = binary.ReadUvarint(r)
		index[i].rawSize, err = binary.ReadUvarint(r)
		if err != nil {
			return nil, 0, err
		}
	}
	return index, int(textColumns), nil
}

// scanBlock đọc tuần tự các dòng trong block; chỉ dựng Row cho dòng có key cần tìm.
func scanBlock(raw []byte, textColumns int, wanted map[string]bool) ([]common.Row, error) {
	var found []common.Row
	pos := 0
	uvarint := func() (uint64, error) {
		v, n := binary.Uvarint(raw[pos:])
		if n <= 0 {
			return 0, fmt.Errorf("corrupt varint at %d", pos)
		}
		pos += n
		return v, nil
	}
	for pos < len(raw) {
		id := int64(binary.LittleEndian.Uint64(raw[pos:]))
		micros := int64(binary.LittleEndian.Uint64(raw[pos+8:]))
		pos += 16
		n, err := uvarint()
		if err != nil {
			return nil, err
		}
		ref := string(raw[pos : pos+int(n)])
		pos += int(n)
		match := wanted[ref]
		var row common.Row
		if match {
			row = common.Row{ID: id, Reference: ref, CreatedAt: time.UnixMicro(micros).UTC(), Texts: make([]*string, textColumns)}
		}
		for c := 0; c < textColumns; c++ {
			n, err := uvarint()
			if err != nil {
				return nil, err
			}
			if n > 0 {
				if match {
					text := string(raw[pos : pos+int(n)-1])
					row.Texts[c] = &text
				}
				pos += int(n) - 1
			}
		}
		if match {
			found = append(found, row)
		}
	}
	return found, nil
}
