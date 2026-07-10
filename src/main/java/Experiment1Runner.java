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
 * Experiment 1 — tightness of upper bounds.
 *
 * <p>Each dataset is split into five consecutive batches of 20%. For every
 * algorithm in {@code spec.algorithms}, the runner replays the batches
 * sequentially and records candidate-generation efficiency together with the
 * three tightness statistics on each batch. Each algorithm runs in its own
 * single-thread executor so that an out-of-memory or timeout failure on one
 * configuration does not contaminate the next.
 */
public class Experiment1Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP1.enableIO;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP1;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir;
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        String[] algorithms = spec.algorithms;

        System.out.println("[exp1] starting tightness study");

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String conf = ConfigBridge.materialize(spec.id, run);

            String datasetName = new File(run.dataset.seqPath).getName().replace("_seq.txt", "");
            double minUtil = run.minUtil;
            double[] ratios = run.batchRatios;

            System.out.println();
            System.out.println("[exp1] dataset=" + datasetName + " minUtil=" + minUtil);

            List<Sequence> fullDB = QSDB_Parser.loadDB(run.dataset.euiPath, run.dataset.seqPath);
            if (fullDB == null || fullDB.isEmpty()) continue;
            int totalSeq = fullDB.size();

            Map<String, Boolean> algoFailed = new HashMap<>();

            for (String algo : algorithms) {
                if (algoFailed.getOrDefault(algo, false)) continue;
                System.out.println("  [" + algo + "]");

                for (int rep = 0; rep < ExperimentConfig.REPEATS; rep++) {
                    if (algoFailed.getOrDefault(algo, false)) break;
                    if (CompletedRuns.shouldSkipAlgorithm(outputDir, logFileName, algo, datasetName, rep, minUtil, ratios)) {
                        System.out.println("    trial " + (rep + 1) + ": resume-skip (all batches present)");
                        continue;
                    }
                    if (ExperimentConfig.REPEATS > 1) {
                        System.out.println("    trial " + (rep + 1) + "/" + ExperimentConfig.REPEATS);
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
                    } else if (algo.equals("HAUSP-UB-L1")) {
                        HAUSP_UB alg = new HAUSP_UB(conf);
                        alg.setConfig(minUtil);
                        alg.enableLayer2IAUUB = false;
                        alg.enableLayer3MFUUB = false;
                        algRef[0] = alg;
                    } else if (algo.equals("HAUSP-UB")) {
                        algRef[0] = new HAUSP_UB(conf);
                        ((HAUSP_UB) algRef[0]).setConfig(minUtil);
                    }

                    ExecutorService executor = Executors.newSingleThreadExecutor();
                    boolean isAlgoFailed = false;
                    List<Sequence> cumulativeDB = new ArrayList<>();
                    int startIndex = 0;

                    for (int bId = 0; bId < ratios.length; bId++) {
                        if (isAlgoFailed) {
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, "SKIPPED");
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
                            } else if (algo.equals("HAUSP-UB-L1") || algo.equals("HAUSP-UB")) {
                                ((HAUSP_UB) algRef[0]).enableIO = ENABLE_IO;
                                return ((HAUSP_UB) algRef[0]).processBatch(finalDeltaBatch, finalBId);
                            }
                            return null;
                        };

                        RunIsolation.forceGC();

                        Future<RunResult> future = executor.submit(task);
                        try {
                            RunResult res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
                            if (res != null) {
                                res.algorithm = algo; res.dataset = datasetName;
                                res.minUtil = minUtil; res.mu = CSVLogger.effectiveMu(algo, mu);
                                res.batchID = bId; res.deltaRatio = ratios[bId];
                                res.runIndex = repeatIndex; res.runStatus = "SUCCESS";
                                CSVLogger.logResult(outputDir, logFileName, res);
                                System.out.println("      batch " + bId + ": OK");
                            }
                        } catch (TimeoutException e) {
                            System.out.println("      batch " + bId + ": timeout");
                            future.cancel(true);
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, "OT");
                            isAlgoFailed = true;
                        } catch (ExecutionException e) {
                            if (e.getCause() instanceof OutOfMemoryError) {
                                System.out.println("      batch " + bId + ": out of memory");
                                logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, "OOM");
                            } else {
                                System.out.println("      batch " + bId + ": error");
                                logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, "ERROR");
                            }
                            isAlgoFailed = true;
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                            isAlgoFailed = true;
                        }

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

                    RunIsolation.forceGC();
                }
            }
        }
        System.out.println();
        System.out.println("[exp1] done");
        System.exit(0);
    }

    private static void logFailedResult(String out, String file, String algo, String dataset,
                                        double minUtil, double mu, double ratio, int bId, int runIndex, String status) {
        RunResult failRes = new RunResult();
        failRes.algorithm = algo; failRes.dataset = dataset; failRes.minUtil = minUtil;
        failRes.mu = CSVLogger.effectiveMu(algo, mu); failRes.deltaRatio = ratio;
        failRes.batchID = bId; failRes.runIndex = runIndex; failRes.runStatus = status;
        CSVLogger.logResult(out, file, failRes);
    }
}
