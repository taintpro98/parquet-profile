package com.mbbank.profile.profiler;

import java.util.List;
import java.util.Locale;

/** Measurements of one operation. Memory values are -1 when unavailable on this OS. */
public record ProfileResult(
        long wallTimeNanos,
        long cpuTimeNanos,
        long peakProcessMemoryBytes,
        long peakHeapMemoryBytes,
        List<Sample> samples) {

    /** One point of the timeline. Elapsed and CPU time are relative to the start of the operation. */
    public record Sample(long elapsedNanos, long cpuNanos, long rssBytes, long heapBytes) {}

    private static final double MEGABYTE = 1024.0 * 1024.0;
    private static final double GIGABYTE = MEGABYTE * 1024.0;
    /** A shorter gap between two samples is too noisy to give a meaningful CPU use. */
    private static final long MIN_PEAK_INTERVAL_NANOS = 50_000_000;

    /** Highest CPU use between two neighbouring samples, in cores. */
    public double peakCores() {
        double average = wallTimeNanos > 0 ? cpuTimeNanos / (double) wallTimeNanos : 0;
        return Math.max(average, peakCores(new Sample(0, 0, 0, 0), samples));
    }

    /** Highest CPU use within samples, in cores, or 0 when no gap is long enough. */
    static double peakCores(Sample before, List<Sample> samples) {
        double peak = 0;
        Sample previous = before;
        for (Sample sample : samples) {
            long elapsed = sample.elapsedNanos() - previous.elapsedNanos();
            if (elapsed >= MIN_PEAK_INTERVAL_NANOS) {
                peak = Math.max(peak, (sample.cpuNanos() - previous.cpuNanos()) / (double) elapsed);
            }
            previous = sample;
        }
        return peak;
    }

    public static String formatSeconds(long nanos, int decimals) {
        return String.format(Locale.US, "%." + decimals + "f s", nanos / 1_000_000_000.0);
    }

    public static String formatCores(double cores) {
        return String.format(Locale.US, "%.2f core", cores);
    }

    public static String formatMillis(long nanos) {
        return String.format(Locale.US, "%,.1f ms", nanos / 1_000_000.0);
    }

    public static String formatBytes(long bytes) {
        if (bytes < 0) {
            return "n/a";
        }
        if (bytes >= 10 * GIGABYTE) {
            return String.format(Locale.US, "%.1f GB", bytes / GIGABYTE);
        }
        return String.format(Locale.US, "%,.0f MB", bytes / MEGABYTE);
    }
}
