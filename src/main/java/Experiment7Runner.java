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
 * Experiment 7 -- long-batch scalability.
 *
 * <p>This experiment addresses the concern that the AU-DUL structures could
 * grow without bound across very long streams. The total dataset volume is
 * held fixed; only the number of batches {@code K} is varied across
 * {@link ExperimentConfig#EXP7_BATCH_COUNTS} (or {@code --k}). Per-batch
 * runtime and peak memory are reported so that any super-linear growth in
 * the bookkeeping cost becomes visible.
 *
 * <p>Also executes Experiment 11 (warm-start schedule) when the launcher sets
 * {@link #SPEC_OVERRIDE} and {@link #SCHEDULE}: batch 0 then holds
 * {@link ExperimentConfig#WARM_START_FIRST_RATIO} of the data and the
 * remaining batches share the rest equally. The {@code Schedule} column of
 * the CSV records which schedule produced each row, and {@code DeltaRatio}
 * is the true share of each batch.
 */
public class Experiment7Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP7.enableIO;
    /** When non-null, run this spec instead of EXP7 (Experiment 11). */
    public static ExperimentConfig.ExperimentSpec SPEC_OVERRIDE = null;
    /** Batch schedule: {@link ExperimentConfig#SCHEDULE_EQUAL} or {@link ExperimentConfig#SCHEDULE_WARM20}. */
    public static String SCHEDULE = ExperimentConfig.SCHEDULE_EQUAL;
    private static long TIMEOUT_MIN;

    /**
     * Uniform single-trial rule (applies to every algorithm identically):
     * repeat trials 2-3 are executed only when the first trial of the same
     * (algorithm, dataset, K) completed within this threshold. Long-running
     * groups are reported from one trial; the run-to-run CV measured on all
     * other configurations bounds their expected variance.
     */
    private static final long SINGLE_TRIAL_RULE_MS = 60L * 60_000L;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = (SPEC_OVERRIDE != null) ? SPEC_OVERRIDE : ExperimentConfig.EXP7;
        ENABLE_IO = spec.enableIO;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir();
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();
        String tag = "[exp" + spec.id + "]";
        final String schedule = SCHEDULE;

        String[] algorithms = ExperimentConfig.filteredAlgos(spec);
        int[] batchCounts = ExperimentConfig.effectiveBatchCounts(
                spec.id == 11 ? ExperimentConfig.EXP11_BATCH_COUNTS : ExperimentConfig.EXP7_BATCH_COUNTS);

        System.out.println(tag + " starting " + spec.title.toLowerCase() + " (schedule=" + schedule + ")");

        // datasetName|algo|K -> wall-clock ms of trial 0 measured in this session
        Map<String, Long> trial0Wall = new HashMap<>();

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String datasetName = run.dataset.csvName();
            double minUtil = run.minUtil;
            double mu = run.mu;

            System.out.println();
            System.out.println(tag + " dataset=" + datasetName);

            for (int K : batchCounts) {
                System.out.println("  K=" + K + " batches");

                final double[] ratios = ExperimentConfig.SCHEDULE_WARM20.equals(schedule)
                        ? ExperimentConfig.warmStartSchedule(K, ExperimentConfig.WARM_START_FIRST_RATIO)
                        : ExperimentConfig.equalSchedule(K);

                String conf = ConfigBridge.materialize(spec.id, run, minUtil, ratios);
                List<List<Sequence>> batches = QSDB_Parser.loadDBByRatios(
                        run.dataset.euiPath, run.dataset.seqPath, ratios);
                if (batches == null || batches.size() < K) continue;

                Map<String, Boolean> algoFailed = new HashMap<>();

                for (int ai = 0; ai < algorithms.length; ai++) {
                    final String algo = algorithms[ai];
                    final int armOrder = ai;
                    if (algoFailed.getOrDefault(algo, false)) continue;
                    System.out.println("    [" + algo + "]");
                    final double muLogged = CSVLogger.effectiveMu(algo, mu);
                    int targetRepeats = ExperimentConfig.REPEATS;

                    for (int rep = 0; rep < targetRepeats; rep++) {
                        if (algoFailed.getOrDefault(algo, false)) break;
                        if (CompletedRuns.shouldSkipAlgorithm(outputDir, logFileName, algo, datasetName, rep, minUtil, ratios, muLogged)) {
                            System.out.println("      trial " + (rep + 1) + ": resume-skip (all batches present)");
                            if (CompletedRuns.groupFailed(outputDir, logFileName, algo, datasetName, rep + 1, minUtil, ratios, muLogged)) {
                                System.out.println("      trial " + (rep + 1) + ": recorded as failed in the CSV; arm not re-attempted");
                                algoFailed.put(algo, true);
                                break;
                            }
                            if (rep == 0) {
                                targetRepeats = Experiment1Runner.raiseRepeats(targetRepeats,
                                        CompletedRuns.groupDurationMs(outputDir, logFileName, algo, datasetName, 0, minUtil, ratios, muLogged));
                            }
                            continue;
                        }
                        if (rep > 0 && CompletedRuns.groupFailed(outputDir, logFileName, algo, datasetName, rep, minUtil, ratios, muLogged)) {
                            System.out.println("      trial " + (rep + 1) + ": skipped (failed in an earlier trial; not re-attempted)");
                            continue;
                        }
                        if (rep > 0) {
                            long t0 = trial0Wall.getOrDefault(datasetName + "|" + algo + "|" + K,
                                    CompletedRuns.groupDurationMs(outputDir, logFileName, algo, datasetName, 0, minUtil, ratios, muLogged));
                            if (t0 > SINGLE_TRIAL_RULE_MS) {
                                System.out.println("      trial " + (rep + 1) + ": skipped (single-trial rule: trial 1 took "
                                        + (t0 / 60000) + " min > " + (SINGLE_TRIAL_RULE_MS / 60000) + " min)");
                                continue;
                            }
                        }
                        if (targetRepeats > 1) {
                            System.out.println("      trial " + (rep + 1) + "/" + targetRepeats);
                        }
                        final int repeatIndex = rep;

                        RunIsolation.forceGC();
                        final Object[] algRef = new Object[1];

                        if (algo.startsWith("HAUSP-UB")) {
                            HAUSP_UB a = HAUSP_UB.fromArmName(algo, conf); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                        } else if (algo.equals("EHAUSM-I")) {
                            EHAUSM_Inc a = new EHAUSM_Inc(conf); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                        } else if (algo.equals("Pre-HAUSPM")) {
                            Pre_HUSPM_adapt a = new Pre_HUSPM_adapt(conf); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                        } else {
                            System.err.println(tag + " unknown arm " + algo + "; skipped");
                            algoFailed.put(algo, true);
                            break;
                        }

                        ExecutorService executor = Executors.newSingleThreadExecutor();
                        boolean isAlgoFailed = false;
                        long groupStartNs = System.nanoTime();
                        long trialTotalMs = 0;

                        // Fairness contract (as reported in the paper): every
                        // algorithm attempts the IDENTICAL K schedule and every
                        // single batch has the SAME per-batch limit of TIMEOUT_MIN
                        // minutes. A batch exceeding the limit is recorded as OT at
                        // that batch (completed batches are kept), the remaining
                        // batches as SKIPPED, and the next algorithm starts. OOM is
                        // recorded the same way. No algorithm gets a reduced schedule.
                        for (int bId = 0; bId < K; bId++) {
                            if (isAlgoFailed) {
                                logFailedResult(outputDir, logFileName, algo, datasetName,
                                        minUtil, mu, ratios[bId], bId, repeatIndex, armOrder, schedule, "SKIPPED");
                                continue;
                            }
                            final int currentBatchId = bId;
                            final List<Sequence> deltaBatch = new ArrayList<>(batches.get(bId));

                            Callable<RunResult> task = () -> {
                                if (algo.startsWith("HAUSP-UB")) {
                                    return ((HAUSP_UB) algRef[0]).processBatch(deltaBatch, currentBatchId);
                                } else if (algo.equals("EHAUSM-I")) {
                                    return ((EHAUSM_Inc) algRef[0]).processBatch(deltaBatch, currentBatchId);
                                } else {
                                    return ((Pre_HUSPM_adapt) algRef[0]).processBatch(deltaBatch, currentBatchId);
                                }
                            };

                            MemorySampler mem = MemorySampler.startIfLive();
                            Future<RunResult> future = executor.submit(task);
                            try {
                                RunResult res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
                                if (res != null) {
                                    if (mem != null) mem.finish(res);
                                    res.algorithm = algo; res.dataset = datasetName;
                                    res.minUtil = minUtil; res.mu = CSVLogger.effectiveMu(algo, mu);
                                    res.batchID = bId; res.deltaRatio = ratios[bId];
                                    res.runIndex = repeatIndex; res.runStatus = "SUCCESS";
                                    res.armOrder = armOrder; res.schedule = schedule;
                                    CSVLogger.logResult(outputDir, logFileName, res);
                                    trialTotalMs += res.tTotal;
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
                                        minUtil, mu, ratios[bId], bId, repeatIndex, armOrder, schedule, "OT");
                                isAlgoFailed = true;
                            } catch (ExecutionException e) {
                                String st = (e.getCause() instanceof OutOfMemoryError) ? "OOM" : "ERROR";
                                System.out.printf(Locale.US, "        batch %3d/%d: %s%n", bId + 1, K, st);
                                if ("ERROR".equals(st)) e.getCause().printStackTrace();
                                logFailedResult(outputDir, logFileName, algo, datasetName,
                                        minUtil, mu, ratios[bId], bId, repeatIndex, armOrder, schedule, st);
                                isAlgoFailed = true;
                            } catch (InterruptedException e) {
                                Thread.currentThread().interrupt();
                                isAlgoFailed = true;
                            }
                            if (mem != null) mem.stop();
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
                        else if (rep == 0) targetRepeats = Experiment1Runner.raiseRepeats(targetRepeats, trialTotalMs);
                        RunIsolation.forceGC();
                    }
                }
            }
        }
        System.out.println();
        System.out.println(tag + " done");
    }

    private static void logFailedResult(String out, String file, String algo, String dataset,
                                        double minUtil, double mu, double ratio, int bId,
                                        int runIndex, int armOrder, String schedule, String status) {
        RunResult failRes = new RunResult();
        failRes.algorithm = algo; failRes.dataset = dataset; failRes.minUtil = minUtil;
        failRes.mu = CSVLogger.effectiveMu(algo, mu); failRes.deltaRatio = ratio;
        failRes.batchID = bId; failRes.runIndex = runIndex; failRes.runStatus = status;
        failRes.armOrder = armOrder; failRes.schedule = schedule;
        CSVLogger.logResult(out, file, failRes);
        CompletedRuns.noteFailure(out, file, algo, dataset, runIndex, minUtil, ratio, failRes.mu, bId);
    }
}
