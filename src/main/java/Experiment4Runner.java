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
 * Experiment 4 — memory footprint and pre-large behaviour.
 *
 * <p>Each dataset is processed in five successive batches of 20%. A
 * background sampler reads the JVM heap usage every 100 ms throughout each
 * batch; the maximum delta above the baseline is reported as the per-batch
 * peak. Pre-HAUSPM is run with μ = 0.20.
 */
public class Experiment4Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP4.enableIO;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP4;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir;
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();

        String[] algorithms = spec.algorithms;
        System.out.println("[exp4] starting memory study");

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String configPath = ConfigBridge.materialize(spec.id, run);

            String datasetName = new File(run.dataset.seqPath).getName().replace("_seq.txt", "");
            double minUtil = run.minUtil;
            double mu = run.mu;
            double[] ratios = run.batchRatios;

            System.out.println();
            System.out.println("[exp4] dataset=" + datasetName.toUpperCase());
            List<List<Sequence>> databaseBatches = QSDB_Parser.loadDBByRatios(
                    run.dataset.euiPath, run.dataset.seqPath, ratios);
            Map<String, Boolean> algoFailed = new HashMap<>();

            for (String algo : algorithms) {
                if (algoFailed.getOrDefault(algo, false)) continue;
                System.out.println("  [" + algo + "]");

                for (int rep = 0; rep < ExperimentConfig.REPEATS; rep++) {
                    if (algoFailed.getOrDefault(algo, false)) break;
                    final int repeatIndex = rep;
                    if (CompletedRuns.shouldSkipAlgorithm(outputDir, logFileName, algo, datasetName, rep, minUtil, ratios)) {
                        System.out.println("    trial " + (rep + 1) + ": resume-skip (all batches present)");
                        continue;
                    }
                    if (ExperimentConfig.REPEATS > 1) {
                        System.out.println("    trial " + (rep + 1) + "/" + ExperimentConfig.REPEATS);
                    }

                    RunIsolation.forceGC();

                    ExecutorService executor = Executors.newSingleThreadExecutor();
                    final Object[] algRef = new Object[1];

                    if (algo.equals("HAUSP-UB")) {
                        HAUSP_UB a = new HAUSP_UB(configPath); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                    } else if (algo.equals("HAUSP-UB-L1")) {
                        HAUSP_UB a = new HAUSP_UB(configPath); a.setConfig(minUtil); a.enableIO = ENABLE_IO;
                        a.enableLayer2IAUUB = false;
                        a.enableLayer3MFUUB = false;
                        algRef[0] = a;
                    } else if (algo.equals("Pre-HAUSPM")) {
                        Pre_HUSPM_adapt a = new Pre_HUSPM_adapt(configPath); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                    } else if (algo.equals("EHAUSM-I")) {
                        EHAUSM_Inc a = new EHAUSM_Inc(configPath); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                    } else if (algo.equals("EHAUSM-R")) {
                        EHAUSM_Remining a = new EHAUSM_Remining(configPath); a.setConfig(minUtil); a.enableIO = ENABLE_IO; algRef[0] = a;
                    }

                    boolean isAlgoFailed = false;
                    List<Sequence> cumulativeDB = new ArrayList<>();

                    for (int bId = 0; bId < databaseBatches.size(); bId++) {
                        System.out.printf("      batch %d (ratio=%.2f) ", bId, ratios[bId]);

                        if (isAlgoFailed) {
                            System.out.println("skipped");
                            logFailedResult(outputDir, logFileName, algo, datasetName, minUtil, mu, ratios[bId], bId, repeatIndex, "SKIPPED");
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
                            try { Thread.sleep(100); } catch (InterruptedException ignored) {}
                        }
                    });
                    sampler.setDaemon(true); sampler.start();

                    Callable<RunResult> task = () -> {
                        if (algo.equals("HAUSP-UB") || algo.equals("HAUSP-UB-L1")) {
                            return ((HAUSP_UB) algRef[0]).processBatch(finalDeltaBatch, currentBatchId);
                        } else if (algo.equals("Pre-HAUSPM")) {
                            return ((Pre_HUSPM_adapt) algRef[0]).processBatch(finalDeltaBatch, currentBatchId);
                        } else if (algo.equals("EHAUSM-I")) {
                            return ((EHAUSM_Inc) algRef[0]).processBatch(finalDeltaBatch, currentBatchId);
                        } else {
                            return ((EHAUSM_Remining) algRef[0]).processBatch(finalCumulativeDB, currentBatchId);
                        }
                    };

                    Future<RunResult> future = executor.submit(task);
                    RunResult res = null;

                    try {
                        res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
                        res.runStatus = "SUCCESS";
                    } catch (TimeoutException e) {
                        future.cancel(true); System.out.println("timeout"); isAlgoFailed = true;
                    } catch (ExecutionException e) {
                        if (e.getCause() instanceof OutOfMemoryError) { System.out.println("out of memory"); }
                        else { System.out.println("error"); }
                        isAlgoFailed = true;
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt(); isAlgoFailed = true;
                    } finally {
                        running.set(false);
                        try { sampler.join(500); } catch (InterruptedException ignored) {}
                    }

                        if (res != null && "SUCCESS".equals(res.runStatus)) {
                            double measuredPeakMB = peakBytes.get() / (1024.0 * 1024.0);
                            if (measuredPeakMB > res.memPeak) res.memPeak = measuredPeakMB;
                            res.algorithm = algo; res.dataset = datasetName; res.minUtil = minUtil;
                            res.mu = CSVLogger.effectiveMu(algo, mu); res.batchID = bId; res.deltaRatio = ratios[bId];
                            res.runIndex = repeatIndex;
                            CSVLogger.logResult(outputDir, logFileName, res);
                            System.out.println("OK (peak=" + String.format(Locale.US, "%.1f", res.memPeak) + " MB)");
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

                    RunIsolation.forceGC();
                }
            }
        }
        System.out.println();
        System.out.println("[exp4] done");
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
