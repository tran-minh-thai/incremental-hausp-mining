import java.io.File;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Experiment 7 — long-batch scalability.
 *
 * <p>This experiment addresses the concern that the AU-DUL structures could
 * grow without bound across very long streams. The total dataset volume is
 * held fixed; only the number of batches {@code K} is varied across
 * {@code {10, 20, 50, 100}}. Per-batch runtime and peak memory are reported
 * so that any super-linear growth in the bookkeeping cost becomes visible.
 */
public class Experiment7Runner {
    public static final boolean ENABLE_IO = ExperimentConfig.EXP7.enableIO;
    private static long TIMEOUT_MIN;

    /** Batch counts swept by the experiment. */
    private static final int[] BATCH_COUNTS = {10, 20, 50, 100};

    /**
     * Uniform single-trial rule (applies to every algorithm identically):
     * repeat trials 2-3 are executed only when the first trial of the same
     * (algorithm, dataset, K) completed within this threshold. Long-running
     * groups are reported from one trial; the run-to-run CV measured on all
     * other configurations bounds their expected variance.
     */
    private static final long SINGLE_TRIAL_RULE_MS = 60L * 60_000L;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP7;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir;
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        String[] algorithms = spec.algorithms;

        System.out.println("[exp7] starting long-batch scalability study");

        // datasetName|algo|K -> wall-clock ms of trial 0 measured in this session
        Map<String, Long> trial0Wall = new HashMap<>();

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String datasetName = new File(run.dataset.seqPath).getName().replace("_seq.txt", "");
            double minUtil = run.minUtil;
            double mu = run.mu;

            System.out.println();
            System.out.println("[exp7] dataset=" + datasetName);

            for (int K : BATCH_COUNTS) {
                System.out.println("  K=" + K + " batches");

                double[] ratios = new double[K];
                double each = 1.0 / K;
                for (int i = 0; i < K; i++) ratios[i] = each;

                String conf = ConfigBridge.materialize(spec.id, run, minUtil, ratios);
                List<List<Sequence>> batches = QSDB_Parser.loadDBByRatios(
                        run.dataset.euiPath, run.dataset.seqPath, ratios);
                if (batches == null || batches.size() < K) continue;

                Map<String, Boolean> algoFailed = new HashMap<>();

                for (String algo : algorithms) {
                    if (algoFailed.getOrDefault(algo, false)) continue;
                    System.out.println("    [" + algo + "]");

                    for (int rep = 0; rep < ExperimentConfig.REPEATS; rep++) {
                        if (algoFailed.getOrDefault(algo, false)) break;
                        // E7 logs each batch with deltaRatio = 1.0 / K. Build a
                        // matching array so shouldSkipAlgorithm computes the same key.
                        double[] keyDeltas = new double[K];
                        java.util.Arrays.fill(keyDeltas, 1.0 / K);
                        if (CompletedRuns.shouldSkipAlgorithm(outputDir, logFileName, algo, datasetName, rep, minUtil, keyDeltas)) {
                            System.out.println("      trial " + (rep + 1) + ": resume-skip (all batches present)");
                            continue;
                        }
                        if (rep > 0 && CompletedRuns.groupFailed(outputDir, logFileName, algo, datasetName, rep, minUtil, keyDeltas)) {
                            System.out.println("      trial " + (rep + 1) + ": skipped (failed in an earlier trial; not re-attempted)");
                            continue;
                        }
                        if (rep > 0) {
                            long t0 = trial0Wall.getOrDefault(datasetName + "|" + algo + "|" + K,
                                    CompletedRuns.groupDurationMs(outputDir, logFileName, algo, datasetName, 0, minUtil, 1.0 / K));
                            if (t0 > SINGLE_TRIAL_RULE_MS) {
                                System.out.println("      trial " + (rep + 1) + ": skipped (single-trial rule: trial 1 took "
                                        + (t0 / 60000) + " min > " + (SINGLE_TRIAL_RULE_MS / 60000) + " min)");
                                continue;
                            }
                        }
                        if (ExperimentConfig.REPEATS > 1) {
                            System.out.println("      trial " + (rep + 1) + "/" + ExperimentConfig.REPEATS);
                        }
                        final int repeatIndex = rep;

                        RunIsolation.forceGC();
                        final Object[] algRef = new Object[1];

                        if (algo.equals("HAUSP-UB")) {
                            HAUSP_UB a = new HAUSP_UB(conf); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                        } else if (algo.equals("EHAUSM-I")) {
                            EHAUSM_Inc a = new EHAUSM_Inc(conf); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                        } else if (algo.equals("Pre-HAUSPM")) {
                            Pre_HUSPM_adapt a = new Pre_HUSPM_adapt(conf); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                        }

                        ExecutorService executor = Executors.newSingleThreadExecutor();
                        boolean isAlgoFailed = false;
                        long groupStartNs = System.nanoTime();

                        // Fairness contract (final, as reported in the paper):
                        // every algorithm attempts the IDENTICAL K schedule and
                        // every single batch has the SAME per-batch limit of
                        // TIMEOUT_MIN minutes (90). A batch exceeding the limit
                        // is recorded as OT at that batch (completed batches are
                        // kept), the remaining batches as SKIPPED, and the next
                        // algorithm starts. OOM is recorded the same way. No
                        // algorithm gets a reduced batch schedule.
                        for (int bId = 0; bId < K; bId++) {
                            if (isAlgoFailed) {
                                logFailedResult(outputDir, logFileName, algo, datasetName,
                                        minUtil, mu, 1.0 / K, bId, repeatIndex, "SKIPPED");
                                continue;
                            }
                            final int currentBatchId = bId;
                            final List<Sequence> deltaBatch = new ArrayList<>(batches.get(bId));

                            Callable<RunResult> task = () -> {
                                if (algo.equals("HAUSP-UB")) {
                                    return ((HAUSP_UB) algRef[0]).processBatch(deltaBatch, currentBatchId);
                                } else if (algo.equals("EHAUSM-I")) {
                                    return ((EHAUSM_Inc) algRef[0]).processBatch(deltaBatch, currentBatchId);
                                } else {
                                    return ((Pre_HUSPM_adapt) algRef[0]).processBatch(deltaBatch, currentBatchId);
                                }
                            };

                            Future<RunResult> future = executor.submit(task);
                            try {
                                RunResult res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
                                if (res != null) {
                                    res.algorithm = algo; res.dataset = datasetName;
                                    res.minUtil = minUtil; res.mu = CSVLogger.effectiveMu(algo, mu);
                                    res.batchID = bId; res.deltaRatio = each;
                                    res.runIndex = repeatIndex; res.runStatus = "SUCCESS";
                                    CSVLogger.logResult(outputDir, logFileName, res);
                                    if (bId == 0 || bId == K - 1 || (bId + 1) % Math.max(1, K / 10) == 0) {
                                        System.out.printf(Locale.US, "        batch %3d/%d: %d ms, %.1f MB%n",
                                                bId + 1, K, res.tTotal, res.memPeak);
                                    }
                                }
                            } catch (TimeoutException e) {
                                future.cancel(true);
                                System.out.printf(Locale.US,
                                        "        batch %3d/%d: OT (exceeded %d-min per-batch limit)%n",
                                        bId + 1, K, TIMEOUT_MIN);
                                logFailedResult(outputDir, logFileName, algo, datasetName,
                                        minUtil, mu, 1.0 / K, bId, repeatIndex, "OT");
                                isAlgoFailed = true;
                            } catch (ExecutionException e) {
                                String st = (e.getCause() instanceof OutOfMemoryError) ? "OOM" : "ERROR";
                                logFailedResult(outputDir, logFileName, algo, datasetName,
                                        minUtil, mu, 1.0 / K, bId, repeatIndex, st);
                                isAlgoFailed = true;
                            } catch (InterruptedException e) {
                                Thread.currentThread().interrupt();
                                isAlgoFailed = true;
                            }
                        }

                        executor.shutdownNow();
                        try { executor.awaitTermination(5, TimeUnit.SECONDS); }
                        catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
                        algRef[0] = null;
                        if (rep == 0) {
                            trial0Wall.put(datasetName + "|" + algo + "|" + K,
                                    (System.nanoTime() - groupStartNs) / 1_000_000L);
                        }
                        if (isAlgoFailed) algoFailed.put(algo, true);
                        RunIsolation.forceGC();
                    }
                }
            }
        }
        System.out.println();
        System.out.println("[exp7] done");
        System.exit(0);
    }

    private static void logFailedResult(String out, String file, String algo, String dataset,
                                        double minUtil, double mu, double ratio, int bId,
                                        int runIndex, String status) {
        RunResult failRes = new RunResult();
        failRes.algorithm = algo; failRes.dataset = dataset; failRes.minUtil = minUtil;
        failRes.mu = CSVLogger.effectiveMu(algo, mu); failRes.deltaRatio = ratio;
        failRes.batchID = bId; failRes.runIndex = runIndex; failRes.runStatus = status;
        CSVLogger.logResult(out, file, failRes);
        CompletedRuns.noteFailure(out, file, algo, dataset, runIndex, minUtil, ratio, bId);
    }
}
