import com.mbbank.profile.config.DatabaseConfig;
import com.mbbank.profile.duckdb.DuckDbReader;
import com.mbbank.profile.profiler.Lookup;
import com.mbbank.profile.profiler.ProfileResult;
import com.mbbank.profile.profiler.Profiler;
import com.mbbank.profile.profiler.ResultFile;
import com.mbbank.profile.profiler.TimelineChart;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Flow: find exactly one reference. */
public final class FindOne {

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            System.err.println("Usage: FindOne <REFERENCE>");
            System.exit(2);
        }
        String reference = args[0];
        DatabaseConfig config = DatabaseConfig.load();
        Path file = config.parquetOutput();

        try (DuckDbReader reader = new DuckDbReader(config)) {
            Profiler.Measured<Map<String, Object>> measured =
                    new Profiler().measure(() -> reader.findByReference(reference));
            ProfileResult profile = measured.profile();
            Map<String, Object> match = measured.value();

            Map<String, String> facts = new LinkedHashMap<>();
            facts.put("Reference", reference);
            facts.put("Found", Boolean.toString(!match.isEmpty()));
            facts.put("Wall Time", ProfileResult.formatSeconds(profile.wallTimeNanos(), 3));
            facts.put("CPU Time", ProfileResult.formatSeconds(profile.cpuTimeNanos(), 3));
            facts.put("Peak CPU", ProfileResult.formatCores(profile.peakCores()));
            facts.put("Peak Process RAM", ProfileResult.formatBytes(profile.peakProcessMemoryBytes()));
            facts.put("Peak JVM Heap", ProfileResult.formatBytes(profile.peakHeapMemoryBytes()));
            facts.put("File Size", ProfileResult.formatBytes(Files.size(file)));

            Path chart = TimelineChart.pathFor(file, "-find-one");
            Path result = ResultFile.pathFor(chart);
            List<Lookup> lookups = List.of(new Lookup(reference, profile.wallTimeNanos(), match));
            ResultFile.write(result, lookups);
            facts.put("Columns", Integer.toString(match.size()));
            TimelineChart.write(chart, "FIND ONE: " + file.getFileName(), facts, profile, lookups);

            System.out.println("=== FIND ONE ===");
            facts.forEach((label, value) -> System.out.printf("%-17s: %s%n", label, value));
            System.out.printf("%-17s: %s%n", "Chart", chart);
            System.out.printf("%-17s: %s%n", "Result File", result);
            if (match.isEmpty()) {
                System.out.println("Result           : not found");
            }
            match.forEach((column, value) -> System.out.printf("%-17s: %s%n", column, value));
        }
    }
}
