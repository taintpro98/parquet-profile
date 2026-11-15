// Command gen sinh 100.000 dòng vào bảng ORACLE_TABLE của Oracle trong Docker (SEED_MODE random | status),
// rồi ghi các bộ reference cho FIND vào bench/refs/. Chỉ chạy khi bảng chưa tồn tại.
//
// Kích thước dòng giống bộ dữ liệu cũ (khoảng 21 KB), nhưng nội dung ngẫu nhiên: mỗi cột TEXT_nn có
// một "kiểu" theo vị trí (họ tên, số tài khoản, số tiền, mã, địa chỉ…) rồi thêm câu ghép từ ngẫu nhiên.
// SEED=<số> để sinh lại đúng một bộ dữ liệu; mặc định lấy ngẫu nhiên và in ra.
package main

import (
	"database/sql"
	"fmt"
	"math/rand/v2"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	goora "github.com/sijms/go-ora/v2"
)

const (
	rows        = 100_000 // cố định mỗi lần sinh
	textColumns = 70
	batchSize   = 200
)


// day: toàn bộ dữ liệu thuộc một ngày, vì mỗi ngày là một file archive (xem CLAUDE.md).
var day = time.Date(2026, 11, 14, 0, 0, 0, 0, time.UTC)

// seedMode: random (mọi cột text ngẫu nhiên) hoặc status (30% số cột là cột trạng thái lặp nhiều).
var seedMode = env("SEED_MODE", "random")

// table đọc từ ORACLE_TABLE như các phương án, để dump đọc đúng bảng vừa sinh.
var table = strings.ToUpper(env("ORACLE_TABLE", "ARCHIVE_RANDOM"))

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "lỗi:", err)
		os.Exit(1)
	}
}

func run() error {
	seed := uint64(time.Now().UnixNano())
	if s := os.Getenv("SEED"); s != "" {
		v, err := strconv.ParseUint(s, 10, 64)
		if err != nil {
			return fmt.Errorf("SEED: %w", err)
		}
		seed = v
	}
	rnd := rand.New(rand.NewPCG(seed, seed^0x9e3779b97f4a7c15))
	if seedMode != "random" && seedMode != "status" {
		return fmt.Errorf("SEED_MODE phải là random hoặc status, nhận %q", seedMode)
	}
	fmt.Printf("SEED=%d SEED_MODE=%s\n", seed, seedMode)

	db, err := open()
	if err != nil {
		return err
	}
	defer db.Close()

	var exists int
	if err := db.QueryRow("SELECT COUNT(*) FROM user_tables WHERE table_name = :1", table).Scan(&exists); err != nil {
		return err
	}
	if exists > 0 {
		return fmt.Errorf("bảng %s đã tồn tại; xoá bằng make seed-clean trước", table)
	}
	if _, err := db.Exec(createTable()); err != nil {
		return err
	}

	references := uniqueReferences(rnd)
	createdAt := make([]time.Time, rows)
	for i := range createdAt {
		createdAt[i] = randomTime(rnd)
	}
	insert := insertSQL()
	start := time.Now()
	for first := 0; first < rows; first += batchSize {
		args := make([]any, 3+textColumns)
		ids := make([]int64, batchSize)
		refs := make([]string, batchSize)
		times := make([]time.Time, batchSize)
		for i := range ids {
			ids[i] = int64(first + i + 1)
			refs[i] = references[first+i]
			times[i] = createdAt[first+i]
		}
		args[0], args[1], args[2] = ids, refs, times
		for c := 0; c < textColumns; c++ {
			values := make([]sql.NullString, batchSize)
			for i := range values {
				values[i].String, values[i].Valid = textValue(rnd, c+1)
			}
			args[3+c] = values
		}
		if _, err := db.Exec(insert, args...); err != nil {
			return fmt.Errorf("chèn dòng %d: %w", first+1, err)
		}
		if done := first + batchSize; done%20_000 == 0 {
			fmt.Printf("   %d/%d dòng (%s)\n", done, rows, time.Since(start).Round(time.Second))
		}
	}
	// Index tra cứu như bảng giao dịch thật: key + timestamp. Khoá chính ID đã có sẵn.
	if _, err := db.Exec("CREATE INDEX IX_" + table + "_KEY ON " + table + " (REFERENCE, CREATED_AT)"); err != nil {
		return err
	}
	if _, err := db.Exec("BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, :1, cascade => TRUE); END;", table); err != nil {
		return err
	}
	return nil
}

func open() (*sql.DB, error) {
	url := env("ORACLE_URL", "jdbc:oracle:thin:@//localhost:1521/FREEPDB1")
	m := regexp.MustCompile(`^jdbc:oracle:thin:@//([^:/]+):(\d+)/(.+)$`).FindStringSubmatch(url)
	if m == nil {
		return nil, fmt.Errorf("ORACLE_URL phải có dạng jdbc:oracle:thin:@//host:port/service")
	}
	port, _ := strconv.Atoi(m[2])
	return sql.Open("oracle", goora.BuildUrl(m[1], port, m[3],
		env("ORACLE_USERNAME", "benchmark"), env("ORACLE_PASSWORD", "benchmark"), nil))
}

func env(name, def string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return def
}

func createTable() string {
	var b strings.Builder
	b.WriteString("CREATE TABLE " + table + " (ID NUMBER(19) NOT NULL, REFERENCE VARCHAR2(64) NOT NULL, CREATED_AT TIMESTAMP NOT NULL")
	for c := 1; c <= textColumns; c++ {
		fmt.Fprintf(&b, ", TEXT_%02d VARCHAR2(1000)", c)
	}
	b.WriteString(", CONSTRAINT PK_" + table + " PRIMARY KEY (ID))")
	return b.String()
}

func insertSQL() string {
	names := []string{"ID", "REFERENCE", "CREATED_AT"}
	binds := []string{":1", ":2", ":3"}
	for c := 1; c <= textColumns; c++ {
		names = append(names, fmt.Sprintf("TEXT_%02d", c))
		binds = append(binds, fmt.Sprintf(":%d", c+3))
	}
	return "INSERT INTO " + table + " (" + strings.Join(names, ", ") + ") VALUES (" + strings.Join(binds, ", ") + ")"
}

// uniqueReferences trả về reference duy nhất, theo thứ tự chèn ngẫu nhiên (không sẵn thứ tự).
func uniqueReferences(rnd *rand.Rand) []string {
	seen := make(map[string]bool, rows)
	refs := make([]string, 0, rows)
	for len(refs) < rows {
		r := fmt.Sprintf("FT26%03d%09d", rnd.IntN(366), rnd.IntN(1_000_000_000))
		if !seen[r] {
			seen[r] = true
			refs = append(refs, r)
		}
	}
	return refs
}

// randomTime trả về một thời điểm ngẫu nhiên trong ngày, chính xác tới micro giây.
func randomTime(rnd *rand.Rand) time.Time {
	return day.Add(time.Duration(rnd.Int64N(int64(24*time.Hour))) / time.Microsecond * time.Microsecond)
}
