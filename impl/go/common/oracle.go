package common

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	godror "github.com/godror/godror"
	goora "github.com/sijms/go-ora/v2"
)

const repeatIDStep = 1_000_000_000

// OracleConfig has the same environment variables and defaults as the Java implementation.
type OracleConfig struct {
	URL, Username, Password, Table, Query        string
	Driver, ClientLib                            string
	Repeat, FetchSize, PrefetchRows, TextColumns int
	ProgressInterval                             int64
	RepeatSingle                                 bool
}

func OracleConfigFromEnv() OracleConfig {
	return OracleConfig{
		URL:              String("ORACLE_URL", "jdbc:oracle:thin:@//localhost:1521/FREEPDB1"),
		Username:         String("ORACLE_USERNAME", "benchmark"),
		Password:         String("ORACLE_PASSWORD", "benchmark"),
		Table:            String("ORACLE_TABLE", "ARCHIVE_RANDOM"),
		Query:            String("ORACLE_QUERY", ""),
		Driver:           String("ORACLE_DRIVER", "godror"),
		ClientLib:        String("ORACLE_CLIENT_LIB", ""),
		Repeat:           int(Int("ORACLE_REPEAT", 1)),
		RepeatSingle:     Bool("ORACLE_REPEAT_SINGLE", false),
		FetchSize:        int(Int("ORACLE_FETCH_SIZE", defaultFetchSize(String("ORACLE_DRIVER", "godror")))),
		PrefetchRows:     int(Int("ORACLE_PREFETCH_ROWS", 0)),
		TextColumns:      int(Int("ORACLE_TEXT_COLUMNS", 70)),
		ProgressInterval: Int("PROGRESS_INTERVAL", 500_000),
	}
}

// defaultFetchSize: godror cấp buffer lô theo độ rộng khai báo của cột (~95 MB mỗi 1.000 dòng với 70 cột
// VARCHAR2(1000)), nên dùng lô 1.000; go-ora giữ 10.000 như ojdbc. Đo ở 1M dòng: lô 1.000 + tắt prefetch
// 448 MB / 339 s, lô 5.000 + prefetch 1229 MB / 343 s; thời gian không đổi theo lô.
func defaultFetchSize(driver string) int64 {
	if driver == "godror" {
		return 1_000
	}
	return 10_000
}

// OracleSource streams the table in REFERENCE order as a RowCursor.
type OracleSource struct {
	config OracleConfig
	db     *sql.DB
	conn   *sql.Conn
	stmt   *sql.Stmt
	rows   *sql.Rows
	pass   int
	count  int64

	id        int64
	reference string
	createdAt time.Time
	texts     []sql.NullString
	dest      []any
}

// The JDBC form //host:port/service is shared with Java, so ORACLE_URL works for both.
var jdbcURL = regexp.MustCompile(`^jdbc:oracle:thin:@//([^:/]+):(\d+)/(.+)$`)

func OpenOracle(c OracleConfig) (*OracleSource, error) {
	if c.Repeat < 1 || c.Repeat > 9999 {
		return nil, fmt.Errorf("ORACLE_REPEAT must be 1 to 9999")
	}
	m := jdbcURL.FindStringSubmatch(c.URL)
	if m == nil {
		return nil, fmt.Errorf("ORACLE_URL must look like jdbc:oracle:thin:@//host:port/service, got %s", c.URL)
	}
	db, err := openDB(c, m[1], m[2], m[3])
	if err != nil {
		return nil, err
	}
	s := &OracleSource{config: c, db: db}
	if err := s.prepare(); err != nil {
		db.Close()
		return nil, err
	}
	s.texts = make([]sql.NullString, c.TextColumns)
	s.dest = []any{&s.id, &s.reference, &s.createdAt}
	for i := range s.texts {
		s.dest = append(s.dest, &s.texts[i])
	}
	return s, nil
}

// openDB mở kết nối bằng driver ORACLE_DRIVER:
//   - godror (mặc định): bọc ODPI-C, thư viện C chính chủ của Oracle (cần Oracle Instant Client,
//     ORACLE_CLIENT_LIB là thư mục chứa nó). Lô fetch giữ trong buffer của thư viện Oracle như ojdbc.
//   - go-ora: Go thuần, tự cài lại giao thức; giải mã cả lô fetch thành giá trị Go ngay khi nhận, tốn
//     khoảng 5 lần kích thước dòng cho mỗi dòng trong lô (đo được: lô 10.000 dòng ~21 KB → ~1.2 GB).
func openDB(c OracleConfig, host, port, service string) (*sql.DB, error) {
	switch c.Driver {
	case "godror":
		var p godror.ConnectionParams
		p.Username, p.Password = c.Username, godror.NewPassword(c.Password)
		p.ConnectString = fmt.Sprintf("%s:%s/%s", host, port, service)
		p.LibDir = c.ClientLib
		p.Timezone, p.NoTZCheck = time.UTC, true // TIMESTAMP không múi giờ: giữ nguyên giờ ghi trong bảng
		p.StandaloneConnection = sql.NullBool{Bool: true, Valid: true}
		return sql.OpenDB(godror.NewConnector(p)), nil
	case "go-ora":
		n, _ := strconv.Atoi(port)
		dsn := goora.BuildUrl(host, n, service, c.Username, c.Password,
			map[string]string{"PREFETCH_ROWS": strconv.Itoa(c.FetchSize)})
		return sql.Open("oracle", dsn)
	}
	return nil, fmt.Errorf("ORACLE_DRIVER must be godror or go-ora, got %s", c.Driver)
}

// prepare pins one connection, because NLS_SORT is a session setting. Archives compare UTF-8
// bytes, so REFERENCE must be binary sorted whatever language the session derives.
func (s *OracleSource) prepare() error {
	ctx := context.Background()
	conn, err := s.db.Conn(ctx)
	if err != nil {
		return err
	}
	s.conn = conn
	if _, err := conn.ExecContext(ctx, "ALTER SESSION SET NLS_SORT = BINARY"); err != nil {
		return err
	}
	query, err := s.selectSQL()
	if err != nil {
		return err
	}
	s.stmt, err = conn.PrepareContext(ctx, query)
	return err
}

func (s *OracleSource) selectSQL() (string, error) {
	var texts strings.Builder
	for i := 1; i <= s.config.TextColumns; i++ {
		fmt.Fprintf(&texts, ", TEXT_%02d", i)
	}
	if s.config.Repeat > 1 {
		if s.config.Query != "" {
			return "", fmt.Errorf("ORACLE_REPEAT cannot be combined with ORACLE_QUERY")
		}
		// The pass number prefixes REFERENCE, so the passes concatenate into one sorted file.
		// The table must hold a single day, or the passes would interleave days.
		if s.config.RepeatSingle {
			// ORACLE_REPEAT_SINGLE: cả ngày trong một câu SELECT (UNION ALL các lượt) như khi chạy thật,
			// thay vì chạy lại câu SELECT ở mỗi lượt. Sai thứ tự thì SSTFileWriter báo lỗi.
			parts := make([]string, s.config.Repeat)
			for p := range parts {
				parts[p] = fmt.Sprintf("SELECT * FROM (SELECT ID + %d * %d, LPAD('%d', 4, '0') || REFERENCE, CREATED_AT%s FROM %s ORDER BY TRUNC(CREATED_AT), REFERENCE)",
					p, repeatIDStep, p, texts.String(), s.config.Table)
			}
			return strings.Join(parts, " UNION ALL "), nil
		}
		return fmt.Sprintf("SELECT ID + :1 * %d, LPAD(:2, 4, '0') || REFERENCE, CREATED_AT%s FROM %s ORDER BY TRUNC(CREATED_AT), REFERENCE",
			repeatIDStep, texts.String(), s.config.Table), nil
	}
	if s.config.Query != "" {
		return s.config.Query, nil
	}
	// Day first, because every day goes to its own file, then REFERENCE within the day.
	return fmt.Sprintf("SELECT ID, REFERENCE, CREATED_AT%s FROM %s ORDER BY TRUNC(CREATED_AT), REFERENCE", texts.String(), s.config.Table), nil
}

func (s *OracleSource) Next() (bool, error) {
	for {
		if s.rows == nil {
			if s.pass == s.config.Repeat || (s.config.RepeatSingle && s.pass == 1) {
				return false, nil
			}
			var args []any
			if s.config.Repeat > 1 && !s.config.RepeatSingle {
				args = []any{s.pass, s.pass}
			}
			if s.config.Driver == "godror" {
				// ORACLE_PREFETCH_ROWS: số dòng Oracle client đọc trước vào buffer riêng, thêm một buffer cỡ lô
				// (0 = tắt, mặc định; -1 = lô + 1). Tắt bớt ~225 MB ở lô 5.000 mà không chậm hơn.
				prefetch := s.config.PrefetchRows
				if prefetch < 0 {
					prefetch = s.config.FetchSize + 1
				}
				args = append(args, godror.FetchArraySize(s.config.FetchSize), godror.PrefetchCount(prefetch))
			}
			s.pass++
			rows, err := s.stmt.Query(args...)
			if err != nil {
				return false, err
			}
			s.rows = rows
		}
		if s.rows.Next() {
			if err := s.rows.Scan(s.dest...); err != nil {
				return false, err
			}
			if s.count++; s.count%s.config.ProgressInterval == 0 {
				fmt.Fprintf(os.Stderr, "Processed %d rows\n", s.count)
			}
			return true, nil
		}
		if err := s.rows.Err(); err != nil {
			return false, err
		}
		s.rows.Close()
		s.rows = nil
	}
}

func (s *OracleSource) ID() int64         { return s.id }
func (s *OracleSource) Reference() string { return s.reference }
func (s *OracleSource) TextColumns() int  { return s.config.TextColumns }

// CreatedAt keeps the wall clock fields and drops whatever location the driver attached.
func (s *OracleSource) CreatedAt() time.Time {
	t := s.createdAt
	return time.Date(t.Year(), t.Month(), t.Day(), t.Hour(), t.Minute(), t.Second(), t.Nanosecond(), time.UTC)
}

// Text: Oracle coi VARCHAR2 rỗng là NULL, nên "" cũng là NULL. godror trả NULL thành "" (Valid=true,
// code cứng trong driver), go-ora trả Valid=false; cả hai ra cùng một kết quả.
func (s *OracleSource) Text(column int) (string, bool) {
	t := s.texts[column-1]
	return t.String, t.Valid && t.String != ""
}

func (s *OracleSource) Close() error {
	if s.rows != nil {
		s.rows.Close()
	}
	if s.stmt != nil {
		s.stmt.Close()
	}
	if s.conn != nil {
		s.conn.Close()
	}
	return s.db.Close()
}
