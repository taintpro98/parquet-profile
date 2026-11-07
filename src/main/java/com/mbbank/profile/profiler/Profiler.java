package com.mbbank.profile.profiler;

import com.sun.management.OperatingSystemMXBean;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.util.ArrayList;
import java.util.List;

/** Measures wall time, process CPU time and memory of one operation, as peaks and as a timeline. */
public final class Profiler {

    private static final long SAMPLE_INTERVAL_MILLIS = 100;

    @FunctionalInterface
    public interface Operation<T> {
        T run() throws Exception;
    }

    @FunctionalInterface
    public interface VoidOperation {
        void run() throws Exception;
    }

    public record Measured<T>(T value, ProfileResult profile) {}

    private final OperatingSystemMXBean os =
            (OperatingSystemMXBean) ManagementFactory.getOperatingSystemMXBean();
    private final MemoryMXBean memory = ManagementFactory.getMemoryMXBean();
    private final MemorySampler sampler = MemorySampler.forCurrentOs();

    private final List<ProfileResult.Sample> samples = new ArrayList<>();
    private long wallStart;
    private long cpuStart;
    private long peakRss;
    private long peakHeap;

    public ProfileResult measure(VoidOperation operation) throws Exception {
        return measure(() -> {
            operation.run();
            return null;
        }).profile();
    }

    public <T> Measured<T> measure(Operation<T> operation) throws Exception {
        samples.clear();
        peakRss = MemorySampler.UNAVAILABLE;
        peakHeap = 0;
        cpuStart = os.getProcessCpuTime();
        wallStart = System.nanoTime();
        Thread samplerThread = startSampling();
        try {
            T value = operation.run();
            long wallTime = System.nanoTime() - wallStart;
            long cpuTime = os.getProcessCpuTime() - cpuStart;

            stopSampling(samplerThread);
            long peakProcess = Math.max(peakRss, sampler.lifetimePeakRssBytes());
            ProfileResult profile =
                    new ProfileResult(wallTime, cpuTime, peakProcess, peakHeap, List.copyOf(samples));
            return new Measured<>(value, profile);
        } finally {
            stopSampling(samplerThread);
        }
    }

    private Thread startSampling() {
        Thread thread = new Thread(() -> {
            try {
                while (!Thread.currentThread().isInterrupted()) {
                    sample();
                    Thread.sleep(SAMPLE_INTERVAL_MILLIS);
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }, "memory-sampler");
        thread.setDaemon(true);
        thread.start();
        return thread;
    }

    /** The join hands the sampler thread's state over to the caller. */
    private void stopSampling(Thread thread) throws InterruptedException {
        if (thread.isAlive()) {
            thread.interrupt();
            thread.join();
            sample();
        }
    }

    private void sample() {
        long rss = sampler.currentRssBytes();
        long heap = memory.getHeapMemoryUsage().getUsed();
        peakRss = Math.max(peakRss, rss);
        peakHeap = Math.max(peakHeap, heap);
        samples.add(new ProfileResult.Sample(
                System.nanoTime() - wallStart, os.getProcessCpuTime() - cpuStart, rss, heap));
    }
}
