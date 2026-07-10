import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Utility class that isolates one algorithm invocation from the next so that
 * runtime and memory measurements remain comparable. Three concerns are
 * handled:
 *
 * <ul>
 *   <li>{@link #forceGC()} drives a synchronous garbage collection cycle by
 *       allocating a {@link WeakReference} and busy-waiting until it is
 *       cleared (or a deadline elapses). Plain {@code System.gc()} is only a
 *       hint and may be ignored by the JVM.</li>
 *   <li>{@link #runIsolated(Callable, long)} executes the task on a dedicated
 *       single-thread executor, waits for termination after
 *       {@code shutdownNow()} and maps the outcome to a stable status string
 *       ({@code SUCCESS}, {@code OOM}, {@code OT}, {@code ERROR},
 *       {@code INTERRUPTED}, {@code STACK_OVERFLOW}).</li>
 *   <li>{@link #cpuTimeNs()} reports CPU time of the current thread in
 *       nanoseconds; unlike {@link System#nanoTime()} it excludes GC pauses
 *       and idle waits, which yields fairer timings.</li>
 * </ul>
 *
 * <p>The class also exposes {@link #spawnChildJVM(String, int, List, long)}
 * for process-level isolation when even shared JIT state must be avoided.
 */
public final class RunIsolation {

    private static final long TEARDOWN_WAIT_SEC = 5;
    private static final long GC_DEADLINE_MS = 2000;

    public static final String STATUS_SUCCESS = "SUCCESS";
    public static final String STATUS_OOM     = "OOM";
    public static final String STATUS_OT      = "OT";
    public static final String STATUS_ERROR   = "ERROR";
    public static final String STATUS_INTR    = "INTERRUPTED";
    public static final String STATUS_STACK   = "STACK_OVERFLOW";

    private RunIsolation() {}

    /**
     * Drives a real garbage-collection cycle. A short-lived object is wrapped
     * in a {@link WeakReference}; the method then calls {@link System#gc()} in
     * a tight loop until the reference is cleared or {@link #GC_DEADLINE_MS}
     * milliseconds have elapsed.
     */
    public static void forceGC() {
        Object sentinel = new Object();
        WeakReference<Object> ref = new WeakReference<>(sentinel);
        sentinel = null;

        long deadline = System.currentTimeMillis() + GC_DEADLINE_MS;
        while (ref.get() != null && System.currentTimeMillis() < deadline) {
            System.gc();
            try { Thread.sleep(50); } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
        }

        System.gc();
        try { Thread.sleep(100); } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /**
     * Runs {@code task} on a dedicated single-thread executor with a timeout
     * expressed in minutes. After completion (or failure) {@code shutdownNow()}
     * is followed by {@code awaitTermination()} so that the worker thread
     * cannot keep references to the previous run alive while the next one
     * starts.
     */
    public static <T> SandboxResult<T> runIsolated(Callable<T> task, long timeoutMinutes) {
        ExecutorService exec = Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "isolation-sandbox");
            t.setDaemon(true);
            return t;
        });

        SandboxResult<T> sr = new SandboxResult<>();
        Future<T> future = null;
        try {
            future = exec.submit(task);
            sr.result = future.get(timeoutMinutes, TimeUnit.MINUTES);
            sr.status = STATUS_SUCCESS;
        } catch (TimeoutException e) {
            if (future != null) future.cancel(true);
            sr.status = STATUS_OT;
            sr.error = e;
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            sr.error = cause;
            if (cause instanceof OutOfMemoryError) {
                sr.status = STATUS_OOM;
            } else if (cause instanceof StackOverflowError) {
                sr.status = STATUS_STACK;
            } else {
                sr.status = STATUS_ERROR;
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            sr.status = STATUS_INTR;
            sr.error = e;
        } finally {
            exec.shutdownNow();
            try {
                exec.awaitTermination(TEARDOWN_WAIT_SEC, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        return sr;
    }

    /** Outcome of {@link #runIsolated(Callable, long)}. */
    public static final class SandboxResult<T> {
        public T result;
        public String status = STATUS_ERROR;
        public Throwable error;

        public boolean isSuccess() {
            return STATUS_SUCCESS.equals(status);
        }
    }

    /**
     * Spawns a child JVM to execute {@code mainClass}. Heap, JIT cache and GC
     * state of the child are independent from the parent, which provides true
     * process-level isolation at the cost of a JVM-startup overhead of one to
     * three seconds per call. The child's stdout and stderr are inherited.
     *
     * @return the exit code of the child, or {@code -1} on timeout,
     *         {@code -2} on I/O error, {@code -3} when interrupted.
     */
    public static int spawnChildJVM(String mainClass, int maxHeapMB,
                                    List<String> programArgs, long timeoutMin) {
        String javaBin = System.getProperty("java.home") + "/bin/java";
        String classPath = System.getProperty("java.class.path");

        List<String> cmd = new ArrayList<>();
        cmd.add(javaBin);
        if (maxHeapMB > 0) {
            cmd.add("-Xmx" + maxHeapMB + "m");
            cmd.add("-Xms" + Math.min(maxHeapMB, 512) + "m");
        }
        cmd.add("-XX:+UseG1GC");
        cmd.add("-cp");
        cmd.add(classPath);
        cmd.add(mainClass);
        if (programArgs != null) cmd.addAll(programArgs);

        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.inheritIO();

        Process p = null;
        try {
            p = pb.start();
            boolean finished = p.waitFor(timeoutMin, TimeUnit.MINUTES);
            if (!finished) {
                p.destroyForcibly();
                try { p.waitFor(5, TimeUnit.SECONDS); }
                catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
                return -1;
            }
            return p.exitValue();
        } catch (IOException e) {
            System.err.println("[isolation] Failed to spawn child JVM: " + e.getMessage());
            return -2;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            if (p != null && p.isAlive()) p.destroyForcibly();
            return -3;
        }
    }

    // ---------------------------------------------------------------------------------
    // CPU-time measurement (excludes GC pauses).
    // ---------------------------------------------------------------------------------
    private static final ThreadMXBean THREAD_MX_BEAN = ManagementFactory.getThreadMXBean();
    static {
        if (THREAD_MX_BEAN.isThreadCpuTimeSupported() && !THREAD_MX_BEAN.isThreadCpuTimeEnabled()) {
            THREAD_MX_BEAN.setThreadCpuTimeEnabled(true);
        }
    }

    /**
     * CPU time of the current thread in nanoseconds. Unlike
     * {@link System#nanoTime()}, this excludes garbage-collection pauses and
     * time spent waiting or sleeping. Falls back to {@link System#nanoTime()}
     * if the JVM does not support per-thread CPU time tracking.
     */
    public static long cpuTimeNs() {
        long t = THREAD_MX_BEAN.getCurrentThreadCpuTime();
        return t < 0 ? System.nanoTime() : t;
    }

    public static long cpuTimeMs() {
        return cpuTimeNs() / 1_000_000L;
    }

    /** Wall-clock time via {@link System#nanoTime()}. */
    public static long wallTimeNs() {
        return System.nanoTime();
    }

    /** Maps an exit code from {@link #spawnChildJVM} into a status string. */
    public static String exitCodeToStatus(int exitCode) {
        switch (exitCode) {
            case 0:  return STATUS_SUCCESS;
            case 1:  return STATUS_OOM;
            case 2:  return STATUS_ERROR;
            case -1: return STATUS_OT;
            case -2: return STATUS_ERROR;
            case -3: return STATUS_INTR;
            default: return STATUS_ERROR;
        }
    }
}
