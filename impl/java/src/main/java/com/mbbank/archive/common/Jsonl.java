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

    /** Tiền tố ,"text_NN": dựng sẵn cho 100 cột đầu: String.format mỗi cột mỗi dòng tốn hơn cả việc ghi chuỗi. */
    private static final String[] COLUMN_PREFIX = new String[100];

    static {
        for (int i = 0; i < COLUMN_PREFIX.length; i++) {
            COLUMN_PREFIX[i] = String.format(",\"text_%02d\":", i + 1);
        }
    }

    public static void write(Writer out, Row row) throws IOException {
        StringBuilder line = new StringBuilder(32 << 10);
        append(line, row);
        out.append(line);
    }

    /**
     * Ghi một dòng JSON vào StringBuilder (không khoá). Không dùng StringWriter: nó là StringBuffer, khoá đồng
     * bộ ở từng ký tự, đo được chậm gấp ~3 lần ở 1M dòng x 21 KB.
     */
    public static void append(StringBuilder out, Row row) {
        out.append("{\"id\":").append(row.id()).append(",\"reference\":");
        string(out, row.reference());
        out.append(",\"created_at\":\"").append(TIMESTAMP.format(row.createdAt())).append('"');
        String[] texts = row.texts();
        for (int i = 0; i < texts.length; i++) {
            out.append(i < COLUMN_PREFIX.length ? COLUMN_PREFIX[i] : String.format(",\"text_%02d\":", i + 1));
            if (texts[i] == null) {
                out.append("null");
            } else {
                string(out, texts[i]);
            }
        }
        out.append("}\n");
    }

    private static void string(StringBuilder out, String value) {
        out.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                default -> {
                    if (c < 0x20) {
                        out.append("\\u00").append(HEX[c >> 4]).append(HEX[c & 0xF]);
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        out.append('"');
    }
}
