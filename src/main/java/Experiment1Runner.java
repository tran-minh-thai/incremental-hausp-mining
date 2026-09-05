import java.io.File;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Experiment 1 -- tightness of upper bounds.
 *
 * <p>Each dataset is split into five consecutive batches of 20%. For every
 * algorithm in the (possibly filtered) arm list, the runner replays the
 * batches sequentially and records candidate-generation efficiency together
 * with the three tightness statistics on each batch. Each algorithm runs in
 * its own single-thread executor so that an out-of-memory or timeout failure
 * on one configuration does not contaminate the next.
 *
 * <p>Also executes Experiment 9 (attribution study) when
 * {@link #SPEC_OVERRIDE} is set by the launcher.
 */
public class Experiment1Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP1.enableIO;
    /** When non-null, the runner executes this spec instead of EXP1 (used for the attribution study, Exp. 9). */
    public static ExperimentConfig.ExperimentSpec SPEC_OVERRIDE = null;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = (SPEC_OVERRIDE != null) ? SPEC_OVERRIDE : ExperimentConfig.EXP1;
        ENABLE_IO = spec.enableIO;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir();
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();
        String tag = "[exp" + spec.id + "]";

        String[] algorithms = ExperimentConfig.filteredAlgos(spec);

        System.out.println(tag + " starting " + spec.title.toLowerCase());

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String conf = ConfigBridge.materialize(spec.id, run);

            String datasetName = run.dataset.csvName();
            double minUtil = run.minUtil;
            double[] ratios = run.batchRatios;

            System.out.println();
            System.out.println(tag + " dataset=" + datasetName + " minUtil=" + minUtil);

            List<Sequence> fullDB = QSDB_Parser.loadDB(run.dataset.euiPath, run.dataset.seqPath);
            if (fullDB == null || fullDB.isEmpty()) continue;
            int totalSeq = fullDB.size();

            Map<String, Boolean> algoFailed = new HashMap<>();

            for (int ai = 0; ai < algorithms.length; ai++) {
                final String algo = algorithms[ai];
                final int armOrder = ai;
                if (algoFailed.getOrDefault(algo, false)) continue;
                System.out.println("  [" + algo + "]");

                final double muLogged = CSVLogger.effectiveMu(algo, run.mu);
                int targetRepeats = ExperimentConfig.REPEATS;

                for (int rep = 0; rep < targetRepeats; rep++) {
                    if (algoFailed.getOrDefault(algo, false)) break;
                    if (CompletedRuns.shouldSkipAlgorithm(outputDir, logFileName, algo, datasetName, rep, minUtil, ratios, muLogged)) {
                        System.out.println("    trial " + (rep + 1) + ": resume-skip (all batches present)");
                        if (CompletedRuns.groupFailed(outputDir, logFileName, algo, datasetName, rep + 1, minUtil, ratios, muLogged)) {
                            // The recorded trial ended in OT/OOM/ERROR: the failure is the result; do not re-attempt.
                            System.out.println("    trial " + (rep + 1) + ": recorded as failed in the CSV; arm not re-attempted");
                            algoFailed.put(algo, true);
                            break;
                        }
                        if (rep == 0) {
                            targetRepeats = raiseRepeats(targetRepeats,
                                    CompletedRuns.groupDurationMs(outputDir, logFileName, algo, datasetName, 0, minUtil, ratios, muLogged));
                        }
                        continue;
                    }
                    if (targetRepeats > 1) {
                        System.out.println("    trial " + (rep + 1) + "/" + targetRepeats);
                    }
                    final int repeatIndex = rep;

                    RunIsolation.forceGC();

                    final Object[] algRef = new Object[1];
                    double mu = 0.0;

                    if (algo.equals("EHAUSM-R")) {
                        algRef[0] = new EHAUSM_Remining(conf);
                    } else if (algo.equals("EHAUSM-I")) {
                        algRef[0] = new EHAUSM_Inc(conf);
                        ((EHAUSM_Inc) algRef[0]).setConfig(minUtil);
                    } else if (algo.equals("Pre-HAUSPM")) {
                        algRef[0] = new Pre_HUSPM_adapt(conf);
                        mu = run.mu;
                    } else if (algo.startsWith("HAUSP-UB")) {
                        HAUSP_UB alg = HAUSP_UB.fromArmName(algo, conf);
                        alg.setConfig(minUtil);
                        algRef[0] = alg;
                    } else {
                        System.err.println(tag + " unknown arm " + algo + "; skipped");
                        algoFailed.put(algo, true);
                        break;
                    }

                    ExecutorService executor = Executors.newSingleThreadExecutor();
                    boolean isAlgoFailed = false;
                    long trialTotalMs = 0;
                    List<Sequence> cumulativeDB = new ArrayList<>();
                    int startIndex = 0;

                    for (int bId = 0; bId < ratios.length; bId++) {
                        if (isAlgoFailed) {
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, armOrder, "SKIPPED");
                            continue;
                        }

                        int batchSize = (int) (totalSeq * ratios[bId]);
                        if (bId == ratios.length - 1) batchSize = totalSeq - startIndex;
                        List<Sequence> deltaBatch = fullDB.subList(startIndex, startIndex + batchSize);
                        cumulativeDB.addAll(deltaBatch);
                        startIndex += batchSize;

                        final List<Sequence> finalCumulativeDB = new ArrayList<>(cumulativeDB);
                        final List<Sequence> finalDeltaBatch = new ArrayList<>(deltaBatch);
                        final int finalBId = bId;

                        Callable<RunResult> task = () -> {
                            if (algo.equals("EHAUSM-R")) {
                                ((EHAUSM_Remining) algRef[0]).enableIO = ENABLE_IO;
                                return ((EHAUSM_Remining) algRef[0]).processBatch(finalCumulativeDB, finalBId);
                            } else if (algo.equals("EHAUSM-I")) {
                                ((EHAUSM_Inc) algRef[0]).enableIO = ENABLE_IO;
                                return ((EHAUSM_Inc) algRef[0]).processBatch(finalDeltaBatch, finalBId);
                            } else if (algo.equals("Pre-HAUSPM")) {
                                ((Pre_HUSPM_adapt) algRef[0]).enableIO = ENABLE_IO;
                                return ((Pre_HUSPM_adapt) algRef[0]).processBatch(finalDeltaBatch, finalBId);
                            } else {
                                ((HAUSP_UB) algRef[0]).enableIO = ENABLE_IO;
                                return ((HAUSP_UB) algRef[0]).processBatch(finalDeltaBatch, finalBId);
                            }
                        };

                        RunIsolation.forceGC();

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
                                res.armOrder = armOrder;
                                CSVLogger.logResult(outputDir, logFileName, res);
                                trialTotalMs += res.tTotal;
                                System.out.println("      batch " + bId + ": OK");
                            }
                        } catch (TimeoutException e) {
                            System.out.println("      batch " + bId + ": timeout");
                            future.cancel(true);
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, armOrder, "OT");
                            isAlgoFailed = true;
                        } catch (ExecutionException e) {
                            if (e.getCause() instanceof OutOfMemoryError) {
                                System.out.println("      batch " + bId + ": out of memory");
                                logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, armOrder, "OOM");
                            } else {
                                System.out.println("      batch " + bId + ": error");
                                e.getCause().printStackTrace();
                                logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, armOrder, "ERROR");
                            }
                            isAlgoFailed = true;
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                            isAlgoFailed = true;
                        }
                        if (mem != null) mem.stop();

                        if (isAlgoFailed) {
                            algRef[0] = null;
                            RunIsolation.forceGC();
                        }
                    }

                    algRef[0] = null;
                    executor.shutdownNow();
                    try { executor.awaitTermination(5, TimeUnit.SECONDS); }
                    catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
                    if (isAlgoFailed) algoFailed.put(algo, true);
                    else if (rep == 0) targetRepeats = raiseRepeats(targetRepeats, trialTotalMs);

                    RunIsolation.forceGC();
                }
            }
        }
        System.out.println();
        System.out.println(tag + " done");
    }

    /** Applies the adaptive repeat rule after the first trial; prints when the count changes. */
    static int raiseRepeats(int current, long trial0TotalMs) {
        int target = ExperimentConfig.targetRepeats(trial0TotalMs);
        if (target != current) {
            System.out.println("    trial 1 took " + trial0TotalMs + " ms -> " + target + " trials for this configuration");
        }
        return target;
    }

    private static void logFailedResult(String out, String file, String algo, String dataset,
                                        double minUtil, double mu, double ratio, int bId, int runIndex,
                                        int armOrder, String status) {
        RunResult failRes = new RunResult();
        failRes.algorithm = algo; failRes.dataset = dataset; failRes.minUtil = minUtil;
        failRes.mu = CSVLogger.effectiveMu(algo, mu); failRes.deltaRatio = ratio;
        failRes.batchID = bId; failRes.runIndex = runIndex; failRes.runStatus = status;
        failRes.armOrder = armOrder;
        CSVLogger.logResult(out, file, failRes);
    }
}
