import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Central declaration of every experimental parameter used by Experiment1..6Runner.
 *
 * <p>Dataset paths are captured by {@link DatasetSpec}. Per-experiment parameters
 * (minimum-utility threshold, μ for the pre-large baseline, batch ratios and the
 * optional sweep arrays {@code minUtils[]}/{@code thresholds[]}) are captured by
 * {@link DatasetRun}. The same {@code DatasetSpec} may appear in several
 * {@code ExperimentSpec}s with different {@code DatasetRun} configurations; this
 * matches the paper, where for instance BIBLE uses 0.10% in Experiment 1 but
 * 0.05% in Experiments 3, 4 and 6.
 *
 * <p>Paths use forward slashes; the {@link java.io.File} API translates them to
 * the host separator on Windows.
 */
public final class ExperimentConfig {

    private ExperimentConfig() {}

    public static final String DATASETS_DIR = "datasets";
    public static final String RESULTS_DIR  = "results";
    public static final String BUILD_DIR    = "build";

    /**
     * Number of independent trials per (dataset × algorithm × batch) configuration.
     * The default of three lets the runners report mean and standard deviation
     * without quadrupling the wall-clock budget. The launcher overrides this
     * value when {@code --repeats N} is supplied on the command line.
     */
    public static int REPEATS = 3;

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
    public static java.util.Set<String> DATASET_FILTER = new java.util.LinkedHashSet<>();

    /**
     * Returns the runs of {@code spec} that match {@link #DATASET_FILTER}.
     * When the filter is empty the original list is returned unchanged.
     */
    public static List<DatasetRun> filteredRuns(ExperimentSpec spec) {
        if (DATASET_FILTER.isEmpty()) return spec.runs;
        List<DatasetRun> out = new java.util.ArrayList<>();
        for (DatasetRun r : spec.runs) {
            if (DATASET_FILTER.contains(r.dataset.name.toLowerCase())) out.add(r);
        }
        return out;
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

    /** Safety-margin coefficient of the pre-large baseline (Experiment 4). */
    private static final double MU_PRELARGE = 0.20;

    // ---------------------------------------------------------------------------------
    // Experiment 1 — candidate-generation efficiency (tightness).
    // Five batches of 20%; minUtil = 0.10% on BIBLE, 0.35% on BMS1, 0.90% on FIFA,
    // 0.30% on KOSARAK, LEVIATHAN and SIGN.
    // ---------------------------------------------------------------------------------
    // HAUSP-UB-L1 (SWU-only variant) is added to isolate the contribution of
    // the IAUUB/SeqMFUUB bounds from that of the engineering layout: it uses
    // the same AU-DUL pool and flat database as HAUSP-UB but skips Layer 2 and
    // Layer 3, so any tightness gap that remains belongs to the bounds.
    public static final ExperimentSpec EXP1 = new ExperimentSpec(
            1, "Tightness of upper bounds",
            RESULTS_DIR + "/exp1", "experiment1_tightness.csv",
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
    // Experiment 2 — ablation analysis of multi-layer pruning.
    // Five minUtil thresholds per dataset over the full database.
    // ---------------------------------------------------------------------------------
    // Algorithms: five-way ablation that isolates each pruning layer.
    //   HAUSP-UB-L1    : Layer 1 (SWU) only; Layer 2 and Layer 3 bypassed.
    //   HAUSP-UB-L1L3  : Layers 1 and 3 active; Layer 2 (IAUUB) bypassed.
    //   HAUSP-UB*      : Layers 1 and 2 active; Layer 3 (SeqMFUUB) bypassed.
    //   HAUSP-UB       : the full three-layer algorithm.
    // EHAUSM-I (single PEAU bound) is included as the no-ablation baseline.
    //
    // Threshold ranges are extended downward so that they include the anchor
    // thresholds of Experiments 3, 4 and 6 on each dataset (see the addendum
    // in the paper).
    public static final ExperimentSpec EXP2 = new ExperimentSpec(
            2, "Pruning power (ablation)",
            RESULTS_DIR + "/exp2", "experiment2_pruning_power.csv",
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
    // Experiment 3 — scalability with respect to batch size.
    // Initial database is 80% of the data; a single incremental update Δ ∈ {5,10,15,20}%
    // is applied (the Δ sweep is generated by the runner, not by this spec).
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP3 = new ExperimentSpec(
            3, "Scalability",
            RESULTS_DIR + "/exp3", "experiment3_scalability.csv",
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
    // Experiment 4 — memory footprint and pre-large behaviour.
    // Five batches of 20%; thresholds are inherited from Experiment 3.
    // ---------------------------------------------------------------------------------
    // HAUSP-UB-L1 is included alongside the full HAUSP-UB so that the memory
    // figures can be split into the share attributable to the engineering
    // (flat AU-DUL pool, shared scratch buffers, length-aware SWU) and the
    // share attributable to the IAUUB/SeqMFUUB pruning.
    public static final ExperimentSpec EXP4 = new ExperimentSpec(
            4, "Memory footprint and pre-large behaviour",
            RESULTS_DIR + "/exp4", "experiment4_memory_prelarge.csv",
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
    // Experiment 5 — single-batch correctness against the oracle.
    // Three discrete thresholds per dataset.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP5 = new ExperimentSpec(
            5, "Single-batch correctness",
            RESULTS_DIR + "/exp5", "experiment5_accuracy.csv",
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
    // Experiment 6 — multi-batch correctness across five consecutive updates.
    // Thresholds differ from Experiments 3 and 4 on five of the six datasets.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP6 = new ExperimentSpec(
            6, "Multi-batch correctness",
            RESULTS_DIR + "/exp6", "experiment6_multibatch_accuracy.csv",
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
    // Experiment 7 — long-batch scalability.
    // Holds the total dataset volume fixed while sweeping the batch count
    // K ∈ {10, 20, 50, 100} (encoded in Experiment7Runner). The minUtil values
    // mirror Experiment 1 so that the per-batch peak memory and runtime can be
    // compared directly with the five-batch reference.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP7 = new ExperimentSpec(
            7, "Long-batch scalability",
            RESULTS_DIR + "/exp7", "experiment7_long_batch.csv",
            150, false,
            // HAUSP-UB first: it is the proposed method and the fastest, so in a
            // time-boxed session its full K-sweep completes before the slower
            // baselines (EHAUSM-I memory-heavy, Pre-HAUSPM always-rescan) consume
            // the budget. Order only affects execution sequence, not data keys.
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
    // Experiment 8 — threshold sensitivity at low minUtil values.
    // For every dataset, the minUtils[] sweep extends below the range used in
    // Experiment 2 so that the η degradation as minUtil approaches the dataset's
    // noise floor can be observed.
    // ---------------------------------------------------------------------------------
    public static final ExperimentSpec EXP8 = new ExperimentSpec(
            8, "Threshold sensitivity",
            RESULTS_DIR + "/exp8", "experiment8_threshold_sensitivity.csv",
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

    public static final ExperimentSpec[] ALL_EXPERIMENTS = {EXP1, EXP2, EXP3, EXP4, EXP5, EXP6, EXP7, EXP8};

    public static ExperimentSpec getById(int id) {
        if (id < 1 || id > ALL_EXPERIMENTS.length) {
            throw new IllegalArgumentException("Experiment id must be in 1.." + ALL_EXPERIMENTS.length);
        }
        return ALL_EXPERIMENTS[id - 1];
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
    }

    /**
     * Parameters of a single dataset within a single experiment.
     *
     * <p>Each runner consumes a list of {@code DatasetRun}; the same
     * {@link DatasetSpec} may be paired with different parameter values across
     * Experiments 1 through 6.
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
        public final String outputDir;
        public final String logFileName;
        public final long timeoutMinutes;
        public final boolean enableIO;
        public final String[] algorithms;
        public final List<DatasetRun> runs;

        public ExperimentSpec(int id, String title, String outputDir, String logFileName,
                              long timeoutMinutes, boolean enableIO,
                              String[] algorithms, List<DatasetRun> runs) {
            this.id = id;
            this.title = title;
            this.outputDir = outputDir;
            this.logFileName = logFileName;
            this.timeoutMinutes = timeoutMinutes;
            this.enableIO = enableIO;
            this.algorithms = algorithms;
            this.runs = runs;
        }
    }
}
