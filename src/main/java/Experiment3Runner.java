import java.io.File;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Experiment 3 — scalability with respect to the size of the incremental
 * update batch.
 *
 * <p>The initial database always consists of 80% of the data (Batch 0). A
 * single incremental update is then applied with Δ ∈ {5%, 10%, 15%, 20%},
 * producing four cost profiles per dataset.
 */
public class Experiment3Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP3.enableIO;
    private static long TIMEOUT_MIN;

    private static final double[][] TEST_RATIOS = {
            {0.8, 0.05},
            {0.8, 0.10},
            {0.8, 0.15},
            {0.8, 0.20}
    };
    private static final double[] DELTA_LABELS = {0.05, 0.10, 0.15, 0.20};

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP3;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir;
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        String[] algorithms = spec.algorithms;

        System.out.println("[exp3] starting scalability study");

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String datasetName = new File(run.dataset.seqPath).getName().replace("_seq.txt", "");
            double minUtil = run.minUtil;
            double mu = run.mu;

            System.out.println();
            System.out.println("[exp3] dataset=" + datasetName);

            Map<String, Boolean> algoFailed = new HashMap<>();
            for (String algo : algorithms) algoFailed.put(algo, false);

            for (int i = 0; i < TEST_RATIOS.length; i++) {
                double[] currentRatios = TEST_RATIOS[i];
                double currentDeltaLabel = DELTA_LABELS[i];
                System.out.println("  Δ=" + currentDeltaLabel);

                String conf = ConfigBridge.materialize(spec.id, run, minUtil, currentRatios);

                List<List<Sequence>> databaseBatches = QSDB_Parser.loadDBByRatios(
                        run.dataset.euiPath, run.dataset.seqPath, currentRatios);

                if (databaseBatches.size() < 2) continue;

                List<Sequence> initialBatch = databaseBatches.get(0);
                List<Sequence> deltaBatch   = new ArrayList<>(databaseBatches.get(1));
                List<Sequence> cumulativeDB = new ArrayList<>(initialBatch);
                cumulativeDB.addAll(deltaBatch);

                for (String algo : algorithms) {
                    for (int rep = 0; rep < ExperimentConfig.REPEATS; rep++) {
                        if (algoFailed.get(algo)) break;
                        if (CompletedRuns.shouldSkip(outputDir, logFileName, algo, datasetName, 1, rep, minUtil, currentDeltaLabel)) {
                            System.out.println("    [" + algo + "] trial " + (rep + 1) + ": resume-skip");
                            continue;
                        }
                        if (ExperimentConfig.REPEATS > 1) {
                            System.out.println("    trial " + (rep + 1) + "/" + ExperimentConfig.REPEATS);
                        }
                        RunIsolation.forceGC();
                        runIncrementalTask(algo, conf, outputDir, logFileName,
                                minUtil, mu, currentDeltaLabel, datasetName,
                                initialBatch, deltaBatch, cumulativeDB, algoFailed, rep);
                    }
                }

                deltaBatch.clear(); cumulativeDB.clear();
                RunIsolation.forceGC();
            }
        }
        System.out.println();
        System.out.println("[exp3] done");
        System.exit(0);
    }

    private static void runIncrementalTask(String algo, String conf, String out, String file,
                                           double util, double mu, double ratioLabel, String dataset,
                                           List<Sequence> init, List<Sequence> delta,
                                           List<Sequence> cumu, Map<String, Boolean> algoFailed,
                                           int repeatIndex) {
        System.out.print("      [" + algo + "] ");

        if (algoFailed.get(algo)) {
            System.out.println("skipped");
            logFailedResult(out, file, algo, dataset, util, mu, ratioLabel, repeatIndex, "SKIPPED");
            return;
        }

        ExecutorService executor = Executors.newSingleThreadExecutor();
        final Object[] algRef = new Object[1];
        boolean isFailed = false;

        Callable<RunResult> task = () -> {
            if (algo.equals("HAUSP-UB")) {
                HAUSP_UB alg = new HAUSP_UB(conf);
                alg.setConfig(util);
                algRef[0] = alg;
                alg.processBatch(init, 0);
                return alg.processBatch(delta, 1);
            } else if (algo.equals("EHAUSM-I")) {
                EHAUSM_Inc alg = new EHAUSM_Inc(conf);
                alg.setConfig(util);
                algRef[0] = alg;
                alg.processBatch(init, 0);
                return alg.processBatch(delta, 1);
            } else if (algo.equals("EHAUSM-R")) {
                EHAUSM_Remining alg = new EHAUSM_Remining(conf);
                alg.setConfig(util);
                algRef[0] = alg;
                return alg.processBatch(cumu, 1);
            } else {
                Pre_HUSPM_adapt alg = new Pre_HUSPM_adapt(conf);
                alg.setConfig(util);
                algRef[0] = alg;
                alg.processBatch(init, 0);
                return alg.processBatch(delta, 1);
            }
        };

        try {
            Future<RunResult> future = executor.submit(task);
            RunResult res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
            res.runStatus = "SUCCESS";
            res.algorithm = algo; res.dataset = dataset; res.minUtil = util;
            res.mu = CSVLogger.effectiveMu(algo, mu); res.deltaRatio = ratioLabel;
            res.runIndex = repeatIndex;
            CSVLogger.logResult(out, file, res);
            System.out.println("OK");
        } catch (Exception e) {
            isFailed = true;
            algoFailed.put(algo, true);
            String status = (e.getCause() instanceof OutOfMemoryError) ? "OOM" : "ERROR";
            if (e instanceof TimeoutException) status = "OT";
            System.out.println(status.toLowerCase());
            logFailedResult(out, file, algo, dataset, util, mu, ratioLabel, repeatIndex, status);
        } finally {
            executor.shutdownNow();
            try { executor.awaitTermination(5, TimeUnit.SECONDS); }
            catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
            algRef[0] = null;
            if (isFailed) RunIsolation.forceGC();
        }
    }

    private static void logFailedResult(String out, String file, String algo, String dataset,
                                        double util, double mu, double ratioLabel, int runIndex, String status) {
        RunResult failRes = new RunResult();
        failRes.algorithm = algo; failRes.dataset = dataset; failRes.minUtil = util;
        failRes.mu = CSVLogger.effectiveMu(algo, mu); failRes.deltaRatio = ratioLabel;
        failRes.runIndex = runIndex; failRes.runStatus = status;
        CSVLogger.logResult(out, file, failRes);
    }
}
