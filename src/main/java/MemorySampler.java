import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Live-set memory measurement for the dedicated memory runs ({@code --mem-mode live}).
 *
 * <p>The default {@code MemPeak(MB)} column is the used heap sampled during the
 * batch. Under a lazily collected 24 GB heap that value contains uncollected
 * garbage and depends on what ran earlier in the same JVM: the legacy FIFA
 * K=100 run read 23 GB at its first batch because the preceding arm had grown
 * the heap, while a fresh JVM reads 0.3 GB for the same batch. This sampler
 * measures instead the heap that survives a full collection: a background
 * thread forces {@code System.gc()} every {@link #INTERVAL_MS} milliseconds and
 * records the used heap right after it; the runner calls {@link #finish} at the
 * end of the batch, which forces one more collection and records the retained
 * heap. Forced collections cost time, so rows produced in this mode carry
 * {@code MemMode=live}, are written to a separate result directory and are
 * never used for runtime tables.
 *
 * <p>Reported per batch: {@code MemLive(MB)} = maximum post-collection used
 * heap observed (a lower bound of the true peak live set, since spikes between
 * two collections are not seen; the sampling interval bounds the gap),
 * {@code MemRetained(MB)} = post-collection used heap at the end of the batch,
 * {@code GcForced} = number of forced collections during the batch (0 means the
 * batch was shorter than one interval and only the end-of-batch value exists).
 */
public final class MemorySampler {

    /** Milliseconds between two forced collections. */
    public static final long INTERVAL_MS = 1000L;

    /** Sampler of the batch currently running, so that an algorithm task can reset the peak between phases. */
    public static volatile MemorySampler CURRENT = null;

    private final MemoryMXBean memBean = ManagementFactory.getMemoryMXBean();
    private final AtomicLong liveMaxBytes = new AtomicLong(0);
    private final AtomicLong forcedCount = new AtomicLong(0);
    private volatile boolean running = true;
    private volatile long retainedBytes = -1;
    private final Thread thread;

    private MemorySampler() {
        thread = new Thread(() -> {
            while (running) {
                sample();
                try { Thread.sleep(INTERVAL_MS); } catch (InterruptedException e) { return; }
            }
        }, "memory-sampler");
        thread.setDaemon(true);
    }

    /** Starts a sampler when the launcher runs in live mode; returns null otherwise. */
    public static MemorySampler startIfLive() {
        if (!ExperimentConfig.MEM_MODE_LIVE) return null;
        MemorySampler s = new MemorySampler();
        CURRENT = s;
        s.thread.start();
        return s;
    }

    /** Resets the running peak (used between the initial load and the update batch of Experiment 3). */
    public static void resetMaxIfLive() {
        MemorySampler s = CURRENT;
        if (s != null) {
            s.sample();
            s.liveMaxBytes.set(0);
            s.forcedCount.set(0);
        }
    }

    private long sample() {
        System.gc();
        long used = memBean.getHeapMemoryUsage().getUsed();
        forcedCount.incrementAndGet();
        liveMaxBytes.accumulateAndGet(used, Math::max);
        return used;
    }

    /** Stops sampling without a final measurement (failed batch). */
    public void stop() {
        running = false;
        thread.interrupt();
        if (CURRENT == this) CURRENT = null;
    }

    /** Stops sampling, forces a final collection and writes the three memory columns into {@code res}. */
    public void finish(RunResult res) {
        running = false;
        thread.interrupt();
        try { thread.join(2000); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
        retainedBytes = sample();
        forcedCount.decrementAndGet(); // the final collection is reported separately as MemRetained
        if (CURRENT == this) CURRENT = null;
        res.memMode = "live";
        res.memLiveMB = liveMaxBytes.get() / (1024.0 * 1024.0);
        res.memRetainedMB = retainedBytes / (1024.0 * 1024.0);
        res.gcForced = forcedCount.get();
    }
}
