import com.mbbank.profile.config.DatabaseConfig;
import com.mbbank.profile.oracle.OracleReader;
import com.mbbank.profile.profiler.ProfileResult;
import com.mbbank.profile.profiler.Profiler;
import com.mbbank.profile.profiler.TimelineChart;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/** Flow: dump the Oracle table into one Parquet file. */
public final class Dump {

    public static void main(String[] args) throws Exception {
        if (args.length != 0) {
            System.err.println("Usage: Dump (settings come from -D options)");
            System.exit(2);
        }
        DatabaseConfig config = DatabaseConfig.load();
        OracleReader reader = new OracleReader(config);
        Profiler.Measured<Long> measured = new Profiler().measure(reader::writeToParquet);
        ProfileResult profile = measured.profile();
        Path output = config.parquetOutput().toAbsolutePath();
        String rows = number(measured.value());
        String fetchSize = number(config.oracleFetchSize());
        String passes = number(config.oracleRepeat());

        Map<String, String> facts = new LinkedHashMap<>();
        facts.put("Rows", rows);
        facts.put("Wall Time", ProfileResult.formatSeconds(profile.wallTimeNanos(), 2));
        facts.put("CPU Time", ProfileResult.formatSeconds(profile.cpuTimeNanos(), 2));
        facts.put("Peak CPU", ProfileResult.formatCores(profile.peakCores()));
        facts.put("Peak Process RAM", ProfileResult.formatBytes(profile.peakProcessMemoryBytes()));
        facts.put("Peak JVM Heap", ProfileResult.formatBytes(profile.peakHeapMemoryBytes()));
        facts.put("File Size", ProfileResult.formatBytes(Files.size(output)));
        facts.put("Max Heap", ProfileResult.formatBytes(Runtime.getRuntime().maxMemory()));
        facts.put("Fetch Size", fetchSize);
        facts.put("Table Passes", passes);

        Path chart = TimelineChart.pathFor(output, "");
        TimelineChart.write(chart, "WRITE PROFILE: " + output.getFileName(), facts, profile);

        System.out.println("=== DUMP ===");
        System.out.printf("%-17s: %s%n", "Source", "Oracle");
        System.out.printf("%-17s: %s%n", "Output", output);
        facts.forEach((label, value) -> System.out.printf("%-17s: %s%n", label, value));
        System.out.printf("%-17s: %s%n", "Chart", chart);
    }

    private static String number(long value) {
        return String.format(Locale.US, "%,d", value);
    }
}
