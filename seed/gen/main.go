// Command gen sinh 100.000 dòng ngẫu nhiên vào bảng ARCHIVE_PROFILE_DATA của Oracle trong Docker,
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
	"path/filepath"
	"regexp"
	"sort"
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

// passes: số lượt đọc bảng để tạo một ngày (ORACLE_REPEAT, Makefile mặc định 100 = 10M dòng).
// Reference trong file lookup có tiền tố lượt 0000..passes-1 cho khớp.
var passes = func() int {
	n, err := strconv.Atoi(env("ORACLE_REPEAT", "100"))
	if err != nil || n < 1 {
		return 100
	}
	return n
}()

// day: toàn bộ dữ liệu thuộc một ngày, vì mỗi ngày là một file archive (xem CLAUDE.md).
var day = time.Date(2026, 11, 14, 0, 0, 0, 0, time.UTC)

// table đọc từ ORACLE_TABLE như các phương án, để dump đọc đúng bảng vừa sinh.
var table = strings.ToUpper(env("ORACLE_TABLE", "ARCHIVE_PROFILE_DATA"))

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
	fmt.Printf("SEED=%d\n", seed)

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
	if _, err := db.Exec("BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, :1); END;", table); err != nil {
		return err
	}
	return writeRefs(rnd, references, createdAt)
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

// writeRefs ghi các bộ lookup (REFERENCE,TIMESTAMP mỗi dòng) cho ngày gồm `passes` lượt:
// one, many-1000 và missing.
func writeRefs(rnd *rand.Rand, references []string, createdAt []time.Time) error {
	const layout = "2006-01-02 15:04:05.000000"
	// Với một lượt, OracleSource không thêm tiền tố lượt vào reference.
	prefix := func(pass int) string {
		if passes == 1 {
			return ""
		}
		return fmt.Sprintf("%04d", pass)
	}
	pick := func() string {
		i := rnd.IntN(len(references))
		return prefix(rnd.IntN(passes)) + references[i] + "," + createdAt[i].Format(layout)
	}
	many := map[string]bool{}
	for len(many) < 1000 {
		many[pick()] = true
	}
	manyList := make([]string, 0, len(many))
	for r := range many {
		manyList = append(manyList, r)
	}
	sort.Strings(manyList)
	rnd.Shuffle(len(manyList), func(i, j int) { manyList[i], manyList[j] = manyList[j], manyList[i] })

	existing := map[string]bool{}
	for _, r := range references {
		existing[r] = true
	}
	i := rnd.IntN(len(references))
	ts := createdAt[i].Format(layout)
	missing := []string{
		"ZZZZ," + ts, // sau cuối file
		"AAAA," + ts, // trước đầu file
		prefix(0) + references[i] + "," + createdAt[i].Add(time.Microsecond).Format(layout), // đúng key, sai giờ
		prefix(0) + references[i] + "," + createdAt[i].AddDate(0, 0, 1).Format(layout),      // đúng key, ngày không có file
	}
	for len(missing) < 6 { // trong khoảng nhưng không tồn tại
		r := fmt.Sprintf("FT26%03d%09d", rnd.IntN(366), rnd.IntN(1_000_000_000))
		if !existing[r] {
			missing = append(missing, prefix(rnd.IntN(passes))+r+","+ts)
		}
	}
	dir := env("REFS_DIR", filepath.Join("bench", "refs"))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	for name, list := range map[string][]string{"one": {pick()}, "many-1000": manyList, "missing": missing} {
		if err := os.WriteFile(filepath.Join(dir, name+".txt"), []byte(strings.Join(list, "\n")+"\n"), 0o644); err != nil {
			return err
		}
	}
	fmt.Printf("   %s/: one.txt, many-1000.txt, missing.txt\n", dir)
	return nil
}
