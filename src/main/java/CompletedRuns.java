import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Tracks the (algorithm, dataset, batchId, runIndex, minUtil, deltaRatio)
 * tuples that already appear in an experiment's CSV output, so that the
 * runners can skip them when {@code --resume} is enabled on the command
 * line.
 *
 * <p>The CSV for each experiment is read once on the first call, cached in
 * memory and reused on subsequent queries during the same JVM invocation.
 * Rows whose {@code Status} column is missing or contains an empty string
 * are considered completed regardless of their numeric content; this means
 * an interrupted run that was killed mid-write may still flag a row as
 * done. Delete partial rows by hand if exact re-runs are required.
 */
public final class CompletedRuns {

    private CompletedRuns() {}

    /** outputDir + "/" + fileName → Set of completed keys. */
    private static final Map<String, Set<String>> CACHE = new HashMap<>();

    /** outputDir + "/" + fileName → keys of rows whose Status was not SUCCESS/SUCCESS_MATCH. */
    private static final Map<String, Set<String>> FAILED_CACHE = new HashMap<>();

    /**
     * outputDir + "/" + fileName → (group key without batch → Σ tTotal(ms) of its
     * SUCCESS rows). Used by Experiment 7's uniform single-trial rule: repeat
     * trials run only when the first trial finished within the time threshold.
     */
    private static final Map<String, Map<String, Long>> DURATION_CACHE = new HashMap<>();

    /**
     * Returns {@code true} if {@code --resume} is active and the given
     * configuration already appears in the experiment's CSV file. Use this
     * variant only for experiments that process a single batch
     * (Experiment 2, Experiment 5, Experiment 8) or for the per-delta logged
     * row of Experiment 3.
     */
    public static boolean shouldSkip(String outputDir, String fileName,
                                     String algorithm, String dataset,
                                     int batchId, int runIndex,
                                     double minUtil, double deltaRatio) {
        if (!ExperimentConfig.RESUME) return false;
        Set<String> done = loadKeys(outputDir, fileName);
        return done.contains(makeKey(algorithm, dataset, batchId, runIndex, minUtil, deltaRatio));
    }

    /**
     * Returns {@code true} when every batch in {@code 0..batchCount-1} for
     * the given (algorithm, dataset, runIndex, minUtil) tuple is already
     * present in the CSV. Incremental experiments (Experiment 1, 4, 6, 7)
     * must use this whole-algorithm check because skipping a single batch
     * would leave the in-memory state inconsistent for the batches that
     * follow.
     */
    public static boolean shouldSkipAlgorithm(String outputDir, String fileName,
                                              String algorithm, String dataset,
                                              int runIndex, double minUtil,
                                              double[] deltaRatios) {
        if (!ExperimentConfig.RESUME) return false;
        Set<String> done = loadKeys(outputDir, fileName);
        for (int b = 0; b < deltaRatios.length; b++) {
            String k = makeKey(algorithm, dataset, b, runIndex, minUtil, deltaRatios[b]);
            if (!done.contains(k)) return false;
        }
        return true;
    }

    private static Set<String> loadKeys(String outputDir, String fileName) {
        String cacheKey = outputDir + "/" + fileName;
        Set<String> cached = CACHE.get(cacheKey);
        if (cached != null) return cached;

        Set<String> keys = new HashSet<>();
        Set<String> failed = new HashSet<>();
        Map<String, Long> durations = new HashMap<>();
        File f = new File(outputDir, fileName);
        if (!f.exists() || f.length() == 0) {
            CACHE.put(cacheKey, keys);
            FAILED_CACHE.put(cacheKey, failed);
            DURATION_CACHE.put(cacheKey, durations);
            return keys;
        }

        try (BufferedReader br = new BufferedReader(new FileReader(f))) {
            String header = br.readLine();
            if (header == null) {
                CACHE.put(cacheKey, keys);
                FAILED_CACHE.put(cacheKey, failed);
                DURATION_CACHE.put(cacheKey, durations);
                return keys;
            }
            String[] cols = header.split(",");
            int iAlgo  = indexOf(cols, "Algorithm");
            int iDs    = indexOf(cols, "Dataset");
            int iBatch = indexOf(cols, "BatchID");
            int iRun   = indexOf(cols, "RunIndex");
            int iMin   = indexOf(cols, "MinUtil");
            int iDelta = indexOf(cols, "DeltaRatio");
            int iStatus = indexOf(cols, "Status");
            int iTTotal = indexOf(cols, "tTotal(ms)");
            if (iAlgo < 0 || iDs < 0 || iBatch < 0 || iMin < 0 || iDelta < 0) {
                System.err.println("[resume] header of " + f + " is unrecognised; skip cache empty");
                CACHE.put(cacheKey, keys);
                FAILED_CACHE.put(cacheKey, failed);
                DURATION_CACHE.put(cacheKey, durations);
                return keys;
            }

            String line;
            while ((line = br.readLine()) != null) {
                if (line.isEmpty()) continue;
                String[] parts = line.split(",", -1);
                if (parts.length <= iDelta) continue;
                try {
                    String algo  = parts[iAlgo].trim();
                    String ds    = parts[iDs].trim();
                    int batchId  = Integer.parseInt(parts[iBatch].trim());
                    int runIdx   = (iRun >= 0 && iRun < parts.length) ? Integer.parseInt(parts[iRun].trim()) : 0;
                    double minU  = Double.parseDouble(parts[iMin].trim());
                    double delta = Double.parseDouble(parts[iDelta].trim());
                    keys.add(makeKey(algo, ds, batchId, runIdx, minU, delta));
                    if (iStatus >= 0 && iStatus < parts.length) {
                        String st = parts[iStatus].trim();
                        if (!st.isEmpty() && !st.equals("SUCCESS") && !st.equals("SUCCESS_MATCH")) {
                            failed.add(makeKey(algo, ds, batchId, runIdx, minU, delta));
                        } else if (iTTotal >= 0 && iTTotal < parts.length) {
                            try {
                                long t = (long) Double.parseDouble(parts[iTTotal].trim());
                                durations.merge(makeGroupKey(algo, ds, runIdx, minU, delta), t, Long::sum);
                            } catch (NumberFormatException ignored2) { /* blank cell */ }
                        }
                    }
                } catch (NumberFormatException ignored) {
                    // malformed row, skip
                }
            }
        } catch (IOException e) {
            System.err.println("[resume] cannot read " + f + ": " + e.getMessage());
        }

        CACHE.put(cacheKey, keys);
        FAILED_CACHE.put(cacheKey, failed);
        DURATION_CACHE.put(cacheKey, durations);
        if (!keys.isEmpty()) {
            System.out.println("[resume] " + f.getName() + ": " + keys.size() + " rows already present, will skip");
        }
        return keys;
    }

    /**
     * Returns {@code true} when the (algorithm, dataset, minUtil) group has a
     * non-SUCCESS row (OT/OOM/ERROR/SKIPPED) in any trial with index below
     * {@code uptoRunIndex} — either read from the CSV or recorded in this JVM
     * via {@link #noteFailure}. Experiment 7 uses this to avoid re-attempting
     * configurations whose failure is already established by an earlier trial.
     */
    public static boolean groupFailed(String outputDir, String fileName,
                                      String algorithm, String dataset,
                                      int uptoRunIndex, double minUtil,
                                      double[] deltaRatios) {
        loadKeys(outputDir, fileName); // ensure caches are populated
        Set<String> failed = FAILED_CACHE.get(outputDir + "/" + fileName);
        if (failed == null || failed.isEmpty()) return false;
        for (int r = 0; r < uptoRunIndex; r++) {
            for (int b = 0; b < deltaRatios.length; b++) {
                if (failed.contains(makeKey(algorithm, dataset, b, r, minUtil, deltaRatios[b]))) return true;
            }
        }
        return false;
    }

    /**
     * Records a failure produced during the current JVM session so that
     * {@link #groupFailed} sees it without re-reading the CSV.
     */
    public static void noteFailure(String outputDir, String fileName,
                                   String algorithm, String dataset,
                                   int runIndex, double minUtil,
                                   double deltaRatio, int batchId) {
        String cacheKey = outputDir + "/" + fileName;
        Set<String> failed = FAILED_CACHE.computeIfAbsent(cacheKey, k -> new HashSet<>());
        failed.add(makeKey(algorithm, dataset, batchId, runIndex, minUtil, deltaRatio));
    }

    /**
     * Total recorded compute time (Σ tTotal of SUCCESS rows) of one trial of a
     * group, in milliseconds; 0 when the trial has no rows. Used by
     * Experiment 7's uniform long-run rule (repeat trials only when the first
     * trial finished within the threshold).
     */
    public static long groupDurationMs(String outputDir, String fileName,
                                       String algorithm, String dataset,
                                       int runIndex, double minUtil,
                                       double deltaRatio) {
        loadKeys(outputDir, fileName); // ensure caches are populated
        Map<String, Long> durations = DURATION_CACHE.get(outputDir + "/" + fileName);
        if (durations == null) return 0L;
        return durations.getOrDefault(makeGroupKey(algorithm, dataset, runIndex, minUtil, deltaRatio), 0L);
    }

    private static String makeGroupKey(String algo, String dataset, int runIndex,
                                       double minUtil, double deltaRatio) {
        return algo + "|" + dataset + "|" + runIndex + "|" +
                String.format(Locale.US, "%.6f", minUtil) + "|" +
                String.format(Locale.US, "%.3f", deltaRatio);
    }

    private static String makeKey(String algo, String dataset, int batchId, int runIndex,
                                  double minUtil, double deltaRatio) {
        return algo + "|" + dataset + "|" + batchId + "|" + runIndex + "|" +
                String.format(Locale.US, "%.6f", minUtil) + "|" +
                String.format(Locale.US, "%.3f", deltaRatio);
    }

    private static int indexOf(String[] cols, String name) {
        for (int i = 0; i < cols.length; i++) {
            if (cols[i].trim().equalsIgnoreCase(name)) return i;
        }
        return -1;
    }
}
