package com.mbbank.archive;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.mbbank.archive.archive.Archives;
import com.mbbank.archive.common.Archive;
import com.mbbank.archive.common.Cli;
import com.mbbank.archive.common.Lookup;
import com.mbbank.archive.common.OracleSource;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;

/**
 * Runs for every option in {@link Archives}: dump ARCHIVE_GOLDEN from Oracle (its rows span many
 * days, so many day files), look every record up by key + timestamp together with lookups that
 * must find nothing, and require the result to equal testdata/golden.jsonl byte for byte.
 * Needs the local Oracle with the golden table: make seed.
 */
class ArchiveContractTest {

    private static final Path GOLDEN = Path.of("../../testdata/golden.jsonl");
    private static final Pattern KEY =
            Pattern.compile("\"reference\":\"([^\"]*)\",\"created_at\":\"([^\"]*)\"");

    /** All Java options, or only those named in IMPL (for example IMPL=java-parquet-duckdb). */
    static List<String> options() {
        String impl = System.getenv("IMPL");
        return Archives.ALL.keySet().stream()
                .filter(name -> impl == null || impl.isBlank() || impl.equals("all")
                        || List.of(impl.split(",")).contains("java-" + name))
                .toList();
    }

    @ParameterizedTest
    @MethodSource("options")
    void dumpThenFindReturnsTheGoldenRecords(String option, @TempDir Path dir) throws Exception {
        List<String> golden = Files.readAllLines(GOLDEN, StandardCharsets.UTF_8);
        Archive archive = Archives.get(option);
        Path archiveDir = dir.resolve("archive");

        long rows = Cli.dump(archive, OracleSource.Config.fromEnv().withTable("ARCHIVE_GOLDEN"), archiveDir);
        assertEquals(golden.size(), rows, "rows dumped");
        try (var files = Files.list(archiveDir)) {
            assertTrue(files.count() > 1, "one file per day");
        }

        List<Lookup> lookups = new ArrayList<>();
        for (String line : golden) {
            Matcher matcher = KEY.matcher(line);
            matcher.find();
            lookups.add(new Lookup(matcher.group(1), LocalDateTime.parse(matcher.group(2), Lookup.TIMESTAMP)));
        }
        Lookup existing = lookups.getFirst();
        lookups.add(new Lookup("REF-MISSING", existing.createdAt()));                        // key không có
        lookups.add(new Lookup(existing.reference(), existing.createdAt().plusNanos(1_000))); // sai giờ
        lookups.add(new Lookup(existing.reference(), existing.createdAt().plusDays(1)));      // sai ngày
        lookups.add(new Lookup("ZZZ", LocalDateTime.of(2001, 1, 1, 0, 0)));                  // ngày không có file

        Path result = dir.resolve("result.jsonl");
        int found = Cli.find(archive, archiveDir, lookups, result);

        assertEquals(golden.size(), found, "records found");
        List<String> actual = Files.readAllLines(result, StandardCharsets.UTF_8);
        for (int i = 0; i < golden.size(); i++) {
            assertEquals(golden.get(i), actual.get(i), "line " + (i + 1));
        }
    }
}
