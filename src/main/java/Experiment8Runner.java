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
 * Experiment 8 -- threshold sensitivity at low minimum-utility values.
 *
 * <p>The experiment quantifies how the candidate-generation efficiency
 * {@code eta = |Cand| / |HAUSP|} degrades as {@code minUtil} approaches the
 * dataset's noise floor. Each {@code DatasetRun} supplies its own
 * {@code minUtils[]} sweep extending below the range used by Experiment 2.
 * Only HAUSP-UB and EHAUSM-I are compared, on the full database in a single
 * pass; the goal is to see whether the bounds remain effective when many
 * patterns are admitted.
 */
public class Experiment8Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP8.enableIO;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP8;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir();
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        String[] algorithms = ExperimentConfig.filteredAlgos(spec);

        System.out.println("[exp8] starting threshold-sensitivity study");

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String datasetName = run.dataset.csvName();
            double[] minUtilsArr = run.minUtils;
            if (minUtilsArr == null || minUtilsArr.length == 0) {
                System.err.println("[exp8] skipping " + datasetName + ": minUtils[] not declared");
                continue;
            }

            System.out.println();
            System.out.println("[exp8] dataset=" + datasetName);

            List<Sequence> fullDB = QSDB_Parser.loadDB(run.dataset.euiPath, run.dataset.seqPath);
            if (fullDB == null || fullDB.isEmpty()) continue;

            Map<String, Boolean> algoFailed = new HashMap<>();

            for (double minUtil : minUtilsArr) {
                System.out.println("  minUtil=" + String.format(Locale.US, "%.6f", minUtil));
                String conf = ConfigBridge.materialize(spec.id, run, minUtil, run.batchRatios);

                for (int ai = 0; ai < algorithms.length; ai++) {
                    final String algo = algorithms[ai];
                    final int armOrder = ai;
                    final double muLogged = CSVLogger.effectiveMu(algo, run.mu);
                    if (algoFailed.getOrDefault(algo, false)) {
                        logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, 0, armOrder, "SKIPPED");
                        continue;
                    }
                    System.out.println("    [" + algo + "]");
                    int targetRepeats = ExperimentConfig.REPEATS;

                    for (int rep = 0; rep < targetRepeats; rep++) {
                        if (algoFailed.getOrDefault(algo, false)) break;
                        if (CompletedRuns.shouldSkip(outputDir, logFileName, algo, datasetName, 0, rep, minUtil, 1.0, muLogged)) {
                            System.out.println("      trial " + (rep + 1) + ": resume-skip");
                            if (CompletedRuns.groupFailed(outputDir, logFileName, algo, datasetName, rep + 1, minUtil, new double[]{1.0}, muLogged)) {
                                System.out.println("      trial " + (rep + 1) + ": recorded as failed in the CSV; arm not re-attempted");
                                algoFailed.put(algo, true);
                                break;
                            }
                            if (rep == 0) {
                                targetRepeats = Experiment1Runner.raiseRepeats(targetRepeats,
                                        CompletedRuns.groupDurationMs(outputDir, logFileName, algo, datasetName, 0, minUtil, new double[]{1.0}, muLogged));
                            }
                            continue;
                        }
                        final int repeatIndex = rep;
                        if (targetRepeats > 1) {
                            System.out.print("      trial " + (rep + 1) + "/" + targetRepeats + " ");
                        } else {
                            System.out.print("      ");
                        }

                        RunIsolation.forceGC();
                        final Object[] algRef = new Object[1];

                        if (algo.equals("EHAUSM-I")) {
                            EHAUSM_Inc a = new EHAUSM_Inc(conf); a.setConfig(minUtil); algRef[0] = a;
                        } else if (algo.startsWith("HAUSP-UB")) {
                            HAUSP_UB a = HAUSP_UB.fromArmName(algo, conf); a.setConfig(minUtil); algRef[0] = a;
                        } else {
                            System.err.println("[exp8] unknown arm " + algo + "; skipped");
                            algoFailed.put(algo, true);
                            break;
                        }

                        ExecutorService executor = Executors.newSingleThreadExecutor();
                        Callable<RunResult> task = () -> {
                            if (algo.equals("EHAUSM-I")) {
                                return ((EHAUSM_Inc) algRef[0]).processBatch(fullDB, 0);
                            }
                            return ((HAUSP_UB) algRef[0]).processBatch(fullDB, 0);
                        };

                        Future<RunResult> future = executor.submit(task);
                        try {
                            RunResult res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
                            if (res != null) {
                                res.algorithm = algo; res.dataset = datasetName; res.minUtil = minUtil;
                                res.mu = muLogged;
                                res.batchID = 0; res.deltaRatio = 1.0;
                                res.runIndex = repeatIndex; res.runStatus = "SUCCESS";
                                res.armOrder = armOrder;
                                CSVLogger.logResult(outputDir, logFileName, res);
                                double eta = (res.hauspFound == 0) ? Double.NaN : ((double) res.numCand / res.hauspFound);
                                System.out.printf(Locale.US, "OK (HAUSP=%d, Cand=%d, eta=%.1f, Peak=%.1f MB)%n",
                                        res.hauspFound, res.numCand, eta, res.memPeak);
                                if (rep == 0) targetRepeats = Experiment1Runner.raiseRepeats(targetRepeats, res.tTotal);
                            }
                        } catch (TimeoutException e) {
                            System.out.println("timeout");
                            future.cancel(true);
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, repeatIndex, armOrder, "OT");
                            algoFailed.put(algo, true);
                        } catch (ExecutionException e) {
                            String st = (e.getCause() instanceof OutOfMemoryError) ? "OOM" : "ERROR";
                            System.out.println(st.toLowerCase());
                            if ("ERROR".equals(st)) e.getCause().printStackTrace();
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, repeatIndex, armOrder, st);
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
        System.out.println("[exp8] done");
    }

    private static void logFailedResult(String out, String file, String algo, String dataset,
                                        double minUtil, double mu, double ratio, int bId,
                                        int runIndex, int armOrder, String status) {
        RunResult failRes = new RunResult();
        failRes.algorithm = algo; failRes.dataset = dataset; failRes.minUtil = minUtil;
        failRes.mu = CSVLogger.effectiveMu(algo, mu); failRes.deltaRatio = ratio;
        failRes.batchID = bId; failRes.runIndex = runIndex; failRes.runStatus = status;
        failRes.armOrder = armOrder;
        CSVLogger.logResult(out, file, failRes);
    }
}
