import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.longs.LongArrayList;

import java.io.*;
import java.text.SimpleDateFormat;
import java.util.*;

/**
 * Pre-HUSPM-adapt: incremental baseline adapted from
 * "High-utility sequential pattern mining in incremental database"
 * by Yan, Li, Hsieh and Wu (The Journal of Supercomputing 81:81, 2025).
 *
 * <p>The pre-large buffer is implemented exactly as in the cited paper:
 *
 * <ol>
 *   <li>safety value {@code f = (Su − Sl) / (1 − Su) × TSUD_atRescan},
 *       derived from Theorems 1 and 2;</li>
 *   <li>for every incoming batch {@code d}, compute {@code TSUd = Σ SU(Sq)};</li>
 *   <li>if {@code (buf + TSUd) ≤ f}, update {@code LSWU ∪ PSWU} through the
 *       sub-procedure and accumulate {@code buf += TSUd}; otherwise re-mine
 *       the union {@code D ∪ d}, reset the buffer and update
 *       {@code TSUD_atRescan = TSUU}. The buffer must accumulate the TOTAL
 *       delta utility (not the per-batch maximum sequence utility): a
 *       pattern's iutil can grow by the utility of every delta sequence that
 *       contains it, so any smaller accumulator under-triggers the rescan and
 *       breaks the exactness guarantee of the pre-large concept;</li>
 *   <li>HUSP test: {@code suU(S) / TSUU ≥ Su}.</li>
 * </ol>
 *
 * <p>The algorithm is adapted for the HAUSP problem (high <em>average</em>
 * utility instead of high utility). Concretely, the threshold becomes
 * {@code iutil(S)/|S| ≥ Su × TSUU}, the upper bound is tightened from SWU to
 * {@code PEAU = max(iutil + rutil)}, and the sub-procedure accumulates
 * delta-contributions on the existing trie nodes without resetting them.
 *
 * <p>Implementation notes:
 *
 * <ul>
 *   <li>{@code TSUD_atRescan} and {@code liveTSU} are kept separate so the
 *       snapshot value is only refreshed on a rescan, as required by the
 *       paper.</li>
 *   <li>The sub-procedure accumulates onto existing nodes; it does not reset
 *       per-node statistics.</li>
 *   <li>Sequences are looked up through {@code Int2ObjectOpenHashMap<Sequence>}
 *       because identifiers are not guaranteed to be dense.</li>
 *   <li>After the no-rescan path the trie is reclassified into Large and
 *       Pre-large because the thresholds shift when {@code TSUU} grows.</li>
 *   <li>The HUSP test consumes {@code liveTSU} (current {@code TSUU}) rather
 *       than the snapshot.</li>
 * </ul>
 */
public class Pre_HUSPM_adapt {

    public boolean enableIO = false;
    private double minUtilPercentage; // Su (upper utility threshold)
    private double mu;                // Sl = Su × (1 − mu); relative gap between Su and Sl.
    private String datasetName;

    // ── BIẾN TRẠNG THÁI INCREMENTAL (paper-faithful) ──────────────────────────────────
    private long TSUD_atRescan = 0;   // TSU snapshot at the last rescan (immutable between rescans).
    private long liveTSU = 0;         // Current cumulative TSUU; refreshed at every batch.
    private long bufTSUd = 0;         // buf = Σ TSUd accumulated since the most recent rescan (Theorem 2, sound trigger)

    // Cumulative database indexed by sid (sids are not assumed to be dense).
    private final List<Sequence> cumulativeDB = new ArrayList<>();
    private final Int2ObjectOpenHashMap<Sequence> seqById = new Int2ObjectOpenHashMap<>();

    // ── CẤU TRÚC TRIE LƯU LSWU ∪ PSWU ──────────────────────────────────────────────────
    static class TrieNode {
        long totalIutil = 0;     // Σ iutil cumulative — = suU(S) cho HUSP test
        long totalPEAU = 0;      // Σ PEAU bound (iutil + rutil); consulted during mining only.
        int support = 0;         // number of distinct sids containing S
        boolean isPreLarge = false; // true while the node lives in LSWU ∪ PSWU.

        Int2ObjectOpenHashMap<TrieNode> iChildren = new Int2ObjectOpenHashMap<>(4);
        Int2ObjectOpenHashMap<TrieNode> sChildren = new Int2ObjectOpenHashMap<>(4);
    }
    private TrieNode rootTrie = new TrieNode();

    // ── FLAT STRUCTURES (per-sequence cache) ──────────────────────────────────────────
    private final Int2ObjectOpenHashMap<long[]> globalRutilTables = new Int2ObjectOpenHashMap<>();
    private final Int2ObjectOpenHashMap<int[]> globalOffsets = new Int2ObjectOpenHashMap<>();
    private final Int2ObjectOpenHashMap<int[]> globalFlatToTid = new Int2ObjectOpenHashMap<>();

    // ── BUFFERS HOT-PATH ──────────────────────────────────────────────────────────────
    private long[] maxT_buffer = new long[8192];
    private int[] modifiedT = new int[8192];
    private long[] maxK_buffer = new long[16384];
    private int[] modifiedK = new int[16384];
    private boolean[] swuSeenBuf = new boolean[10000];
    private int[] swuSeenListBuf = new int[10000];

    // ── METRICS ───────────────────────────────────────────────────────────────────────
    private long hauspCount = 0;
    private long candidateCount = 0;
    private long prunedL2 = 0;
    private double peakMemory = 0;

    // Utility list (2-array form, aligned with the AU-DUL layout).
    static final class UtilityList {
        int itemSize;
        LongArrayList eLocs = new LongArrayList();   // sid<<32 | flatIdx
        LongArrayList eIutils = new LongArrayList();

        private int lastAggSid = -1;
        private long runMaxIutil = 0, runMaxPEAU = 0;
        private long aggTotalIutil = 0, aggTotalPEAU = 0;
        private int aggSupportCount = 0;

        public UtilityList(int itemSize) { this.itemSize = itemSize; }

        public void addElement(int sid, int flatIdx, long iutil, long rutil) {
            eLocs.add(((long) sid << 32) | (flatIdx & 0xFFFFFFFFL));
            eIutils.add(iutil);
            long currentPEAU = iutil + rutil;

            if (sid != lastAggSid) {
                if (lastAggSid != -1) {
                    aggTotalIutil += runMaxIutil;
                    aggTotalPEAU += runMaxPEAU;
                    aggSupportCount++;
                }
                lastAggSid = sid;
                runMaxIutil = iutil;
                runMaxPEAU = currentPEAU;
            } else {
                if (iutil > runMaxIutil) runMaxIutil = iutil;
                if (currentPEAU > runMaxPEAU) runMaxPEAU = currentPEAU;
            }
        }

        public long[] evaluate() {
            if (lastAggSid == -1) return new long[]{0, 0, 0};
            return new long[]{
                    aggTotalIutil + runMaxIutil,
                    aggTotalPEAU + runMaxPEAU,
                    aggSupportCount + 1
            };
        }
    }

    // ── CONSTRUCTOR ──────────────────────────────────────────────────────────────────
    public Pre_HUSPM_adapt(String configPath) {
        Properties prop = new Properties();
        try (FileInputStream fis = new FileInputStream(configPath)) {
            prop.load(fis);
            this.minUtilPercentage = Double.parseDouble(prop.getProperty("minUtil.percentage", "0.01"));
            this.mu = Double.parseDouble(prop.getProperty("mu.ratio", "0.1"));
            this.datasetName = new File(prop.getProperty("dataset.path", "unknown")).getName().replace("_seq.txt", "");
        } catch (IOException e) {
            e.printStackTrace();
        }
        Arrays.fill(maxT_buffer, -1L);
        Arrays.fill(maxK_buffer, -1L);
    }

    /** Update the minimum-utility threshold at runtime (used by the threshold sweep). */
    public void setConfig(double minUtil) {
        this.minUtilPercentage = minUtil;
    }

    // ====================================================================================
    // processBatch: entry point invoked once per incremental batch.
    // ====================================================================================
    public RunResult processBatch(List<Sequence> deltaBatch, int batchId) {
        // CPU time at nanosecond resolution, excluding GC pauses.
        long startTimeNs = RunIsolation.cpuTimeNs();
        long startScanNs = RunIsolation.cpuTimeNs();
        hauspCount = 0; candidateCount = 0; prunedL2 = 0;
        peakMemory = 0; // reset per batch so the peak does not accumulate across batches

        // Step 1: scan the delta, compute TSUd, append to the cumulative database.
        long tsu_d = 0;
        for (Sequence seq : deltaBatch) {
            tsu_d += seq.totalUtility;
            cumulativeDB.add(seq);
            seqById.put(seq.sid, seq);
        }
        liveTSU += tsu_d; // TSUU is refreshed at every batch.

        // ── STEP 2: build flat structures for the new sequences ──────────────────────
        prepareFlatStructures(deltaBatch);

        // Step 3: compute the safety value f and decide whether to rescan.
        double su = minUtilPercentage;
        double sl = Math.max(0.0001, minUtilPercentage * (1.0 - mu));

        // [PAPER Theorem 2] f = (Su − Sl)/(1 − Su) × TSUD_atRescan
        // Rescan when this is the first batch or when (buf + TSUd) > f.
        boolean needRescan;
        if (batchId == 0 || TSUD_atRescan == 0) {
            needRescan = true;
        } else {
            double f = ((su - sl) / Math.max(1e-12, 1.0 - su)) * TSUD_atRescan;
            needRescan = (bufTSUd + tsu_d) > f;
        }

        long tScan = (RunIsolation.cpuTimeNs() - startScanNs) / 1_000_000L;
        long startMiningNs = RunIsolation.cpuTimeNs();

        // Step 4: execute the chosen branch.
        if (needRescan) {
            // [PAPER Algorithm 3 — Rescan-Mining]
            TSUD_atRescan = liveTSU;     // fresh snapshot
            bufTSUd = 0;                 // reset buffer
            rootTrie = new TrieNode();   // discard the previous trie
            mineFromScratch(cumulativeDB, liveTSU, su, sl);
        } else {
            // [PAPER Algorithm 1 lines 6-18 + Algorithm 2 Sub-Procedure]
            bufTSUd += tsu_d;
            // Accumulate the delta contribution into the existing trie; do not reset.
            updateExistingPatterns(deltaBatch);
            // Reclassify nodes against (Sl, Su) × TSUU = liveTSU.
            reclassifyTrie(rootTrie, su, sl, liveTSU, 0);
        }

        // Step 5: count HAUSP patterns.
        // HUSP test against the current TSUU: iutil/|S| ≥ Su × liveTSU.
        long[] finalMetrics = countHAUSPInTrie(rootTrie, su * liveTSU, 0);
        hauspCount = finalMetrics[0];
        long shausActiveCount = finalMetrics[1];

        long tMining = (RunIsolation.cpuTimeNs() - startMiningNs) / 1_000_000L;
        peakMemory = Math.max(peakMemory,
                (Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory()) / (1024.0 * 1024.0));

        return buildRunResult(batchId, startTimeNs, tScan, tMining, shausActiveCount);
    }

    // ====================================================================================
    // FLAT STRUCTURES — per-sequence cache, build once
    // ====================================================================================
    private void prepareFlatStructures(List<Sequence> deltaBatch) {
        for (Sequence seq : deltaBatch) {
            if (globalRutilTables.containsKey(seq.sid)) continue;
            int n = seq.itemsets.size();
            int[] offsets = new int[n];
            int total = 0;
            for (int t = 0; t < n; t++) {
                offsets[t] = total;
                total += seq.itemsets.get(t).items.size();
            }
            globalOffsets.put(seq.sid, offsets);
            globalRutilTables.put(seq.sid, getRUtilTableFlat(seq, offsets, total));

            int[] flatToTid = new int[total];
            for (int t = 0; t < n; t++) {
                int off = offsets[t];
                int sz = seq.itemsets.get(t).items.size();
                for (int j = 0; j < sz; j++) flatToTid[off + j] = t;
            }
            globalFlatToTid.put(seq.sid, flatToTid);
        }
    }

    // ====================================================================================
    // RESCAN-MINING (Algorithm 3 of the paper): full mining over U = D ∪ d.
    // ====================================================================================
    private void mineFromScratch(List<Sequence> db, long totalUtil, double su, double sl) {
        double thresholdSl = sl * totalUtil;

        // Step 1: compute per-item SWU via the mask + dirty-list (zero allocations).
        long[] globalItemSWU = new long[10000];
        for (Sequence seq : db) {
            long suDB = seq.totalUtility;
            int seenCount = 0;
            for (Itemset is : seq.itemsets) {
                for (ItemQ it : is.items) {
                    if (it.id >= globalItemSWU.length) {
                        int newSize = it.id + 5000;
                        globalItemSWU = Arrays.copyOf(globalItemSWU, newSize);
                        if (it.id >= swuSeenBuf.length) {
                            swuSeenBuf = Arrays.copyOf(swuSeenBuf, newSize);
                            swuSeenListBuf = Arrays.copyOf(swuSeenListBuf, newSize);
                        }
                    }
                    if (!swuSeenBuf[it.id]) {
                        swuSeenBuf[it.id] = true;
                        swuSeenListBuf[seenCount++] = it.id;
                        globalItemSWU[it.id] += suDB;
                    }
                }
            }
            for (int i = 0; i < seenCount; i++) swuSeenBuf[swuSeenListBuf[i]] = false;
        }

        // Step 2: build 1-sequence utility lists for items with SWU ≥ Sl × TSUU (PSWU ∪ LSWU).
        Int2ObjectOpenHashMap<UtilityList> initialULs = new Int2ObjectOpenHashMap<>(16);
        for (Sequence seq : db) {
            long[] rutilFlat = globalRutilTables.get(seq.sid);
            int[] offsRoot = globalOffsets.get(seq.sid);
            int n = seq.itemsets.size();
            for (int t = 0; t < n; t++) {
                int offT = offsRoot[t];
                int sz = seq.itemsets.get(t).items.size();
                for (int k = 0; k < sz; k++) {
                    ItemQ it = seq.itemsets.get(t).items.get(k);
                    if (globalItemSWU[it.id] >= thresholdSl) {
                        int flatIdx = offT + k;
                        UtilityList ul = initialULs.computeIfAbsent(it.id, kk -> new UtilityList(1));
                        ul.addElement(seq.sid, flatIdx, it.utility, rutilFlat[flatIdx]);
                    }
                }
            }
        }

        // Step 3: recursive DFS mining under the tight HAUSP PEAU bound.
        int[] keys = initialULs.keySet().toIntArray();
        Arrays.sort(keys);
        for (int key : keys) {
            TrieNode child = new TrieNode();
            // 1-sequences are stored under rootTrie.sChildren by convention.
            rootTrie.sChildren.put(key, child);
            dfsFull(child, initialULs.get(key), thresholdSl);
        }
    }

    /** Deep DFS mining with Tier-2 PEAU pruning. */
    private void dfsFull(TrieNode node, UtilityList ul, double thresholdSl) {
        candidateCount++;
        long[] ev = ul.evaluate();
        node.totalIutil = ev[0];
        node.totalPEAU = ev[1];
        node.support = (int) ev[2];

        // The node itself is pre-large when its own average utility reaches Sl
        // at its own length |S|; this must be decided independently of the
        // extension bound, otherwise short boundary patterns (whose PEAU is
        // close to iutil) are wrongly dropped from the trie and the final
        // HAUSP count becomes incomplete — pre-large must stay exact.
        node.isPreLarge = node.totalIutil >= thresholdSl * ul.itemSize;

        // Extension pruning only: no descendant of length >= |S|+1 can reach Sl.
        if (node.totalPEAU < thresholdSl * (ul.itemSize + 1)) {
            prunedL2++;
            return;
        }

        // Generate i-extensions and s-extensions.
        Int2ObjectOpenHashMap<UtilityList> iExMap = new Int2ObjectOpenHashMap<>(8);
        Int2ObjectOpenHashMap<UtilityList> sExMap = new Int2ObjectOpenHashMap<>(8);
        projectExtensions(ul, iExMap, sExMap, /*restrictToTrie=*/null);

        int[] iKeys = iExMap.keySet().toIntArray();
        for (int key : iKeys) {
            TrieNode child = new TrieNode();
            node.iChildren.put(key, child);
            dfsFull(child, iExMap.get(key), thresholdSl);
            // Drop the child when it has been pruned and carries no descendants.
            if (!child.isPreLarge && child.iChildren.isEmpty() && child.sChildren.isEmpty()) {
                node.iChildren.remove(key);
            }
        }
        int[] sKeys = sExMap.keySet().toIntArray();
        for (int key : sKeys) {
            TrieNode child = new TrieNode();
            node.sChildren.put(key, child);
            dfsFull(child, sExMap.get(key), thresholdSl);
            if (!child.isPreLarge && child.iChildren.isEmpty() && child.sChildren.isEmpty()) {
                node.sChildren.remove(key);
            }
        }
    }

    // ====================================================================================
    // SUB-PROCEDURE (Algorithm 2): no-rescan path that accumulates the delta into the existing trie.
    // ====================================================================================
    private void updateExistingPatterns(List<Sequence> deltaBatch) {
        // Build 1-sequence utility lists from the delta, restricted to items already in the trie.
        Int2ObjectOpenHashMap<UtilityList> initialULs = new Int2ObjectOpenHashMap<>(8);
        for (Sequence seq : deltaBatch) {
            long[] rutilFlat = globalRutilTables.get(seq.sid);
            int[] offsRoot = globalOffsets.get(seq.sid);
            int n = seq.itemsets.size();
            for (int t = 0; t < n; t++) {
                int offT = offsRoot[t];
                int sz = seq.itemsets.get(t).items.size();
                for (int k = 0; k < sz; k++) {
                    ItemQ it = seq.itemsets.get(t).items.get(k);
                    // Per the paper, only update S ∈ LSWUD ∪ PSWUD; do not introduce new patterns.
                    if (rootTrie.sChildren.containsKey(it.id)) {
                        int flatIdx = offT + k;
                        UtilityList ul = initialULs.computeIfAbsent(it.id, kk -> new UtilityList(1));
                        ul.addElement(seq.sid, flatIdx, it.utility, rutilFlat[flatIdx]);
                    }
                }
            }
        }

        // dfsRestricted accumulates onto existing nodes; it must not reset them.
        int[] keys = initialULs.keySet().toIntArray();
        for (int key : keys) {
            TrieNode child = rootTrie.sChildren.get(key);
            if (child != null) dfsRestricted(child, initialULs.get(key));
        }
    }

    /** Restricted DFS: descend the existing trie only, accumulating evidence from the delta. */
    private void dfsRestricted(TrieNode node, UtilityList ul) {
        long[] ev = ul.evaluate();
        node.totalIutil += ev[0];
        node.totalPEAU += ev[1];
        node.support += (int) ev[2];

        if (node.iChildren.isEmpty() && node.sChildren.isEmpty()) return;

        Int2ObjectOpenHashMap<UtilityList> iExMap = new Int2ObjectOpenHashMap<>(4);
        Int2ObjectOpenHashMap<UtilityList> sExMap = new Int2ObjectOpenHashMap<>(4);
        projectExtensions(ul, iExMap, sExMap, node);

        int[] iKeys = iExMap.keySet().toIntArray();
        for (int key : iKeys) {
            TrieNode child = node.iChildren.get(key);
            if (child != null) dfsRestricted(child, iExMap.get(key));
        }
        int[] sKeys = sExMap.keySet().toIntArray();
        for (int key : sKeys) {
            TrieNode child = node.sChildren.get(key);
            if (child != null) dfsRestricted(child, sExMap.get(key));
        }
    }

    // ====================================================================================
    // Extension generation shared by dfsFull and dfsRestricted.
    // restrictNode == null: generate every extension; restrictNode != null: only generate
    // extensions whose key already exists in the restriction's children.
    // ====================================================================================
    private void projectExtensions(UtilityList ul,
                                   Int2ObjectOpenHashMap<UtilityList> iExMap,
                                   Int2ObjectOpenHashMap<UtilityList> sExMap,
                                   TrieNode restrictNode) {
        int size = ul.eLocs.size();
        int p = 0;
        while (p < size) {
            int sid = (int) (ul.eLocs.getLong(p) >>> 32);
            int startP = p;
            while (p < size && (int) (ul.eLocs.getLong(p) >>> 32) == sid) p++;

            // Look up by sid through the hashmap; do not rely on list-index ordering.
            Sequence seq = seqById.get(sid);
            if (seq == null) continue;
            long[] rutilFlat = globalRutilTables.get(sid);
            int[] offsets = globalOffsets.get(sid);
            int[] flatToTid = globalFlatToTid.get(sid);
            if (rutilFlat == null || offsets == null || flatToTid == null) continue;

            int n = seq.itemsets.size();
            int totalItems = rutilFlat.length;

            // Make sure the buffers are large enough.
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

            // Step 1: scatter iutil of (S, sid) into the maxT/maxK buffers.
            int modTCount = 0, modKCount = 0;
            for (int i = startP; i < p; i++) {
                int flatIdx = (int) ul.eLocs.getLong(i);
                int tid = flatToTid[flatIdx];
                long iu = ul.eIutils.getLong(i);

                if (tid + 1 < n) {
                    if (maxT_buffer[tid + 1] == -1L) {
                        modifiedT[modTCount++] = tid + 1;
                        maxT_buffer[tid + 1] = iu;
                    } else if (iu > maxT_buffer[tid + 1]) {
                        maxT_buffer[tid + 1] = iu;
                    }
                }
                int nextFlat = flatIdx + 1;
                if (nextFlat < totalItems && flatToTid[nextFlat] == tid) {
                    if (maxK_buffer[nextFlat] == -1L) {
                        modifiedK[modKCount++] = nextFlat;
                        maxK_buffer[nextFlat] = iu;
                    } else if (iu > maxK_buffer[nextFlat]) {
                        maxK_buffer[nextFlat] = iu;
                    }
                }
            }

            // Step 2: scan the sequence left-to-right and emit i-/s-extensions.
            long maxSPrefix = -1;
            for (int t = 0; t < n; t++) {
                if (maxT_buffer[t] > maxSPrefix) maxSPrefix = maxT_buffer[t];
                Itemset is = seq.itemsets.get(t);
                long currentMaxI = -1;
                int sz = is.items.size();
                for (int k = 0; k < sz; k++) {
                    int flatIdx = offsets[t] + k;
                    if (maxK_buffer[flatIdx] > currentMaxI) currentMaxI = maxK_buffer[flatIdx];
                    ItemQ it = is.items.get(k);

                    if (currentMaxI != -1L) {
                        if (restrictNode == null || restrictNode.iChildren.containsKey(it.id)) {
                            iExMap.computeIfAbsent(it.id, kk -> new UtilityList(ul.itemSize + 1))
                                    .addElement(sid, flatIdx, currentMaxI + it.utility, rutilFlat[flatIdx]);
                        }
                    }
                    if (t > 0 && maxSPrefix != -1L) {
                        if (restrictNode == null || restrictNode.sChildren.containsKey(it.id)) {
                            sExMap.computeIfAbsent(it.id, kk -> new UtilityList(ul.itemSize + 1))
                                    .addElement(sid, flatIdx, maxSPrefix + it.utility, rutilFlat[flatIdx]);
                        }
                    }
                }
            }

            // Reset buffer dirty
            for (int i = 0; i < modTCount; i++) maxT_buffer[modifiedT[i]] = -1L;
            for (int i = 0; i < modKCount; i++) maxK_buffer[modifiedK[i]] = -1L;
        }
    }

    // ====================================================================================
    // Reclassification step of Algorithm 2: after a no-rescan, reclassify trie nodes.
    // ====================================================================================
    /**
     * Reclassify every node against Sl × TSUU and Su × TSUU using iutil / |S|.
     * - iutil/|S| ≥ Su × TSUU  ⇒ Large (HUSP candidate)
     * - Sl × TSUU ≤ iutil/|S| < Su × TSUU ⇒ Pre-large
     * - Otherwise, mark as small (isPreLarge = false); the next rescan may drop the node.
     *
     * The HAUSP variant uses iutil / |S| (average utility) instead of su(S) / TSUU.
     */
    private void reclassifyTrie(TrieNode node, double su, double sl, long TSUU, int depth) {
        if (depth > 0) {
            double avgU = (double) node.totalIutil / depth;
            double normSWU = avgU / Math.max(1.0, TSUU);
            node.isPreLarge = (normSWU >= sl);
        }
        for (TrieNode child : node.iChildren.values()) reclassifyTrie(child, su, sl, TSUU, depth + 1);
        for (TrieNode child : node.sChildren.values()) reclassifyTrie(child, su, sl, TSUU, depth + 1);
    }

    // ====================================================================================
    // HUSP COUNT (Algorithm 1 lines 24-27)
    // ====================================================================================
    /**
     * HAUSP count: iutil / |S| ≥ Su × TSUU iff totalIutil ≥ Su × TSUU × |S|.
     * Returns {@code [hauspCount, totalActivePatterns]}.
     */
    private long[] countHAUSPInTrie(TrieNode node, double thresholdSuxTSUU, int depth) {
        long[] counts = new long[]{0, 0};
        if (depth > 0) {
            counts[1] = 1;
            if (node.totalIutil >= thresholdSuxTSUU * depth) counts[0] = 1;
        }
        for (TrieNode child : node.iChildren.values()) {
            long[] r = countHAUSPInTrie(child, thresholdSuxTSUU, depth + 1);
            counts[0] += r[0]; counts[1] += r[1];
        }
        for (TrieNode child : node.sChildren.values()) {
            long[] r = countHAUSPInTrie(child, thresholdSuxTSUU, depth + 1);
            counts[0] += r[0]; counts[1] += r[1];
        }
        return counts;
    }

    // ====================================================================================
    // RUTIL FLAT TABLE — index: offsets[t] + k
    // ====================================================================================
    private long[] getRUtilTableFlat(Sequence seq, int[] offsets, int totalItems) {
        int n = seq.itemsets.size();
        long[] table = new long[totalItems];
        long running = 0;
        for (int t = n - 1; t >= 0; t--) {
            int off = offsets[t];
            int sz = seq.itemsets.get(t).items.size();
            for (int k = sz - 1; k >= 0; k--) {
                table[off + k] = running;
                running += seq.itemsets.get(t).items.get(k).utility;
            }
        }
        return table;
    }

    // ====================================================================================
    // RESULT BUILD
    // ====================================================================================
    private RunResult buildRunResult(int bId, long startNs, long tScan, long tMining, long shausActive) {
        RunResult res = new RunResult();
        res.timestamp = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date());
        res.algorithm = "Pre-HAUSPM";
        res.dataset = datasetName;
        res.batchID = bId;
        res.minUtil = minUtilPercentage;
        res.mu = mu;
        res.totalDBUtility = liveTSU;
        res.cumulativeDBSize = cumulativeDB.size();
        res.tScan = tScan;
        res.tMining = tMining;
        res.tTotal = (RunIsolation.cpuTimeNs() - startNs) / 1_000_000L;
        res.numCand = candidateCount;
        res.numPrunedL2 = prunedL2;
        res.hauspFound = hauspCount;
        res.shausActive = shausActive;
        res.memPeak = peakMemory;
        return res;
    }
}
