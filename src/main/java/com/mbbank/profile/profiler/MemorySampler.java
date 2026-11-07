package com.mbbank.profile.profiler;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * Reads the resident set size of this process, which includes heap, metaspace, thread stacks and
 * native allocations such as DuckDB's buffers. Java has no portable API for it, so this is OS specific.
 */
public interface MemorySampler {

    long UNAVAILABLE = -1;

    /** Current RSS in bytes, or {@link #UNAVAILABLE}. */
    long currentRssBytes();

    /** Peak RSS tracked by the OS over the whole process lifetime, or {@link #UNAVAILABLE}. */
    default long lifetimePeakRssBytes() {
        return UNAVAILABLE;
    }

    static MemorySampler forCurrentOs() {
        String os = System.getProperty("os.name").toLowerCase();
        if (os.contains("linux")) {
            return new Linux();
        }
        if (os.contains("mac")) {
            return new Mac();
        }
        return () -> UNAVAILABLE;
    }

    /** Reads VmRSS and the kernel high-water mark VmHWM from /proc/self/status. */
    final class Linux implements MemorySampler {

        private static final Path STATUS = Path.of("/proc/self/status");

        @Override
        public long currentRssBytes() {
            return readKilobytes("VmRSS:");
        }

        @Override
        public long lifetimePeakRssBytes() {
            return readKilobytes("VmHWM:");
        }

        private static long readKilobytes(String field) {
            try {
                List<String> lines = Files.readAllLines(STATUS);
                for (String line : lines) {
                    if (line.startsWith(field)) {
                        String kilobytes = line.substring(field.length()).replace("kB", "").trim();
                        return Long.parseLong(kilobytes) * 1024;
                    }
                }
                return UNAVAILABLE;
            } catch (IOException e) {
                return UNAVAILABLE;
            }
        }
    }

    /** Runs "ps" because macOS has no /proc. Each sample spawns a short-lived process. */
    final class Mac implements MemorySampler {

        private final String pid = Long.toString(ProcessHandle.current().pid());

        @Override
        public long currentRssBytes() {
            try {
                Process process = new ProcessBuilder("ps", "-o", "rss=", "-p", pid).redirectErrorStream(true).start();
                try (InputStream out = process.getInputStream()) {
                    String kilobytes = new String(out.readAllBytes(), StandardCharsets.US_ASCII).trim();
                    return process.waitFor() == 0 ? Long.parseLong(kilobytes) * 1024 : UNAVAILABLE;
                }
            } catch (IOException | NumberFormatException e) {
                return UNAVAILABLE;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return UNAVAILABLE;
            }
        }
    }
}
