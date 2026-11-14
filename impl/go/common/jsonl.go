package common

import (
	"bufio"
	"fmt"
	"strconv"
)

const hexDigits = "0123456789abcdef"

// WriteJSONL writes the canonical FIND output that every implementation must produce byte for byte:
// the same output as Python's json.dumps(record, ensure_ascii=False, separators=(",", ":")).
func WriteJSONL(w *bufio.Writer, row Row) {
	w.WriteString(`{"id":`)
	w.WriteString(strconv.FormatInt(row.ID, 10))
	w.WriteString(`,"reference":`)
	writeString(w, row.Reference)
	w.WriteString(`,"created_at":"`)
	w.WriteString(row.CreatedAt.Format("2006-01-02 15:04:05.000000"))
	w.WriteByte('"')
	for i, text := range row.Texts {
		fmt.Fprintf(w, `,"text_%02d":`, i+1)
		if text == nil {
			w.WriteString("null")
		} else {
			writeString(w, *text)
		}
	}
	w.WriteString("}\n")
}

func writeString(w *bufio.Writer, s string) {
	w.WriteByte('"')
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch c {
		case '"':
			w.WriteString(`\"`)
		case '\\':
			w.WriteString(`\\`)
		case '\n':
			w.WriteString(`\n`)
		case '\r':
			w.WriteString(`\r`)
		case '\t':
			w.WriteString(`\t`)
		case '\b':
			w.WriteString(`\b`)
		case '\f':
			w.WriteString(`\f`)
		default:
			if c < 0x20 {
				w.WriteString(`\u00`)
				w.WriteByte(hexDigits[c>>4])
				w.WriteByte(hexDigits[c&0xF])
			} else {
				w.WriteByte(c) // UTF-8 bytes pass through unchanged
			}
		}
	}
	w.WriteByte('"')
}
