import java.io.File;
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
 * Experiment 2 — ablation analysis of multi-layer pruning.
 *
 * <p>Each dataset is processed over its full {@code minUtils[]} sweep on the
 * complete database (a single batch of size 1.0). Three configurations are
 * compared: EHAUSM-I (PEAU only), HAUSP-UB* (SWU + IAUUB) and the full
 * HAUSP-UB with all three layers active.
 */
public class Experiment2Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP2.enableIO;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP2;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir;
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        String[] algorithms = spec.algorithms;

        System.out.println("[exp2] starting ablation study");

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String datasetName = new File(run.dataset.seqPath).getName().replace("_seq.txt", "");
            double[] minUtilsArr = run.minUtils;
            if (minUtilsArr == null || minUtilsArr.length == 0) {
                System.err.println("[exp2] skipping " + datasetName + ": minUtils[] not declared");
                continue;
            }

            System.out.println();
            System.out.println("[exp2] dataset=" + datasetName);

            List<Sequence> fullDB = QSDB_Parser.loadDB(run.dataset.euiPath, run.dataset.seqPath);
            if (fullDB == null || fullDB.isEmpty()) continue;

            Map<String, Boolean> algoFailed = new HashMap<>();

            for (double minUtil : minUtilsArr) {
                System.out.println("  minUtil=" + String.format(Locale.US, "%.6f", minUtil));

                String conf = ConfigBridge.materialize(spec.id, run, minUtil, run.batchRatios);

                for (String algo : algorithms) {
                    if (algoFailed.getOrDefault(algo, false)) {
                        logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, 0, "SKIPPED");
                        continue;
                    }

                    System.out.println("    [" + algo + "]");

                    for (int rep = 0; rep < ExperimentConfig.REPEATS; rep++) {
                        if (algoFailed.getOrDefault(algo, false)) break;
                        final int repeatIndex = rep;
                        if (CompletedRuns.shouldSkip(outputDir, logFileName, algo, datasetName, 0, repeatIndex, minUtil, 1.0)) {
                            System.out.println("      trial " + (rep + 1) + "/" + ExperimentConfig.REPEATS + " resume-skip");
                            continue;
                        }
                        if (ExperimentConfig.REPEATS > 1) {
                            System.out.print("      trial " + (rep + 1) + "/" + ExperimentConfig.REPEATS + " ");
                        } else {
                            System.out.print("      ");
                        }

                        RunIsolation.forceGC();

                        final Object[] algRef = new Object[1];

                        if (algo.equals("EHAUSM-I")) {
                            EHAUSM_Inc alg = new EHAUSM_Inc(conf);
                            alg.setConfig(minUtil);
                            algRef[0] = alg;
                        } else if (algo.equals("HAUSP-UB*")) {
                            HAUSP_UB_IAUUB alg = new HAUSP_UB_IAUUB(conf);
                            alg.setConfig(minUtil);
                            algRef[0] = alg;
                        } else if (algo.equals("HAUSP-UB-L1")) {
                            HAUSP_UB alg = new HAUSP_UB(conf);
                            alg.setConfig(minUtil);
                            alg.enableLayer2IAUUB = false;
                            alg.enableLayer3MFUUB = false;
                            algRef[0] = alg;
                        } else if (algo.equals("HAUSP-UB-L1L3")) {
                            HAUSP_UB alg = new HAUSP_UB(conf);
                            alg.setConfig(minUtil);
                            alg.enableLayer2IAUUB = false;
                            alg.enableLayer3MFUUB = true;
                            algRef[0] = alg;
                        } else if (algo.equals("HAUSP-UB")) {
                            HAUSP_UB alg = new HAUSP_UB(conf);
                            alg.setConfig(minUtil);
                            algRef[0] = alg;
                        }

                        ExecutorService executor = Executors.newSingleThreadExecutor();

                        Callable<RunResult> task = () -> {
                            if (algo.equals("EHAUSM-I")) {
                                return ((EHAUSM_Inc) algRef[0]).processBatch(fullDB, 0);
                            } else if (algo.equals("HAUSP-UB*")) {
                                return ((HAUSP_UB_IAUUB) algRef[0]).processBatch(fullDB, 0);
                            } else {
                                return ((HAUSP_UB) algRef[0]).processBatch(fullDB, 0);
                            }
                        };

                        Future<RunResult> future = executor.submit(task);
                        try {
                            RunResult res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
                            if (res != null) {
                                res.algorithm = algo; res.dataset = datasetName; res.minUtil = minUtil;
                                res.mu = CSVLogger.effectiveMu(algo, 0.1); res.batchID = 0; res.deltaRatio = 1.0;
                                res.runIndex = repeatIndex; res.runStatus = "SUCCESS";
                                CSVLogger.logResult(outputDir, logFileName, res);
                                System.out.println("OK (Cand=" + res.numCand
                                        + ", Peak=" + String.format(Locale.US, "%.1f", res.memPeak) + " MB)");
                            }
                        } catch (TimeoutException e) {
                            System.out.println("timeout");
                            future.cancel(true);
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, repeatIndex, "OT");
                            algoFailed.put(algo, true);
                        } catch (ExecutionException e) {
                            if (e.getCause() instanceof OutOfMemoryError) {
                                System.out.println("out of memory");
                                logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, repeatIndex, "OOM");
                            } else {
                                System.out.println("error");
                                e.getCause().printStackTrace();
                                logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, repeatIndex, "ERROR");
                            }
                            algoFailed.put(algo, true);
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                        } finally {
                            executor.shutdownNow();
                            try { executor.awaitTermination(5, TimeUnit.SECONDS); }
                            catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
                        }

                        algRef[0] = null;
                        RunIsolation.forceGC();
                    }
                }
            }
        }
        System.out.println();
        System.out.println("[exp2] done");
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
