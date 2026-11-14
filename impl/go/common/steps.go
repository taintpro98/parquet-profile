package common

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const timestampLayout = "2006-01-02 15:04:05.000000"

// Lookup is one FIND request: key + timestamp. The timestamp picks the day file, the key the record.
type Lookup struct {
	Reference string
	CreatedAt time.Time
}

func dayFile(archive Archive, dir string, day string) string {
	return filepath.Join(dir, day+"."+archive.Extension())
}

// Dump writes one file per day, DIR/YYYY-MM-DD.EXT. Each goes to a .tmp file first and is renamed
// only on success, so a failed dump never replaces a day that was already archived.
func Dump(archive Archive, source OracleConfig, dir string) (int64, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return 0, err
	}
	oracle, err := OpenOracle(source)
	if err != nil {
		return 0, err
	}
	defer oracle.Close()
	days := &daySplitter{source: oracle, written: map[string]bool{}}
	var total int64
	for {
		more, err := days.nextDay()
		if err != nil {
			return total, err
		}
		if !more {
			return total, nil
		}
		file := dayFile(archive, dir, days.day)
		tmp := file + ".tmp"
		rows, err := archive.Dump(days, tmp)
		if err == nil {
			err = os.Rename(tmp, file)
		}
		os.Remove(tmp)
		if err != nil {
			return total, err
		}
		total += rows
	}
}

// Find looks each key up in the file of its day and keeps a record only when its timestamp
// matches too. It writes the canonical result file, sorted by reference, and returns the count.
func Find(archive Archive, dir string, lookups []Lookup, out string) (int, error) {
	if info, err := os.Stat(dir); err != nil || !info.IsDir() {
		return 0, fmt.Errorf("archive directory not found: %s", dir)
	}
	byDay := map[string]map[string]map[int64]bool{} // day -> key -> timestamps asked, in micros
	for _, l := range lookups {
		day := l.CreatedAt.Format(time.DateOnly)
		if byDay[day] == nil {
			byDay[day] = map[string]map[int64]bool{}
		}
		if byDay[day][l.Reference] == nil {
			byDay[day][l.Reference] = map[int64]bool{}
		}
		byDay[day][l.Reference][l.CreatedAt.UnixMicro()] = true
	}
	var rows []Row
	for day, keys := range byDay {
		file := dayFile(archive, dir, day)
		if _, err := os.Stat(file); err != nil {
			continue // nothing archived for that day
		}
		refs := make([]string, 0, len(keys))
		for k := range keys {
			refs = append(refs, k)
		}
		sort.Strings(refs)
		found, err := archive.Find(file, refs)
		if err != nil {
			return 0, err
		}
		for _, row := range found {
			if keys[row.Reference][row.CreatedAt.UnixMicro()] {
				rows = append(rows, row)
			}
		}
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Reference < rows[j].Reference })
	f, err := os.Create(out)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	w := bufio.NewWriter(f)
	for _, row := range rows {
		WriteJSONL(w, row)
	}
	if err := w.Flush(); err != nil {
		return 0, err
	}
	return len(rows), f.Close()
}

// ReadLookups reads one "REFERENCE,YYYY-MM-DD HH:MM:SS.ffffff" per line; duplicates count once.
func ReadLookups(file string) ([]Lookup, error) {
	data, err := os.ReadFile(file)
	if err != nil {
		return nil, err
	}
	seen := map[Lookup]bool{}
	var lookups []Lookup
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		comma := strings.LastIndexByte(line, ',')
		if comma < 0 {
			return nil, fmt.Errorf("expected REFERENCE,TIMESTAMP: %s", line)
		}
		t, err := time.Parse(timestampLayout, strings.TrimSpace(line[comma+1:]))
		if err != nil {
			return nil, err
		}
		l := Lookup{Reference: strings.TrimSpace(line[:comma]), CreatedAt: t}
		if !seen[l] {
			seen[l] = true
			lookups = append(lookups, l)
		}
	}
	if len(lookups) == 0 {
		return nil, fmt.Errorf("no lookups in %s", file)
	}
	return lookups, nil
}

// daySplitter splits a cursor ordered by (day, REFERENCE) into one cursor per day.
type daySplitter struct {
	source  RowCursor
	written map[string]bool
	pending bool // the source is on a row that belongs to the next day
	first   bool
	day     string
}

func (d *daySplitter) nextDay() (bool, error) {
	if !d.pending {
		more, err := d.source.Next()
		if err != nil || !more {
			return false, err
		}
	}
	d.day = d.source.CreatedAt().Format(time.DateOnly)
	if d.written[d.day] {
		return false, fmt.Errorf("rows are not ordered by day: %s appears twice", d.day)
	}
	d.written[d.day] = true
	d.pending, d.first = false, true
	return true, nil
}

func (d *daySplitter) Next() (bool, error) {
	if d.first {
		d.first = false
		return true, nil
	}
	if d.pending {
		return false, nil
	}
	more, err := d.source.Next()
	if err != nil || !more {
		return false, err
	}
	if d.source.CreatedAt().Format(time.DateOnly) != d.day {
		d.pending = true
		return false, nil
	}
	return true, nil
}

func (d *daySplitter) ID() int64                      { return d.source.ID() }
func (d *daySplitter) Reference() string              { return d.source.Reference() }
func (d *daySplitter) CreatedAt() time.Time           { return d.source.CreatedAt() }
func (d *daySplitter) TextColumns() int               { return d.source.TextColumns() }
func (d *daySplitter) Text(column int) (string, bool) { return d.source.Text(column) }
