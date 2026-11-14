package com.mbbank.archive.common;

import com.mbbank.archive.archive.Archives;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * The command line shared by every implementation, Java and Go alike:
 *
 * <pre>
 * list
 * dump --impl NAME --archive DIR
 * find --impl NAME --archive DIR --refs LOOKUPS --out RESULT.jsonl
 * </pre>
 *
 * DIR holds one file per day. LOOKUPS has one "REFERENCE,YYYY-MM-DD HH:MM:SS.ffffff" per line.
 *
 * The last stdout line is JSON for the runner: {"rows":N} after dump, {"found":N} after find.
 */
public final class Cli {

    private Cli() {}

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            usage();
        }
        Map<String, String> options = options(args);
        switch (args[0]) {
            case "list" -> Archives.ALL.forEach((name, archive) ->
                    System.out.println(name + "\t" + archive.get().extension()));
            case "dump" -> {
                long rows = dump(
                        Archives.get(required(options, "impl")),
                        OracleSource.Config.fromEnv(),
                        Path.of(required(options, "archive")));
                System.out.printf("{\"rows\":%d}%n", rows);
            }
            case "find" -> {
                int found = find(
                        Archives.get(required(options, "impl")),
                        Path.of(required(options, "archive")),
                        Lookup.read(Path.of(required(options, "refs"))),
                        Path.of(required(options, "out")));
                System.out.printf("{\"found\":%d}%n", found);
            }
            default -> usage();
        }
    }

    /**
     * Writes one file per day, DIR/YYYY-MM-DD.EXT. Each goes to a .tmp file first and is renamed only
     * on success, so a failed dump never replaces a day that was already archived.
     */
    public static long dump(Archive archive, OracleSource.Config source, Path dir) throws Exception {
        Files.createDirectories(dir);
        long rows = 0;
        try (OracleSource oracle = new OracleSource(source)) {
            DaySplitter days = new DaySplitter(oracle);
            while (days.nextDay()) {
                Path file = dayFile(archive, dir, days.day());
                Path tmp = file.resolveSibling(file.getFileName() + ".tmp");
                try {
                    rows += archive.dump(days, tmp);
                    Files.move(tmp, file, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
                } finally {
                    Files.deleteIfExists(tmp);
                }
            }
        }
        return rows;
    }

    /**
     * Looks each key up in the file of its day and keeps a record only when its timestamp matches
     * too. Writes the canonical result file, sorted by reference, and returns the number found.
     */
    public static int find(Archive archive, Path dir, List<Lookup> lookups, Path out) throws Exception {
        if (!Files.isDirectory(dir)) {
            throw new IllegalArgumentException("Archive directory not found: " + dir);
        }
        // day -> key -> the timestamps asked for that key
        Map<LocalDate, Map<String, Set<LocalDateTime>>> byDay = new TreeMap<>();
        for (Lookup lookup : lookups) {
            byDay.computeIfAbsent(lookup.day(), day -> new LinkedHashMap<>())
                    .computeIfAbsent(lookup.reference(), key -> new HashSet<>())
                    .add(lookup.createdAt());
        }
        List<Row> rows = new ArrayList<>();
        for (Map.Entry<LocalDate, Map<String, Set<LocalDateTime>>> day : byDay.entrySet()) {
            Path file = dayFile(archive, dir, day.getKey());
            if (!Files.exists(file)) {
                continue; // nothing archived for that day
            }
            for (Row row : archive.find(file, day.getValue().keySet())) {
                if (day.getValue().getOrDefault(row.reference(), Set.of()).contains(row.createdAt())) {
                    rows.add(row);
                }
            }
        }
        rows.sort(Comparator.comparing(Row::reference));
        try (BufferedWriter writer = Files.newBufferedWriter(out, StandardCharsets.UTF_8)) {
            for (Row row : rows) {
                Jsonl.write(writer, row);
            }
        }
        return rows.size();
    }

    private static Path dayFile(Archive archive, Path dir, LocalDate day) {
        return dir.resolve(day + "." + archive.extension());
    }

    private static Map<String, String> options(String[] args) {
        Map<String, String> options = new HashMap<>();
        for (int i = 1; i < args.length; i += 2) {
            if (!args[i].startsWith("--") || i + 1 >= args.length) {
                usage();
            }
            options.put(args[i].substring(2), args[i + 1]);
        }
        return options;
    }

    private static String required(Map<String, String> options, String name) {
        String value = options.get(name);
        if (value == null) {
            usage();
        }
        return value;
    }

    private static void usage() {
        System.err.println("""
                Usage:
                  list
                  dump --impl NAME --archive DIR
                  find --impl NAME --archive DIR --refs LOOKUPS --out RESULT.jsonl""");
        System.exit(2);
    }
}
