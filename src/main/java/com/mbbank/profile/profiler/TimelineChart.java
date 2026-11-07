package com.mbbank.profile.profiler;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.StringJoiner;

/**
 * Writes a self-contained HTML chart of CPU and memory over time. A long run has tens of thousands
 * of samples, so they are merged into at most MAX_POINTS buckets. Each bucket keeps its highest
 * memory values and both its highest and its average CPU use, so peaks survive.
 */
public final class TimelineChart {

    private static final int MAX_POINTS = 800;
    private static final double NANOS_PER_SECOND = 1_000_000_000.0;

    private TimelineChart() {}

    /** The chart goes to the current directory, named so that runs never overwrite each other. */
    public static Path pathFor(Path parquetFile, String label) {
        String name = parquetFile.getFileName().toString().replaceFirst("\\.parquet$", "");
        String time = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss"));
        return Path.of(name + label + "-profile-" + time + ".html");
    }

    public static void write(Path file, String title, Map<String, String> facts, ProfileResult profile)
            throws IOException {
        write(file, title, facts, profile, List.of());
    }

    public static void write(
            Path file, String title, Map<String, String> facts, ProfileResult profile, List<Lookup> lookups)
            throws IOException {
        Files.createDirectories(file.toAbsolutePath().getParent());
        String html = template().replace("/*DATA*/null", json(title, facts, profile, lookups));
        Files.writeString(file, html);
    }

    private static String template() throws IOException {
        try (InputStream in = TimelineChart.class.getResourceAsStream("/profile-chart.html")) {
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static String json(String title, Map<String, String> facts, ProfileResult profile, List<Lookup> lookups) {
        StringJoiner seconds = new StringJoiner(",", "[", "]");
        StringJoiner cores = new StringJoiner(",", "[", "]");
        StringJoiner coresPeak = new StringJoiner(",", "[", "]");
        StringJoiner rss = new StringJoiner(",", "[", "]");
        StringJoiner heap = new StringJoiner(",", "[", "]");

        List<ProfileResult.Sample> samples = profile.samples();
        int bucketSize = Math.max(1, (int) Math.ceil(samples.size() / (double) MAX_POINTS));
        ProfileResult.Sample previous = new ProfileResult.Sample(0, 0, 0, 0);
        for (int start = 0; start < samples.size(); start += bucketSize) {
            int end = Math.min(start + bucketSize, samples.size());
            List<ProfileResult.Sample> bucket = samples.subList(start, end);
            long maxRss = -1;
            long maxHeap = 0;
            for (ProfileResult.Sample sample : bucket) {
                maxRss = Math.max(maxRss, sample.rssBytes());
                maxHeap = Math.max(maxHeap, sample.heapBytes());
            }
            ProfileResult.Sample last = samples.get(end - 1);
            long elapsed = last.elapsedNanos() - previous.elapsedNanos();
            double used = elapsed > 0 ? (last.cpuNanos() - previous.cpuNanos()) / (double) elapsed : 0;

            seconds.add(String.format(Locale.US, "%.3f", last.elapsedNanos() / NANOS_PER_SECOND));
            cores.add(String.format(Locale.US, "%.3f", used));
            double peak = Math.max(used, ProfileResult.peakCores(previous, bucket));
            coresPeak.add(String.format(Locale.US, "%.3f", peak));
            rss.add(maxRss < 0 ? "null" : Long.toString(maxRss));
            heap.add(Long.toString(maxHeap));
            previous = last;
        }

        StringJoiner factsJson = new StringJoiner(",", "[", "]");
        facts.forEach((label, value) -> factsJson.add("[" + quote(label) + "," + quote(value) + "]"));

        return "{\"title\":" + quote(title)
                + ",\"facts\":" + factsJson
                + ",\"seconds\":" + seconds
                + ",\"cores\":" + cores
                + ",\"coresPeak\":" + coresPeak
                + ",\"rss\":" + rss
                + ",\"heap\":" + heap
                + ",\"lookups\":" + lookupsJson(lookups) + "}";
    }

    /** Every match has the same columns, so they are listed once and each row only holds the values. */
    private static String lookupsJson(List<Lookup> lookups) {
        List<String> columns = lookups.stream()
                .filter(Lookup::found)
                .findFirst()
                .map(lookup -> List.copyOf(lookup.shown().keySet()))
                .orElse(List.of());

        StringJoiner names = new StringJoiner(",", "[", "]");
        columns.forEach(column -> names.add(quote(column)));

        StringJoiner rows = new StringJoiner(",", "[", "]");
        for (Lookup lookup : lookups) {
            StringJoiner values = new StringJoiner(",", "[", "]");
            Map<String, String> shown = lookup.shown();
            columns.forEach(column -> values.add(quote(shown.getOrDefault(column, ""))));
            rows.add("[" + quote(lookup.reference())
                    + "," + (lookup.nanos() == Lookup.NOT_TIMED
                            ? "null"
                            : String.format(Locale.US, "%.3f", lookup.nanos() / 1_000_000.0))
                    + "," + lookup.found()
                    + "," + values + "]");
        }
        return "{\"columns\":" + names + ",\"rows\":" + rows + "}";
    }

    /** Escapes a string for a JSON value inside a script element. */
    static String quote(String value) {
        StringBuilder quoted = new StringBuilder("\"");
        for (char c : value.toCharArray()) {
            if (c == '\\' || c == '"') {
                quoted.append('\\').append(c);
            } else if (c < ' ' || c == '<') {
                quoted.append(String.format("\\u%04x", (int) c));
            } else {
                quoted.append(c);
            }
        }
        return quoted.append('"').toString();
    }
}
