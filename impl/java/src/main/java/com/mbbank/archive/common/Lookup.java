package com.mbbank.archive.common;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** One FIND request: key + timestamp. The timestamp picks the day file, the key the record. */
public record Lookup(String reference, LocalDateTime createdAt) {

    public static final DateTimeFormatter TIMESTAMP = DateTimeFormatter.ofPattern("uuuu-MM-dd HH:mm:ss.SSSSSS");

    public LocalDate day() {
        return createdAt.toLocalDate();
    }

    /** One "REFERENCE,YYYY-MM-DD HH:MM:SS.ffffff" per line; blank lines skipped, duplicates count once. */
    public static List<Lookup> read(Path file) throws IOException {
        Set<Lookup> lookups = new LinkedHashSet<>();
        for (String line : Files.readAllLines(file)) {
            if (line.isBlank()) {
                continue;
            }
            int comma = line.lastIndexOf(',');
            if (comma < 0) {
                throw new IllegalArgumentException("Expected REFERENCE,TIMESTAMP: " + line);
            }
            lookups.add(new Lookup(
                    line.substring(0, comma).strip(),
                    LocalDateTime.parse(line.substring(comma + 1).strip(), TIMESTAMP)));
        }
        if (lookups.isEmpty()) {
            throw new IllegalArgumentException("No lookups in " + file);
        }
        return new ArrayList<>(lookups);
    }
}
