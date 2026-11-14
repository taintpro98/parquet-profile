package common

import "time"

// Row is one complete record as Find returns it. Texts[i] is nil for NULL.
type Row struct {
	ID        int64
	Reference string
	CreatedAt time.Time // wall clock in UTC, no time zone meaning
	Texts     []*string
}

// RowCursor is what Dump reads, in REFERENCE order, one row at a time.
type RowCursor interface {
	Next() (bool, error)
	ID() int64
	Reference() string
	CreatedAt() time.Time
	TextColumns() int
	// Text returns column 1..TextColumns(); ok is false for NULL.
	Text(column int) (value string, ok bool)
}

// Archive is one storage option. Each implementation lives in one file under package archive.
type Archive interface {
	Extension() string
	Dump(rows RowCursor, out string) (int64, error)
	// Find returns the complete record of every reference that exists, in any order.
	Find(file string, references []string) ([]Row, error)
}
