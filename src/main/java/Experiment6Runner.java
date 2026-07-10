import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Experiment 6 — multi-batch correctness across five consecutive updates.
 *
 * <p>For each dataset, the data are split into five batches of 20%. After each
 * batch, the HAUSP set returned by the proposed incremental HAUSP-UB on the
 * delta is compared with the result of a complete re-mining performed by
 * EHAUSM-R on the accumulated database. A disagreement at any batch terminates
 * the remaining batches of that dataset.
 */
public class Experiment6Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP6.enableIO;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP6;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir;
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        System.out.println("[exp6] starting multi-batch correctness verification");

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String configPath = ConfigBridge.materialize(spec.id, run);

            double minUtil = run.minUtil;
            String datasetName = new File(run.dataset.seqPath).getName().replace("_seq.txt", "");
            double[] ratios = run.batchRatios;

            System.out.println();
            System.out.println("[exp6] dataset=" + datasetName.toUpperCase());

            List<List<Sequence>> batches = QSDB_Parser.loadDBByRatios(
                    run.dataset.euiPath, run.dataset.seqPath, ratios);
            if (batches == null || batches.isEmpty()) continue;

            for (int rep = 0; rep < ExperimentConfig.REPEATS; rep++) {
                // E6 writes its own narrow CSV (no DeltaRatio column), so we
                // skip resume support here; users can manually delete partial
                // rows or rerun the whole experiment when needed.

                if (ExperimentConfig.REPEATS > 1) {
                    System.out.println("  trial " + (rep + 1) + "/" + ExperimentConfig.REPEATS);
                }

                RunIsolation.forceGC();

                final Object[] fullAlgoRef = new Object[1];
                HAUSP_UB fullAlgo = new HAUSP_UB(configPath);
                fullAlgo.setConfig(minUtil);
                fullAlgo.enableIO = ENABLE_IO;
                fullAlgoRef[0] = fullAlgo;

                List<Sequence> cumulativeDB = new ArrayList<>();
                boolean isDatasetFailed = false;

                for (int bId = 0; bId < batches.size(); bId++) {
                    if (isDatasetFailed) {
                        System.out.println("    batch " + bId + " skipped (previous batch failed)");
                        logResult(outputDir, logFileName, datasetName, minUtil, bId, rep, -1, -1, "SKIPPED");
                        continue;
                    }

                    List<Sequence> deltaBatch = batches.get(bId);
                    cumulativeDB.addAll(deltaBatch);

                    System.out.print("    batch " + bId + " ");

                    RunIsolation.forceGC();

                    RunResult resOracle = runOracle(configPath, new ArrayList<>(cumulativeDB), bId);
                    RunResult resFull = runFull(fullAlgoRef, deltaBatch, bId);

                    if (resOracle != null && resFull != null &&
                            "SUCCESS".equals(resOracle.runStatus) && "SUCCESS".equals(resFull.runStatus)) {
                        if (resOracle.hauspFound == resFull.hauspFound) {
                            System.out.println("matched (" + resFull.hauspFound + " patterns)");
                            logResult(outputDir, logFileName, datasetName, minUtil, bId, rep,
                                    resOracle.hauspFound, resFull.hauspFound, "SUCCESS_MATCH");
                        } else {
                            System.out.println("MISMATCH (oracle=" + resOracle.hauspFound
                                    + ", hausp-ub=" + resFull.hauspFound + ")");
                            logResult(outputDir, logFileName, datasetName, minUtil, bId, rep,
                                    resOracle.hauspFound, resFull.hauspFound, "MISMATCH");
                            isDatasetFailed = true;
                        }
                    } else {
                        System.out.println("error");
                        logResult(outputDir, logFileName, datasetName, minUtil, bId, rep, -1, -1, "ERROR");
                        isDatasetFailed = true;
                    }

                    if (isDatasetFailed) {
                        fullAlgoRef[0] = null;
                        cumulativeDB.clear();
                        RunIsolation.forceGC();
                    }
                }

                fullAlgoRef[0] = null;
                RunIsolation.forceGC();
            }
        }
        System.out.println();
        System.out.println("[exp6] done");
        System.exit(0);
    }

    private static RunResult runOracle(String conf, List<Sequence> data, int bId) {
        ExecutorService executor = Executors.newSingleThreadExecutor();
        final Object[] oracleRef = new Object[1];
        oracleRef[0] = new EHAUSM_Remining(conf);
        boolean failed = false;
        RunResult res = null;

        Callable<RunResult> task = () -> {
            ((EHAUSM_Remining) oracleRef[0]).enableIO = ENABLE_IO;
            return ((EHAUSM_Remining) oracleRef[0]).processBatch(data, bId);
        };

        Future<RunResult> future = executor.submit(task);
        try {
            res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
            res.runStatus = "SUCCESS";
        } catch (Exception e) {
            failed = true;
            res = new RunResult();
            res.runStatus = (e.getCause() instanceof OutOfMemoryError) ? "OOM" : "ERROR";
        } finally {
            executor.shutdownNow();
            try { executor.awaitTermination(5, TimeUnit.SECONDS); }
            catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
        }
        if (failed) {
            oracleRef[0] = null;
            RunIsolation.forceGC();
        }
        return res;
    }

    private static RunResult runFull(Object[] algRef, List<Sequence> data, int bId) {
        ExecutorService executor = Executors.newSingleThreadExecutor();
        RunResult res = null;
        Callable<RunResult> task = () -> ((HAUSP_UB) algRef[0]).processBatch(data, bId);

        Future<RunResult> future = executor.submit(task);
        try {
            res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
            res.runStatus = "SUCCESS";
        } catch (Exception e) {
            res = new RunResult();
            res.runStatus = (e.getCause() instanceof OutOfMemoryError) ? "OOM" : "ERROR";
        } finally {
            executor.shutdownNow();
            try { executor.awaitTermination(5, TimeUnit.SECONDS); }
            catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
        }
        return res;
    }

    private static void logResult(String outDir, String file, String dataset, double minUtil, int bId,
                                  int runIndex, long hauspOracle, long hauspFull, String status) {
        try (PrintWriter writer = new PrintWriter(new FileWriter(outDir + "/" + file, true))) {
            File f = new File(outDir + "/" + file);
            if (f.length() == 0) writer.println("Dataset,MinUtil,BatchID,RunIndex,HAUSP_EHAUSM-R,HAUSP_HAUSP-UB,Status");
            writer.printf(Locale.US, "%s,%.6f,%d,%d,%d,%d,%s%n",
                    dataset, minUtil, bId, runIndex, hauspOracle, hauspFull, status);
        } catch (IOException e) {
            e.printStackTrace();
        }
    }
}
