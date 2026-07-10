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
 * Experiment 8 — threshold sensitivity at low minimum-utility values.
 *
 * <p>The experiment quantifies how the candidate-generation efficiency
 * {@code η = |Cand| / |HAUSP|} degrades as {@code minUtil} approaches the
 * dataset's noise floor. Each {@code DatasetRun} supplies its own
 * {@code minUtils[]} sweep extending below the range used by Experiment 2.
 * Only HAUSP-UB and EHAUSM-I are compared, on the full database in a single
 * pass; the goal is to see whether the bounds remain effective when many
 * patterns are admitted.
 */
public class Experiment8Runner {
    public static final boolean ENABLE_IO = ExperimentConfig.EXP8.enableIO;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP8;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir;
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        String[] algorithms = spec.algorithms;

        System.out.println("[exp8] starting threshold-sensitivity study");

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String datasetName = new File(run.dataset.seqPath).getName().replace("_seq.txt", "");
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

                for (String algo : algorithms) {
                    if (algoFailed.getOrDefault(algo, false)) {
                        logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, 0, "SKIPPED");
                        continue;
                    }
                    System.out.println("    [" + algo + "]");

                    for (int rep = 0; rep < ExperimentConfig.REPEATS; rep++) {
                        if (algoFailed.getOrDefault(algo, false)) break;
                        if (CompletedRuns.shouldSkip(outputDir, logFileName, algo, datasetName, 0, rep, minUtil, 1.0)) {
                            System.out.println("      trial " + (rep + 1) + ": resume-skip");
                            continue;
                        }
                        final int repeatIndex = rep;
                        if (ExperimentConfig.REPEATS > 1) {
                            System.out.print("      trial " + (rep + 1) + "/" + ExperimentConfig.REPEATS + " ");
                        } else {
                            System.out.print("      ");
                        }

                        RunIsolation.forceGC();
                        final Object[] algRef = new Object[1];

                        if (algo.equals("EHAUSM-I")) {
                            EHAUSM_Inc a = new EHAUSM_Inc(conf); a.setConfig(minUtil); algRef[0] = a;
                        } else if (algo.equals("HAUSP-UB")) {
                            HAUSP_UB a = new HAUSP_UB(conf); a.setConfig(minUtil); algRef[0] = a;
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
                                res.mu = CSVLogger.effectiveMu(algo, run.mu);
                                res.batchID = 0; res.deltaRatio = 1.0;
                                res.runIndex = repeatIndex; res.runStatus = "SUCCESS";
                                CSVLogger.logResult(outputDir, logFileName, res);
                                double eta = (res.hauspFound == 0) ? Double.NaN : ((double) res.numCand / res.hauspFound);
                                System.out.printf(Locale.US, "OK (HAUSP=%d, Cand=%d, η=%.1f, Peak=%.1f MB)%n",
                                        res.hauspFound, res.numCand, eta, res.memPeak);
                            }
                        } catch (TimeoutException e) {
                            System.out.println("timeout");
                            future.cancel(true);
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, repeatIndex, "OT");
                            algoFailed.put(algo, true);
                        } catch (ExecutionException e) {
                            String st = (e.getCause() instanceof OutOfMemoryError) ? "OOM" : "ERROR";
                            System.out.println(st.toLowerCase());
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, run.mu, 1.0, 0, repeatIndex, st);
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
    }
}
