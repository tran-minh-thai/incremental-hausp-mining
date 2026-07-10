/**
 * Data transfer object that carries the measurements of a single
 * algorithm/dataset/batch invocation between the runners and {@link CSVLogger}.
 */
public class RunResult {

    // General identification.
    public String timestamp = "";
    public String algorithm = "";
    public String dataset = "";
    public int batchID = 0;
    /** Index of the repeated trial; zero-based, single-run by default. */
    public int runIndex = 0;
    public String runStatus = "";

    // Input parameters.
    public double minUtil = 0.0;
    public double mu = 0.0;
    public double deltaRatio = 0.0;

    // Database properties.
    public long totalDBUtility = 0;
    public int cumulativeDBSize = 0;

    // Timing (milliseconds).
    public long tScan = 0;
    public long tMining = 0;
    public long tTotal = 0;

    // Per-layer pruning time (milliseconds). Populated by HAUSP_UB and its ablation variants.
    public long tLayer1 = 0;
    public long tLayer2 = 0;
    public long tLayer3 = 0;

    // Pruning counters.
    public long numCand = 0;
    public long numPrunedL1 = 0; // SWU.
    public long numPrunedL2 = 0; // IAUUB.
    public long numPrunedL3 = 0; // SeqMFUUB.

    // Tightness ratios per upper bound.
    public double ratioTightnessPEAU  = 0.0;
    public double ratioTightnessIAUUB = 0.0;
    public double ratioTightnessMFUUB = 0.0;

    // Output and memory.
    public long hauspFound = 0;
    public long shausActive = 0;
    public double memPeak = 0.0;

    // AU-DUL pool statistics. Populated by HAUSP_UB and HAUSP_UB_IAUUB; the
    // EHAUSM and Pre-HAUSPM baselines leave them at zero.
    public long poolBorrows = 0;     // total get() calls
    public long poolReuses  = 0;     // get() calls served from the pool
    public long poolPeakLive = 0;    // largest simultaneously borrowed
    public long audulActive = 0;     // live AU-DULs at the end of the batch
}
