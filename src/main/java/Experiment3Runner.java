import java.io.File;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Experiment 3 -- scalability with respect to the size of the incremental
 * update batch.
 *
 * <p>The initial database always consists of 80% of the data (Batch 0). A
 * single incremental update is then applied with a relative size taken from
 * {@link ExperimentConfig#EXP3_DELTAS}, producing four cost profiles per
 * dataset. Only the update batch is logged.
 *
 * <p>Also executes Experiment 10 (pre-large safety-margin sensitivity) when
 * the launcher sets {@link #SPEC_OVERRIDE}, {@link #MU_SWEEP} and
 * {@link #DELTAS_OVERRIDE}: the same 80%/20% split is then repeated for every
 * value of mu, and the {@code mu}, {@code RescanTriggered}, {@code BufferUtil}
 * and {@code SafetyBound} columns record what the pre-large buffer did.
 */
public class Experiment3Runner {
    public static boolean ENABLE_IO = ExperimentConfig.EXP3.enableIO;
    /** When non-null, run this spec instead of EXP3 (Experiment 10). */
    public static ExperimentConfig.ExperimentSpec SPEC_OVERRIDE = null;
    /** When non-null, repeat every configuration for each of these mu values (Experiment 10). */
    public static double[] MU_SWEEP = null;
    /** When non-null, replaces {@link ExperimentConfig#EXP3_DELTAS}. */
    public static double[] DELTAS_OVERRIDE = null;
    private static long TIMEOUT_MIN;

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = (SPEC_OVERRIDE != null) ? SPEC_OVERRIDE : ExperimentConfig.EXP3;
        ENABLE_IO = spec.enableIO;
        TIMEOUT_MIN = ExperimentConfig.effectiveTimeoutMinutes(spec);
        String outputDir = spec.outputDir();
        String logFileName = spec.logFileName;
        new File(outputDir).mkdirs();
        String tag = "[exp" + spec.id + "]";

        String[] algorithms = ExperimentConfig.filteredAlgos(spec);
        double[] deltas = (DELTAS_OVERRIDE != null) ? DELTAS_OVERRIDE : ExperimentConfig.EXP3_DELTAS;

        System.out.println(tag + " starting " + spec.title.toLowerCase());

        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            String datasetName = run.dataset.csvName();
            double minUtil = run.minUtil;
            double[] mus = (MU_SWEEP != null) ? MU_SWEEP : new double[]{run.mu};

            System.out.println();
            System.out.println(tag + " dataset=" + datasetName);

            Map<String, Boolean> algoFailed = new HashMap<>();
            for (String algo : algorithms) algoFailed.put(algo, false);

            for (double currentDeltaLabel : deltas) {
                double[] currentRatios = {0.8, currentDeltaLabel};
                System.out.println("  delta=" + currentDeltaLabel);

                List<List<Sequence>> databaseBatches = QSDB_Parser.loadDBByRatios(
                        run.dataset.euiPath, run.dataset.seqPath, currentRatios);

                if (databaseBatches.size() < 2) continue;

                List<Sequence> initialBatch = databaseBatches.get(0);
                List<Sequence> deltaBatch   = new ArrayList<>(databaseBatches.get(1));
                List<Sequence> cumulativeDB = new ArrayList<>(initialBatch);
                cumulativeDB.addAll(deltaBatch);

                for (double mu : mus) {
                    if (MU_SWEEP != null) System.out.println("    mu=" + String.format(Locale.US, "%.2f", mu));
                    String conf = (MU_SWEEP != null)
                            ? ConfigBridge.materialize(spec.id, run, minUtil, currentRatios, mu)
                            : ConfigBridge.materialize(spec.id, run, minUtil, currentRatios);

                    for (int ai = 0; ai < algorithms.length; ai++) {
                        String algo = algorithms[ai];
                        double muLogged = CSVLogger.effectiveMu(algo, mu);
                        int targetRepeats = ExperimentConfig.REPEATS;
                        for (int rep = 0; rep < targetRepeats; rep++) {
                            if (algoFailed.get(algo)) break;
                            if (CompletedRuns.shouldSkip(outputDir, logFileName, algo, datasetName, 1, rep, minUtil, currentDeltaLabel, muLogged)) {
                                System.out.println("    [" + algo + "] trial " + (rep + 1) + ": resume-skip");
                                if (CompletedRuns.groupFailed(outputDir, logFileName, algo, datasetName, rep + 1, minUtil,
                                        new double[]{Double.NaN, currentDeltaLabel}, muLogged)) {
                                    System.out.println("    [" + algo + "] trial " + (rep + 1) + ": recorded as failed in the CSV; arm not re-attempted");
                                    algoFailed.put(algo, true);
                                    break;
                                }
                                if (rep == 0) {
                                    targetRepeats = Experiment1Runner.raiseRepeats(targetRepeats,
                                            CompletedRuns.groupDurationMs(outputDir, logFileName, algo, datasetName, 0, minUtil,
                                                    new double[]{Double.NaN, currentDeltaLabel}, muLogged));
                                }
                                continue;
                            }
                            if (targetRepeats > 1) {
                                System.out.println("    trial " + (rep + 1) + "/" + targetRepeats);
                            }
                            RunIsolation.forceGC();
                            long t = runIncrementalTask(algo, conf, outputDir, logFileName,
                                    minUtil, mu, currentDeltaLabel, datasetName,
                                    initialBatch, deltaBatch, cumulativeDB, algoFailed, rep, ai);
                            if (rep == 0 && t >= 0) targetRepeats = Experiment1Runner.raiseRepeats(targetRepeats, t);
                        }
                    }
                }

                deltaBatch.clear(); cumulativeDB.clear();
                RunIsolation.forceGC();
            }
        }
        System.out.println();
        System.out.println(tag + " done");
    }

    /** @return tTotal(ms) of the logged update batch, or -1 when the trial failed. */
    private static long runIncrementalTask(String algo, String conf, String out, String file,
                                           double util, double mu, double ratioLabel, String dataset,
                                           List<Sequence> init, List<Sequence> delta,
                                           List<Sequence> cumu, Map<String, Boolean> algoFailed,
                                           int repeatIndex, int armOrder) {
        System.out.print("      [" + algo + "] ");

        if (algoFailed.get(algo)) {
            System.out.println("skipped");
            logFailedResult(out, file, algo, dataset, util, mu, ratioLabel, repeatIndex, armOrder, "SKIPPED");
            return -1;
        }

        ExecutorService executor = Executors.newSingleThreadExecutor();
        final Object[] algRef = new Object[1];
        boolean isFailed = false;
        long tTotal = -1;

        Callable<RunResult> task = () -> {
            if (algo.startsWith("HAUSP-UB")) {
                HAUSP_UB alg = HAUSP_UB.fromArmName(algo, conf);
                alg.setConfig(util);
                alg.enableIO = ENABLE_IO;
                algRef[0] = alg;
                alg.processBatch(init, 0);
                MemorySampler.resetMaxIfLive(); // the logged row is the update batch only
                return alg.processBatch(delta, 1);
            } else if (algo.equals("EHAUSM-I")) {
                EHAUSM_Inc alg = new EHAUSM_Inc(conf);
                alg.setConfig(util);
                alg.enableIO = ENABLE_IO;
                algRef[0] = alg;
                alg.processBatch(init, 0);
                MemorySampler.resetMaxIfLive(); // the logged row is the update batch only
                return alg.processBatch(delta, 1);
            } else if (algo.equals("EHAUSM-R")) {
                EHAUSM_Remining alg = new EHAUSM_Remining(conf);
                alg.setConfig(util);
                alg.enableIO = ENABLE_IO;
                algRef[0] = alg;
                return alg.processBatch(cumu, 1);
            } else if (algo.equals("Pre-HAUSPM")) {
                Pre_HUSPM_adapt alg = new Pre_HUSPM_adapt(conf);
                alg.setConfig(util);
                alg.enableIO = ENABLE_IO;
                algRef[0] = alg;
                alg.processBatch(init, 0);
                MemorySampler.resetMaxIfLive(); // the logged row is the update batch only
                return alg.processBatch(delta, 1);
            }
            throw new IllegalArgumentException("unknown arm " + algo);
        };

        MemorySampler mem = MemorySampler.startIfLive();
        try {
            Future<RunResult> future = executor.submit(task);
            RunResult res = future.get(TIMEOUT_MIN, TimeUnit.MINUTES);
            if (mem != null) mem.finish(res);
            res.runStatus = "SUCCESS";
            res.algorithm = algo; res.dataset = dataset; res.minUtil = util;
            res.mu = CSVLogger.effectiveMu(algo, mu); res.deltaRatio = ratioLabel;
            res.runIndex = repeatIndex; res.armOrder = armOrder;
            CSVLogger.logResult(out, file, res);
            tTotal = res.tTotal;
            if (res.rescanTriggered >= 0) {
                System.out.println("OK (rescan=" + res.rescanTriggered + ", buffer=" + res.bufferUtil
                        + ", safety=" + String.format(Locale.US, "%.0f", res.safetyBound) + ")");
            } else {
                System.out.println("OK");
            }
        } catch (Exception e) {
            isFailed = true;
            algoFailed.put(algo, true);
            String status = (e.getCause() instanceof OutOfMemoryError) ? "OOM" : "ERROR";
            if (e instanceof TimeoutException) status = "OT";
            System.out.println(status.toLowerCase());
            if ("ERROR".equals(status) && e.getCause() != null) e.getCause().printStackTrace();
            logFailedResult(out, file, algo, dataset, util, mu, ratioLabel, repeatIndex, armOrder, status);
        } finally {
            if (mem != null) mem.stop();
            executor.shutdownNow();
            try { executor.awaitTermination(5, TimeUnit.SECONDS); }
            catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
            algRef[0] = null;
            if (isFailed) RunIsolation.forceGC();
        }
        return tTotal;
    }

    private static void logFailedResult(String out, String file, String algo, String dataset,
                                        double util, double mu, double ratioLabel, int runIndex,
                                        int armOrder, String status) {
        RunResult failRes = new RunResult();
        failRes.algorithm = algo; failRes.dataset = dataset; failRes.minUtil = util;
        failRes.mu = CSVLogger.effectiveMu(algo, mu); failRes.deltaRatio = ratioLabel;
        failRes.batchID = 1; failRes.runIndex = runIndex; failRes.runStatus = status;
        failRes.armOrder = armOrder;
        CSVLogger.logResult(out, file, failRes);
    }
}
