package common

import (
	"bufio"
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	mrand "math/rand/v2"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Sample bốc ngẫu nhiên size dòng từ Oracle và ghi vào dir:
//
//	lookups.txt    REFERENCE,TIMESTAMP mỗi dòng, theo thứ tự ngẫu nhiên (find N lấy N dòng đầu)
//	expected.jsonl bản ghi đầy đủ của các key đó, định dạng chuẩn, để so toàn vẹn
//	unknown.txt    một key không tồn tại, cùng ngày, để find-unknown phải trả rỗng
//	meta.json      mã của lần bốc, số key, số lượt, bảng
//
// Với ORACLE_REPEAT > 1, mỗi key được gán một lượt ngẫu nhiên giống hệt cách dump dựng một ngày:
// REFERENCE thêm tiền tố LPAD(lượt, 4) và ID cộng lượt × 1.000.000.000.
func Sample(c OracleConfig, size int, dir string) error {
	src, err := OpenOracle(c)
	if err != nil {
		return err
	}
	defer src.Close()
	ctx := context.Background()

	ids, err := randomIDs(ctx, src.conn, c.Table, size)
	if err != nil {
		return err
	}
	if len(ids) < size {
		return fmt.Errorf("bảng %s chỉ có %d dòng, cần %d", c.Table, len(ids), size)
	}
	rows, err := rowsByID(ctx, src.conn, c, ids)
	if err != nil {
		return err
	}

	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	lookups, err := os.Create(filepath.Join(dir, "lookups.txt"))
	if err != nil {
		return err
	}
	defer lookups.Close()
	expected, err := os.Create(filepath.Join(dir, "expected.jsonl"))
	if err != nil {
		return err
	}
	defer expected.Close()
	lw, ew := bufio.NewWriter(lookups), bufio.NewWriter(expected)
	for _, id := range ids { // giữ thứ tự ngẫu nhiên của lần bốc
		row := rows[id]
		if c.Repeat > 1 {
			pass := mrand.IntN(c.Repeat)
			row.Reference = fmt.Sprintf("%04d%s", pass, row.Reference)
			row.ID += int64(pass) * repeatIDStep
		}
		fmt.Fprintf(lw, "%s,%s\n", row.Reference, row.CreatedAt.Format(timestampLayout))
		WriteJSONL(ew, row)
	}
	if err := lw.Flush(); err != nil {
		return err
	}
	if err := ew.Flush(); err != nil {
		return err
	}

	unknown, err := unknownKey(ctx, src.conn, c, rows[ids[0]].CreatedAt)
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(dir, "unknown.txt"), []byte(unknown+"\n"), 0o644); err != nil {
		return err
	}

	token := make([]byte, 4)
	rand.Read(token)
	meta, _ := json.MarshalIndent(map[string]any{
		"id": hex.EncodeToString(token), "size": size, "repeat": c.Repeat, "table": c.Table,
		"time": time.Now().Format("2006-01-02 15:04"),
	}, "", "  ")
	return os.WriteFile(filepath.Join(dir, "meta.json"), append(meta, '\n'), 0o644)
}

// randomIDs chọn ngẫu nhiên trên khoá chính, rẻ hơn nhiều so với sắp xếp cả dòng 21 KB.
func randomIDs(ctx context.Context, conn *sql.Conn, table string, size int) ([]int64, error) {
	rows, err := conn.QueryContext(ctx, fmt.Sprintf(
		"SELECT ID FROM (SELECT ID FROM %s ORDER BY DBMS_RANDOM.VALUE) WHERE ROWNUM <= :1", table), size)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

// rowsByID đọc đủ cột của các ID đã chọn, theo lô 500 tham số.
func rowsByID(ctx context.Context, conn *sql.Conn, c OracleConfig, ids []int64) (map[int64]Row, error) {
	var texts strings.Builder
	for i := 1; i <= c.TextColumns; i++ {
		fmt.Fprintf(&texts, ", TEXT_%02d", i)
	}
	out := make(map[int64]Row, len(ids))
	for start := 0; start < len(ids); start += 500 {
		batch := ids[start:min(start+500, len(ids))]
		marks := make([]string, len(batch))
		args := make([]any, len(batch))
		for i, id := range batch {
			marks[i] = fmt.Sprintf(":%d", i+1)
			args[i] = id
		}
		query := fmt.Sprintf("SELECT ID, REFERENCE, CREATED_AT%s FROM %s WHERE ID IN (%s)",
			texts.String(), c.Table, strings.Join(marks, ","))
		rows, err := conn.QueryContext(ctx, query, args...)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var row Row
			var createdAt time.Time
			nulls := make([]sql.NullString, c.TextColumns)
			dest := []any{&row.ID, &row.Reference, &createdAt}
			for i := range nulls {
				dest = append(dest, &nulls[i])
			}
			if err := rows.Scan(dest...); err != nil {
				rows.Close()
				return nil, err
			}
			row.CreatedAt = time.Date(createdAt.Year(), createdAt.Month(), createdAt.Day(), createdAt.Hour(),
				createdAt.Minute(), createdAt.Second(), createdAt.Nanosecond(), time.UTC)
			row.Texts = make([]*string, c.TextColumns)
			for i, n := range nulls {
				if n.Valid {
					value := n.String
					row.Texts[i] = &value
				}
			}
			out[row.ID] = row
		}
		if err := rows.Close(); err != nil {
			return nil, err
		}
	}
	return out, nil
}

// unknownKey tạo một REFERENCE không có trong bảng, cùng ngày với dữ liệu, để tra phải trả rỗng.
func unknownKey(ctx context.Context, conn *sql.Conn, c OracleConfig, day time.Time) (string, error) {
	for {
		ref := fmt.Sprintf("FT26%03d%09d", mrand.IntN(366), mrand.IntN(1_000_000_000))
		var n int
		if err := conn.QueryRowContext(ctx, fmt.Sprintf("SELECT COUNT(*) FROM %s WHERE REFERENCE = :1", c.Table), ref).Scan(&n); err != nil {
			return "", err
		}
		if n == 0 {
			if c.Repeat > 1 {
				ref = fmt.Sprintf("%04d%s", mrand.IntN(c.Repeat), ref)
			}
			return ref + "," + day.Format(timestampLayout), nil
		}
	}
}
