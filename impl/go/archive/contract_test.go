package archive

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
	"time"

	"parquetprofile/common"
)

// TestContract runs for every option in All: dump ARCHIVE_GOLDEN from Oracle (its rows span many
// days, so many day files), look every record up by key + timestamp together with lookups that
// must find nothing, and require the result to equal testdata/golden.jsonl byte for byte.
// Needs the local Oracle with the golden table: make seed.
func TestContract(t *testing.T) {
	data, err := os.ReadFile("../../../testdata/golden.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	golden := strings.Split(strings.TrimSuffix(string(data), "\n"), "\n")
	var lookups []common.Lookup
	for _, m := range regexp.MustCompile(`"reference":"([^"]*)","created_at":"([^"]*)"`).FindAllStringSubmatch(string(data), -1) {
		ts, err := time.Parse("2006-01-02 15:04:05.000000", m[2])
		if err != nil {
			t.Fatal(err)
		}
		lookups = append(lookups, common.Lookup{Reference: m[1], CreatedAt: ts})
	}
	existing := lookups[0]
	lookups = append(lookups,
		common.Lookup{Reference: "REF-MISSING", CreatedAt: existing.CreatedAt},                            // key không có
		common.Lookup{Reference: existing.Reference, CreatedAt: existing.CreatedAt.Add(time.Microsecond)}, // sai giờ
		common.Lookup{Reference: existing.Reference, CreatedAt: existing.CreatedAt.AddDate(0, 0, 1)},      // sai ngày
		common.Lookup{Reference: "ZZZ", CreatedAt: time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)},           // ngày không có file
	)

	impl := os.Getenv("IMPL") // all Go options, or only those named, e.g. IMPL=go-parquet-duckdb
	for _, name := range Names() {
		if impl != "" && impl != "all" && !slices.Contains(strings.Split(impl, ","), "go-"+name) {
			continue
		}
		t.Run(name, func(t *testing.T) {
			a, _ := Get(name)
			dir := filepath.Join(t.TempDir(), "archive")
			source := common.OracleConfigFromEnv()
			source.Table = "ARCHIVE_GOLDEN"

			rows, err := common.Dump(a, source, dir)
			if err != nil {
				t.Fatal(err)
			}
			if rows != int64(len(golden)) {
				t.Fatalf("rows dumped = %d, want %d", rows, len(golden))
			}
			if files, _ := os.ReadDir(dir); len(files) < 2 {
				t.Fatalf("want one file per day, got %d files", len(files))
			}
			out := filepath.Join(t.TempDir(), "result.jsonl")
			found, err := common.Find(a, dir, lookups, out)
			if err != nil {
				t.Fatal(err)
			}
			if found != len(golden) {
				t.Fatalf("records found = %d, want %d", found, len(golden))
			}
			result, _ := os.ReadFile(out)
			actual := strings.Split(strings.TrimSuffix(string(result), "\n"), "\n")
			for i := range golden {
				if actual[i] != golden[i] {
					t.Fatalf("line %d\n got: %.300s\nwant: %.300s", i+1, actual[i], golden[i])
				}
			}
		})
	}
}
