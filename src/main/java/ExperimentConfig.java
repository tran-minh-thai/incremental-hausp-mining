import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Central declaration of every experimental parameter used by the runners.
 *
 * <p>Dataset paths are captured by {@link DatasetSpec}. Per-experiment parameters
 * (minimum-utility threshold, mu for the pre-large baseline, batch ratios and the
 * optional sweep arrays {@code minUtils[]}/{@code thresholds[]}) are captured by
 * {@link DatasetRun}. The same {@code DatasetSpec} may appear in several
 * {@code ExperimentSpec}s with different {@code DatasetRun} configurations; this
 * matches the paper, where for instance BIBLE uses 0.10% in Experiment 1 but
 * 0.05% in Experiments 3, 4 and 6.
 *
 * <p>Command-line overrides (repeats, dataset/arm filters, result directory,
 * timeout, K list) are stored in the mutable statics below; the launcher sets
 * them before any runner starts, and runners read them lazily at run time.
 *
 * <p>{@code --dump-config json} prints the whole declaration as JSON so that
 * the analysis scripts read thresholds and schedules from here instead of
 * retyping them.
 *
 * <p>Paths use forward slashes; the {@link java.io.File} API translates them to
 * the host separator on Windows.
 */
public final class ExperimentConfig {

    private ExperimentConfig() {}

    public static final String DATASETS_DIR = "datasets";

    /**
     * Root directory of the result CSVs. The launcher overrides it with
     * {@code --results-dir}; {@link ExperimentSpec#outputDir()} reads it at
     * run time, so a spec never captures the default at class initialisation.
     */
    public static String RESULTS_DIR = "results";
    public static final String BUILD_DIR    = "build";

    /**
     * Number of independent trials per (dataset x algorithm x batch) configuration.
     * The default of three lets the runners report mean and standard deviation
     * without quadrupling the wall-clock budget. The launcher overrides this
     * value when {@code --repeats N} is supplied on the command line.
     */
    public static int REPEATS = 3;

    /**
     * Adaptive repeat count ({@code --repeats-min-seconds S}, 0 = off). When the
     * first trial of a configuration completes in less than S seconds the
     * configuration is repeated more often, because measurement noise is a
     * larger share of a short run: under 1 s -> 15 trials, 1-10 s -> 10,
     * 10-120 s -> 5, otherwise {@link #REPEATS}. Never fewer than REPEATS.
     */
    public static double REPEATS_MIN_SECONDS = 0.0;

    /** Repeat count for a configuration whose first trial took {@code trial0TotalMs}; -1 = unknown. */
    public static int targetRepeats(long trial0TotalMs) {
        if (REPEATS_MIN_SECONDS <= 0 || trial0TotalMs < 0) return REPEATS;
        if (trial0TotalMs >= REPEATS_MIN_SECONDS * 1000.0) return REPEATS;
        if (trial0TotalMs < 1_000L) return Math.max(REPEATS, 15);
        if (trial0TotalMs < 10_000L) return Math.max(REPEATS, 10);
        if (trial0TotalMs < 120_000L) return Math.max(REPEATS, 5);
        return REPEATS;
    }

    /**
     * When {@code true}, runners consult {@link CompletedRuns} before each
     * configuration and skip those whose CSV row already exists. Toggled by
     * the launcher when {@code --resume} is supplied on the command line.
     */
    public static boolean RESUME = false;

    /**
     * Global timeout override (minutes). When &gt; 0, replaces every experiment's
     * own {@code timeoutMinutes} setting. Set by the launcher when
     * {@code --timeout N} is supplied. Zero means "use each experiment's default".
     */
    public static long TIMEOUT_OVERRIDE_MIN = 0;

    /** Returns the effective timeout in minutes for {@code spec}, honouring the override. */
    public static long effectiveTimeoutMinutes(ExperimentSpec spec) {
        return TIMEOUT_OVERRIDE_MIN > 0 ? TIMEOUT_OVERRIDE_MIN : spec.timeoutMinutes;
    }

    /**
     * Lower-case dataset short-names to keep when iterating an experiment's runs.
     * Empty (default) means "run every dataset declared in the spec". The
     * launcher fills this in from {@code --dataset name1,name2} and {@link #filteredRuns}
     * applies it lazily inside each runner.
     */
    public static Set<String> DATASET_FILTER = new LinkedHashSet<>();

    /**
     * Arm names to keep (exact strings as written in the CSV, e.g. {@code HAUSP-UB-L1}).
     * Empty means "every arm of the spec". Filled from {@code --algo a1,a2};
     * {@link #filteredAlgos} applies it, preserving the spec's order.
     */
    public static Set<String> ALGO_FILTER = new LinkedHashSet<>();

    /**
     * Batch counts K to run in Experiments 7 and 11 ({@code --k 10,20}); empty
     * means the experiment's own list ({@link #EXP7_BATCH_COUNTS} or {@link #EXP11_BATCH_COUNTS}).
     */
    public static List<Integer> K_FILTER = new ArrayList<>();

    /**
     * Returns the runs of {@code spec} that match {@link #DATASET_FILTER}.
     * When the filter is empty the original list is returned unchanged.
     */
    public static List<DatasetRun> filteredRuns(ExperimentSpec spec) {
        if (DATASET_FILTER.isEmpty()) return spec.runs;
        List<DatasetRun> out = new ArrayList<>();
        for (DatasetRun r : spec.runs) {
            if (DATASET_FILTER.contains(r.dataset.name.toLowerCase())) out.add(r);
        }
        if (out.isEmpty() && DATASET_FILTER.contains(EXAMPLE.name)) out.add(smokeRun(spec));
        return out;
    }

    /**
     * Toy-database run used to exercise any experiment end to end
     * ({@code --dataset example}). The toy database is not part of the paper's
     * specs (except Experiment 6), so a run is synthesised here: it copies the
     * batch schedule of the spec's first run and uses the thresholds of the
     * worked example (5% anchor; 10%/5%/2% for sweeps).
     */
    private static DatasetRun smokeRun(ExperimentSpec spec) {
        DatasetRun template = spec.runs.get(0);
        double[] sweep = {0.10, 0.05, 0.02};
        if (template.minUtils != null) return DatasetRun.withMinUtils(EXAMPLE, sweep, MU_PRELARGE);
        if (template.thresholds != null) return DatasetRun.withThresholds(EXAMPLE, sweep, MU_PRELARGE);
        return DatasetRun.simple(EXAMPLE, 0.05, MU_PRELARGE, template.batchRatios);
    }

    /**
     * Returns the arms of {@code spec} that match {@link #ALGO_FILTER}, in the
     * spec's order. Every runner iterates this list instead of
     * {@code spec.algorithms} so that {@code --algo} applies uniformly.
     */
    public static String[] filteredAlgos(ExperimentSpec spec) {
        if (ALGO_FILTER.isEmpty()) return spec.algorithms;
        List<String> out = new ArrayList<>();
        for (String a : spec.algorithms) if (ALGO_FILTER.contains(a)) out.add(a);
        return out.toArray(new String[0]);
    }

    /** Batch counts K for Experiments 7 and 11 after applying {@code --k}. */
    public static int[] effectiveBatchCounts(int[] defaults) {
        if (K_FILTER.isEmpty()) return defaults;
        int[] ks = new int[K_FILTER.size()];
        for (int i = 0; i < ks.length; i++) ks[i] = K_FILTER.get(i);
        return ks;
    }

    // ---------------------------------------------------------------------------------
    // Dataset paths.
    // ---------------------------------------------------------------------------------

    public static final DatasetSpec BIBLE = new DatasetSpec(
            "bible",
            DATASETS_DIR + "/bible/BIBLE_seq.txt",
            DATASETS_DIR + "/bible/BIBLE_eui.txt");

    public static final DatasetSpec BMS1 = new DatasetSpec(
            "bms1_spmf",
            DATASETS_DIR + "/bms1_spmf/BMS1_SPMF_seq.txt",
            DATASETS_DIR + "/bms1_spmf/BMS1_SPMF_eui.txt");

    public static final DatasetSpec FIFA = new DatasetSpec(
            "fifa",
            DATASETS_DIR + "/fifa/FIFA_seq.txt",
            DATASETS_DIR + "/fifa/FIFA_eui.txt");

    public static final DatasetSpec KOSARAK = new DatasetSpec(
            "kosarak",
            DATASETS_DIR + "/kosarak/KOSARAK_seq.txt",
            DATASETS_DIR + "/kosarak/KOSARAK_eui.txt");

    public static final DatasetSpec LEVIATHAN = new DatasetSpec(
            "leviathan",
            DATASETS_DIR + "/leviathan/LEVIATHAN_seq.txt",
            DATASETS_DIR + "/leviathan/LEVIATHAN_eui.txt");

    public static final DatasetSpec SIGN = new DatasetSpec(
            "sign",
            DATASETS_DIR + "/sign/SIGN_seq.txt",
            DATASETS_DIR + "/sign/SIGN_eui.txt");

    /** Tiny toy database used by the worked example in the paper. */
    public static final DatasetSpec EXAMPLE = new DatasetSpec(
            "example",
            DATASETS_DIR + "/example/example_seq.txt",
            DATASETS_DIR + "/example/example_eui.txt");

    /** Synthetic QSDB generated via IBM Quest-style generator
     *  (~47K sequences, ~68K distinct items, avg 2.4 itemsets/seq). */
    public static final DatasetSpec SYN_C8T1S5I8N5K = new DatasetSpec(
            "syn_c8t1s5i8n5k",
            DATASETS_DIR + "/syn/C8T1S5I8N5K_seq.txt",
            DATASETS_DIR + "/syn/C8T1S5I8N5K_eui.txt");

    /** Real-world benchmarks plus toy example and synthetic dataset, indexed by short name. */
    public static final Map<String, DatasetSpec> ALL_DATASETS;
    static {
        Map<String, DatasetSpec> m = new LinkedHashMap<>();
        for (DatasetSpec d : new DatasetSpec[]{BIBLE, BMS1, FIFA, KOSARAK, LEVIATHAN, SIGN, EXAMPLE, SYN_C8T1S5I8N5K}) {
            m.put(d.name, d);
        }
        ALL_DATASETS = Collections.unmodifiableMap(m);
    }

    // ---------------------------------------------------------------------------------
    // Recurring constants.
    // ---------------------------------------------------------------------------------

    /** Five equally-sized batches of 20% each, used in Experiments 1, 4 and 6. */
    private static final double[] FIVE_BATCH_20 = {0.2, 0.2, 0.2, 0.2, 0.2};

    /** Safety-margin coefficient of the pre-large baseline (Experiments 1, 3, 4, 7). */
    public static final double MU_PRELARGE = 0.20;

    /** Safety-margin values swept by Experiment 10 (pre-large sensitivity). */
    public static final double[] MU_SWEEP = {0.05, 0.10, 0.20, 0.40};

    /** Increment sizes of Experiment 3: initial load 80%, one update of this relative size. */
    public static final double[] EXP3_DELTAS = {0.05, 0.10, 0.15, 0.20};

    /** Batch counts swept by Experiment 7 (equal-sized batches, total volume fixed). */
    public static final int[] EXP7_BATCH_COUNTS = {10, 20, 50, 100};

    /** Batch counts of Experiment 11 (warm-start schedule); the paper reports K = 100. */
    public static final int[] EXP11_BATCH_COUNTS = {100};

    /** Share of the data loaded as batch 0 under the warm-start schedule of Experiment 11. */
    public static final double WARM_START_FIRST_RATIO = 0.20;

    /** Schedule labels written to the {@code Schedule} CSV column. */
    public static final String SCHEDULE_EQUAL = "equal";
    public static final String SCHEDULE_WARM20 = "warm20";

    /** K equal batches. */
    public static double[] equalSchedule(int K) {
        double[] r = new double[K];
        Arrays.fill(r, 1.0 / K);
        return r;
    }

    /**
     * Warm-start schedule: batch 0 holds {@code firstRatio} of the data, the
     * remaining {@code K-1} batches share the rest equally. Used by
     * Experiment 11 to give the K = 100 configuration a first batch of the
     * same size as the five-batch experiments, so that an OT at batch 0 can
     * no longer be blamed on a 1%-sized initial load.
     */
    public static double[] warmStartSchedule(int K, double firstRatio) {
        if (K < 2) throw new IllegalArgumentException("warm-start schedule needs K >= 2, got " + K);
        if (firstRatio <= 0 || firstRatio >= 1) throw new IllegalArgumentException("firstRatio must be in (0,1), got " + firstRatio);
        double[] r = new double[K];
        r[0] = firstRatio;
        double rest = (1.0 - firstRatio) / (K - 1);
        for (int i = 1; i < K; i++) r[i] = rest;
        return r;
    }

    // ---------------------------------------------------------------------------------
    // Experiment 1 -- candidate-generation efficiency (tightness).
    // Five batches of 20%; minUtil = 0.10% on BIBLE, 0.35% on BMS1, 1.20% on FIFA,
    // 0.30% on KOSARAK, LEVIATHAN and SIGN, 0.02% on SYN.
    // ---------------------------------------------------------------------------------
    // HAUSP-UB-L1 (SWU-only variant) is added to isolate the contribution of
    // the IAUUB/SeqMFUUB bounds from that of the engineering layout: it uses
    // the same AU-DUL pool and flat database as HAUSP-UB but skips Layer 2 and
    // Layer 3, so any tightness gap that remains belongs to the bounds.
    public static final ExperimentSpec EXP1 = new ExperimentSpec(
            1, "Tightness of upper bounds",
            "exp1", "experiment1_tightness.csv",
            150, true,
            new String[]{"EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM", "HAUSP-UB-L1", "HAUSP-UB"},
            Arrays.asList(
                    DatasetRun.simple(BIBLE,            0.0010, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(BMS1,             0.0035, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(FIFA,             0.0120, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(KOSARAK,          0.0030, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(LEVIATHAN,        0.0030, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(SIGN,             0.0030, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(SYN_C8T1S5I8N5K,  0.0002, MU_PRELARGE, FIVE_BATCH_20)
            )
    );

    // ---------------------------------------------------------------------------------
    // Experiment 2 -- ablation analysis of multi-layer pruning.
    // Five to seven minUtil thresholds per dataset over the full database.
    // ---------------------------------------------------------------------------------
    // Algorithms: five-way ablation that isolates each pruning layer.
    //   HAUSP-UB-L1    : Layer 1 (SWU) only; Layer 2 and Layer 3 bypassed.
    //   HAUSP-UB-L1L3  : Layers 1 and 3 active; Layer 2 (IAUUB) bypassed.
    //   HAUSP-UB*      : Layers 1 and 2 active; Layer 3 (SeqMFUUB) bypassed.
    //   HAUSP-UB       : the full three-layer algorithm.
    // EHAUSM-I (single PEAU bound) is included as the no-ablation baseline.
    //
    // Threshold ranges are extended downward so that they include the anchor
    // thresholds of Experiments 3, 4 and 6 on each dataset.
    public static final ExperimentSpec EXP2 = new ExperimentSpec(
            2, "Pruning power (ablation)",
            "exp2", "experiment2_pruning_power.csv",
            150, false,
            new String[]{"EHAUSM-I", "HAUSP-UB-L1", "HAUSP-UB-L1L3", "HAUSP-UB*", "HAUSP-UB"},
            Arrays.asList(
                    // Each sweep includes every anchor threshold used by Experiments
                    // 1, 3, 4, 6 and 7 on the same dataset, keeping thresholds comparable.
                    DatasetRun.withMinUtils(BIBLE,            new double[]{0.0010, 0.0009, 0.0008, 0.0007, 0.0006, 0.0005}, MU_PRELARGE),
                    DatasetRun.withMinUtils(BMS1,             new double[]{0.0060, 0.0050, 0.0045, 0.0040, 0.0035}, MU_PRELARGE),
                    DatasetRun.withMinUtils(FIFA,             new double[]{0.0120, 0.0110, 0.0100, 0.0095, 0.0090}, MU_PRELARGE),
                    DatasetRun.withMinUtils(KOSARAK,          new double[]{0.0080, 0.0070, 0.0060, 0.0050, 0.0040, 0.0030}, MU_PRELARGE),
                    DatasetRun.withMinUtils(LEVIATHAN,        new double[]{0.0060, 0.0050, 0.0040, 0.0030, 0.0020}, MU_PRELARGE),
                    DatasetRun.withMinUtils(SIGN,             new double[]{0.0090, 0.0080, 0.0070, 0.0060, 0.0050, 0.0040, 0.0030}, MU_PRELARGE),
                    DatasetRun.withMinUtils(SYN_C8T1S5I8N5K,  new double[]{0.00030, 0.00025, 0.00020, 0.00015, 0.00010}, MU_PRELARGE)
            )
    );

    // ---------------------------------------------------------------------------------
    // Experiment 3 -- scalability with respect to batch size.
    // Initial database is 80% of the data; a single incremental update of relative
    // size EXP3_DELTAS[i] is applied (the sweep is generated by the runner).
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP3 = new ExperimentSpec(
            3, "Scalability",
            "exp3", "experiment3_scalability.csv",
            150, false,
            new String[]{"EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM", "HAUSP-UB"},
            Arrays.asList(
                    DatasetRun.simple(BIBLE,            0.0005, MU_PRELARGE, new double[]{0.8, 0.2}),
                    DatasetRun.simple(BMS1,             0.0035, MU_PRELARGE, new double[]{0.8, 0.2}),
                    DatasetRun.simple(FIFA,             0.0120, MU_PRELARGE, new double[]{0.8, 0.2}),
                    DatasetRun.simple(KOSARAK,          0.0050, MU_PRELARGE, new double[]{0.8, 0.2}),
                    DatasetRun.simple(LEVIATHAN,        0.0030, MU_PRELARGE, new double[]{0.8, 0.2}),
                    DatasetRun.simple(SIGN,             0.0030, MU_PRELARGE, new double[]{0.8, 0.2}),
                    DatasetRun.simple(SYN_C8T1S5I8N5K,  0.0002, MU_PRELARGE, new double[]{0.8, 0.2})
            )
    );

    // ---------------------------------------------------------------------------------
    // Experiment 4 -- memory footprint and pre-large behaviour.
    // Five batches of 20%; thresholds are inherited from Experiment 3.
    // ---------------------------------------------------------------------------------
    // HAUSP-UB-L1 is included alongside the full HAUSP-UB so that the memory
    // figures can be split into the share attributable to the engineering
    // (flat AU-DUL pool, shared scratch buffers, length-aware SWU) and the
    // share attributable to the IAUUB/SeqMFUUB pruning.
    public static final ExperimentSpec EXP4 = new ExperimentSpec(
            4, "Memory footprint and pre-large behaviour",
            "exp4", "experiment4_memory_prelarge.csv",
            150, false,
            new String[]{"EHAUSM-R", "EHAUSM-I", "Pre-HAUSPM", "HAUSP-UB-L1", "HAUSP-UB"},
            Arrays.asList(
                    DatasetRun.simple(BIBLE,            0.0005, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(BMS1,             0.0035, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(FIFA,             0.0120, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(KOSARAK,          0.0050, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(LEVIATHAN,        0.0030, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(SIGN,             0.0030, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(SYN_C8T1S5I8N5K,  0.0002, MU_PRELARGE, FIVE_BATCH_20)
            )
    );

    // ---------------------------------------------------------------------------------
    // Experiment 5 -- single-batch correctness against the oracle.
    // Three discrete thresholds per dataset.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP5 = new ExperimentSpec(
            5, "Single-batch correctness",
            "exp5", "experiment5_accuracy.csv",
            150, true,
            new String[]{"EHAUSM-R", "HAUSP-UB"},
            Arrays.asList(
                    DatasetRun.withThresholds(BIBLE,            new double[]{0.000500, 0.000400, 0.000250}, MU_PRELARGE),
                    DatasetRun.withThresholds(BMS1,             new double[]{0.003500, 0.003200, 0.003000}, MU_PRELARGE),
                    DatasetRun.withThresholds(FIFA,             new double[]{0.015000, 0.012000, 0.010000}, MU_PRELARGE),
                    DatasetRun.withThresholds(KOSARAK,          new double[]{0.004000, 0.003000, 0.002500}, MU_PRELARGE),
                    DatasetRun.withThresholds(LEVIATHAN,        new double[]{0.003000, 0.002400, 0.001500}, MU_PRELARGE),
                    DatasetRun.withThresholds(SIGN,             new double[]{0.008000, 0.006400, 0.004000}, MU_PRELARGE),
                    DatasetRun.withThresholds(SYN_C8T1S5I8N5K,  new double[]{0.000500, 0.000300, 0.000100}, MU_PRELARGE)
            )
    );

    // ---------------------------------------------------------------------------------
    // Experiment 6 -- multi-batch correctness across five consecutive updates.
    // Thresholds differ from Experiments 3 and 4 on five of the six datasets.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP6 = new ExperimentSpec(
            6, "Multi-batch correctness",
            "exp6", "experiment6_multibatch_accuracy.csv",
            150, true,
            new String[]{"EHAUSM-R", "HAUSP-UB"},
            Arrays.asList(
                    DatasetRun.simple(EXAMPLE,          0.0500, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(BIBLE,            0.0005, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(BMS1,             0.0035, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(FIFA,             0.0090, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(KOSARAK,          0.0050, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(LEVIATHAN,        0.0050, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(SIGN,             0.0050, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(SYN_C8T1S5I8N5K,  0.0002, MU_PRELARGE, FIVE_BATCH_20)
            )
    );

    // ---------------------------------------------------------------------------------
    // Experiment 7 -- long-batch scalability.
    // Holds the total dataset volume fixed while sweeping the batch count
    // K over EXP7_BATCH_COUNTS. The minUtil values mirror Experiment 1 so that
    // the per-batch peak memory and runtime can be compared directly with the
    // five-batch reference.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP7 = new ExperimentSpec(
            7, "Long-batch scalability",
            "exp7", "experiment7_long_batch.csv",
            150, false,
            // HAUSP-UB first: it is the proposed method and the fastest, so in a
            // time-boxed session its full K-sweep completes before the slower
            // baselines (EHAUSM-I memory-heavy, Pre-HAUSPM always-rescan) consume
            // the budget. Order only affects execution sequence, not data keys;
            // the ArmOrder column records it.
            new String[]{"HAUSP-UB", "EHAUSM-I", "Pre-HAUSPM"},
            Arrays.asList(
                    // Execution order only (no scientific meaning): light datasets
                    // first so a time-boxed session maximises completed groups;
                    // KOSARAK (heaviest remaining) goes last.
                    DatasetRun.simple(BIBLE,            0.0010, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(BMS1,             0.0035, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(FIFA,             0.0120, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(LEVIATHAN,        0.0030, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(SIGN,             0.0030, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(SYN_C8T1S5I8N5K,  0.0002, MU_PRELARGE, FIVE_BATCH_20),
                    DatasetRun.simple(KOSARAK,          0.0050, MU_PRELARGE, FIVE_BATCH_20)
            )
    );

    // ---------------------------------------------------------------------------------
    // Experiment 8 -- threshold sensitivity at low minUtil values.
    // For every dataset, the minUtils[] sweep extends below the range used in
    // Experiment 2 so that the eta degradation as minUtil approaches the dataset's
    // noise floor can be observed.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP8 = new ExperimentSpec(
            8, "Threshold sensitivity",
            "exp8", "experiment8_threshold_sensitivity.csv",
            150, false,
            new String[]{"EHAUSM-I", "HAUSP-UB"},
            Arrays.asList(
                    // 0.00025 probes the BIBLE noise floor (135,751 patterns).
                    DatasetRun.withMinUtils(BIBLE,            new double[]{0.000400, 0.000350, 0.000300, 0.000250}, MU_PRELARGE),
                    DatasetRun.withMinUtils(BMS1,             new double[]{0.003500, 0.003200, 0.003000}, MU_PRELARGE),
                    DatasetRun.withMinUtils(FIFA,             new double[]{0.012000, 0.010000, 0.008000}, MU_PRELARGE),
                    DatasetRun.withMinUtils(KOSARAK,          new double[]{0.003500, 0.003000, 0.002500}, MU_PRELARGE),
                    DatasetRun.withMinUtils(LEVIATHAN,        new double[]{0.002000, 0.001500, 0.001000}, MU_PRELARGE),
                    DatasetRun.withMinUtils(SIGN,             new double[]{0.003000, 0.002500, 0.002000}, MU_PRELARGE),
                    DatasetRun.withMinUtils(SYN_C8T1S5I8N5K,  new double[]{0.000300, 0.000200, 0.000100, 0.000080}, MU_PRELARGE)
            )
    );

    // ---------------------------------------------------------------------------------
    // Experiment 9 -- attribution of the runtime/memory gap (opt-in, 2026-09-04).
    // One design decision changes per arm, from the EHAUSM baselines to the full
    // HAUSP-UB, on the five-batch schedule and thresholds of Experiment 1:
    //   EHAUSM-I                       persistent pattern tree, PEAU at node, utility lists
    //   EHAUSM-R                       no retention (re-mine), PEAU at node
    //   HAUSP-UB[noL2+L3@node+nopool]  flat layout + EUCS, PEAU-form at node, fresh lists
    //   HAUSP-UB[noL2+nopool]          + Layer-3 test on the child before recursion
    //   HAUSP-UB[noL2]                 + memory pool  (== HAUSP-UB-L1L3)
    //   HAUSP-UB                       + Layer-2 IAUUB during assembly
    // Runs through Experiment1Runner (same measurement code, separate CSV).
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP9 = new ExperimentSpec(
            9, "Attribution of the runtime/memory gap",
            "exp9", "experiment9_attribution.csv",
            150, false,
            new String[]{"EHAUSM-I", "EHAUSM-R", "HAUSP-UB[noL2+L3@node+nopool]", "HAUSP-UB[noL2+nopool]", "HAUSP-UB[noL2]", "HAUSP-UB"},
            EXP1.runs);

    // ---------------------------------------------------------------------------------
    // Experiment 10 -- pre-large safety-margin sensitivity (opt-in, 2026-09-04).
    // Same setting as Experiment 3 at delta = 20% (initial load 80%, one update
    // of 20%), single arm Pre-HAUSPM, mu swept over MU_SWEEP on every dataset.
    // Records whether the update batch triggered a rescan, the buffered utility
    // and the safety value, so that "the safety bound is violated at every
    // batch" is backed by a measurement rather than a single mu.
    // Runs through Experiment3Runner with the mu sweep enabled.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP10 = new ExperimentSpec(
            10, "Pre-large safety-margin sensitivity",
            "exp10", "experiment10_prelarge_mu.csv",
            150, false,
            new String[]{"Pre-HAUSPM"},
            EXP3.runs);

    // ---------------------------------------------------------------------------------
    // Experiment 11 -- long-batch scalability under a warm-start schedule (opt-in,
    // 2026-09-04). Same arms, datasets and thresholds as Experiment 7, but batch 0
    // holds WARM_START_FIRST_RATIO of the data and the remaining K-1 batches share
    // the rest equally. Motivation: on SIGN and SYN every algorithm exceeded the
    // per-batch limit at batch 0 of the equal K >= 20 / 50 schedules, where batch
    // 0 is 1-5% of the data; this schedule tests whether a first batch of the
    // size used in the five-batch experiments lets the K = 100 run proceed.
    // Runs through Experiment7Runner with SCHEDULE = warm20.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP11 = new ExperimentSpec(
            11, "Long-batch scalability, warm-start schedule",
            "exp11", "experiment11_warm_start.csv",
            150, false,
            new String[]{"HAUSP-UB", "EHAUSM-I", "Pre-HAUSPM"},
            EXP7.runs);

    /** The eight experiments of the paper; "--exp all" runs exactly these. */
    public static final ExperimentSpec[] ALL_EXPERIMENTS = {EXP1, EXP2, EXP3, EXP4, EXP5, EXP6, EXP7, EXP8};
    /** Opt-in studies (not part of "all"). */
    public static final ExperimentSpec[] EXTRA_EXPERIMENTS = {EXP9, EXP10, EXP11};

    public static ExperimentSpec getById(int id) {
        for (ExperimentSpec s : ALL_EXPERIMENTS) if (s.id == id) return s;
        for (ExperimentSpec s : EXTRA_EXPERIMENTS) if (s.id == id) return s;
        throw new IllegalArgumentException("Unknown experiment id " + id
                + " (1..8, or 9 = attribution study, 10 = pre-large mu sweep, 11 = warm-start K sweep)");
    }

    // ---------------------------------------------------------------------------------
    // JSON export (--dump-config json). Hand-rolled to avoid a JSON dependency.
    // ---------------------------------------------------------------------------------

    /** Every declared parameter as a JSON document; consumed by analysis/*.py. */
    public static String toJson() {
        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"results_dir_default\": \"results\",\n");
        sb.append("  \"repeats_default\": 3,\n");
        sb.append("  \"mu_prelarge\": ").append(num(MU_PRELARGE)).append(",\n");
        sb.append("  \"mu_sweep\": ").append(arr(MU_SWEEP)).append(",\n");
        sb.append("  \"exp3_deltas\": ").append(arr(EXP3_DELTAS)).append(",\n");
        sb.append("  \"exp7_batch_counts\": ").append(arr(EXP7_BATCH_COUNTS)).append(",\n");
        sb.append("  \"exp11_batch_counts\": ").append(arr(EXP11_BATCH_COUNTS)).append(",\n");
        sb.append("  \"warm_start_first_ratio\": ").append(num(WARM_START_FIRST_RATIO)).append(",\n");
        sb.append("  \"datasets\": [\n");
        int i = 0;
        for (DatasetSpec d : ALL_DATASETS.values()) {
            sb.append("    {\"name\": \"").append(d.name).append("\", \"csv_name\": \"").append(d.csvName())
              .append("\", \"seq_path\": \"").append(d.seqPath).append("\", \"eui_path\": \"").append(d.euiPath).append("\"}");
            sb.append(++i < ALL_DATASETS.size() ? ",\n" : "\n");
        }
        sb.append("  ],\n");
        sb.append("  \"experiments\": [\n");
        List<ExperimentSpec> all = new ArrayList<>(Arrays.asList(ALL_EXPERIMENTS));
        all.addAll(Arrays.asList(EXTRA_EXPERIMENTS));
        for (int e = 0; e < all.size(); e++) {
            ExperimentSpec s = all.get(e);
            sb.append("    {\"id\": ").append(s.id).append(", \"title\": \"").append(s.title).append("\",\n");
            sb.append("     \"output_subdir\": \"").append(s.outputSubdir).append("\", \"log_file\": \"").append(s.logFileName).append("\",\n");
            sb.append("     \"timeout_minutes\": ").append(s.timeoutMinutes).append(", \"enable_io\": ").append(s.enableIO).append(",\n");
            sb.append("     \"algorithms\": [");
            for (int a = 0; a < s.algorithms.length; a++) {
                if (a > 0) sb.append(", ");
                sb.append("\"").append(s.algorithms[a]).append("\"");
            }
            sb.append("],\n     \"runs\": [\n");
            for (int r = 0; r < s.runs.size(); r++) {
                DatasetRun run = s.runs.get(r);
                sb.append("       {\"dataset\": \"").append(run.dataset.name).append("\", \"csv_name\": \"").append(run.dataset.csvName())
                  .append("\", \"min_util\": ").append(num(run.minUtil)).append(", \"mu\": ").append(num(run.mu))
                  .append(", \"batch_ratios\": ").append(arr(run.batchRatios))
                  .append(", \"min_utils\": ").append(run.minUtils == null ? "null" : arr(run.minUtils))
                  .append(", \"thresholds\": ").append(run.thresholds == null ? "null" : arr(run.thresholds)).append("}");
                sb.append(r + 1 < s.runs.size() ? ",\n" : "\n");
            }
            sb.append("     ]}").append(e + 1 < all.size() ? ",\n" : "\n");
        }
        sb.append("  ]\n}\n");
        return sb.toString();
    }

    private static String num(double v) {
        String s = String.format(Locale.US, "%.10f", v);
        s = s.replaceAll("0+$", "");
        if (s.endsWith(".")) s += "0";
        return s;
    }

    private static String arr(double[] a) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < a.length; i++) {
            if (i > 0) sb.append(", ");
            sb.append(num(a[i]));
        }
        return sb.append("]").toString();
    }

    private static String arr(int[] a) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < a.length; i++) {
            if (i > 0) sb.append(", ");
            sb.append(a[i]);
        }
        return sb.append("]").toString();
    }

    // ---------------------------------------------------------------------------------
    // Nested types.
    // ---------------------------------------------------------------------------------

    /** Where a dataset lives on disk. */
    public static final class DatasetSpec {
        public final String name;
        public final String seqPath;
        public final String euiPath;

        public DatasetSpec(String name, String seqPath, String euiPath) {
            this.name = name;
            this.seqPath = seqPath;
            this.euiPath = euiPath;
        }

        /** Name written to the CSV {@code Dataset} column (file stem without {@code _seq.txt}). */
        public String csvName() {
            return new java.io.File(seqPath).getName().replace("_seq.txt", "");
        }
    }

    /**
     * Parameters of a single dataset within a single experiment.
     *
     * <p>Each runner consumes a list of {@code DatasetRun}; the same
     * {@link DatasetSpec} may be paired with different parameter values across
     * experiments.
     */
    public static final class DatasetRun {
        public final DatasetSpec dataset;
        /** Primary threshold (used by Experiments 1, 3, 4 and 6). */
        public final double minUtil;
        public final double mu;
        public final double[] batchRatios;
        /** Threshold sweep for Experiment 2; {@code null} otherwise. */
        public final double[] minUtils;
        /** Discrete threshold list for Experiment 5; {@code null} otherwise. */
        public final double[] thresholds;

        private DatasetRun(DatasetSpec dataset, double minUtil, double mu,
                           double[] batchRatios, double[] minUtils, double[] thresholds) {
            this.dataset = dataset;
            this.minUtil = minUtil;
            this.mu = mu;
            this.batchRatios = batchRatios;
            this.minUtils = minUtils;
            this.thresholds = thresholds;
        }

        /** Single-threshold run with the given batch schedule (E1, E3, E4, E6). */
        public static DatasetRun simple(DatasetSpec d, double minUtil, double mu, double[] ratios) {
            return new DatasetRun(d, minUtil, mu, ratios, null, null);
        }

        /** Sweep of minUtil values on the full database (E2). */
        public static DatasetRun withMinUtils(DatasetSpec d, double[] minUtils, double mu) {
            return new DatasetRun(d, minUtils[0], mu, new double[]{1.0}, minUtils, null);
        }

        /** Discrete list of thresholds on the full database (E5). */
        public static DatasetRun withThresholds(DatasetSpec d, double[] thresholds, double mu) {
            return new DatasetRun(d, thresholds[0], mu, new double[]{1.0}, null, thresholds);
        }
    }

    /** Complete description of a single experimental scenario. */
    public static final class ExperimentSpec {
        public final int id;
        public final String title;
        /** Sub-directory under {@link ExperimentConfig#RESULTS_DIR}, e.g. {@code exp1}. */
        public final String outputSubdir;
        public final String logFileName;
        public final long timeoutMinutes;
        public final boolean enableIO;
        public final String[] algorithms;
        public final List<DatasetRun> runs;

        public ExperimentSpec(int id, String title, String outputSubdir, String logFileName,
                              long timeoutMinutes, boolean enableIO,
                              String[] algorithms, List<DatasetRun> runs) {
            this.id = id;
            this.title = title;
            this.outputSubdir = outputSubdir;
            this.logFileName = logFileName;
            this.timeoutMinutes = timeoutMinutes;
            this.enableIO = enableIO;
            this.algorithms = algorithms;
            this.runs = runs;
        }

        /** Result directory of this experiment, resolved against the current {@link ExperimentConfig#RESULTS_DIR}. */
        public String outputDir() {
            return RESULTS_DIR + "/" + outputSubdir;
        }
    }
}
