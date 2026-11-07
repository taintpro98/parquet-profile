import com.mbbank.profile.config.DatabaseConfig;
import com.mbbank.profile.duckdb.DuckDbReader;
import com.mbbank.profile.profiler.Lookup;
import com.mbbank.profile.profiler.ProfileResult;
import com.mbbank.profile.profiler.Profiler;
import com.mbbank.profile.profiler.ResultFile;
import com.mbbank.profile.profiler.TimelineChart;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Flow: find many references with one WHERE reference IN (...) query. */
public final class FindMany {

    private static final int MAX_PRINTED_LOOKUPS = 20;

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            System.err.println("Usage: FindMany <REFERENCE|@file> [<REFERENCE|@file> ...]");
            System.exit(2);
        }
        Set<String> references = references(args);
        DatabaseConfig config = DatabaseConfig.load();
        Path file = config.parquetOutput();

        try (DuckDbReader reader = new DuckDbReader(config)) {
            Profiler.Measured<Map<String, Map<String, Object>>> measured =
                    new Profiler().measure(() -> reader.findByReferences(references));
            ProfileResult profile = measured.profile();

            List<Lookup> lookups = new ArrayList<>(references.size());
            for (String reference : references) {
                Map<String, Object> match = measured.value().getOrDefault(reference, Map.of());
                lookups.add(new Lookup(reference, Lookup.NOT_TIMED, match));
            }
            long found = lookups.stream().filter(Lookup::found).count();

            Map<String, String> facts = new LinkedHashMap<>();
            facts.put("Lookups", String.format(Locale.US, "%,d", lookups.size()));
            facts.put("Found", String.format(Locale.US, "%,d", found));
            facts.put("Wall Time", ProfileResult.formatSeconds(profile.wallTimeNanos(), 3));
            facts.put("CPU Time", ProfileResult.formatSeconds(profile.cpuTimeNanos(), 3));
            facts.put("Peak CPU", ProfileResult.formatCores(profile.peakCores()));
            facts.put("Peak Process RAM", ProfileResult.formatBytes(profile.peakProcessMemoryBytes()));
            facts.put("Peak JVM Heap", ProfileResult.formatBytes(profile.peakHeapMemoryBytes()));
            facts.put("Per Reference", ProfileResult.formatMillis(profile.wallTimeNanos() / lookups.size()));
            facts.put("File Size", ProfileResult.formatBytes(Files.size(file)));

            Path chart = TimelineChart.pathFor(file, "-find-many-" + lookups.size());
            Path result = ResultFile.pathFor(chart);
            long exportStart = System.nanoTime();
            ResultFile.write(result, lookups);
            facts.put("Export Time", ProfileResult.formatSeconds(System.nanoTime() - exportStart, 3));
            facts.put("Result Size", ProfileResult.formatBytes(Files.size(result)));
            TimelineChart.write(chart, "FIND MANY: " + file.getFileName(), facts, profile, lookups);

            System.out.println("=== FIND MANY ===");
            facts.forEach((label, value) -> System.out.printf("%-17s: %s%n", label, value));
            System.out.printf("%-17s: %s%n", "Chart", chart);
            System.out.printf("%-17s: %s%n", "Result File", result);
            if (lookups.size() <= MAX_PRINTED_LOOKUPS) {
                for (Lookup lookup : lookups) {
                    System.out.printf(
                            "%s  %s%n", lookup.reference(), lookup.found() ? lookup.shown() : "not found");
                }
            }
        }
    }

    /** Each argument is a reference, or @file with one reference per line. Duplicates count once. */
    private static Set<String> references(String[] args) throws IOException {
        Set<String> references = new LinkedHashSet<>();
        for (String arg : args) {
            if (arg.startsWith("@")) {
                Files.readAllLines(Path.of(arg.substring(1))).stream()
                        .map(String::strip)
                        .filter(line -> !line.isEmpty())
                        .forEach(references::add);
            } else {
                references.add(arg);
            }
        }
        if (references.isEmpty()) {
            throw new IllegalArgumentException("No references to find");
        }
        return references;
    }
}
