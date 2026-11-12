import it.unimi.dsi.fastutil.ints.IntArrayList;
import it.unimi.dsi.fastutil.longs.Long2LongOpenHashMap;
import it.unimi.dsi.fastutil.longs.LongOpenHashSet;

import java.io.*;
import java.text.SimpleDateFormat;
import java.util.*;

/**
 * HAUSP-UB: the proposed algorithm for incremental high-average-utility
 * sequential pattern mining with a three-layer pruning architecture: the length-aware SWU
 * filter (Layer 1); the coupled test on every assembled child before any recursion, which
 * releases a rejected child list at once (Layer 2, early release); and the same coupled bound
 * tested on entry to a node (Layer 3). Until 2026-10-16 Layer 2 tested a decoupled estimate
 * accumulated during assembly; the coupled value it is replaced by is never larger and is folded
 * into each list as it is assembled.
 *
 * <p>The SeqMFUUB tightness metric is reported as an average of ratios. A length-aware SWU
 * filter is applied before each EUCS lookup to avoid wasted cache probes.
 *
 * <p>Internal counters and intermediate AU-DUL pools are reused across
 * batches to keep per-batch cost proportional to {@code |Δ|} rather than to
 * the accumulated database.
 */
public class HAUSP_UB {
    private final AUDULPool audulPool = new AUDULPool();
    public boolean enableIO = false;
    /** Gate the per-candidate tightness division; set to {@code false} for pure benchmarking. */
    public boolean enableTightnessMetric = true;

    /**
     * When {@code false}, Layer 2 (the coupled test on every child before recursion) is bypassed,
     * so a child is tested only where the recursion reaches it or on node entry, if Layer 3 is
     * on. The field keeps its old name, which the arm options and the CSV columns refer to.
     */
    public boolean enableLayer2IAUUB = true;

    /**
     * When {@code false}, the SeqMFUUB filter at Layer 3 is bypassed in
     * {@code miningDFS}. Used by the ablation study.
     */
    public boolean enableLayer3MFUUB = true;

    /**
     * Attribution study (2026-09-04). When {@code false}, the Layer-3 test is
     * applied only on entry to miningDFS (the node-level placement used by the
     * EHAUSM baselines); when {@code true} (default) every child is also tested
     * in processRecurse before recursion, so a rejected child never becomes a
     * node. Only meaningful when enableLayer3MFUUB is true.
     */
    public boolean childLevelL3 = true;

    /**
     * Attribution study (2026-09-04). When {@code false}, AUDULPool.get()
     * always allocates a fresh list and release() drops it for the garbage
     * collector, so the arm measures the layout without the pool.
     */
    public boolean enablePool = true;

    /**
     * When {@code false}, the EUCS matrices/maps are neither built nor consulted
     * (since 2026-09-08; before that only the lookups were bypassed), so
     * the arm HAUSP-UB[noL2+L3@node+noEUCS] builds exactly the search tree of
     * EHAUSM-R (same length-aware SWU filter, same coupled bound applied on
     * node entry) and must report the same number of lists assembled.
     */
    public boolean enableEUCS = true;

    /**
     * Control arm (2026-10-16). When {@code true}, every batch after the first rebuilds the total
     * utility, the SWU of each item, the flat database and the single-item lists from all
     * sequences seen so far, instead of appending the new batch to them. Everything else, the
     * search included, is unchanged, so the arm measures what append-only maintenance saves.
     */
    public boolean rebuildEachBatch = false;

    /**
     * Top-k mode (author decision, 2026-10-16). When {@code topK > 0} a batch returns the patterns
     * whose average utility is at least the k-th largest one (ties with the k-th value included)
     * instead of those reaching minUtil x totalU. The internal threshold starts at the k-th largest
     * single-item average utility and, with {@link #warmStart}, at the k-th value of the previous
     * batch: a batch of new sequences or of events appended to stored sequences, with nonnegative
     * utilities, can only raise every pattern's utility, so that value is still reached by at
     * least k patterns. It is raised to the k-th best value found as the search proceeds. Every
     * pruning comparison uses it lowered by a relative 1e-12, so a rounding error can never discard
     * a pattern tied with the final k-th value; the final selection compares exact integers.
     */
    public int topK = 0;
    public boolean warmStart = false;
    private double topKTau = 0.0;
    private double lastTauK = 0.0;
    private long tauKUtil = 0;
    private int tauKLen = 1;
    private static final double TAU_SLACK = 1.0 - 1e-12;
    /** Min-heap of the k best (utility, length) pairs found in this batch, by average utility. */
    private final PriorityQueue<long[]> topKHeap = new PriorityQueue<>(
            (a, b) -> Long.compare(a[0] * b[1], b[0] * a[1]));
    /** Every pattern found at or above the internal threshold; filtered at the end of the batch. */
    private final List<TopKEntry> topKEntries = new ArrayList<>();
    private final List<TopKEntry> topKResult = new ArrayList<>();

    /** A pattern found in top-k mode: its utility, its length, and its item sequence (-1 separates itemsets). */
    public static final class TopKEntry {
        public final long util;
        public final int len;
        public final int[] pattern;
        TopKEntry(long util, int len, int[] pattern) { this.util = util; this.len = len; this.pattern = pattern; }
    }

    /** The top-k patterns of the last batch (empty outside top-k mode). */
    public List<TopKEntry> topKResult() { return Collections.unmodifiableList(topKResult); }

    /** The k-th largest average utility of the last batch, as utility and length (top-k mode). */
    public long[] topKValue() { return new long[]{tauKUtil, tauKLen}; }

    /** The current version of every sequence seen in the run, by its key, kept only by the rebuild arm. */
    private final LinkedHashMap<Integer, Sequence> seenByKey = new LinkedHashMap<>();

    /**
     * Mixed update model (author decision, 2026-10-16). A batch may hold new sequences and events
     * appended to stored ones: a sequence of the batch whose id (its key) is already stored carries
     * itemsets appended after that sequence's last itemset. Every sequence entering the structures,
     * new or a lengthened version, takes the next internal id, so the single-item lists stay in
     * increasing id order. A lengthened sequence replaces its stored version: the old version's
     * contribution is subtracted from totalU, the SWU of its items and the totals of its items'
     * single-item lists; its entries stay in those lists as dead entries, which the search skips and
     * a compaction removes once they exceed a quarter of the live ones.
     */
    private final it.unimi.dsi.fastutil.ints.Int2IntOpenHashMap keyToInternal = newKeyMap();
    private int nextInternal = 0;
    private boolean[] deadSid = new boolean[0];
    private boolean anyDead = false;
    private long deadEntries = 0, liveEntries = 0;
    private int liveSequences = 0;
    /** Sequences of the last batch that lengthened a stored sequence. */
    public int appendedLastBatch = 0;
    private static final double COMPACT_AT = 0.25;

    /**
     * Append in place (arm option {@code prefix}). A sequence of the batch whose key is stored keeps
     * its internal id: its new positions are written after its last one, with pre[k] continuing from
     * the stored u(S); no stored position, and no entry of a single-item list, is rewritten. Only
     * u(S), the SWU of its items, the EUCS of its item pairs and totalU change. A single-item list
     * then is a concatenation of runs, one per batch that added an entry out of id order; the runs
     * are merged by sequence id when the list is read (rootView), which also recomputes the list's
     * per-sequence maxima from the current u(S).
     */
    boolean appendInPlace = false;
    /** Positions written to the per-sequence arrays in the last batch (new sequences, rewritten or appended parts). */
    public long positionsWrittenLastBatch = 0;
    /** Single-item lists merged into one run because they reached MAX_RUNS, over the whole run. */
    public long rootRunMerges = 0;
    /** Index of the internal id in the batch's touched-sequence lists, or -1 for a new sequence. */
    private final it.unimi.dsi.fastutil.ints.Int2IntOpenHashMap touchedIndex = newKeyMap();
    private final IntArrayList touchedOldLen = new IntArrayList();
    private final IntArrayList touchedOldIsets = new IntArrayList();
    /** Start index of every run of a single-item list after the first; null while the list is one run. */
    private IntArrayList[] rootRuns = new IntArrayList[10000];
    private static final int MAX_RUNS = 32;
    /** Fault injection for the prefix-encoding check (null in every real run). */
    static String FAULT = null;

    private static it.unimi.dsi.fastutil.ints.Int2IntOpenHashMap newKeyMap() {
        it.unimi.dsi.fastutil.ints.Int2IntOpenHashMap m = new it.unimi.dsi.fastutil.ints.Int2IntOpenHashMap();
        m.defaultReturnValue(-1);
        return m;
    }

    /**
     * Builds an ablation/attribution arm from its name. Accepted names:
     * HAUSP-UB, HAUSP-UB-L1, HAUSP-UB-L1L3, and the bracket form
     * HAUSP-UB[opt+opt+...] with opt in {noL2, noL3, L3@node, nopool, noEUCS, rebuild, prefix} ('+' separated: arm names
     * are CSV fields).
     *
     * <p>Layer 2 is the coupled test applied to every assembled child before any recursion
     * (author decision, 2026-10-16). It uses the Layer-3 bound, so noL3 is accepted only together
     * with noL2; and with Layer 2 on no child reaches the recursion untested, so the place of the
     * Layer-3 child test (L3@node) is meaningful only together with noL2.
     */
    public static HAUSP_UB fromArmName(String name, String conf) {
        HAUSP_UB alg = new HAUSP_UB(conf);
        alg.profilePhases = ExperimentConfig.PROFILE_PHASES;
        if (name.equals("HAUSP-UB-L1")) { alg.enableLayer2IAUUB = false; alg.enableLayer3MFUUB = false; return alg; }
        if (name.equals("HAUSP-UB-L1L3")) { alg.enableLayer2IAUUB = false; return alg; }
        int b = name.indexOf('[');
        // A name this method does not recognise used to fall through and return the full
        // algorithm, so a run could be labelled with an arm it did not use. Refuse instead.
        if (b < 0 && !name.equals("HAUSP-UB")) {
            throw new IllegalArgumentException("unknown arm name: " + name
                    + " (expected HAUSP-UB, HAUSP-UB-L1, HAUSP-UB-L1L3, or HAUSP-UB[opt+opt])");
        }
        if (b >= 0 && !name.endsWith("]")) {
            throw new IllegalArgumentException("malformed arm name: " + name);
        }
        if (b >= 0) {
            for (String opt : name.substring(b + 1, name.length() - 1).split("\\+")) {
                switch (opt.trim()) {
                    case "noL2":    alg.enableLayer2IAUUB = false; break;
                    case "noL3":    alg.enableLayer3MFUUB = false; break;
                    case "L3@node": alg.childLevelL3 = false; break;
                    case "nopool":  alg.enablePool = false; break;
                    case "noEUCS":  alg.enableEUCS = false; break;
                    case "rebuild": alg.rebuildEachBatch = true; break;
                    case "prefix":  alg.appendInPlace = true; break;
                    case "": break;
                    default: throw new IllegalArgumentException("unknown arm option: " + opt);
                }
            }
        }
        if (alg.appendInPlace && alg.rebuildEachBatch) {
            throw new IllegalArgumentException("arm " + name + ": prefix keeps the structures across batches, "
                    + "rebuild discards them; the two options exclude each other");
        }
        if (alg.enableLayer2IAUUB && !alg.enableLayer3MFUUB) {
            throw new IllegalArgumentException("arm " + name + ": Layer 2 is the coupled test and needs the Layer-3 bound, "
                    + "so noL3 requires noL2");
        }
        if (alg.enableLayer2IAUUB && !alg.childLevelL3) {
            throw new IllegalArgumentException("arm " + name + ": with Layer 2 every child is tested before the recursion, "
                    + "so L3@node requires noL2");
        }
        return alg;
    }

    /** Cumulative CPU time spent in each pruning layer for the current batch (nanoseconds). */
    /**
     * Per-node phase timers (Layer-2 pass, Layer-3 node test). Off by default since
     * 2026-09-10: each ThreadMXBean CPU-time read costs ~365 ns on the measurement
     * machine, i.e. more than the comparison it brackets, and the baselines carry no
     * such read inside their recursion. Enable with the launcher flag --profile-phases
     * for a dedicated profiling run; never for timing that is compared across arms.
     * Batch-level timers (scan, Layer 1, total) are unaffected.
     */
    /** Bytes each per-depth slot costs across the four always-allocated scratch arrays (two child
     *  maps, two dirty lists). Four long[] of running estimates were removed with the decoupled
     *  estimate on 2026-10-16. */
    private static final int BYTES_PER_DEPTH_SLOT = 2 * 8 + 2 * 4;
    public boolean profilePhases = false;
    public long timeLayer1Ns = 0;
    public long timeLayer2Ns = 0;
    public long timeLayer3Ns = 0;
    public double minUtilPercentage;
    public long totalDBUtility = 0;

    private AUDUL[] globalAUDULs = new AUDUL[10000];
    private int[] itemToCompact;
    private int[] compactToItem;
    private int compactCount = 0;
    /** SWU of the promising items in compact (descending) order; see promisingUpTo. */
    private long[] sortedPromisingSWU;
    /**
     * Bytes currently held by the per-depth scratch arrays. Printed at the end of a batch under
     * {@code --profile-phases}; it is deliberately NOT a CSV column, because adding one changes the
     * result schema and would open a new result generation. The 2026-09-14 memory run had to infer
     * this quantity instead of reading it, which is why it is exposed at all.
     */
    private long depthArrayBytes = 0;
    private Sequence[] dbArray;
    private int[][] flatItemIds;
    private long[][] flatItemUtils;
    /** Prefix utility of every position: pre[k] = sum of the utilities of the positions before k. */
    private long[][] flatPre;
    /** u(S) of every stored sequence; the remaining utility at k is seqU - pre[k] - utils[k]. */
    private long[] seqU;
    /** Number of positions and of itemsets in use; the per-sequence arrays may be longer (spare capacity). */
    private int[] flatLen;
    private int[] isetCount;
    private int[][] flatToTid;
    private int[][] itemsetOffsets;
    private int[][] itemsetSizes;
    private String datasetName = "BMS1_SPMF";
    private long[] globalItemSWU = new long[10000];

    // HYBRID LAYOUT FOR THE INCREMENTAL EUCS
    // Above this directed-pair count the EUCS switches from a dense matrix to a
    // sparse Long2LongOpenHashMap. With newN > ~5657 the dense matrix exceeds
    // 32M entries and would otherwise require multi-GB allocations on sparse
    // databases; small databases stay on the cache-friendly dense layout.
    private static final long DENSE_EUCS_THRESHOLD = 32_000_000L;
    private boolean useSparseEUCS = false;
    private int currentMatrixN = 0;

    private long[] selfEUCS;
    private long[] sEUCS;
    private long[] iEUCS;
    private int[] iEUCS_seq;
    private long[] triRowOffsetsRaw;

    private Long2LongOpenHashMap sEUCSMap;
    private Long2LongOpenHashMap iEUCSMap;
    private final LongOpenHashSet seqPairsSeen = new LongOpenHashSet(256);

    private int maxItemIdEver = 0;
    private int maxSidEver = 0;

    private long[] maxT_buffer = new long[8192];
    private int[] modifiedT = new int[8192];
    private long[] maxK_buffer = new long[16384];
    private int[] modifiedK = new int[16384];

    private int[] currentPattern = new int[4096];
    private AUDUL[][] iExArrays = new AUDUL[200][];
    private AUDUL[][] sExArrays = new AUDUL[200][];
    private int[][] iDirtyList = new int[200][];
    private int[][] sDirtyList = new int[200][];

    private long[][] iEucsCacheByDepth = new long[200][];
    private long[][] sEucsCacheByDepth = new long[200][];
    private int[][] eucsCacheDirtyByDepth = new int[200][];

    private long hauspCount, candidateCount;
    /**
     * 2026-09-03 counting fix. {@code candidateCount} now counts every utility
     * list that is ASSEMBLED (root lists that enter the DFS plus every child
     * list borrowed from the pool), whether or not the list is later rejected
     * by Layer 2 or Layer 3. This is the same operational definition as
     * EHAUSM_Inc/EHAUSM_Remining, which count a node before their PEAU test.
     * Previously it counted only children that had already passed Layers 2-3,
     * so eta = Cand/HAUSP was not comparable across algorithms (the published
     * "117-1,251x" reduction was this counting difference, not pruning).
     * {@code recursedCount} keeps the old meaning (children recursed into).
     */
    private long recursedCount;
    private long prunedL1, prunedL1_5, prunedL2, prunedL3, prunedL_TwoPass;
    /** Share of prunedL3 applied on node entry in miningDFS (the node was already recursed into); the rest is the child-level test in processRecurse. */
    private long prunedL3Node;
    private double peakMemory = 0;
    private int peakMemCounter = 0;

    // Memory attribution. flatBytes is the running total of the flat-database
    // array payloads; the peak* fields are copied from the live structures at
    // the moment a new per-batch heap peak is recorded (see
    // snapshotMemoryBreakdown) and exported through RunResult.
    private long flatBytes = 0;
    private long peakPoolBytes = -1, peakFlatBytes = -1, peakEucsBytes = -1, peakRootBytes = -1;

    private boolean[] swuSeenBuf;
    private int[] swuSeenListBuf;
    private int[] firstTidBuf;
    private int[] lastTidBuf;
    private boolean[] seenCBuf;
    private int[] uListBuf;

    // Tightness measurement: Tightness(MFUUB) = (1/N) * Σ [ au(α) / MFUUB_normalized(α) ] over the
    // N nodes entered, with au(α) = evalIutil / |α| and MFUUB_normalized(α) = evalMFUUB / |α|. The
    // IAUUB tightness was the decoupled estimate's and went with it on 2026-10-16; its column is 0.
    private double sumTightnessMFUUB = 0.0; // Σ [ au(α) / MFUUB_normalized(α) ]
    private long   countTightnessNode = 0;  // N: number of candidate nodes measured.
    public double tightnessPEAU  = 0.0;
    public double tightnessIAUUB = 0.0;
    public double tightnessMFUUB = 0.0;

    public HAUSP_UB(String configPath) {
        Properties prop = new Properties();
        try (FileInputStream fis = new FileInputStream(configPath)) {
            prop.load(fis);
            this.minUtilPercentage = Double.parseDouble(prop.getProperty("minUtil.percentage", "0.01"));
            this.datasetName = new File(prop.getProperty("dataset.path", "unknown")).getName().replace("_seq.txt", "");
        } catch (Exception e) {
            this.minUtilPercentage = 0.005;
        }
        Arrays.fill(maxT_buffer, -1L);
        Arrays.fill(maxK_buffer, -1L);
    }

    public void setConfig(double minUtil) {
        this.minUtilPercentage = minUtil;
        audulPool.reuse = enablePool;
    }

    public void reset() {
        if (globalAUDULs != null) {
            for (int i = 0; i < globalAUDULs.length; i++) {
                if (globalAUDULs[i] != null) {
                    audulPool.release(globalAUDULs[i]);
                    globalAUDULs[i] = null;
                }
            }
        }
        totalDBUtility = 0;
        maxItemIdEver = 0;
        maxSidEver = 0;
        compactCount = 0;
        keyToInternal.clear();
        nextInternal = 0;
        deadSid = new boolean[0];
        anyDead = false;
        deadEntries = 0;
        liveEntries = 0;
        liveSequences = 0;

        if (globalItemSWU != null) Arrays.fill(globalItemSWU, 0);

        dbArray = null;
        flatItemIds = null;
        flatItemUtils = null;
        flatToTid = null;
        flatPre = null;
        seqU = null;
        flatLen = null;
        isetCount = null;
        rootRuns = new IntArrayList[10000];
        touchedIndex.clear();
        flatBytes = 0;
        itemsetOffsets = null;
        itemsetSizes = null;

        selfEUCS = null;
        sEUCSMap = null;
        iEUCSMap = null;

        sEUCS = null;
        iEUCS = null;
        iEUCS_seq = null;
        triRowOffsetsRaw = null;
        currentMatrixN = 0;

        itemToCompact = null;
        compactToItem = null;
    }

    private static long symKey(int cA, int cB) {
        int lo = Math.min(cA, cB);
        int hi = Math.max(cA, cB);
        return ((long) hi << 32) | (lo & 0xFFFFFFFFL);
    }

    private static long dirKey(int cA, int cB) {
        return ((long) cA << 32) | (cB & 0xFFFFFFFFL);
    }

    private void updatePeakMemory() {
        // Sample once every 4096 calls to cut JNI calls into Runtime by 4×
        // without noticeably degrading peak-memory accuracy.
        if ((++peakMemCounter & 4095) != 0) return;
        long totalMem = Runtime.getRuntime().totalMemory();
        long freeMem = Runtime.getRuntime().freeMemory();
        double currentMemMB = (totalMem - freeMem) / 1024.0 / 1024.0;
        if (currentMemMB > peakMemory) { peakMemory = currentMemMB; snapshotMemoryBreakdown(); }
    }

    private void forcePeakMemorySample() {
        long totalMem = Runtime.getRuntime().totalMemory();
        long freeMem = Runtime.getRuntime().freeMemory();
        double currentMemMB = (totalMem - freeMem) / 1024.0 / 1024.0;
        if (currentMemMB > peakMemory) { peakMemory = currentMemMB; snapshotMemoryBreakdown(); }
    }

    /**
     * Copies the size of the persistent structures at the moment a new
     * per-batch heap peak is observed, so that the peak can be attributed:
     * scratch AU-DULs owned by the pool, single-item AU-DUL roots, flat
     * database arrays and the EUCS matrices/maps. All values are bytes of
     * array payload (object headers excluded); the EUCS map figure is an
     * estimate from the map capacity.
     */
    private void snapshotMemoryBreakdown() {
        peakFlatBytes = flatBytes;
        peakEucsBytes = eucsBytesNow();
        peakRootBytes = audulPool.rootArrayBytes;
        peakPoolBytes = Math.max(0L, audulPool.arrayBytes - audulPool.rootArrayBytes);
    }

    /** Bytes held by the EUCS structures: dense matrices when allocated, otherwise the open-hash maps. */
    private long eucsBytesNow() {
        long bytes = 0;
        if (selfEUCS != null) bytes += 8L * selfEUCS.length;
        if (sEUCS != null) bytes += 8L * sEUCS.length;
        if (iEUCS != null) bytes += 8L * iEUCS.length;
        if (iEUCS_seq != null) bytes += 4L * iEUCS_seq.length;
        if (triRowOffsetsRaw != null) bytes += 8L * triRowOffsetsRaw.length;
        bytes += openHashBytes(sEUCSMap == null ? 0 : sEUCSMap.size());
        bytes += openHashBytes(iEUCSMap == null ? 0 : iEUCSMap.size());
        return bytes;
    }

    /** Payload of a fastutil Long2LongOpenHashMap holding {@code size} entries at load factor 0.75 (key + value arrays). */
    private static long openHashBytes(int size) {
        if (size <= 0) return 0;
        long n = 16;
        while (n * 3 / 4 < size) n <<= 1;
        return (n + 1) * 16L;
    }

    public RunResult processBatch(List<Sequence> deltaBatch, int batchId) {
        if (batchId == 0) reset();
        if (batchId == 0) lastTauK = 0.0;
        topKHeap.clear();
        topKEntries.clear();
        topKResult.clear();
        if (rebuildEachBatch) {
            if (batchId == 0) seenByKey.clear();
            for (Sequence s : deltaBatch) {
                Sequence old = seenByKey.get(s.sid);
                seenByKey.put(s.sid, old == null ? s : concat(old, s, s.sid));
            }
        }
        hauspCount = 0; candidateCount = 0; recursedCount = 0;
        prunedL1 = 0; prunedL1_5 = 0; prunedL2 = 0; prunedL3 = 0; prunedL_TwoPass = 0; prunedL3Node = 0;
        timeLayer1Ns = 0; timeLayer2Ns = 0; timeLayer3Ns = 0;
        audulPool.resetCounters();

        sumTightnessMFUUB = 0.0;
        countTightnessNode = 0;
        tightnessPEAU  = 0.0;
        tightnessIAUUB = 0.0;
        tightnessMFUUB = 0.0;

        // CPU time at nanosecond resolution, excluding GC pauses.
        long startTimeNs = RunIsolation.cpuTimeNs();
        if (rebuildEachBatch && batchId > 0) {
            // Rebuild arm: discard every maintained structure and scan all sequences seen so far,
            // inside the timed preprocessing phase.
            reset();
            deltaBatch = new ArrayList<>(seenByKey.values());
        }
        deltaBatch = appendInPlace ? toInternalInPlace(deltaBatch) : toInternal(deltaBatch);
        positionsWrittenLastBatch = 0;
        peakMemory = 0;
        peakPoolBytes = peakFlatBytes = peakEucsBytes = peakRootBytes = -1;

        for (Sequence seq : deltaBatch) {
            if (seq.sid > maxSidEver) maxSidEver = seq.sid;
            for (Itemset is : seq.itemsets) {
                for (ItemQ it : is.items) if (it.id > maxItemIdEver) maxItemIdEver = it.id;
            }
        }

        int reqSid = maxSidEver + 1;
        dbArray = (dbArray == null) ? new Sequence[reqSid + 1000] : (reqSid >= dbArray.length ? Arrays.copyOf(dbArray, reqSid + 1000) : dbArray);
        flatItemIds = (flatItemIds == null) ? new int[reqSid + 1000][] : (reqSid >= flatItemIds.length ? Arrays.copyOf(flatItemIds, reqSid + 1000) : flatItemIds);
        flatItemUtils = (flatItemUtils == null) ? new long[reqSid + 1000][] : (reqSid >= flatItemUtils.length ? Arrays.copyOf(flatItemUtils, reqSid + 1000) : flatItemUtils);
        flatToTid = (flatToTid == null) ? new int[reqSid + 1000][] : (reqSid >= flatToTid.length ? Arrays.copyOf(flatToTid, reqSid + 1000) : flatToTid);
        flatPre = (flatPre == null) ? new long[reqSid + 1000][] : (reqSid >= flatPre.length ? Arrays.copyOf(flatPre, reqSid + 1000) : flatPre);
        seqU = (seqU == null) ? new long[reqSid + 1000] : (reqSid >= seqU.length ? Arrays.copyOf(seqU, reqSid + 1000) : seqU);
        flatLen = (flatLen == null) ? new int[reqSid + 1000] : (reqSid >= flatLen.length ? Arrays.copyOf(flatLen, reqSid + 1000) : flatLen);
        isetCount = (isetCount == null) ? new int[reqSid + 1000] : (reqSid >= isetCount.length ? Arrays.copyOf(isetCount, reqSid + 1000) : isetCount);
        itemsetOffsets = (itemsetOffsets == null) ? new int[reqSid + 1000][] : (reqSid >= itemsetOffsets.length ? Arrays.copyOf(itemsetOffsets, reqSid + 1000) : itemsetOffsets);
        itemsetSizes = (itemsetSizes == null) ? new int[reqSid + 1000][] : (reqSid >= itemsetSizes.length ? Arrays.copyOf(itemsetSizes, reqSid + 1000) : itemsetSizes);

        if (globalAUDULs.length <= maxItemIdEver) {
            int newSize = maxItemIdEver + 5000;
            globalAUDULs = Arrays.copyOf(globalAUDULs, newSize);
            globalItemSWU = Arrays.copyOf(globalItemSWU, newSize);
            rootRuns = Arrays.copyOf(rootRuns, newSize);

        }

        int swuBufSize = maxItemIdEver + 1;
        if (swuSeenBuf == null || swuSeenBuf.length < swuBufSize) {
            swuSeenBuf = new boolean[swuBufSize + 1024];
            swuSeenListBuf = new int[swuBufSize + 1024];
        }
        long l1Start = RunIsolation.cpuTimeNs();
        for (Sequence seq : deltaBatch) {
            int tIdx = touchedIndex.get(seq.sid);
            if (tIdx >= 0) {                           // appended part of a stored sequence
                swuForAppend(seq, touchedOldLen.getInt(tIdx));
                continue;
            }
            totalDBUtility += seq.totalUtility;
            dbArray[seq.sid] = seq;
            long su = seq.totalUtility;
            int seenCount = 0;
            List<Itemset> isets = seq.itemsets;
            int isn = isets.size();
            for (int ti = 0; ti < isn; ti++) {
                List<ItemQ> items = isets.get(ti).items;
                int ilen = items.size();
                for (int ki = 0; ki < ilen; ki++) {
                    int id = items.get(ki).id;
                    if (!swuSeenBuf[id]) {
                        swuSeenBuf[id] = true;
                        swuSeenListBuf[seenCount++] = id;
                        globalItemSWU[id] += su;
                    }
                }
            }
            for (int i = 0; i < seenCount; i++) swuSeenBuf[swuSeenListBuf[i]] = false;
        }

        double threshold = minUtilPercentage * totalDBUtility;

        IntArrayList promisingListTemp = new IntArrayList();
        for (int i = 0; i <= maxItemIdEver; i++) {
            if (globalItemSWU[i] >= threshold) {
                promisingListTemp.add(i);
            } else if (globalItemSWU[i] > 0) {
                prunedL1++;
            }
        }
        timeLayer1Ns += RunIsolation.cpuTimeNs() - l1Start;

        for (Sequence seq : deltaBatch) {
            int tIdx = touchedIndex.get(seq.sid);
            if (tIdx >= 0) {
                appendFlat(seq, touchedOldLen.getInt(tIdx), touchedOldIsets.getInt(tIdx));
                continue;
            }
            int n = seq.itemsets.size();
            int totalItems = 0;
            for (Itemset is : seq.itemsets) totalItems += is.items.size();

            int[] ids = new int[totalItems];
            long[] uts = new long[totalItems];
            int[] tids = new int[totalItems];
            long[] rfFlat = new long[totalItems];
            int[] offsets = new int[n];
            int[] sizes = new int[n];

            long rf = 0;
            int ptr = totalItems - 1;
            for (int t = n - 1; t >= 0; t--) {
                Itemset is = seq.itemsets.get(t);
                offsets[t] = ptr - is.items.size() + 1;
                sizes[t] = is.items.size();
                for (int k = is.items.size() - 1; k >= 0; k--) {
                    ItemQ it = is.items.get(k);
                    rf += it.utility;
                    ids[ptr] = it.id;
                    uts[ptr] = it.utility;
                    tids[ptr] = t;
                    rfFlat[ptr] = rf - it.utility;
                    ptr--;
                }
            }
            flatItemIds[seq.sid] = ids;
            flatItemUtils[seq.sid] = uts;
            flatToTid[seq.sid] = tids;
            positionsWrittenLastBatch += totalItems;
            long[] preFlat = new long[totalItems];
            long run = 0;
            for (int i = 0; i < totalItems; i++) { preFlat[i] = run; run += uts[i]; }
            flatPre[seq.sid] = preFlat;
            seqU[seq.sid] = run;
            flatLen[seq.sid] = totalItems;
            isetCount[seq.sid] = n;
            itemsetOffsets[seq.sid] = offsets;
            itemsetSizes[seq.sid] = sizes;
            flatBytes += 4L * ids.length + 8L * uts.length + 4L * tids.length + 8L * rfFlat.length
                    + 4L * offsets.length + 4L * sizes.length;
            liveEntries += totalItems;
            liveSequences++;

            for (int i = 0; i < totalItems; i++) addRootEntry(ids[i], seq.sid, i, uts[i], rfFlat[i]);
        }

        if (anyDead && deadEntries > COMPACT_AT * liveEntries) compactRoots();

        if (topK > 0) {
            // Initial internal threshold: the k-th largest single-item average utility (those k
            // single items are patterns), and with warm start the previous batch's k-th value.
            PriorityQueue<Long> singles = new PriorityQueue<>();
            for (int i = 0; i <= maxItemIdEver && i < globalAUDULs.length; i++) {
                AUDUL root = rootView(i);
                if (root == null) continue;
                root.evaluate();
                singles.add(root.evalIutil);
                if (singles.size() > topK) singles.poll();
                if (root != globalAUDULs[i]) audulPool.release(root);
            }
            double init = singles.size() == topK ? singles.peek() : 0.0;
            if (warmStart) init = Math.max(init, lastTauK);
            topKTau = init;
            threshold = init * TAU_SLACK;
            promisingListTemp.clear();
            prunedL1 = 0;
            for (int i = 0; i <= maxItemIdEver; i++) {
                if (globalItemSWU[i] >= threshold) promisingListTemp.add(i);
                else if (globalItemSWU[i] > 0) prunedL1++;
            }
        }

        // Sparsely reset itemToCompact at positions touched by the previous
        // batch instead of allocating a fresh array and calling Arrays.fill.
        if (itemToCompact != null && compactToItem != null) {
            for (int i = 0; i < compactCount; i++) {
                int oldId = compactToItem[i];
                if (oldId < itemToCompact.length) itemToCompact[oldId] = -1;
            }
        }
        if (itemToCompact == null) {
            itemToCompact = new int[maxItemIdEver + 5000];
            Arrays.fill(itemToCompact, -1);
        } else if (itemToCompact.length <= maxItemIdEver) {
            int oldLen = itemToCompact.length;
            int newLen = maxItemIdEver + 5000;
            itemToCompact = Arrays.copyOf(itemToCompact, newLen);
            for (int i = oldLen; i < newLen; i++) itemToCompact[i] = -1;
        }
        compactCount = 0;

        compactToItem = promisingListTemp.toIntArray();
        final long[] swuRef = globalItemSWU;
        it.unimi.dsi.fastutil.ints.IntArrays.quickSort(compactToItem, (a, b) -> Long.compare(swuRef[b], swuRef[a]));

        compactCount = compactToItem.length;
        for (int i = 0; i < compactCount; i++) {
            itemToCompact[compactToItem[i]] = i;
        }
        // SWU of the promising items in compact order (descending). Compact ids are assigned by
        // descending SWU, and the length-aware test at a node of length L admits exactly the items
        // with SWU >= threshold*(L+1); those therefore form a PREFIX of the compact order, so the
        // per-depth arrays only have to be as long as that prefix.
        if (sortedPromisingSWU == null || sortedPromisingSWU.length < compactCount) {
            sortedPromisingSWU = new long[compactCount];
        }
        for (int i = 0; i < compactCount; i++) sortedPromisingSWU[i] = globalItemSWU[compactToItem[i]];

        // EUCS matrices (dense) or maps (sparse): allocated, grown and updated only
        // when the pre-filter is enabled, so that the noEUCS arm carries neither
        // their lookups nor their memory.
        if (enableEUCS) {
        int itemBufSize = maxItemIdEver + 1;
        if (selfEUCS == null) {
            selfEUCS = new long[itemBufSize + 1024];
        } else if (selfEUCS.length < itemBufSize) {
            selfEUCS = Arrays.copyOf(selfEUCS, itemBufSize + 1024);
        }

        int requiredN = maxItemIdEver + 1;
        int newN = (currentMatrixN >= requiredN) ? currentMatrixN : requiredN + 2000;
        boolean matrixResized = (newN > currentMatrixN);

        long dirSize = (long) newN * newN;
        long triSize = (long) newN * (newN - 1) / 2;

        useSparseEUCS = (dirSize > DENSE_EUCS_THRESHOLD) || (triSize > DENSE_EUCS_THRESHOLD);

        if (selfEUCS == null || selfEUCS.length < newN) {
            selfEUCS = Arrays.copyOf(selfEUCS == null ? new long[0] : selfEUCS, newN + 1024);
        }

        if (useSparseEUCS) {
            if (sEUCSMap == null) {
                int initialCapacity = (sEUCS != null) ? (1 << 21) : (1 << 16);
                sEUCSMap = new Long2LongOpenHashMap(initialCapacity);
                sEUCSMap.defaultReturnValue(0L);
                iEUCSMap = new Long2LongOpenHashMap(initialCapacity);
                iEUCSMap.defaultReturnValue(0L);

                if (sEUCS != null) {
                    for (int i = 0; i < currentMatrixN; i++) {
                        for (int j = 0; j < currentMatrixN; j++) {
                            long val = sEUCS[i * currentMatrixN + j];
                            if (val > 0) sEUCSMap.put(dirKey(i, j), val);
                        }
                        long offset = (long) i * currentMatrixN - ((long) i * (i + 1)) / 2;
                        for (int j = i + 1; j < currentMatrixN; j++) {
                            long val = iEUCS[(int) (offset + (j - i - 1))];
                            if (val > 0) iEUCSMap.put(symKey(i, j), val);
                        }
                    }
                }
            }
            sEUCS = null;
            iEUCS = null;
            iEUCS_seq = null;
            currentMatrixN = newN;

        } else {
            sEUCSMap = null;
            iEUCSMap = null;

            if (sEUCS == null) {
                sEUCS = new long[(int) dirSize];
                iEUCS = new long[(int) triSize];
                iEUCS_seq = new int[(int) triSize];
                Arrays.fill(iEUCS_seq, -1);
                currentMatrixN = newN;

            } else if (newN > currentMatrixN) {
                long[] new_sEUCS = new long[(int) dirSize];
                long[] new_iEUCS = new long[(int) triSize];
                int[] new_iEUCS_seq = new int[(int) triSize];
                Arrays.fill(new_iEUCS_seq, -1);

                for (int i = 0; i < currentMatrixN; i++) {
                    System.arraycopy(sEUCS, i * currentMatrixN, new_sEUCS, i * newN, currentMatrixN);
                }

                for (int i = 0; i < currentMatrixN; i++) {
                    int oldOffset = (int) ((long) i * currentMatrixN - ((long) i * (i + 1)) / 2);
                    int newOffset = (int) ((long) i * newN - ((long) i * (i + 1)) / 2);
                    int length = currentMatrixN - (i + 1);
                    if (length > 0) {
                        System.arraycopy(iEUCS, oldOffset, new_iEUCS, newOffset, length);
                        System.arraycopy(iEUCS_seq, oldOffset, new_iEUCS_seq, newOffset, length);
                    }
                }

                sEUCS = new_sEUCS;
                iEUCS = new_iEUCS;
                iEUCS_seq = new_iEUCS_seq;
                currentMatrixN = newN;
            }
        }

        if (triRowOffsetsRaw == null || triRowOffsetsRaw.length < newN) {
            triRowOffsetsRaw = new long[newN + 1024];
            matrixResized = true;
        }

        if (matrixResized) {
            for (int i = 0; i < newN; i++) {
                triRowOffsetsRaw[i] = (long) i * newN - ((long) i * (i + 1)) / 2;
            }
        }

        if (firstTidBuf == null || firstTidBuf.length < itemBufSize) {
            firstTidBuf = new int[itemBufSize + 1024];
            lastTidBuf = new int[itemBufSize + 1024];
            seenCBuf = new boolean[itemBufSize + 1024];
            uListBuf = new int[itemBufSize + 1024];
            Arrays.fill(firstTidBuf, -1);
        }

        for (Sequence seq : deltaBatch) {
            long su = seq.totalUtility;
            int sid = seq.sid;
            int tIdx = touchedIndex.get(sid);
            if (tIdx >= 0) {
                eucsForAppend(sid, touchedOldLen.getInt(tIdx), touchedOldIsets.getInt(tIdx), su);
                continue;
            }

            int[] ids = flatItemIds[sid];
            if (ids == null) continue;
            int[] tids = flatToTid[sid];

            int uSize = 0;

            for (int i = 0; i < ids.length; i++) {
                int itemId = ids[i];
                if (!seenCBuf[itemId]) {
                    seenCBuf[itemId] = true;
                    uListBuf[uSize++] = itemId;
                }
                if (firstTidBuf[itemId] == -1) firstTidBuf[itemId] = tids[i];
                lastTidBuf[itemId] = tids[i];
            }

            if (useSparseEUCS) {
                for (int i = 0; i < uSize; i++) {
                    int iA = uListBuf[i];
                    int firstA = firstTidBuf[iA];
                    int lastA = lastTidBuf[iA];
                    if (firstA < lastA) selfEUCS[iA] += su;
                    for (int j = i + 1; j < uSize; j++) {
                        int iB = uListBuf[j];
                        if (firstA < lastTidBuf[iB]) sEUCSMap.addTo(dirKey(iA, iB), su);
                        if (firstTidBuf[iB] < lastA) sEUCSMap.addTo(dirKey(iB, iA), su);
                    }
                }
            } else {
                for (int i = 0; i < uSize; i++) {
                    int iA = uListBuf[i];
                    int firstA = firstTidBuf[iA];
                    int lastA = lastTidBuf[iA];
                    if (firstA < lastA) selfEUCS[iA] += su;
                    for (int j = i + 1; j < uSize; j++) {
                        int iB = uListBuf[j];
                        if (firstA < lastTidBuf[iB]) sEUCS[iA * currentMatrixN + iB] += su;
                        if (firstTidBuf[iB] < lastA) sEUCS[iB * currentMatrixN + iA] += su;
                    }
                }
            }

            int[] bOffsets = itemsetOffsets[sid];
            int[] bSizes = itemsetSizes[sid];
            int nIsets = bSizes.length;

            if (useSparseEUCS) {
                seqPairsSeen.clear();
                for (int ti = 0; ti < nIsets; ti++) {
                    int isz = bSizes[ti];
                    if (isz < 2) continue;
                    int off = bOffsets[ti];
                    for (int i = 0; i < isz; i++) {
                        int iA = ids[off + i];
                        for (int j = i + 1; j < isz; j++) {
                            int iB = ids[off + j];
                            if (iA == iB) continue;
                            long key = symKey(iA, iB);
                            if (seqPairsSeen.add(key)) iEUCSMap.addTo(key, su);
                        }
                    }
                }
            } else {
                for (int ti = 0; ti < nIsets; ti++) {
                    int isz = bSizes[ti];
                    if (isz < 2) continue;
                    int off = bOffsets[ti];
                    for (int i = 0; i < isz; i++) {
                        int iA = ids[off + i];
                        for (int j = i + 1; j < isz; j++) {
                            int iB = ids[off + j];
                            if (iA == iB) continue;

                            int min = iA < iB ? iA : iB;
                            int max = iA > iB ? iA : iB;
                            int idx = (int) (triRowOffsetsRaw[min] + (max - min - 1));

                            if (iEUCS_seq[idx] != sid) {
                                iEUCS_seq[idx] = sid;
                                iEUCS[idx] += su;
                            }
                        }
                    }
                }
            }

            for (int i = 0; i < uSize; i++) {
                int iA = uListBuf[i];
                seenCBuf[iA] = false;
                firstTidBuf[iA] = -1;
            }
        }
        } // enableEUCS

        long startMiningNs = RunIsolation.cpuTimeNs();
        forcePeakMemorySample();

        // The pattern dump goes to out/, which is not in the repository: a fresh clone has no
        // such directory. Without this the FileWriter throws, the try block that holds the whole
        // mining loop is skipped, and the run still records SUCCESS with zero patterns. The
        // baselines already create it; this side did not.
        new java.io.File("out").mkdirs();
        try (BufferedWriter writer = enableIO ? new BufferedWriter(new FileWriter("out/HAUSP_"
                + datasetName + "_B" + batchId + (topK > 0 ? "_top" + topK : "_mu" + minUtilPercentage) + ".txt")) : null) {
            for (int cId = 0; cId < compactCount; cId++) {
                int itemId = compactToItem[cId];
                AUDUL dul = rootView(itemId);
                if (topK > 0) threshold = Math.max(threshold, topKTau * TAU_SLACK);
                if (dul != null) {
                    // Root list counted here, before the root test, so that the
                    // count matches the baselines: EHAUSM-R/I and Pre-HAUSPM pass
                    // every root with SWU >= threshold to their DFS (counted) and
                    // reject it there by PEAU; HAUSP-UB rejects the same roots by
                    // the cheaper SWU >= 2*threshold test below (prunedL1_5).
                    candidateCount++;
                    dul.evaluate();

                    boolean canBeHAUSP = dul.evalIutil >= threshold;
                    boolean canExtend = globalItemSWU[itemId] >= (threshold * 2);

                    if (!canBeHAUSP && !canExtend) {
                        prunedL1_5++;
                        if (dul != globalAUDULs[itemId]) audulPool.release(dul);
                        continue;
                    }

                    currentPattern[0] = itemId;
                    miningDFS(dul, threshold, writer, 0, cId, 1);
                    if (dul != globalAUDULs[itemId]) audulPool.release(dul);
                }
            }
            if (topK > 0) finishTopK(writer);
        } catch (IOException e) {
            e.printStackTrace();
        }

        // Tightness aggregation: average of ratios over the N nodes entered.
        this.tightnessPEAU  = 0.0;
        this.tightnessIAUUB = 0.0;
        this.tightnessMFUUB = (countTightnessNode == 0) ? 0.0 : sumTightnessMFUUB / countTightnessNode;

        // ns → ms when exported to RunResult (keeps the long-milliseconds contract with CSVLogger)
        long tScanMs = (startMiningNs - startTimeNs) / 1_000_000L;
        long tMiningMs = (RunIsolation.cpuTimeNs() - startMiningNs) / 1_000_000L;
        if (profilePhases) printMemoryBreakdown();
        return buildRunResult(batchId, startTimeNs, tScanMs, tMiningMs);
    }

    /**
     * DFS over the extensions of pattern α.
     *
     * @param dul       AU-DUL of α; the caller must invoke {@code evaluate()} beforehand.
     * @param threshold  minUtil × totalDBUtil
     */
    private void miningDFS(AUDUL dul, double threshold, BufferedWriter writer, int depth, int lastCompactId, int patternLen) throws IOException {
        recursedCount++;
        updatePeakMemory();
        dul.evaluate();
        if (topK > 0) threshold = Math.max(threshold, topKTau * TAU_SLACK);

        double reqHAUSP = threshold * dul.itemSize;
        double reqExtend = threshold * (dul.itemSize + 1);

        if (topK > 0) {
            if (dul.evalIutil >= reqHAUSP) {
                offerTopK(dul.evalIutil, dul.itemSize, patternLen);
                threshold = Math.max(threshold, topKTau * TAU_SLACK);
                reqExtend = threshold * (dul.itemSize + 1);
            }
        } else if (dul.evalIutil >= reqHAUSP) {
            hauspCount++;
            if (enableIO && writer != null) {
                writer.write(getPatternString(patternLen) + "\t"
                        + String.format(Locale.US, "%.2f", (double) dul.evalIutil / dul.itemSize)
                        + "\t" + dul.evalSupport + "\n");
            }
        }

        // Tightness: au(α) / MFUUB_normalized(α); both share |α|, so the ratio is evalIutil / evalMFUUB.
        if (enableTightnessMetric && dul.evalMFUUB > 0) {
            sumTightnessMFUUB += (double) dul.evalIutil / dul.evalMFUUB;
            countTightnessNode++;
        }

        // Layer 3 (MFUUB): no descendant of α can be a HAUSP or a productive extension.
        if (enableLayer3MFUUB) {
            long l3Start = profilePhases ? RunIsolation.cpuTimeNs() : 0L;
            boolean pruneL3 = dul.evalMFUUB < reqExtend;
            if (profilePhases) timeLayer3Ns += RunIsolation.cpuTimeNs() - l3Start;
            if (pruneL3) {
                prunedL3++;
                prunedL3Node++;
                return;
            }
        }

        if (useSparseEUCS) {
            generateExtensionsSparse(dul, threshold, writer, depth, lastCompactId, patternLen, reqExtend);
        } else {
            generateExtensionsDense(dul, threshold, writer, depth, lastCompactId, patternLen, reqExtend);
        }
    }

    private void generateExtensionsDense(AUDUL dul, double threshold, BufferedWriter writer, int depth, int lastCompactId, int patternLen, double reqUtil) throws IOException {
        ensureDFSArraysCapacity(depth, promisingUpTo(threshold * (dul.itemSize + 1)));

        AUDUL[] iExMap = iExArrays[depth];
        AUDUL[] sExMap = sExArrays[depth];
        int[] iDirty = iDirtyList[depth];
        int[] sDirty = sDirtyList[depth];
        int iDirtyCount = 0, sDirtyCount = 0;

        final int lastItemId = compactToItem[lastCompactId];

        final long[] iCache = iEucsCacheByDepth[depth];
        final long[] sCache = sEucsCacheByDepth[depth];
        final int[] cacheDirty = eucsCacheDirtyByDepth[depth];
        int cacheDirtyCount = 0;

        // Length-aware dynamic SWU pruning at depth k+1.
        double minSWU = threshold * (dul.itemSize + 1);

        int p = 0;
        while (p < dul.count) {
            int sid = dul.getSid(p);
            int startP = p;
            while (p < dul.count && dul.getSid(p) == sid) p++;
            if (anyDead && deadSid[sid]) continue;     // a replaced version, left in a single-item list

            int[] items = flatItemIds[sid];
            long[] uts = flatItemUtils[sid];
            int[] tids = flatToTid[sid];
            long[] pre = flatPre[sid];
            long seqUtil = seqU[sid];
            int[] offsets = itemsetOffsets[sid];
            int[] sizes = itemsetSizes[sid];
            int n = isetCount[sid];
            int totalItems = flatLen[sid];

            if (totalItems > maxK_buffer.length) {
                int oldLen = maxK_buffer.length;
                int newLen = totalItems + 2000;
                maxK_buffer = Arrays.copyOf(maxK_buffer, newLen);
                modifiedK = Arrays.copyOf(modifiedK, newLen);
                for (int i = oldLen; i < newLen; i++) maxK_buffer[i] = -1L;
            }
            if (n > maxT_buffer.length) {
                int oldLen = maxT_buffer.length;
                int newLen = n + 500;
                maxT_buffer = Arrays.copyOf(maxT_buffer, newLen);
                modifiedT = Arrays.copyOf(modifiedT, newLen);
                for (int i = oldLen; i < newLen; i++) maxT_buffer[i] = -1L;
            }

            int minT = Integer.MAX_VALUE;
            int modTCount = 0;
            int modKCount = 0;

            for (int i = startP; i < p; i++) {
                int flatIdx = dul.getFlatIdx(i);
                int tid = tids[flatIdx];
                long iu = dul.iutils[i];

                if (tid < minT) minT = tid;

                int nT = tid + 1;
                if (nT < n) {
                    if (maxT_buffer[nT] == -1L) {
                        modifiedT[modTCount++] = nT;
                        maxT_buffer[nT] = iu;
                    } else if (iu > maxT_buffer[nT]) maxT_buffer[nT] = iu;
                }

                int nK = flatIdx + 1;
                if (nK < totalItems && tids[nK] == tid) {
                    if (maxK_buffer[nK] == -1L) {
                        modifiedK[modKCount++] = nK;
                        maxK_buffer[nK] = iu;
                    } else if (iu > maxK_buffer[nK]) maxK_buffer[nK] = iu;
                }
            }

            long maxS = -1;

            for (int t = minT; t < n; t++) {
                long tVal = maxT_buffer[t];
                if (tVal > maxS) maxS = tVal;

                int sz = sizes[t];
                if (sz == 0) continue;

                int offset = offsets[t];
                long currentMaxI = -1;

                for (int k = 0; k < sz; k++) {
                    int flatIdx = offset + k;
                    long kVal = maxK_buffer[flatIdx];
                    if (kVal > currentMaxI) currentMaxI = kVal;

                    int itemId = items[flatIdx];

                    // Skip the EUCS lookup when the item already fails the SWU bound.
                    if (globalItemSWU[itemId] < minSWU) continue;

                    int cId = itemToCompact[itemId];
                    if (cId == -1) continue;

                    boolean validI = (currentMaxI != -1L);
                    boolean validS = (maxS != -1L);
                    if (!validI && !validS) continue;

                    long iEucsVal = 0L;
                    long sEucsVal = 0L;
                    if (enableEUCS) {
                    iEucsVal = iCache[cId];
                    if (iEucsVal == -1L) {
                        if (lastItemId == itemId) {
                            iEucsVal = 0L;
                            sEucsVal = selfEUCS[itemId];
                        } else {
                            int min = lastItemId < itemId ? lastItemId : itemId;
                            int max = lastItemId > itemId ? lastItemId : itemId;
                            iEucsVal = iEUCS[(int) (triRowOffsetsRaw[min] + (max - min - 1))];
                            sEucsVal = sEUCS[lastItemId * currentMatrixN + itemId];
                        }
                        iCache[cId] = iEucsVal;
                        sCache[cId] = sEucsVal;
                        cacheDirty[cacheDirtyCount++] = cId;
                    } else {
                        sEucsVal = sCache[cId];
                    }
                    }

                    boolean finalValidI = validI && (!enableEUCS || iEucsVal >= threshold);
                    boolean finalValidS = validS && (!enableEUCS || sEucsVal >= threshold);
                    if (!finalValidI && !finalValidS) continue;

                    long util = uts[flatIdx];
                    long rf = seqUtil - pre[flatIdx] - util;

                    if (finalValidI) {
                        long iu = currentMaxI + util;

                        AUDUL c = iExMap[cId];
                        if (c == null) {
                            c = audulPool.get(dul.itemSize + 1);
                            candidateCount++; // child list assembled
                            iExMap[cId] = c;
                            iDirty[iDirtyCount++] = cId;
                        }
                        c.addElementLight(sid, flatIdx, iu, rf);
                    }

                    if (finalValidS) {
                        long iu = maxS + util;

                        AUDUL c = sExMap[cId];
                        if (c == null) {
                            c = audulPool.get(dul.itemSize + 1);
                            candidateCount++; // child list assembled
                            sExMap[cId] = c;
                            sDirty[sDirtyCount++] = cId;
                        }
                        c.addElementLight(sid, flatIdx, iu, rf);
                    }
                }
            }

            for (int i = 0; i < modTCount; i++) maxT_buffer[modifiedT[i]] = -1L;
            for (int i = 0; i < modKCount; i++) maxK_buffer[modifiedK[i]] = -1L;
        }

        for (int i = 0; i < cacheDirtyCount; i++) {
            int cid = cacheDirty[i];
            iCache[cid] = -1L;
            sCache[cid] = -1L;
        }

        // Layer 2 (author decision, 2026-10-16): the coupled test on every assembled child, I and S,
        // before the recursion reaches any of them. Its two values were folded into each list while
        // it was assembled, so evaluate() is O(1), and a rejected list goes back to the pool at once.
        // Without Layer 2 the child is tested where the recursion reaches it (processRecurse) or on
        // node entry (miningDFS), as the arm's flags say.
        long l2Start = profilePhases ? RunIsolation.cpuTimeNs() : 0L;
        double reqChildHAUSP  = threshold * (dul.itemSize + 1);
        double reqChildExtend = reqChildHAUSP + threshold;
        if (enableLayer2IAUUB) {
            iDirtyCount = coupledFilter(iExMap, iDirty, iDirtyCount, reqChildHAUSP, reqChildExtend);
            sDirtyCount = coupledFilter(sExMap, sDirty, sDirtyCount, reqChildHAUSP, reqChildExtend);
        }
        if (profilePhases) timeLayer2Ns += RunIsolation.cpuTimeNs() - l2Start;

        processRecurse(iExMap, iDirty, iDirtyCount, true,  threshold, writer, depth, patternLen, lastCompactId, reqChildHAUSP);
        processRecurse(sExMap, sDirty, sDirtyCount, false, threshold, writer, depth, patternLen, lastCompactId, reqChildHAUSP);
    }

    private void generateExtensionsSparse(AUDUL dul, double threshold, BufferedWriter writer, int depth, int lastCompactId, int patternLen, double reqUtil) throws IOException {
        ensureDFSArraysCapacity(depth, promisingUpTo(threshold * (dul.itemSize + 1)));

        AUDUL[] iExMap = iExArrays[depth];
        AUDUL[] sExMap = sExArrays[depth];
        int[] iDirty = iDirtyList[depth];
        int[] sDirty = sDirtyList[depth];
        int iDirtyCount = 0, sDirtyCount = 0;

        final int lastItemId = compactToItem[lastCompactId];

        final long[] iCache = iEucsCacheByDepth[depth];
        final long[] sCache = sEucsCacheByDepth[depth];
        final int[] cacheDirty = eucsCacheDirtyByDepth[depth];
        int cacheDirtyCount = 0;

        // Length-aware dynamic SWU pruning at depth k+1.
        double minSWU = threshold * (dul.itemSize + 1);

        int p = 0;
        while (p < dul.count) {
            int sid = dul.getSid(p);
            int startP = p;
            while (p < dul.count && dul.getSid(p) == sid) p++;
            if (anyDead && deadSid[sid]) continue;     // a replaced version, left in a single-item list

            int[] items = flatItemIds[sid];
            long[] uts = flatItemUtils[sid];
            int[] tids = flatToTid[sid];
            long[] pre = flatPre[sid];
            long seqUtil = seqU[sid];
            int[] offsets = itemsetOffsets[sid];
            int[] sizes = itemsetSizes[sid];
            int n = isetCount[sid];
            int totalItems = flatLen[sid];

            if (totalItems > maxK_buffer.length) {
                int oldLen = maxK_buffer.length;
                int newLen = totalItems + 2000;
                maxK_buffer = Arrays.copyOf(maxK_buffer, newLen);
                modifiedK = Arrays.copyOf(modifiedK, newLen);
                for (int i = oldLen; i < newLen; i++) maxK_buffer[i] = -1L;
            }
            if (n > maxT_buffer.length) {
                int oldLen = maxT_buffer.length;
                int newLen = n + 500;
                maxT_buffer = Arrays.copyOf(maxT_buffer, newLen);
                modifiedT = Arrays.copyOf(modifiedT, newLen);
                for (int i = oldLen; i < newLen; i++) maxT_buffer[i] = -1L;
            }

            int minT = Integer.MAX_VALUE;
            int modTCount = 0;
            int modKCount = 0;

            for (int i = startP; i < p; i++) {
                int flatIdx = dul.getFlatIdx(i);
                int tid = tids[flatIdx];
                long iu = dul.iutils[i];

                if (tid < minT) minT = tid;

                int nT = tid + 1;
                if (nT < n) {
                    if (maxT_buffer[nT] == -1L) {
                        modifiedT[modTCount++] = nT;
                        maxT_buffer[nT] = iu;
                    } else if (iu > maxT_buffer[nT]) maxT_buffer[nT] = iu;
                }

                int nK = flatIdx + 1;
                if (nK < totalItems && tids[nK] == tid) {
                    if (maxK_buffer[nK] == -1L) {
                        modifiedK[modKCount++] = nK;
                        maxK_buffer[nK] = iu;
                    } else if (iu > maxK_buffer[nK]) maxK_buffer[nK] = iu;
                }
            }

            long maxS = -1;

            for (int t = minT; t < n; t++) {
                long tVal = maxT_buffer[t];
                if (tVal > maxS) maxS = tVal;

                int sz = sizes[t];
                if (sz == 0) continue;

                int offset = offsets[t];
                long currentMaxI = -1;

                for (int k = 0; k < sz; k++) {
                    int flatIdx = offset + k;
                    long kVal = maxK_buffer[flatIdx];
                    if (kVal > currentMaxI) currentMaxI = kVal;

                    int itemId = items[flatIdx];

                    // Skip the EUCS lookup when the item already fails the SWU bound.
                    if (globalItemSWU[itemId] < minSWU) continue;

                    int cId = itemToCompact[itemId];
                    if (cId == -1) continue;

                    boolean validI = (currentMaxI != -1L);
                    boolean validS = (maxS != -1L);
                    if (!validI && !validS) continue;

                    long iEucsVal = 0L;
                    long sEucsVal = 0L;
                    if (enableEUCS) {
                    iEucsVal = iCache[cId];
                    if (iEucsVal == -1L) {
                        if (lastItemId == itemId) {
                            iEucsVal = 0L;
                            sEucsVal = selfEUCS[itemId];
                        } else {
                            iEucsVal = iEUCSMap.get(symKey(lastItemId, itemId));
                            sEucsVal = sEUCSMap.get(dirKey(lastItemId, itemId));
                        }
                        iCache[cId] = iEucsVal;
                        sCache[cId] = sEucsVal;
                        cacheDirty[cacheDirtyCount++] = cId;
                    } else {
                        sEucsVal = sCache[cId];
                    }
                    }

                    boolean finalValidI = validI && (!enableEUCS || iEucsVal >= threshold);
                    boolean finalValidS = validS && (!enableEUCS || sEucsVal >= threshold);
                    if (!finalValidI && !finalValidS) continue;

                    long util = uts[flatIdx];
                    long rf = seqUtil - pre[flatIdx] - util;

                    if (finalValidI) {
                        long iu = currentMaxI + util;

                        AUDUL c = iExMap[cId];
                        if (c == null) {
                            c = audulPool.get(dul.itemSize + 1);
                            candidateCount++; // child list assembled
                            iExMap[cId] = c;
                            iDirty[iDirtyCount++] = cId;
                        }
                        c.addElementLight(sid, flatIdx, iu, rf);
                    }

                    if (finalValidS) {
                        long iu = maxS + util;

                        AUDUL c = sExMap[cId];
                        if (c == null) {
                            c = audulPool.get(dul.itemSize + 1);
                            candidateCount++; // child list assembled
                            sExMap[cId] = c;
                            sDirty[sDirtyCount++] = cId;
                        }
                        c.addElementLight(sid, flatIdx, iu, rf);
                    }
                }
            }

            for (int i = 0; i < modTCount; i++) maxT_buffer[modifiedT[i]] = -1L;
            for (int i = 0; i < modKCount; i++) maxK_buffer[modifiedK[i]] = -1L;
        }

        for (int i = 0; i < cacheDirtyCount; i++) {
            int cid = cacheDirty[i];
            iCache[cid] = -1L;
            sCache[cid] = -1L;
        }

        // Layer 2 (author decision, 2026-10-16): the coupled test on every assembled child, I and S,
        // before the recursion reaches any of them. Its two values were folded into each list while
        // it was assembled, so evaluate() is O(1), and a rejected list goes back to the pool at once.
        // Without Layer 2 the child is tested where the recursion reaches it (processRecurse) or on
        // node entry (miningDFS), as the arm's flags say.
        long l2Start = profilePhases ? RunIsolation.cpuTimeNs() : 0L;
        double reqChildHAUSP  = threshold * (dul.itemSize + 1);
        double reqChildExtend = reqChildHAUSP + threshold;
        if (enableLayer2IAUUB) {
            iDirtyCount = coupledFilter(iExMap, iDirty, iDirtyCount, reqChildHAUSP, reqChildExtend);
            sDirtyCount = coupledFilter(sExMap, sDirty, sDirtyCount, reqChildHAUSP, reqChildExtend);
        }
        if (profilePhases) timeLayer2Ns += RunIsolation.cpuTimeNs() - l2Start;

        processRecurse(iExMap, iDirty, iDirtyCount, true,  threshold, writer, depth, patternLen, lastCompactId, reqChildHAUSP);
        processRecurse(sExMap, sDirty, sDirtyCount, false, threshold, writer, depth, patternLen, lastCompactId, reqChildHAUSP);
    }

    /**
     * Layer 2: the coupled test on every assembled child of one kind before any recursion. A child
     * that can be neither a HAUSP (evalIutil &lt; theta*len) nor extended (evalMFUUB &lt;
     * theta*(len+1)) is released at once; the others keep their order. It is the test the
     * child-level arm applies in processRecurse, applied to all children first.
     *
     * @return the number of children kept at the front of {@code dirtyList}
     */
    private int coupledFilter(AUDUL[] mapArray, int[] dirtyList, int dirtyCount,
                              double reqChildHAUSP, double reqChildExtend) {
        int kept = 0;
        for (int i = 0; i < dirtyCount; i++) {
            int cId = dirtyList[i];
            AUDUL c = mapArray[cId];
            c.evaluate();
            if (c.evalIutil >= reqChildHAUSP || c.evalMFUUB >= reqChildExtend) {
                dirtyList[kept++] = cId;
            } else {
                audulPool.release(c);
                mapArray[cId] = null;
                prunedL_TwoPass++;
            }
        }
        return kept;
    }

    private void processRecurse(AUDUL[] mapArray, int[] dirtyList, int dirtyCount, boolean isI,
                                double threshold, BufferedWriter writer, int depth, int patternLen,
                                int lastCompactId, double reqChildHAUSP) throws IOException {
        if (dirtyCount == 0) return;

        if (patternLen + 2 >= currentPattern.length) {
            currentPattern = Arrays.copyOf(currentPattern, currentPattern.length * 2);
        }

        double reqChildExtend = reqChildHAUSP + threshold;

        for (int i = 0; i < dirtyCount; i++) {
            int cId = dirtyList[i];
            AUDUL child = mapArray[cId];
            mapArray[cId] = null;

            int origId = compactToItem[cId];

            // Layer 3 (MFUUB) pruning of the child, where the recursion reaches it. With Layer 2
            // every child here has passed this very test already, so it is not applied again.
            // 2026-09-03 ablation fix: this test was applied regardless of
            // enableLayer3MFUUB, so the HAUSP-UB-L1 variant (both flags false)
            // still pruned at Layer 3 and was indistinguishable from L1L3.
            if (!enableLayer2IAUUB && enableLayer3MFUUB && childLevelL3) {
                child.evaluate();
                if (child.evalIutil < reqChildHAUSP && child.evalMFUUB < reqChildExtend) {
                    prunedL3++;
                    audulPool.release(child);
                    continue;
                }
            }

            int oldLen = patternLen;
            if (!isI) currentPattern[patternLen++] = -1;
            currentPattern[patternLen++] = origId;

            miningDFS(child, threshold, writer, depth + 1, cId, patternLen);

            patternLen = oldLen;
            audulPool.release(child);
        }
    }

    /** A sequence holding {@code a}'s itemsets followed by {@code b}'s, under id {@code sid}. */
    private static Sequence concat(Sequence a, Sequence b, int sid) {
        Sequence s = new Sequence(sid);
        for (Itemset is : a.itemsets) s.addItemset(is);
        for (Itemset is : b.itemsets) s.addItemset(is);
        return s;
    }

    /**
     * The batch as the structures store it: each sequence under the next internal id. A sequence
     * whose key is stored lengthens it: the stored version is dropped and its lengthened version
     * enters as a new sequence. Two parts of one key in the same batch are joined in order.
     */
    private List<Sequence> toInternal(List<Sequence> batch) {
        List<Sequence> out = new ArrayList<>(batch.size());
        it.unimi.dsi.fastutil.ints.Int2IntOpenHashMap inBatch = newKeyMap();
        appendedLastBatch = 0;
        for (Sequence s : batch) {
            int pos = inBatch.get(s.sid);
            if (pos >= 0) {                                   // another part of a key already in this batch
                Sequence prev = out.get(pos);
                out.set(pos, concat(prev, s, prev.sid));
                continue;
            }
            int cur = keyToInternal.get(s.sid);
            Sequence entering;
            if (cur >= 0) {
                entering = concat(dbArray[cur], s, nextInternal);
                dropStored(cur);
                appendedLastBatch++;
            } else {
                entering = new Sequence(nextInternal);
                entering.itemsets = s.itemsets;
                entering.totalUtility = s.totalUtility;
            }
            keyToInternal.put(s.sid, nextInternal++);
            inBatch.put(s.sid, out.size());
            out.add(entering);
        }
        return out;
    }

    /**
     * The batch as {@code appendInPlace} stores it. A new key takes the next internal id. A stored key
     * keeps its id: its entry here holds only the appended itemsets, and its old position and itemset
     * counts are recorded for the steps of this batch. Two parts of one key in the same batch are
     * joined in order. The result is sorted by internal id, so that every run a batch adds to a
     * single-item list is in id order.
     */
    private List<Sequence> toInternalInPlace(List<Sequence> batch) {
        List<Sequence> out = new ArrayList<>(batch.size());
        it.unimi.dsi.fastutil.ints.Int2IntOpenHashMap inBatch = newKeyMap();
        touchedIndex.clear();
        touchedOldLen.clear();
        touchedOldIsets.clear();
        appendedLastBatch = 0;
        for (Sequence s : batch) {
            int pos = inBatch.get(s.sid);
            if (pos >= 0) {
                Sequence prev = out.get(pos);
                out.set(pos, concat(prev, s, prev.sid));
                continue;
            }
            int cur = keyToInternal.get(s.sid);
            Sequence entering;
            if (cur >= 0) {
                entering = concat(new Sequence(cur), s, cur);
                touchedIndex.put(cur, touchedOldLen.size());
                touchedOldLen.add(flatLen[cur]);
                touchedOldIsets.add(isetCount[cur]);
                appendedLastBatch++;
            } else {
                entering = new Sequence(nextInternal);
                entering.itemsets = s.itemsets;
                entering.totalUtility = s.totalUtility;
                keyToInternal.put(s.sid, nextInternal++);
            }
            inBatch.put(s.sid, out.size());
            out.add(entering);
        }
        out.sort(Comparator.comparingInt(x -> x.sid));
        return out;
    }

    /**
     * SWU and totalU for the appended part {@code a} of a stored sequence with {@code oldLen}
     * positions: an item already in the sequence gains u(A); an item new to it gains u(S') in full.
     */
    private void swuForAppend(Sequence a, int oldLen) {
        int sid = a.sid;
        long uA = a.totalUtility;
        long uNew = seqU[sid] + uA;
        totalDBUtility += uA;
        int[] ids = flatItemIds[sid];
        int seen = 0;
        for (int i = 0; i < oldLen; i++) {
            int id = ids[i];
            if (!swuSeenBuf[id]) {
                swuSeenBuf[id] = true;
                swuSeenListBuf[seen++] = id;
                globalItemSWU[id] += "swu_full".equals(FAULT) ? uNew : uA;
            }
        }
        for (Itemset is : a.itemsets) {
            for (ItemQ it : is.items) {
                if (!swuSeenBuf[it.id]) {
                    swuSeenBuf[it.id] = true;
                    swuSeenListBuf[seen++] = it.id;
                    globalItemSWU[it.id] += uNew;
                }
            }
        }
        for (int i = 0; i < seen; i++) swuSeenBuf[swuSeenListBuf[i]] = false;
    }

    /**
     * Writes the appended part {@code a} after the {@code oldLen} positions and {@code oldIsets}
     * itemsets of its stored sequence. Stored positions are not touched: pre continues from the
     * stored u(S). Arrays grow by half their length when full.
     */
    private void appendFlat(Sequence a, int oldLen, int oldIsets) {
        int sid = a.sid;
        int m = 0;
        for (Itemset is : a.itemsets) m += is.items.size();
        int nNew = a.itemsets.size();
        // fault "merge_last": the first appended itemset is merged into the last stored one
        int shift = "merge_last".equals(FAULT) && oldIsets > 0 ? 1 : 0;
        int len = oldLen + m, nIs = oldIsets + nNew - shift;
        if (flatItemIds[sid].length < len) {
            int old = flatItemIds[sid].length;
            int cap = Math.max(len, old + old / 2 + 4);
            flatItemIds[sid] = Arrays.copyOf(flatItemIds[sid], cap);
            flatItemUtils[sid] = Arrays.copyOf(flatItemUtils[sid], cap);
            flatToTid[sid] = Arrays.copyOf(flatToTid[sid], cap);
            flatPre[sid] = Arrays.copyOf(flatPre[sid], cap);
            flatBytes += 24L * (cap - old);
        }
        if (itemsetSizes[sid].length < nIs) {
            int old = itemsetSizes[sid].length;
            int cap = Math.max(nIs, old + old / 2 + 4);
            itemsetOffsets[sid] = Arrays.copyOf(itemsetOffsets[sid], cap);
            itemsetSizes[sid] = Arrays.copyOf(itemsetSizes[sid], cap);
            flatBytes += 8L * (cap - old);
        }
        int[] ids = flatItemIds[sid];
        long[] uts = flatItemUtils[sid];
        int[] tids = flatToTid[sid];
        long[] pre = flatPre[sid];
        long run = "pre_from_zero".equals(FAULT) ? 0 : seqU[sid];
        int ptr = oldLen;
        for (int t = 0; t < nNew; t++) {
            Itemset is = a.itemsets.get(t);
            int ti = oldIsets + t - shift;
            if (t == 0 && shift == 1) {
                itemsetSizes[sid][ti] += is.items.size();
            } else {
                itemsetOffsets[sid][ti] = ptr;
                itemsetSizes[sid][ti] = is.items.size();
            }
            for (ItemQ it : is.items) {
                ids[ptr] = it.id;
                uts[ptr] = it.utility;
                tids[ptr] = ti;
                pre[ptr] = run;
                run += it.utility;
                ptr++;
            }
        }
        if (!"no_useq".equals(FAULT)) seqU[sid] += a.totalUtility;
        if ("touch_old".equals(FAULT) && oldLen > 0) pre[oldLen - 1] += 1;
        flatLen[sid] = len;
        isetCount[sid] = nIs;
        for (Itemset is : a.itemsets) dbArray[sid].addItemset(is);
        liveEntries += m;
        positionsWrittenLastBatch += m;
        for (int i = oldLen; i < len; i++) addRootEntry(ids[i], sid, i, uts[i], seqU[sid] - pre[i] - uts[i]);
    }

    private int[] firstOldBuf = new int[0], lastOldBuf = new int[0];
    private final LongOpenHashSet oldPairsSeen = new LongOpenHashSet(256);

    /**
     * EUCS for a stored sequence that gained {@code uA} after its {@code oldLen} positions and
     * {@code oldIsets} itemsets: every pair (and every self pair) the sequence held before gains
     * u(A); every pair present only in S' gains u(S') in full. The same rule as the SWU.
     */
    private void eucsForAppend(int sid, int oldLen, int oldIsets, long uA) {
        long uNew = seqU[sid];
        int len = flatLen[sid];
        int[] ids = flatItemIds[sid];
        int[] tids = flatToTid[sid];
        if (firstOldBuf.length < firstTidBuf.length) {
            firstOldBuf = new int[firstTidBuf.length];
            lastOldBuf = new int[firstTidBuf.length];
            Arrays.fill(firstOldBuf, -1);
        }
        boolean full = "eucs_full".equals(FAULT);
        int uSize = 0;
        for (int i = 0; i < len; i++) {
            int id = ids[i];
            if (!seenCBuf[id]) {
                seenCBuf[id] = true;
                uListBuf[uSize++] = id;
            }
            if (firstTidBuf[id] == -1) firstTidBuf[id] = tids[i];
            lastTidBuf[id] = tids[i];
            if (i < oldLen) {
                if (firstOldBuf[id] == -1) firstOldBuf[id] = tids[i];
                lastOldBuf[id] = tids[i];
            }
        }
        for (int i = 0; i < uSize; i++) {
            int iA = uListBuf[i];
            int fA = firstTidBuf[iA], lA = lastTidBuf[iA];
            boolean oldA = firstOldBuf[iA] != -1;
            if (fA < lA) selfEUCS[iA] += (!full && oldA && firstOldBuf[iA] < lastOldBuf[iA]) ? uA : uNew;
            for (int j = i + 1; j < uSize; j++) {
                int iB = uListBuf[j];
                boolean oldB = firstOldBuf[iB] != -1;
                if (fA < lastTidBuf[iB]) {
                    long add = (!full && oldA && oldB && firstOldBuf[iA] < lastOldBuf[iB]) ? uA : uNew;
                    if (useSparseEUCS) sEUCSMap.addTo(dirKey(iA, iB), add);
                    else sEUCS[iA * currentMatrixN + iB] += add;
                }
                if (firstTidBuf[iB] < lA) {
                    long add = (!full && oldA && oldB && firstOldBuf[iB] < lastOldBuf[iA]) ? uA : uNew;
                    if (useSparseEUCS) sEUCSMap.addTo(dirKey(iB, iA), add);
                    else sEUCS[iB * currentMatrixN + iA] += add;
                }
            }
        }
        int[] off = itemsetOffsets[sid], sz = itemsetSizes[sid];
        int nIs = isetCount[sid];
        oldPairsSeen.clear();
        seqPairsSeen.clear();
        for (int ti = 0; ti < nIs; ti++) {
            int isz = sz[ti];
            if (isz < 2) continue;
            int o = off[ti];
            for (int a = 0; a < isz; a++) {
                for (int b = a + 1; b < isz; b++) {
                    int iA = ids[o + a], iB = ids[o + b];
                    if (iA == iB) continue;
                    long key = symKey(Math.min(iA, iB), Math.max(iA, iB));
                    long add;
                    if (ti < oldIsets) {
                        if (!oldPairsSeen.add(key)) continue;
                        add = full ? uNew : uA;
                    } else {
                        if (oldPairsSeen.contains(key) || !seqPairsSeen.add(key)) continue;
                        add = uNew;
                    }
                    if (useSparseEUCS) {
                        iEUCSMap.addTo(symKey(iA, iB), add);
                    } else {
                        int min = Math.min(iA, iB), max = Math.max(iA, iB);
                        iEUCS[(int) (triRowOffsetsRaw[min] + (max - min - 1))] += add;
                    }
                }
            }
        }
        for (int i = 0; i < uSize; i++) {
            int iA = uListBuf[i];
            seenCBuf[iA] = false;
            firstTidBuf[iA] = -1;
            firstOldBuf[iA] = -1;
        }
    }

    /**
     * Adds one entry to a single-item list. Under {@code appendInPlace} an entry whose sequence id
     * is below the list's last id starts a new run; a list reaching MAX_RUNS runs is first merged
     * into one (amortized, counted in the batch).
     */
    private void addRootEntry(int item, int sid, int flatIdx, long iu, long rf) {
        AUDUL r = globalAUDULs[item];
        if (r == null) {
            r = audulPool.get(1);
            audulPool.markRoot(r);
            globalAUDULs[item] = r;
        } else if (appendInPlace && r.count > 0 && sid < r.getSid(r.count - 1)) {
            IntArrayList runs = rootRuns[item];
            if (runs != null && runs.size() + 1 >= MAX_RUNS) {
                AUDUL merged = mergedView(item);
                rootRunMerges++;
                audulPool.release(r);
                audulPool.markRoot(merged);
                globalAUDULs[item] = merged;
                rootRuns[item] = null;
                runs = null;
                r = merged;
            }
            if (!"no_run".equals(FAULT)) {
                if (runs == null) {
                    runs = new IntArrayList(2);
                    rootRuns[item] = runs;
                }
                runs.add(r.count);
            }
        }
        r.addElementLight(sid, flatIdx, iu, rf);
    }

    /**
     * The single-item list of {@code item} as the search reads it. Without {@code appendInPlace}
     * it is the stored list. With it, a fresh list (released by the caller) holding the stored
     * entries merged by sequence id across runs, positions in increasing order within a sequence,
     * with every per-sequence maximum computed from the current u(S).
     */
    private AUDUL rootView(int item) {
        AUDUL r = item < globalAUDULs.length ? globalAUDULs[item] : null;
        if (r == null || !appendInPlace || "stale_root".equals(FAULT)) return r;
        return mergedView(item);
    }

    private AUDUL mergedView(int item) {
        AUDUL r = globalAUDULs[item];
        IntArrayList runs = rootRuns[item];
        int k = runs == null ? 1 : runs.size() + 1;
        int[] pos = new int[k], end = new int[k];
        for (int j = 0; j < k; j++) {
            pos[j] = j == 0 ? 0 : runs.getInt(j - 1);
            end[j] = j == k - 1 ? r.count : runs.getInt(j);
        }
        AUDUL v = audulPool.get(1);
        while (true) {
            int best = -1, bestSid = Integer.MAX_VALUE;
            for (int j = 0; j < k; j++) {
                if (pos[j] < end[j]) {
                    int sj = r.getSid(pos[j]);
                    if (sj < bestSid) { bestSid = sj; best = j; }
                }
            }
            if (best < 0) break;
            for (int j = best; j < k; j++) {
                while (pos[j] < end[j] && r.getSid(pos[j]) == bestSid) {
                    int e = pos[j]++;
                    int idx = r.getFlatIdx(e);
                    long iu = r.iutils[e];
                    v.addElementLight(bestSid, idx, iu, seqU[bestSid] - flatPre[bestSid][idx] - flatItemUtils[bestSid][idx]);
                }
            }
        }
        return v;
    }

    // ---- read access for analysis/PrefixEncodingCheck (package-private, no effect on a run) ----
    int internalId(int key) { return keyToInternal.get(key); }
    int positions(int sid) { return flatLen[sid]; }
    int itemsets(int sid) { return isetCount[sid]; }
    int[] posItems(int sid) { return flatItemIds[sid]; }
    long[] posUtils(int sid) { return flatItemUtils[sid]; }
    int[] posTids(int sid) { return flatToTid[sid]; }
    long[] posPre(int sid) { return flatPre[sid]; }
    int[] isetOffsets(int sid) { return itemsetOffsets[sid]; }
    int[] isetSizes(int sid) { return itemsetSizes[sid]; }
    long sequenceUtility(int sid) { return seqU[sid]; }
    long totalUtility() { return totalDBUtility; }
    long swu(int item) { return item < globalItemSWU.length ? globalItemSWU[item] : 0; }
    boolean eucsOn() { return enableEUCS; }
    long selfEucs(int a) { return a < selfEUCS.length ? selfEUCS[a] : 0; }
    long sEucs(int a, int b) {
        if (useSparseEUCS) return sEUCSMap.get(dirKey(a, b));
        return a < currentMatrixN && b < currentMatrixN ? sEUCS[a * currentMatrixN + b] : 0;
    }
    long iEucs(int a, int b) {
        int min = Math.min(a, b), max = Math.max(a, b);
        if (useSparseEUCS) return iEUCSMap.get(symKey(min, max));
        return max < currentMatrixN ? iEUCS[(int) (triRowOffsetsRaw[min] + (max - min - 1))] : 0;
    }
    /** The list the search reads for {@code item} as (sid, position, utility) triples, plus its evaluated sums. */
    long[] rootSnapshot(int item) {
        AUDUL v = rootView(item);
        if (v == null) return new long[]{0, 0, 0};
        v.evaluate();
        long[] out = new long[3 + 3 * v.count];
        out[0] = v.evalIutil; out[1] = v.evalMFUUB; out[2] = v.evalSupport;
        for (int e = 0; e < v.count; e++) {
            out[3 + 3 * e] = v.getSid(e); out[4 + 3 * e] = v.getFlatIdx(e); out[5 + 3 * e] = v.iutils[e];
        }
        if (v != globalAUDULs[item]) audulPool.release(v);
        return out;
    }

    /**
     * Removes a stored sequence's contribution: totalU, the SWU of each of its items, and in each of
     * its items' single-item lists the per-sequence maxima the list had added for it. Its entries
     * stay in those lists, marked dead, and its flat arrays are released.
     */
    private void dropStored(int sid) {
        int[] ids = flatItemIds[sid];
        long[] uts = flatItemUtils[sid];
        long[] pre = flatPre[sid];
        long su = dbArray[sid].totalUtility;
        totalDBUtility -= su;
        Map<Integer, long[]> terms = new HashMap<>();
        for (int i = 0; i < ids.length; i++) {
            long[] t = terms.computeIfAbsent(ids[i], x -> new long[]{Long.MIN_VALUE, Long.MIN_VALUE});
            if (uts[i] > t[0]) t[0] = uts[i];
            long rfi = seqU[sid] - pre[i] - uts[i];
            if (uts[i] + rfi > t[1]) t[1] = uts[i] + rfi;
        }
        for (Map.Entry<Integer, long[]> e : terms.entrySet()) {
            globalItemSWU[e.getKey()] -= su;
            globalAUDULs[e.getKey()].dropSequence(sid, e.getValue()[0], e.getValue()[1]);
        }
        if (deadSid.length <= sid) deadSid = Arrays.copyOf(deadSid, Math.max(sid + 1, 2 * deadSid.length + 1024));
        deadSid[sid] = true;
        anyDead = true;
        deadEntries += ids.length;
        liveEntries -= ids.length;
        liveSequences--;
        flatBytes -= 4L * ids.length + 8L * uts.length + 4L * flatToTid[sid].length + 8L * pre.length
                + 4L * itemsetOffsets[sid].length + 4L * itemsetSizes[sid].length;
        dbArray[sid] = null;
        flatItemIds[sid] = null;
        flatItemUtils[sid] = null;
        flatToTid[sid] = null;
        flatPre[sid] = null;
        itemsetOffsets[sid] = null;
        itemsetSizes[sid] = null;
    }

    /** Rewrites every single-item list without its dead entries; amortized O(1) per entry. */
    private void compactRoots() {
        for (int i = 0; i < globalAUDULs.length; i++) {
            AUDUL old = globalAUDULs[i];
            if (old == null) continue;
            AUDUL fresh = audulPool.get(1);
            audulPool.markRoot(fresh);
            for (int e = 0; e < old.count; e++) {
                int sid = old.getSid(e);
                if (sid < deadSid.length && deadSid[sid]) continue;
                int flatIdx = old.getFlatIdx(e);
                fresh.addElementLight(sid, flatIdx, old.iutils[e],
                        seqU[sid] - flatPre[sid][flatIdx] - flatItemUtils[sid][flatIdx]);
            }
            audulPool.release(old);
            if (fresh.count == 0) {
                audulPool.release(fresh);
                globalAUDULs[i] = null;
            } else {
                globalAUDULs[i] = fresh;
            }
        }
        Arrays.fill(deadSid, false);
        anyDead = false;
        deadEntries = 0;
    }

    /** Records a pattern found in top-k mode and raises the internal threshold to the k-th best value. */
    private void offerTopK(long util, int len, int patternLen) {
        long[] v = {util, len};
        if (topKHeap.size() < topK) topKHeap.add(v);
        else if (util * topKHeap.peek()[1] > topKHeap.peek()[0] * len) { topKHeap.poll(); topKHeap.add(v); }
        if (topKHeap.size() == topK) {
            long[] kth = topKHeap.peek();
            topKTau = Math.max(topKTau, (double) kth[0] / kth[1]);
        }
        if ((double) util / len >= topKTau * TAU_SLACK) {
            topKEntries.add(new TopKEntry(util, len, Arrays.copyOf(currentPattern, patternLen)));
            if (topKEntries.size() > 4 * topK + 1024) {
                double cut = topKTau * TAU_SLACK;
                topKEntries.removeIf(e -> (double) e.util / e.len < cut);
            }
        }
    }

    /** Ends a top-k batch: the exact k-th value, the patterns at or above it, and their dump. */
    private void finishTopK(BufferedWriter writer) throws IOException {
        if (topKHeap.size() == topK) {
            long[] kth = topKHeap.peek();
            tauKUtil = kth[0];
            tauKLen = (int) kth[1];
        } else {
            tauKUtil = 0;
            tauKLen = 1;
        }
        for (TopKEntry e : topKEntries) {
            if (e.util * tauKLen >= tauKUtil * e.len) topKResult.add(e);
        }
        hauspCount = topKResult.size();
        lastTauK = (double) tauKUtil / tauKLen;
        if (enableIO && writer != null) {
            for (TopKEntry e : topKResult) {
                writer.write(patternString(e.pattern) + "\t"
                        + String.format(Locale.US, "%.2f", (double) e.util / e.len) + "\n");
            }
        }
    }

    private static String patternString(int[] p) {
        StringBuilder sb = new StringBuilder("<(");
        for (int i = 0; i < p.length; i++) {
            if (p[i] == -1) sb.append(")(");
            else {
                if (i > 0 && p[i - 1] != -1) sb.append(",");
                sb.append(p[i]);
            }
        }
        sb.append(")>");
        return sb.toString();
    }

    private String getPatternString(int patternLen) {
        StringBuilder sb = new StringBuilder("<(");
        for (int i = 0; i < patternLen; i++) {
            if (currentPattern[i] == -1) sb.append(")(");
            else {
                if (i > 0 && currentPattern[i - 1] != -1) sb.append(",");
                sb.append(currentPattern[i]);
            }
        }
        sb.append(")>");
        return sb.toString();
    }

    /**
     * Number of promising items whose SWU reaches {@code minSWU}. Compact ids are in descending
     * SWU order, so the admitted items are the compact ids below this bound.
     */
    private int promisingUpTo(double minSWU) {
        int lo = 0, hi = compactCount;
        while (lo < hi) {
            int mid = (lo + hi) >>> 1;
            if (sortedPromisingSWU[mid] >= minSWU) lo = mid + 1; else hi = mid;
        }
        return lo;
    }

    private void ensureDFSArraysCapacity(int depth, int need) {
        if (depth >= iExArrays.length) {
            int newLen = depth + 64;
            iExArrays = Arrays.copyOf(iExArrays, newLen);
            sExArrays = Arrays.copyOf(sExArrays, newLen);
            iDirtyList = Arrays.copyOf(iDirtyList, newLen);
            sDirtyList = Arrays.copyOf(sDirtyList, newLen);
            iEucsCacheByDepth = Arrays.copyOf(iEucsCacheByDepth, newLen);
            sEucsCacheByDepth = Arrays.copyOf(sEucsCacheByDepth, newLen);
            eucsCacheDirtyByDepth = Arrays.copyOf(eucsCacheDirtyByDepth, newLen);
        }
        if (iExArrays[depth] == null || iExArrays[depth].length < need + 1) {
            int size = need + 1;
            depthArrayBytes -= (iExArrays[depth] == null) ? 0L : (long) iExArrays[depth].length * BYTES_PER_DEPTH_SLOT;
            depthArrayBytes += (long) size * BYTES_PER_DEPTH_SLOT;
            iExArrays[depth] = new AUDUL[size];
            sExArrays[depth] = new AUDUL[size];
            iDirtyList[depth] = new int[size];
            sDirtyList[depth] = new int[size];
            // The three EUCS lookup caches are allocated only when the pre-filter is active.
            // Every read and write of them sits inside an `if (enableEUCS)` block, and the
            // clean-up loop runs cacheDirtyCount times, which stays 0 without the pre-filter, so
            // an arm that does not use EUCS never touches them. They are the widest of the
            // per-depth arrays (two long[] and one int[] of |P| at every depth reached, never
            // released), and the algorithm of this paper does not use the pre-filter.
            if (enableEUCS) {
                iEucsCacheByDepth[depth] = new long[size];
                sEucsCacheByDepth[depth] = new long[size];
                eucsCacheDirtyByDepth[depth] = new int[size];
                Arrays.fill(iEucsCacheByDepth[depth], -1L);
                Arrays.fill(sEucsCacheByDepth[depth], -1L);
            }
        }
    }


    /**
     * Byte-level breakdown of every structure this algorithm retains, printed under
     * {@code --profile-phases}. Written on 2026-09-15 because two successive estimates of where the
     * live heap sits were wrong: the first over-counted the per-depth arrays by assuming maximum
     * depth, and the second left 272 MB of the synthetic corpus unattributed. Array object headers
     * and the outer reference arrays are included, because on a sparse dataset they are not noise.
     */
    private void printMemoryBreakdown() {
        final int HDR = 16, REF = 8;   // array object header, reference slot
        long flatPayload = flatBytes, flatOverhead = 0;
        int seqs = 0;
        if (flatItemIds != null) {
            for (int i = 0; i < flatItemIds.length; i++) {
                if (flatItemIds[i] == null) continue;
                seqs++;
                flatOverhead += 6L * HDR;               // six arrays per sequence
            }
            flatOverhead += 6L * REF * flatItemIds.length;   // the six outer arrays
        }
        long rootObjects = 0, rootArrays = 0;
        int items = 0;
        if (globalAUDULs != null) {
            for (AUDUL a : globalAUDULs) {
                if (a == null) continue;
                items++;
                rootObjects += 96;                      // object header + fields, approximate
                rootArrays += 2L * HDR + a.arrayBytes();
            }
            rootObjects += (long) REF * globalAUDULs.length;
        }
        long byItem = 0;
        if (itemToCompact != null) byItem += 4L * itemToCompact.length;
        if (compactToItem != null) byItem += 4L * compactToItem.length;
        if (globalItemSWU != null) byItem += 8L * globalItemSWU.length;
        if (sortedPromisingSWU != null) byItem += 8L * sortedPromisingSWU.length;
        long M = 1048576;
        System.out.printf("      [profile] sequences %d, items %d | flat payload %.1f MB + overhead %.1f MB "
                        + "| single-item lists %.1f MB (objects %.1f) | per-item buffers %.1f MB "
                        + "| per-depth arrays %.1f MB | pool %.1f MB | EUCS %.1f MB%n",
                seqs, items, flatPayload / (double) M, flatOverhead / (double) M,
                rootArrays / (double) M, rootObjects / (double) M, byItem / (double) M,
                depthArrayBytes / (double) M, audulPool.arrayBytes / (double) M, eucsBytesNow() / (double) M);
    }

    private RunResult buildRunResult(int bId, long startNs, long tScan, long tMining) {
        RunResult res = new RunResult();
        res.timestamp = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date());
        res.algorithm = "HAUSP-UB";
        res.dataset = datasetName;
        res.batchID = bId;
        res.minUtil = minUtilPercentage;
        res.deltaRatio = 0;
        res.totalDBUtility = totalDBUtility;
        res.cumulativeDBSize = liveSequences;
        res.tScan = tScan;
        res.tMining = tMining;
        res.tTotal = (RunIsolation.cpuTimeNs() - startNs) / 1_000_000L;
        res.tLayer1 = timeLayer1Ns / 1_000_000L;
        res.tLayer2 = timeLayer2Ns / 1_000_000L;
        res.tLayer3 = timeLayer3Ns / 1_000_000L;
        res.numCand = candidateCount;
        res.numRecursed = recursedCount;
        res.hauspFound = hauspCount;
        res.memPeak = this.peakMemory;
        res.poolBytes = peakPoolBytes;
        res.flatBytes = peakFlatBytes;
        res.eucsBytes = peakEucsBytes;
        res.audulRootBytes = peakRootBytes;

        res.numPrunedL1 = prunedL1 + prunedL1_5;
        res.numPrunedL2 = prunedL_TwoPass + prunedL2;
        res.numPrunedL3 = prunedL3;
        res.numPrunedL3Node = prunedL3Node;
        res.numPrunedL1Root = prunedL1_5;

        res.ratioTightnessPEAU = this.tightnessPEAU;
        res.ratioTightnessIAUUB = this.tightnessIAUUB;
        res.ratioTightnessMFUUB = this.tightnessMFUUB;

        res.poolBorrows  = audulPool.borrows;
        res.poolReuses   = audulPool.reuses;
        res.poolPeakLive = audulPool.peakBorrowed;

        long live = 0;
        if (globalAUDULs != null) {
            for (int i = 0; i < globalAUDULs.length; i++) {
                if (globalAUDULs[i] != null) live++;
            }
        }
        res.audulActive = live;

        return res;
    }

    static final class AUDULPool {
        private AUDUL[] pool = new AUDUL[4096];
        private int top = -1;

        // Lifetime counters. {@link #borrows} grows on every get() call;
        // {@link #reuses} counts only the calls that returned a pooled
        // instance instead of allocating a fresh one. {@link #peakBorrowed}
        // tracks the largest number of simultaneously borrowed AU-DULs.
        long borrows = 0;
        long reuses  = 0;
        long peakBorrowed = 0;
        long currentBorrowed = 0;

        boolean reuse = true; // set from HAUSP_UB.enablePool in setConfig

        // Array payload (bytes) of every AU-DUL this pool has handed out and
        // still owns: borrowed or returned, including the single-item roots.
        // Maintained incrementally on allocation, growth, shrink and drop.
        long arrayBytes = 0;
        // Share of arrayBytes held by the AU-DULs currently marked as roots.
        long rootArrayBytes = 0;

        public AUDUL get(int size) {
            borrows++;
            currentBorrowed++;
            if (currentBorrowed > peakBorrowed) peakBorrowed = currentBorrowed;
            if (reuse && top >= 0) {
                reuses++;
                AUDUL a = pool[top--];
                a.init(size);
                return a;
            }
            AUDUL a = new AUDUL(size);
            a.owner = this;
            arrayBytes += a.arrayBytes();
            return a;
        }

        /** Marks {@code a} as a single-item root kept across batches (memory attribution only). */
        public void markRoot(AUDUL a) {
            if (!a.isRoot) { a.isRoot = true; rootArrayBytes += a.arrayBytes(); }
        }

        /** Called by AUDUL when its arrays grow or shrink by {@code deltaBytes}. */
        void onResize(AUDUL a, long deltaBytes) {
            arrayBytes += deltaBytes;
            if (a.isRoot) rootArrayBytes += deltaBytes;
        }

        public void release(AUDUL a) {
            if (a.isRoot) { a.isRoot = false; rootArrayBytes -= a.arrayBytes(); }
            if (!reuse) {
                arrayBytes -= a.arrayBytes();
                a.owner = null;
                if (currentBorrowed > 0) currentBorrowed--;
                return;
            }
            if (a.locs.length > 8192) {
                long before = a.arrayBytes();
                a.locs = new long[8192];
                a.iutils = new long[8192];
                arrayBytes += a.arrayBytes() - before;
            }
            if (++top >= pool.length) pool = Arrays.copyOf(pool, pool.length * 2);
            pool[top] = a;
            if (currentBorrowed > 0) currentBorrowed--;
        }

        /** Reset only the lifetime counters; the pooled instances are preserved. */
        public void resetCounters() {
            borrows = 0;
            reuses = 0;
            peakBorrowed = 0;
            currentBorrowed = 0;
        }
    }

    static final class AUDUL {
        public long evalIutil;
        public int evalSupport;
        public long evalMFUUB; // Tightest MFUUB seen at this node.
        int itemSize;

        long[] locs = new long[8];
        long[] iutils = new long[8];

        /** Pool that allocated this list (memory attribution); null for lists created outside the pool. */
        AUDULPool owner;
        /** True while the list is one of the single-item roots kept across batches. */
        boolean isRoot;

        /** Bytes of array payload currently held by this list. */
        long arrayBytes() {
            return 8L * (locs.length + iutils.length);
        }

        int count = 0;
        private int lastAggSid = -1;
        private long runMaxIutil = 0;
        private long runMaxMFUUB = 0;
        private long aggTotalIutil = 0;
        private long aggTotalMFUUB = 0;
        private int aggSupportCount = 0;
        /** True when the open (last) sequence group was dropped and contributes nothing. */
        private boolean openEmpty = false;

        public AUDUL(int size) {
            init(size);
        }

        public void init(int size) {
            this.itemSize = size;
            this.lastAggSid = -1;
            this.count = 0;
            this.runMaxIutil = 0;
            this.runMaxMFUUB = 0;
            this.aggTotalIutil = 0;
            this.aggTotalMFUUB = 0;
            this.aggSupportCount = 0;
            this.openEmpty = false;
        }

        /** Removes one sequence's per-sequence maxima from the totals (single-item lists only). */
        void dropSequence(int sid, long termIutil, long termMFUUB) {
            if (sid == lastAggSid && !openEmpty) {
                runMaxIutil = 0;
                runMaxMFUUB = 0;
                openEmpty = true;
            } else {
                aggTotalIutil -= termIutil;
                aggTotalMFUUB -= termMFUUB;
                aggSupportCount--;
            }
        }

        public void addElementLight(int sid, int flatIdx, long iu, long rf) {
            if (count >= locs.length) {
                int newCapacity = locs.length * 2;
                if (owner != null) owner.onResize(this, 16L * (newCapacity - locs.length));
                locs = Arrays.copyOf(locs, newCapacity);
                iutils = Arrays.copyOf(iutils, newCapacity);
            }

            locs[count] = ((long) sid << 32) | (flatIdx & 0xFFFFFFFFL);
            iutils[count++] = iu;

            // Compute PEAU/MFUUB in place using max(iutil + rutilFull).
            long currentMFUUB = iu + rf;

            if (sid != lastAggSid) {
                if (lastAggSid != -1 && !openEmpty) {
                    aggTotalIutil += runMaxIutil;
                    aggTotalMFUUB += runMaxMFUUB;
                    aggSupportCount++;
                }
                openEmpty = false;
                lastAggSid = sid;
                runMaxIutil = iu;
                runMaxMFUUB = currentMFUUB;
            } else {
                if (iu > runMaxIutil) runMaxIutil = iu;
                if (currentMFUUB > runMaxMFUUB) runMaxMFUUB = currentMFUUB;
            }
        }

        public void evaluate() {
            if (lastAggSid == -1) {
                evalIutil = 0;
                evalMFUUB = 0;
                evalSupport = 0;
            } else {
                evalIutil = aggTotalIutil + runMaxIutil;
                evalMFUUB = aggTotalMFUUB + runMaxMFUUB;
                evalSupport = aggSupportCount + (openEmpty ? 0 : 1);
            }
        }

        public int getSid(int index) {
            return (int) (locs[index] >>> 32);
        }

        public int getFlatIdx(int index) {
            return (int) locs[index];
        }
    }
}