package com.mbbank.profile.profiler;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.StringJoiner;

/** Writes every found record as JSON Lines: all columns in file order, with null for NULL. */
public final class ResultFile {

    private ResultFile() {}

    /** Sits next to the chart under the same name. */
    public static Path pathFor(Path chart) {
        return Path.of(chart.toString().replaceFirst("\\.html$", ".jsonl"));
    }

    public static void write(Path file, List<Lookup> lookups) throws IOException {
        try (BufferedWriter writer = Files.newBufferedWriter(file)) {
            for (Lookup lookup : lookups) {
                if (lookup.found()) {
                    writer.write(json(lookup.match()));
                    writer.newLine();
                }
            }
        }
    }

    private static String json(Map<String, Object> record) {
        StringJoiner fields = new StringJoiner(",", "{", "}");
        record.forEach((column, value) -> fields.add(TimelineChart.quote(column) + ":" + jsonValue(value)));
        return fields.toString();
    }

    private static String jsonValue(Object value) {
        if (value == null || value instanceof Number) {
            return String.valueOf(value);
        }
        return TimelineChart.quote(value.toString());
    }
}
