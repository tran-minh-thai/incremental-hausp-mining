/**
 * Data transfer object that carries the measurements of a single
 * algorithm/dataset/batch invocation between the runners and {@link CSVLogger}.
 *
 * <p>Fields initialised to {@code -1} (or {@code NaN}) are "not measured by
 * this algorithm": the logger writes them as empty cells so that a reader
 * gets a missing value rather than a fake zero.
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
    /** Position of the arm inside the arm list executed for this run (0-based); records measurement order. */
    public int armOrder = -1;
    /** Batch schedule label: "equal" (K equal batches), "warm20" (20% first batch, rest equal), empty otherwise. */
    public String schedule = "";
    /** Identifier of the JVM run that produced the row; see {@link RunMeta}. */
    public String runId = RunMeta.RUN_ID;

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
    /** Utility lists assembled (root lists entering the search plus every child list built). Same definition for every algorithm. */
    public long numCand = 0;
    public long numPrunedL1 = 0; // SWU.
    public long numPrunedL2 = 0; // IAUUB.
    public long numPrunedL3 = 0; // SeqMFUUB.
    /** Part of numPrunedL3 applied on node entry (node already recursed into); HAUSP_UB only, -1 otherwise. */
    public long numPrunedL3Node = -1;
    /** Root lists (SWU >= threshold) rejected by the root test iutil < threshold and SWU < 2*threshold; HAUSP-UB arms only, -1 otherwise. */
    public long numPrunedL1Root = -1;
    /** Children recursed into (post Layer-2/3). Zero for baselines, whose numCand already has this meaning minus their own prunedL2. */
    public long numRecursed = 0;

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

    // Memory attribution, sampled at the moment the batch's peak heap usage was
    // recorded (HAUSP_UB only). Bytes held by the arrays of the persistent
    // structures; -1 when the algorithm does not report them.
    /** Arrays of every AU-DUL owned by the pool, borrowed or returned, excluding the single-item roots. */
    public long poolBytes = -1;
    /** Flat database arrays (item ids, utilities, itemset index, remaining utility) of the accumulated DB. */
    public long flatBytes = -1;
    /** Dense EUCS matrices or the sparse EUCS maps (estimated from their capacity). */
    public long eucsBytes = -1;
    /** Arrays of the single-item AU-DULs kept across batches. */
    public long audulRootBytes = -1;

    // Pre-large bookkeeping (Pre-HAUSPM only): -1 / NaN for the other algorithms.
    /** 1 when the batch triggered a rescan of the accumulated database, 0 when it was absorbed by the buffer. */
    public int rescanTriggered = -1;
    /** Total inserted utility accumulated in the pre-large buffer at the end of the batch (0 right after a rescan). */
    public long bufferUtil = -1;
    /** Quantity tested against the safety value: buffer before the batch plus the utility of the batch. */
    public long bufferTested = -1;
    /** Safety value f = (Su - Sl)/(1 - Su) x TSU_at_last_rescan against which the buffer is tested. */
    public double safetyBound = Double.NaN;
}
