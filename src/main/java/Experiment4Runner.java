import java.io.File;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
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
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Experiment 4 -- memory footprint and pre-large behaviour.
 *
 * <p>Each dataset is processed in five successive batches of 20%. A
 * background sampler reads the JVM heap usage every 100 ms throughout each
 * batch; the maximum delta above the baseline is reported as the per-batch
 * peak. Pre-HAUSPM is run with mu = 0.20.
 */
public class Experiment4Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP4.enableIO;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP4;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir();
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        String[] algorithms = ExperimentConfig.filteredAlgos(spec);
        System.out.println("[exp4] starting memory study");

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String configPath = ConfigBridge.materialize(spec.id, run);

            String datasetName = run.dataset.csvName();
            double minUtil = run.minUtil;
            double mu = run.mu;
            double[] ratios = run.batchRatios;

            System.out.println();
            System.out.println("[exp4] dataset=" + datasetName.toUpperCase());
            List<List<Sequence>> databaseBatches = QSDB_Parser.loadDBByRatios(
                    run.dataset.euiPath, run.dataset.seqPath, ratios);
            Map<String, Boolean> algoFailed = new HashMap<>();

            for (int ai = 0; ai < algorithms.length; ai++) {
                final String algo = algorithms[ai];
                final int armOrder = ai;
                if (algoFailed.getOrDefault(algo, false)) continue;
                System.out.println("  [" + algo + "]");

                final double muLogged = CSVLogger.effectiveMu(algo, mu);
                int targetRepeats = ExperimentConfig.REPEATS;

                for (int rep = 0; rep < targetRepeats; rep++) {
                    if (algoFailed.getOrDefault(algo, false)) break;
                    final int repeatIndex = rep;
                    if (CompletedRuns.shouldSkipAlgorithm(outputDir, logFileName, algo, datasetName, rep, minUtil, ratios, muLogged)) {
                        System.out.println("    trial " + (rep + 1) + ": resume-skip (all batches present)");
                        if (CompletedRuns.groupFailed(outputDir, logFileName, algo, datasetName, rep + 1, minUtil, ratios, muLogged)) {
                            // The recorded trial ended in OT/OOM/ERROR: the failure is the result; do not re-attempt.
                            System.out.println("    trial " + (rep + 1) + ": recorded as failed in the CSV; arm not re-attempted");
                            algoFailed.put(algo, true);
                            break;
                        }
                        if (rep == 0) {
                            targetRepeats = Experiment1Runner.raiseRepeats(targetRepeats,
                                    CompletedRuns.groupDurationMs(outputDir, logFileName, algo, datasetName, 0, minUtil, ratios, muLogged));
                        }
                        continue;
                    }
                    if (targetRepeats > 1) {
                        System.out.println("    trial " + (rep + 1) + "/" + targetRepeats);
                    }

                    RunIsolation.forceGC();

                    ExecutorService executor = Executors.newSingleThreadExecutor();
                    final Object[] algRef = new Object[1];

                    if (algo.startsWith("HAUSP-UB")) {
                        HAUSP_UB a = HAUSP_UB.fromArmName(algo, configPath); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                    } else if (algo.equals("Pre-HAUSPM")) {
                        Pre_HUSPM_adapt a = new Pre_HUSPM_adapt(configPath); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                    } else if (algo.equals("EHAUSM-I")) {
                        EHAUSM_Inc a = new EHAUSM_Inc(configPath); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                    } else if (algo.equals("EHAUSM-R")) {
                        EHAUSM_Remining a = new EHAUSM_Remining(configPath); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                    } else {
                        System.err.println("[exp4] unknown arm " + algo + "; skipped");
                        algoFailed.put(algo, true);
                        executor.shutdownNow();
                        break;
                    }

                    boolean isAlgoFailed = false;
                    long trialTotalMs = 0;
                    List<Sequence> cumulativeDB = new ArrayList<>();

                    for (int bId = 0; bId < databaseBatches.size(); bId++) {
                        System.out.printf("      batch %d (ratio=%.2f) ", bId, ratios[bId]);

                        if (isAlgoFailed) {
                            System.out.println("skipped");
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, armOrder, "SKIPPED");
                            continue;
                        }

                        List<Sequence> currentBatchData = databaseBatches.get(bId);
                        cumulativeDB.addAll(currentBatchData);

                        final int currentBatchId = bId;
                        final List<Sequence> finalDeltaBatch = new ArrayList<>(currentBatchData);
                        final List<Sequence> finalCumulativeDB = new ArrayList<>(cumulativeDB);

                        RunIsolation.forceGC();

                        AtomicLong peakBytes = new AtomicLong(0);
                        AtomicBoolean running = new AtomicBoolean(true);
                        MemoryMXBean memBean = ManagementFactory.getMemoryMXBean();

                        long baseline = memBean.getHeapMemoryUsage().getUsed();

                        Thread sampler = new Thread(() -> {
                            while (running.get()) {
                                long used = memBean.getHeapMemoryUsage().getUsed();
                                long delta = used - baseline;
                                if (delta > peakBytes.get()) peakBytes.set(delta);
                                try { Thread.sleep(100); } catch (InterruptedException ignored) { }
                            }
                        });
                        sampler.setDaemon(true); sampler.start();

                        Callable<RunResult> task = () -> {
                            if (algo.startsWith("HAUSP-UB")) {
                                return ((HAUSP_UB) algRef[0]).processBatch(finalDeltaBatch, currentBatchId);
                            } else if (algo.equals("Pre-HAUSPM")) {
                                return ((Pre_HUSPM_adapt) algRef[0]).processBatch(finalDeltaBatch, currentBatchId);
                            } else if (algo.equals("EHAUSM-I")) {
                                return ((EHAUSM_Inc) algRef[0]).processBatch(finalDeltaBatch, currentBatchId);
                            } else {
                                return ((EHAUSM_Remining) algRef[0]).processBatch(finalCumulativeDB, currentBatchId);
                            }
                        };

                        MemorySampler mem = MemorySampler.startIfLive();
                        Future<RunResult> future = executor.submit(task);
                        RunResult res = null;
                        String failStatus = null;

                        try {
                            res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
                            res.runStatus = "SUCCESS";
                        } catch (TimeoutException e) {
                            future.cancel(true); System.out.println("timeout"); isAlgoFailed = true; failStatus = "OT";
                        } catch (ExecutionException e) {
                            if (e.getCause() instanceof OutOfMemoryError) { System.out.println("out of memory"); failStatus = "OOM"; }
                            else { System.out.println("error"); e.getCause().printStackTrace(); failStatus = "ERROR"; }
                            isAlgoFailed = true;
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt(); isAlgoFailed = true; failStatus = "ERROR";
                        } finally {
                            running.set(false);
                            try { sampler.join(500); } catch (InterruptedException ignored) { }
                        }

                        if (mem != null) { if (res != null && "SUCCESS".equals(res.runStatus)) mem.finish(res); else mem.stop(); }
                        if (res != null && "SUCCESS".equals(res.runStatus)) {
                            double measuredPeakMB = peakBytes.get() / (1024.0 * 1024.0);
                            if (measuredPeakMB > res.memPeak) res.memPeak = measuredPeakMB;
                            res.algorithm = algo; res.dataset = datasetName; res.minUtil = minUtil;
                            res.mu = CSVLogger.effectiveMu(algo, mu); res.batchID = bId; res.deltaRatio = ratios[bId];
                            res.runIndex = repeatIndex; res.armOrder = armOrder;
                            CSVLogger.logResult(outputDir, logFileName, res);
                            trialTotalMs += res.tTotal;
                            System.out.println("OK (peak=" + String.format(Locale.US, "%.1f", res.memPeak) + " MB)");
                        } else if (failStatus != null) {
                            // Record the failure at the batch where it happened (status and batch id).
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, armOrder, failStatus);
                        }

                        if (isAlgoFailed) {
                            algRef[0] = null;
                            cumulativeDB.clear();
                            RunIsolation.forceGC();
                        }
                    }
                    algRef[0] = null;
                    executor.shutdownNow();
                    try { executor.awaitTermination(5, TimeUnit.SECONDS); }
                    catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
                    if (isAlgoFailed) algoFailed.put(algo, true);
                    else if (rep == 0) targetRepeats = Experiment1Runner.raiseRepeats(targetRepeats, trialTotalMs);

                    RunIsolation.forceGC();
                }
            }
        }
        System.out.println();
        System.out.println("[exp4] done");
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
