import java.io.File;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Experiment 5 — single-batch correctness verification against the oracle.
 *
 * <p>For each dataset and each threshold in {@code run.thresholds[]}, the
 * runner mines the full database with both the re-mining oracle (EHAUSM-R) and
 * the proposed HAUSP-UB, then compares the resulting pattern counts. If
 * {@code thresholds[]} is null, the runner falls back to the heuristic
 * {base, 0.8·base, 0.5·base}.
 */
public class Experiment5Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP5.enableIO;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP5;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir;
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        System.out.println("[exp5] starting single-batch correctness verification");

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String configPath = ConfigBridge.materialize(spec.id, run);

            String datasetName = new File(run.dataset.seqPath).getName().replace("_seq.txt", "");
            double baseUtil = run.minUtil;
            double mu = run.mu;

            System.out.println();
            System.out.println("[exp5] dataset=" + datasetName.toUpperCase());

            List<Sequence> fullDatabase = QSDB_Parser.loadDB(run.dataset.euiPath, run.dataset.seqPath);
            if (fullDatabase == null || fullDatabase.isEmpty()) continue;

            double[] thresholds = (run.thresholds != null && run.thresholds.length > 0)
                    ? run.thresholds
                    : new double[]{baseUtil, baseUtil * 0.8, baseUtil * 0.5};

            Map<String, Boolean> algoFailed = new HashMap<>();
            algoFailed.put("EHAUSM-R", false);
            algoFailed.put("HAUSP-UB", false);

            for (double util : thresholds) {
                System.out.printf("  minUtil=%.6f%n", util);

                for (int rep = 0; rep < ExperimentConfig.REPEATS; rep++) {
                    if (ExperimentConfig.REPEATS > 1) {
                        System.out.println("    trial " + (rep + 1) + "/" + ExperimentConfig.REPEATS);
                    }
                    RunResult reminingRes = runSingleTask("EHAUSM-R",
                            configPath, outputDir, logFileName, util, mu, datasetName, fullDatabase, algoFailed, rep);

                    RunResult fullRes = runSingleTask("HAUSP-UB",
                            configPath, outputDir, logFileName, util, mu, datasetName, fullDatabase, algoFailed, rep);

                    if (reminingRes != null && fullRes != null &&
                            "SUCCESS".equals(reminingRes.runStatus) && "SUCCESS".equals(fullRes.runStatus)) {
                        if (reminingRes.hauspFound == fullRes.hauspFound) {
                            System.out.printf("      matched: %d patterns%n", reminingRes.hauspFound);
                        } else {
                            System.err.printf("      MISMATCH: oracle=%d, hausp-ub=%d%n",
                                    reminingRes.hauspFound, fullRes.hauspFound);
                        }
                    }
                }
            }
        }
        System.out.println();
        System.out.println("[exp5] done");
        System.exit(0);
    }

    private static RunResult runSingleTask(String algo, String conf, String out, String file,
                                           double util, double mu, String dataset, List<Sequence> data,
                                           Map<String, Boolean> algoFailed, int repeatIndex) {
        System.out.print("      [" + algo + "] ");

        if (algoFailed.get(algo)) {
            System.out.println("skipped");
            logFailedResult(out, file, algo, dataset, util, mu, repeatIndex, "SKIPPED");
            return null;
        }

        RunIsolation.forceGC();

        ExecutorService executor = Executors.newSingleThreadExecutor();
        final Object[] algRef = new Object[1];
        RunResult res = null;

        try {
            Callable<RunResult> task = () -> {
                if (algo.equals("HAUSP-UB")) {
                    HAUSP_UB alg = new HAUSP_UB(conf);
                    alg.setConfig(util);
                    algRef[0] = alg;
                    alg.enableIO = ENABLE_IO;
                    return alg.processBatch(data, 0);
                } else {
                    EHAUSM_Remining alg = new EHAUSM_Remining(conf);
                    alg.setConfig(util);
                    algRef[0] = alg;
                    alg.enableIO = ENABLE_IO;
                    return alg.processBatch(data, 0);
                }
            };

            Future<RunResult> future = executor.submit(task);
            try {
                res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
                res.runStatus = "SUCCESS";
            } catch (Exception e) {
                algoFailed.put(algo, true);
                res = new RunResult();
                res.runStatus = (e.getCause() instanceof OutOfMemoryError) ? "OOM" : "ERROR";
                System.out.print(res.runStatus.toLowerCase() + " ");
            }

            if (res != null && "SUCCESS".equals(res.runStatus)) {
                res.algorithm = algo; res.dataset = dataset; res.minUtil = util;
                res.mu = CSVLogger.effectiveMu(algo, mu); res.deltaRatio = 1.0; res.batchID = 0;
                res.runIndex = repeatIndex;
                CSVLogger.logResult(out, file, res);
                System.out.println("OK (" + res.hauspFound + " patterns)");
            } else {
                logFailedResult(out, file, algo, dataset, util, mu, repeatIndex, res != null ? res.runStatus : "ERROR");
            }
        } catch (Exception ignored) {
        } finally {
            executor.shutdownNow();
            try { executor.awaitTermination(5, TimeUnit.SECONDS); }
            catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
            algRef[0] = null;
            RunIsolation.forceGC();
        }
        return res;
    }

    private static void logFailedResult(String out, String file, String algo, String dataset,
                                        double util, double mu, int runIndex, String status) {
        RunResult failRes = new RunResult();
        failRes.algorithm = algo; failRes.dataset = dataset; failRes.minUtil = util;
        failRes.mu = CSVLogger.effectiveMu(algo, mu); failRes.batchID = 0; failRes.deltaRatio = 1.0;
        failRes.runIndex = runIndex; failRes.runStatus = status;
        CSVLogger.logResult(out, file, failRes);
    }
}
