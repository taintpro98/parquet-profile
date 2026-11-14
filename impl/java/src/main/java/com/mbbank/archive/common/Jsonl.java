package com.mbbank.archive.common;

import java.io.IOException;
import java.io.Writer;
import java.time.format.DateTimeFormatter;

/**
 * Writes the canonical FIND output that every implementation must produce byte for byte: the same
 * output as Python's {@code json.dumps(record, ensure_ascii=False, separators=(",", ":"))}.
 */
public final class Jsonl {

    private static final DateTimeFormatter TIMESTAMP = DateTimeFormatter.ofPattern("uuuu-MM-dd HH:mm:ss.SSSSSS");
    private static final char[] HEX = "0123456789abcdef".toCharArray();

    private Jsonl() {}

    public static void write(Writer out, Row row) throws IOException {
        out.write("{\"id\":");
        out.write(Long.toString(row.id()));
        out.write(",\"reference\":");
        string(out, row.reference());
        out.write(",\"created_at\":\"");
        out.write(TIMESTAMP.format(row.createdAt()));
        out.write('"');
        String[] texts = row.texts();
        for (int i = 0; i < texts.length; i++) {
            out.write(String.format(",\"text_%02d\":", i + 1));
            if (texts[i] == null) {
                out.write("null");
            } else {
                string(out, texts[i]);
            }
        }
        out.write("}\n");
    }

    private static void string(Writer out, String value) throws IOException {
        out.write('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> out.write("\\\"");
                case '\\' -> out.write("\\\\");
                case '\n' -> out.write("\\n");
                case '\r' -> out.write("\\r");
                case '\t' -> out.write("\\t");
                case '\b' -> out.write("\\b");
                case '\f' -> out.write("\\f");
                default -> {
                    if (c < 0x20) {
                        out.write("\\u00");
                        out.write(HEX[c >> 4]);
                        out.write(HEX[c & 0xF]);
                    } else {
                        out.write(c);
                    }
                }
            }
        }
        out.write('"');
    }
}
