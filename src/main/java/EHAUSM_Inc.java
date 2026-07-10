import it.unimi.dsi.fastutil.ints.Int2ObjectMap;
import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.ints.IntArrayList;
import it.unimi.dsi.fastutil.longs.LongArrayList;
import java.io.*;
import java.text.SimpleDateFormat;
import java.util.*;

/**
 * EHAUSM-Inc: incremental baseline reimplemented from the EHAUSM framework.
 *
 * <p>The algorithm maintains utility information across batches and uses PEAU
 * as its sole upper bound. PEAU is computed as the maximum of
 * {@code iutil + rutil} per occurrence rather than the sum of two independent
 * maxima, which keeps the bound tight enough to match the original baseline.
 * The pre-large buffer is intentionally disabled so that all pruning decisions
 * rely on the absolute threshold, isolating the contribution of the
 * single-bound architecture.
 */
public class EHAUSM_Inc {
    public boolean enableIO = false;
    /** Gate per-candidate tightness division; disable for pure benchmarking. */
    public boolean enableTightnessMetric = true;
    // Flat 1-D rutil storage; avoids the per-row header overhead of long[][].
    private final Int2ObjectOpenHashMap<long[]> globalRutilTables = new Int2ObjectOpenHashMap<>();
    private final Int2ObjectOpenHashMap<Sequence> globalDatabase = new Int2ObjectOpenHashMap<>();
    private final Int2ObjectOpenHashMap<int[]> globalOffsets = new Int2ObjectOpenHashMap<>();
    // flatToTid[flatIdx] = tid; lets us derive tid from flatIdx in O(1).
    private final Int2ObjectOpenHashMap<int[]> globalFlatToTid = new Int2ObjectOpenHashMap<>();

    private long[] maxT_buffer = new long[8192];
    private int[] modifiedT = new int[8192];
    private long[] maxK_buffer = new long[16384];
    private int[] modifiedK = new int[16384];

    // Mask + dirty-list for the SWU scan; replaces a HashSet<Integer> to avoid boxing.
    private boolean[] swuSeenBuf = new boolean[10000];
    private int[] swuSeenListBuf = new int[10000];

    static final class PatternKey {
        final int[] items; final int hash;
        PatternKey(int[] items) { this.items = items; this.hash = Arrays.hashCode(items); }
        PatternKey iExtend(int item) {
            int[] next = Arrays.copyOf(items, items.length + 1);
            next[next.length - 1] = item; return new PatternKey(next);
        }
        PatternKey sExtend(int item) {
            int[] next = Arrays.copyOf(items, items.length + 2);
            next[next.length - 2] = -1; next[next.length - 1] = item; return new PatternKey(next);
        }
        @Override public boolean equals(Object obj) {
            if (this == obj) return true;
            if (!(obj instanceof PatternKey)) return false;
            return Arrays.equals(this.items, ((PatternKey) obj).items);
        }
        @Override public int hashCode() { return hash; }
        @Override public String toString() {
            StringBuilder sb = new StringBuilder("<(");
            for (int i = 0; i < items.length; i++) {
                if (items[i] == -1) sb.append(")(");
                else { if (i > 0 && items[i - 1] != -1) sb.append(","); sb.append(items[i]); }
            }
            return sb.append(")>").toString();
        }
    }

    // 2-array utility-list following the AU-DUL layout of HAUSP_UB.
    //   Old: eSids(int) + eTids(int) + eIdxs(int) + eIutils(long) + eRutils(long) = 28B/element
    //   New: eLocs(long packing sid<<32|flatIdx) + eIutils(long)                  = 16B/element (~43% less)
    //   tid is derived through flatToTid[flatIdx]; rutil is looked up in
    //   globalRutilTables[sid][flatIdx] only when needed (inside append, not on the hot path).
    static final class UtilityList {
        int itemSize;
        int oldSize = 0;

        LongArrayList eLocs = new LongArrayList();    // sid<<32 | flatIdx
        LongArrayList eIutils = new LongArrayList();

        private int lastAggSid = -1;
        private long runMaxI = 0, runMaxPEAU = 0;
        private long aggTotalI = 0, aggTotalPEAU = 0;
        private int aggSupport = 0;

        public UtilityList(int itemSize) { this.itemSize = itemSize; }

        public void addElement(int sid, int flatIdx, long iutil, long rutil) {
            eLocs.add(((long) sid << 32) | (flatIdx & 0xFFFFFFFFL));
            eIutils.add(iutil);

            long currentPEAU = iutil + rutil;

            if (sid != lastAggSid) {
                if (lastAggSid != -1) {
                    aggTotalI += runMaxI; aggTotalPEAU += runMaxPEAU; aggSupport++;
                }
                lastAggSid = sid; runMaxI = iutil; runMaxPEAU = currentPEAU;
            } else {
                if (iutil > runMaxI) runMaxI = iutil;
                if (currentPEAU > runMaxPEAU) runMaxPEAU = currentPEAU;
            }
        }

        public void append(UtilityList other, Int2ObjectOpenHashMap<long[]> rutilTablesMap) {
            int sz = other.eLocs.size();
            for (int i = 0; i < sz; i++) {
                long packed = other.eLocs.getLong(i);
                int sid = (int) (packed >>> 32);
                int flatIdx = (int) packed;
                long rutil = rutilTablesMap.get(sid)[flatIdx];
                this.addElement(sid, flatIdx, other.eIutils.getLong(i), rutil);
            }
        }

        public void trimToSize() { eLocs.trim(); eIutils.trim(); }

        public void markOld() { this.oldSize = eLocs.size(); }

        public long[] evaluate() {
            if (lastAggSid == -1) return new long[]{0, 0, 0};
            return new long[]{aggTotalI + runMaxI, aggTotalPEAU + runMaxPEAU, aggSupport + 1};
        }

        // Hot-path helpers that decode sid/flatIdx from a packed eLocs[i] long.
        public int getSid(int i) { return (int) (eLocs.getLong(i) >>> 32); }
        public int getFlatIdx(int i) { return (int) eLocs.getLong(i); }
    }

    static final class IncNode {
        PatternKey key;
        UtilityList ul;
        // Lazy initialisation; leaves keep these null and allocate on first child.
        Int2ObjectOpenHashMap<IncNode> iEx;
        Int2ObjectOpenHashMap<IncNode> sEx;

        IncNode(PatternKey key, UtilityList ul) { this.key = key; this.ul = ul; }

        Int2ObjectOpenHashMap<IncNode> ensureIEx() {
            if (iEx == null) iEx = new Int2ObjectOpenHashMap<>(4);
            return iEx;
        }
        Int2ObjectOpenHashMap<IncNode> ensureSEx() {
            if (sEx == null) sEx = new Int2ObjectOpenHashMap<>(4);
            return sEx;
        }
    }

    private final Int2ObjectOpenHashMap<IncNode> rootNodes = new Int2ObjectOpenHashMap<>();

    private double minUtilPercentage, mu;
    private long totalDBUtility = 0;
    private String datasetName;
    private long[] globalItemSWU = new long[10000];
    private int maxItemIdEver = 0;

    private long hauspCount, candidateCount, prunedL1, prunedL2;
    private double sumTightnessPEAU = 0.0;
    private long countTightnessPEAU = 0;
    private int currentBatchId = 0;

    public EHAUSM_Inc(String configPath) {
        Properties prop = new Properties();
        try (FileInputStream fis = new FileInputStream(configPath)) {
            prop.load(fis);
            this.minUtilPercentage = Double.parseDouble(prop.getProperty("minUtil.percentage", "0.01"));
            this.mu = Double.parseDouble(prop.getProperty("mu.ratio", "0.1"));
            this.datasetName = new File(prop.getProperty("dataset.path", "unknown")).getName().replace("_seq.txt", "");
        } catch (IOException e) { e.printStackTrace(); }
        Arrays.fill(maxT_buffer, -1L);
        Arrays.fill(maxK_buffer, -1L);
    }

    public void setConfig(double minUtil) {
        this.minUtilPercentage = minUtil;
    }

    public RunResult processBatch(List<Sequence> deltaBatch, int batchId) {
        this.currentBatchId = batchId;
        hauspCount = 0; candidateCount = 0; prunedL1 = 0; prunedL2 = 0;
        sumTightnessPEAU = 0.0; countTightnessPEAU = 0;
        // CPU time at nanosecond resolution, excluding GC pauses.
        long startTimeNs = RunIsolation.cpuTimeNs();
        long startScanNs = RunIsolation.cpuTimeNs();

        int firstSidOfDelta = deltaBatch.isEmpty() ? Integer.MAX_VALUE : deltaBatch.get(0).sid;

        for (Sequence seq : deltaBatch) {
            totalDBUtility += seq.totalUtility;
            globalDatabase.put(seq.sid, seq);

            // Build offsets first, then fill the 1-D rutil array using those offsets.
            int n = seq.itemsets.size();
            int[] offsets = new int[n];
            int totalItems = 0;
            for (int t = 0; t < n; t++) {
                offsets[t] = totalItems;
                totalItems += seq.itemsets.get(t).items.size();
            }
            globalOffsets.put(seq.sid, offsets);
            globalRutilTables.put(seq.sid, getRUtilTableFlat(seq, offsets, totalItems));

            // Build flatToTid[] in parallel so the utility-list only stores (sid, flatIdx).
            int[] flatToTid = new int[totalItems];
            for (int t = 0; t < n; t++) {
                int off = offsets[t];
                int sz = seq.itemsets.get(t).items.size();
                for (int j = 0; j < sz; j++) flatToTid[off + j] = t;
            }
            globalFlatToTid.put(seq.sid, flatToTid);

            // Scan SWU through the mask + dirty-list.
            int seenCount = 0;
            long su = seq.totalUtility;
            for (Itemset is : seq.itemsets) {
                for (ItemQ item : is.items) {
                    int id = item.id;
                    if (id > maxItemIdEver) {
                        maxItemIdEver = id;
                        if (id >= globalItemSWU.length) {
                            int newLen = id + 5000;
                            globalItemSWU = Arrays.copyOf(globalItemSWU, newLen);
                            swuSeenBuf = Arrays.copyOf(swuSeenBuf, newLen);
                            swuSeenListBuf = Arrays.copyOf(swuSeenListBuf, newLen);
                        }
                    }
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

        for (int i = 0; i <= maxItemIdEver; i++) {
            if (globalItemSWU[i] > 0 && globalItemSWU[i] < threshold) {
                prunedL1++;
            }
        }

        Int2ObjectOpenHashMap<UtilityList> deltaRoots = new Int2ObjectOpenHashMap<>();
        for (Sequence seq : deltaBatch) {
            // 1-D layout: index = offsets[t] + k instead of rutilTable[t][k].
            long[] rutilFlat = globalRutilTables.get(seq.sid);
            int[] offsRoot = globalOffsets.get(seq.sid);
            for (int t = 0; t < seq.itemsets.size(); t++) {
                Itemset is = seq.itemsets.get(t);
                int offT = offsRoot[t];
                for (int k = 0; k < is.items.size(); k++) {
                    ItemQ item = is.items.get(k);
                    if (globalItemSWU[item.id] >= threshold) {
                        // addElement signature: (sid, flatIdx, iutil, rutil).
                        int flatIdx = offT + k;
                        deltaRoots.computeIfAbsent(item.id, key -> new UtilityList(1))
                                .addElement(seq.sid, flatIdx, item.utility, rutilFlat[flatIdx]);
                    }
                }
            }
        }

        // Single-pass dispatch over the surviving items.
        Int2ObjectOpenHashMap<UtilityList> newItemULs = null;
        boolean[] isNewItem = null;
        for (Int2ObjectMap.Entry<UtilityList> entry : deltaRoots.int2ObjectEntrySet()) {
            int item = entry.getIntKey();
            if (rootNodes.get(item) == null) {
                if (newItemULs == null) {
                    newItemULs = new Int2ObjectOpenHashMap<>();
                    isNewItem = new boolean[maxItemIdEver + 1];
                }
                if (item < isNewItem.length) isNewItem[item] = true;
                newItemULs.put(item, new UtilityList(1));
            }
        }

        if (newItemULs != null) {
            int[] sids = globalDatabase.keySet().toIntArray();
            Arrays.sort(sids);
            for (int sid : sids) {
                if (sid >= firstSidOfDelta) break;
                Sequence seq = globalDatabase.get(sid);
                // 1-D flat rutil.
                long[] rtFlat = globalRutilTables.get(sid);
                int[] offsScan = globalOffsets.get(sid);
                int sn = seq.itemsets.size();
                for (int t = 0; t < sn; t++) {
                    Itemset is = seq.itemsets.get(t);
                    int isSize = is.items.size();
                    int offT = offsScan[t];
                    for (int k = 0; k < isSize; k++) {
                        ItemQ it = is.items.get(k);
                        int id = it.id;
                        if (id < isNewItem.length && isNewItem[id]) {
                            // addElement(sid, flatIdx, iutil, rutil).
                            int flatIdx = offT + k;
                            newItemULs.get(id).addElement(sid, flatIdx, it.utility, rtFlat[flatIdx]);
                        }
                    }
                }
            }
        }

        for (Int2ObjectMap.Entry<UtilityList> entry : deltaRoots.int2ObjectEntrySet()) {
            int item = entry.getIntKey();
            IncNode root = rootNodes.get(item);
            if (root != null) {
                root.ul.append(entry.getValue(), globalRutilTables);
            } else {
                UtilityList oldUl = (newItemULs != null) ? newItemULs.get(item) : new UtilityList(1);
                if (oldUl == null) oldUl = new UtilityList(1);
                oldUl.markOld();
                oldUl.append(entry.getValue(), globalRutilTables);
                rootNodes.put(item, new IncNode(new PatternKey(new int[]{item}), oldUl));
            }
        }
        long tScan = (RunIsolation.cpuTimeNs() - startScanNs) / 1_000_000L;

        long startMiningNs = RunIsolation.cpuTimeNs();
        String outFileName = "out/EHAUSM_Inc_" + datasetName + "_B" + batchId + ".txt";
        new File("out").mkdirs();

        BufferedWriter writer = null;
        try {
            if (this.enableIO) {
                writer = new BufferedWriter(new FileWriter(outFileName), 65536);
                writer.write("# EHAUSM-Inc: incremental baseline (no pre-large buffer).\n");
            }

            int[] rKeys = rootNodes.keySet().toIntArray();
            Arrays.sort(rKeys);
            for (int item : rKeys) {
                IncNode root = rootNodes.get(item);
                boolean keep = miningDFS(root, threshold, writer);
                if (!keep) rootNodes.remove(item);
            }
        } catch (IOException e) { e.printStackTrace();
        } finally {
            if (writer != null) { try { writer.close(); } catch (IOException ignored) {} }
        }

        for (IncNode root : rootNodes.values()) markOldTree(root);

        long tMining = (RunIsolation.cpuTimeNs() - startMiningNs) / 1_000_000L;
        return buildRunResult(batchId, startTimeNs, tScan, tMining,
                (double) deltaBatch.size() / globalDatabase.size());
    }

    private boolean miningDFS(IncNode currentNode, double threshold, BufferedWriter writer) throws IOException {
        UtilityList ul = currentNode.ul;
        candidateCount++;

        long[] ev = ul.evaluate();
        long totalIutil = ev[0], exactPEAU = ev[1];
        int support = (int) ev[2];

        double actualAU = (double) totalIutil / ul.itemSize;

        // Gate the tightness metric.
        if (enableTightnessMetric) {
            double boundAU = (double) exactPEAU / (ul.itemSize + 1);
            if (boundAU > 0) { sumTightnessPEAU += (actualAU / boundAU); countTightnessPEAU++; }
        }

        double reqHAUSP = threshold * ul.itemSize;
        double reqExtend = threshold * (ul.itemSize + 1);

        boolean isHAUSP = (totalIutil >= reqHAUSP);
        if (isHAUSP) {
            hauspCount++;
            if (this.enableIO && writer != null) {
                writer.write(currentNode.key.toString() + "\t" + String.format(Locale.US, "%.2f", actualAU) + "\t" + support + "\n");
            }
        }

        if (exactPEAU < reqExtend) {
            prunedL2++;
            // Null-check before clearing to avoid a NullPointerException on lazy slots.
            if (currentNode.iEx != null) currentNode.iEx.clear();
            if (currentNode.sEx != null) currentNode.sEx.clear();
            return isHAUSP;
        }

        ul.trimToSize();
        generateExtensions(currentNode, threshold, writer);
        return true;
    }

    private void generateExtensions(IncNode parent, double threshold, BufferedWriter writer) throws IOException {
        UtilityList ul = parent.ul;

        Int2ObjectOpenHashMap<UtilityList> deltaIExMap = new Int2ObjectOpenHashMap<>();
        Int2ObjectOpenHashMap<UtilityList> deltaSExMap = new Int2ObjectOpenHashMap<>();
        projectList(ul, ul.oldSize, ul.eLocs.size(), deltaIExMap, deltaSExMap, threshold);

        // Null-check before iterating the previous-iteration map.
        if (parent.iEx != null) {
            for (Int2ObjectMap.Entry<IncNode> entry : parent.iEx.int2ObjectEntrySet()) {
                UtilityList dUl = deltaIExMap.remove(entry.getIntKey());
                if (dUl != null) entry.getValue().ul.append(dUl, globalRutilTables);
            }
        }
        if (parent.sEx != null) {
            for (Int2ObjectMap.Entry<IncNode> entry : parent.sEx.int2ObjectEntrySet()) {
                UtilityList dUl = deltaSExMap.remove(entry.getIntKey());
                if (dUl != null) entry.getValue().ul.append(dUl, globalRutilTables);
            }
        }

        if (!deltaIExMap.isEmpty() || !deltaSExMap.isEmpty()) {
            Int2ObjectOpenHashMap<UtilityList> oldIExMap = new Int2ObjectOpenHashMap<>();
            Int2ObjectOpenHashMap<UtilityList> oldSExMap = new Int2ObjectOpenHashMap<>();

            projectList(ul, 0, ul.oldSize, oldIExMap, oldSExMap, threshold);

            for (Int2ObjectMap.Entry<UtilityList> entry : deltaIExMap.int2ObjectEntrySet()) {
                int item = entry.getIntKey();
                UtilityList fullUl = oldIExMap.get(item);
                if (fullUl == null) fullUl = new UtilityList(ul.itemSize + 1);
                fullUl.markOld();
                fullUl.append(entry.getValue(), globalRutilTables);
                // Use ensureIEx() so the slot is allocated lazily when first written.
                parent.ensureIEx().put(item, new IncNode(parent.key.iExtend(item), fullUl));
            }
            for (Int2ObjectMap.Entry<UtilityList> entry : deltaSExMap.int2ObjectEntrySet()) {
                int item = entry.getIntKey();
                UtilityList fullUl = oldSExMap.get(item);
                if (fullUl == null) fullUl = new UtilityList(ul.itemSize + 1);
                fullUl.markOld();
                fullUl.append(entry.getValue(), globalRutilTables);
                // Use ensureSEx() so the slot is allocated lazily when first written.
                parent.ensureSEx().put(item, new IncNode(parent.key.sExtend(item), fullUl));
            }
        }

        // Null-check before recursing into the next depth.
        if (parent.iEx != null) {
            int[] iKeys = parent.iEx.keySet().toIntArray();
            for (int item : iKeys) {
                boolean keep = miningDFS(parent.iEx.get(item), threshold, writer);
                if (!keep) parent.iEx.remove(item);
            }
        }

        if (parent.sEx != null) {
            int[] sKeys = parent.sEx.keySet().toIntArray();
            for (int item : sKeys) {
                boolean keep = miningDFS(parent.sEx.get(item), threshold, writer);
                if (!keep) parent.sEx.remove(item);
            }
        }
    }

    private void projectList(UtilityList ul, int startIdx, int endIdx,
                             Int2ObjectOpenHashMap<UtilityList> iExMap,
                             Int2ObjectOpenHashMap<UtilityList> sExMap, double threshold) {
        int size = endIdx, p = startIdx;
        double minSWU = threshold * (ul.itemSize + 1);

        while (p < size) {
            // eLocs packs (sid, flatIdx); getSid/getFlatIdx are O(1).
            int sid = ul.getSid(p);
            int startP = p;
            while (p < size && ul.getSid(p) == sid) p++;
            int endP = p;

            Sequence seq = globalDatabase.get(sid);
            if (seq == null) continue;

            // 1-D flat rutil.
            long[] rutilFlat = globalRutilTables.get(sid);
            int[] offsets = globalOffsets.get(sid);
            // flatToTid lets us derive tid from flatIdx in O(1).
            int[] flatToTid = globalFlatToTid.get(sid);
            int n = seq.itemsets.size(), modTCount = 0, modKCount = 0;

            int totalItems = offsets[n - 1] + seq.itemsets.get(n - 1).items.size();

            if (totalItems > maxK_buffer.length) {
                int oldLen = maxK_buffer.length;
                maxK_buffer = Arrays.copyOf(maxK_buffer, totalItems + 2000);
                modifiedK = Arrays.copyOf(modifiedK, totalItems + 2000);
                for (int i = oldLen; i < maxK_buffer.length; i++) maxK_buffer[i] = -1L;
            }
            if (n > maxT_buffer.length) {
                int oldLen = maxT_buffer.length;
                maxT_buffer = Arrays.copyOf(maxT_buffer, n + 500);
                modifiedT = Arrays.copyOf(modifiedT, n + 500);
                for (int i = oldLen; i < maxT_buffer.length; i++) maxT_buffer[i] = -1L;
            }

            for (int i = startP; i < endP; i++) {
                // (flatIdx, derived tid) instead of storing eTids/eIdxs explicitly.
                int flatIdx = ul.getFlatIdx(i);
                int tid = flatToTid[flatIdx];
                long iu = ul.eIutils.getLong(i);

                if (tid + 1 < n) {
                    if (maxT_buffer[tid + 1] == -1L) {
                        modifiedT[modTCount++] = tid + 1; maxT_buffer[tid + 1] = iu;
                    } else if (iu > maxT_buffer[tid + 1]) maxT_buffer[tid + 1] = iu;
                }
                int nextFlat = flatIdx + 1;
                // "next item in the same itemset" iff nextFlat < totalItems and the tid matches.
                if (nextFlat < totalItems && flatToTid[nextFlat] == tid) {
                    if (maxK_buffer[nextFlat] == -1L) {
                        modifiedK[modKCount++] = nextFlat; maxK_buffer[nextFlat] = iu;
                    } else if (iu > maxK_buffer[nextFlat]) maxK_buffer[nextFlat] = iu;
                }
            }

            long maxSPrefix = -1L;
            for (int t = 0; t < n; t++) {
                if (maxT_buffer[t] > maxSPrefix) maxSPrefix = maxT_buffer[t];
                Itemset is = seq.itemsets.get(t);
                long currentMaxI = -1;
                for (int k = 0; k < is.items.size(); k++) {
                    int flatIdx = offsets[t] + k;
                    if (maxK_buffer[flatIdx] > currentMaxI) currentMaxI = maxK_buffer[flatIdx];
                    ItemQ it = is.items.get(k);

                    if (globalItemSWU[it.id] < minSWU) continue;

                    if (currentMaxI != -1L) {
                        // addElement(sid, flatIdx, iutil, rutil); tid is derived from flatToTid.
                        iExMap.computeIfAbsent(it.id, key -> new UtilityList(ul.itemSize + 1))
                                .addElement(sid, flatIdx, currentMaxI + it.utility, rutilFlat[flatIdx]);
                    }
                    if (t > 0 && maxSPrefix != -1L) {
                        sExMap.computeIfAbsent(it.id, key -> new UtilityList(ul.itemSize + 1))
                                .addElement(sid, flatIdx, maxSPrefix + it.utility, rutilFlat[flatIdx]);
                    }
                }
            }
            for (int i = 0; i < modTCount; i++) maxT_buffer[modifiedT[i]] = -1L;
            for (int i = 0; i < modKCount; i++) maxK_buffer[modifiedK[i]] = -1L;
        }
    }

    // Build rutil in a 1-D layout (flatIdx = offsets[t] + k) and traverse it
    // backwards so the semantics of rutil are preserved without the per-row
    // header overhead of long[][].
    private long[] getRUtilTableFlat(Sequence seq, int[] offsets, int totalItems) {
        int n = seq.itemsets.size();
        long[] table = new long[totalItems];
        long runningR = 0;
        for (int t = n - 1; t >= 0; t--) {
            Itemset is = seq.itemsets.get(t);
            int m = is.items.size();
            int off = offsets[t];
            for (int k = m - 1; k >= 0; k--) {
                table[off + k] = runningR;
                runningR += is.items.get(k).utility;
            }
        }
        return table;
    }

    private void markOldTree(IncNode node) {
        node.ul.markOld();
        // Null-check before traversing child branches.
        if (node.iEx != null) for (IncNode child : node.iEx.values()) markOldTree(child);
        if (node.sEx != null) for (IncNode child : node.sEx.values()) markOldTree(child);
    }

    private RunResult buildRunResult(int bId, long startNs, long tScan, long tMining, double dRatio) {
        RunResult res = new RunResult();
        res.timestamp = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date());
        res.algorithm = "EHAUSM-I";
        res.dataset = datasetName; res.batchID = bId;
        res.minUtil = minUtilPercentage; res.mu = mu; res.deltaRatio = dRatio;
        res.totalDBUtility = totalDBUtility; res.cumulativeDBSize = globalDatabase.size();
        res.tScan = tScan; res.tMining = tMining;
        res.tTotal = (RunIsolation.cpuTimeNs() - startNs) / 1_000_000L;

        res.numCand = candidateCount;
        res.numPrunedL1 = prunedL1; res.numPrunedL2 = prunedL2; res.numPrunedL3 = 0;

        res.ratioTightnessPEAU = (countTightnessPEAU == 0) ? 0.0 : (sumTightnessPEAU / countTightnessPEAU);
        res.ratioTightnessIAUUB = 0.0; res.ratioTightnessMFUUB = 0.0;
        res.hauspFound = hauspCount;
        res.shausActive = 0;

        res.memPeak = (Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory()) / (1024.0 * 1024.0);
        return res;
    }
}