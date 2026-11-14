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

	goora "github.com/sijms/go-ora/v2"
)

const repeatIDStep = 1_000_000_000

// OracleConfig has the same environment variables and defaults as the Java implementation.
type OracleConfig struct {
	URL, Username, Password, Table, Query string
	Repeat, FetchSize, TextColumns        int
	ProgressInterval                      int64
}

func OracleConfigFromEnv() OracleConfig {
	return OracleConfig{
		URL:              String("ORACLE_URL", "jdbc:oracle:thin:@//localhost:1521/FREEPDB1"),
		Username:         String("ORACLE_USERNAME", "benchmark"),
		Password:         String("ORACLE_PASSWORD", "benchmark"),
		Table:            String("ORACLE_TABLE", "ARCHIVE_PROFILE_DATA"),
		Query:            String("ORACLE_QUERY", ""),
		Repeat:           int(Int("ORACLE_REPEAT", 1)),
		FetchSize:        int(Int("ORACLE_FETCH_SIZE", 1000)),
		TextColumns:      int(Int("ORACLE_TEXT_COLUMNS", 70)),
		ProgressInterval: Int("PROGRESS_INTERVAL", 500_000),
	}
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
	port, _ := strconv.Atoi(m[2])
	dsn := goora.BuildUrl(m[1], port, m[3], c.Username, c.Password,
		map[string]string{"PREFETCH_ROWS": strconv.Itoa(c.FetchSize)})
	db, err := sql.Open("oracle", dsn)
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
			if s.pass == s.config.Repeat {
				return false, nil
			}
			var args []any
			if s.config.Repeat > 1 {
				args = []any{s.pass, s.pass}
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

func (s *OracleSource) Text(column int) (string, bool) {
	t := s.texts[column-1]
	return t.String, t.Valid
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
