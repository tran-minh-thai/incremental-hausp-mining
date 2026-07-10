import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.ints.IntArrayList;
import it.unimi.dsi.fastutil.longs.LongArrayList;

import java.io.*;
import java.text.SimpleDateFormat;
import java.util.*;

/**
 * EHAUSM-Remining: oracle baseline that re-mines the entire accumulated
 * database from scratch at every batch and is therefore used as a correctness
 * reference for the incremental algorithms.
 *
 * <p>Internally the implementation mirrors the data structures and pruning
 * logic of {@link EHAUSM_Inc}, including the PEAU upper bound computed as the
 * maximum of {@code iutil + rutil} per occurrence and length-aware SWU
 * pruning in the DFS.
 */
public class EHAUSM_Remining {

    public boolean enableIO = false;
    // 1-D flat rutil storage; avoids the per-row header overhead of long[][].
    private Int2ObjectOpenHashMap<long[]> globalRutilTables = new Int2ObjectOpenHashMap<>();
    // Per-sid offsets cache; sequences are immutable so they are built once.
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

    private long totalExactUtility = 0;
    private long totalPEAU = 0;

    static final class PatternKey {
        final int[] items;
        final int hash;

        PatternKey(int[] items) {
            this.items = items;
            this.hash = Arrays.hashCode(items);
        }

        PatternKey iExtend(int item) {
            int[] next = Arrays.copyOf(items, items.length + 1);
            next[next.length - 1] = item;
            return new PatternKey(next);
        }

        PatternKey sExtend(int item) {
            int[] next = Arrays.copyOf(items, items.length + 2);
            next[next.length - 2] = -1;
            next[next.length - 1] = item;
            return new PatternKey(next);
        }

        @Override
        public boolean equals(Object obj) {
            if (this == obj) return true;
            if (!(obj instanceof PatternKey)) return false;
            return Arrays.equals(this.items, ((PatternKey) obj).items);
        }

        @Override
        public int hashCode() { return hash; }

        @Override
        public String toString() {
            StringBuilder sb = new StringBuilder("<(");
            for (int i = 0; i < items.length; i++) {
                if (items[i] == -1) sb.append(")(");
                else {
                    if (i > 0 && items[i - 1] != -1) sb.append(",");
                    sb.append(items[i]);
                }
            }
            return sb.append(")>").toString();
        }
    }

    // 2-array utility list (16 B/element) instead of 5 arrays (28 B/element).
    static final class UtilityList {
        int itemSize;

        LongArrayList eLocs   = new LongArrayList();    // sid<<32 | flatIdx
        LongArrayList eIutils = new LongArrayList();

        private int lastAggSid = -1;
        private long runMaxIutil = 0, runMaxPEAU = 0;
        private long aggTotalIutil = 0, aggTotalPEAU = 0;
        private int aggSupportCount = 0;

        public UtilityList(int itemSize) {
            this.itemSize = itemSize;
        }

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

        public void trimToSize() { eLocs.trim(); eIutils.trim(); }

        public int getSid(int i)     { return (int) (eLocs.getLong(i) >>> 32); }
        public int getFlatIdx(int i) { return (int) eLocs.getLong(i); }

        public long[] evaluate() {
            if (lastAggSid == -1) return new long[]{0, 0, 0};
            return new long[]{
                    aggTotalIutil + runMaxIutil,
                    aggTotalPEAU + runMaxPEAU,
                    aggSupportCount + 1
            };
        }
    }

    private List<Sequence> globalDatabase = new ArrayList<>();
    private Map<PatternKey, UtilityList> historicalULs = new HashMap<>();
    private Int2ObjectOpenHashMap<PatternKey> singletonKeys = new Int2ObjectOpenHashMap<>();
    private Int2ObjectOpenHashMap<Sequence> seqById = new Int2ObjectOpenHashMap<>();

    // Global SWU array, used in place of a boolean mask.
    private long[] globalItemSWU = new long[10000];

    private double minUtilPercentage;
    private long totalDBUtility = 0;
    private String datasetName;

    private long hauspCount = 0;
    private long candidateCount = 0;
    private long prunedL1 = 0, prunedL2 = 0;

    public EHAUSM_Remining(String configPath) {
        Properties prop = new Properties();
        try (FileInputStream fis = new FileInputStream(configPath)) {
            prop.load(fis);
            this.minUtilPercentage = Double.parseDouble(prop.getProperty("minUtil.percentage", "0.01"));
            this.datasetName = new File(prop.getProperty("dataset.path", "unknown")).getName().replace("_seq.txt", "");
        } catch (IOException e) { e.printStackTrace(); }
        Arrays.fill(maxT_buffer, -1L);
        Arrays.fill(maxK_buffer, -1L);
    }

    public void setConfig(double minUtil) {
        this.minUtilPercentage = minUtil;
    }

    private PatternKey getSingletonKey(int item) {
        PatternKey k = singletonKeys.get(item);
        if (k == null) {
            k = new PatternKey(new int[]{item});
            singletonKeys.put(item, k);
        }
        return k;
    }

    public RunResult processBatch(List<Sequence> cumulativeDB, int batchId) {
        hauspCount = 0; candidateCount = 0;
        prunedL1 = 0; prunedL2 = 0;
        totalExactUtility = 0; totalPEAU = 0;
        totalDBUtility = 0;

        globalDatabase = cumulativeDB;
        historicalULs.clear();
        seqById.clear();
        Arrays.fill(globalItemSWU, 0L);

        // CPU time at nanosecond resolution, excluding GC pauses.
        long startTimeNs = RunIsolation.cpuTimeNs();
        long startScanNs = RunIsolation.cpuTimeNs();

        int maxItemId = 0;
        for (Sequence seq : globalDatabase) {
            totalDBUtility += seq.totalUtility;
            seqById.put(seq.sid, seq);

            // Scan SWU through the mask + dirty-list, without allocating HashSet/Integer objects.
            int seenCount = 0;
            long su = seq.totalUtility;
            for (Itemset is : seq.itemsets) {
                for (ItemQ it : is.items) {
                    int id = it.id;
                    if (id > maxItemId) maxItemId = id;
                    if (id >= globalItemSWU.length) {
                        int newLen = id + 5000;
                        globalItemSWU = Arrays.copyOf(globalItemSWU, newLen);
                        swuSeenBuf = Arrays.copyOf(swuSeenBuf, newLen);
                        swuSeenListBuf = Arrays.copyOf(swuSeenListBuf, newLen);
                    }
                    if (!swuSeenBuf[id]) {
                        swuSeenBuf[id] = true;
                        swuSeenListBuf[seenCount++] = id;
                        globalItemSWU[id] += su;
                    }
                }
            }
            for (int i = 0; i < seenCount; i++) swuSeenBuf[swuSeenListBuf[i]] = false;

            // Build offsets first, then fill the 1-D rutil array using those offsets.
            if (!globalRutilTables.containsKey(seq.sid)) {
                int n = seq.itemsets.size();
                int[] offsets = new int[n];
                int total = 0;
                for (int t = 0; t < n; t++) {
                    offsets[t] = total;
                    total += seq.itemsets.get(t).items.size();
                }
                globalOffsets.put(seq.sid, offsets);
                globalRutilTables.put(seq.sid, getRUtilTableFlat(seq, offsets, total));

                // Build flatToTid[] alongside the offsets.
                int[] flatToTid = new int[total];
                for (int t = 0; t < n; t++) {
                    int off = offsets[t];
                    int sz = seq.itemsets.get(t).items.size();
                    for (int j = 0; j < sz; j++) flatToTid[off + j] = t;
                }
                globalFlatToTid.put(seq.sid, flatToTid);
            }
        }

        double threshold = minUtilPercentage * totalDBUtility;

        // Build the utility lists for 1-itemsets (Layer 1 pruning).
        for (Sequence seq : globalDatabase) {
            // 1-D flat rutil.
            long[] rutilFlat = globalRutilTables.get(seq.sid);
            int[] offsRoot = globalOffsets.get(seq.sid);
            int n = seq.itemsets.size();
            for (int t = 0; t < n; t++) {
                Itemset is = seq.itemsets.get(t);
                int offT = offsRoot[t];
                for (int k = 0; k < is.items.size(); k++) {
                    ItemQ it = is.items.get(k);
                    if (globalItemSWU[it.id] >= threshold) {
                        PatternKey p = getSingletonKey(it.id);
                        UtilityList ul = historicalULs.computeIfAbsent(p, key -> new UtilityList(1));
                        // addElement(sid, flatIdx, iutil, rutil).
                        int flatIdx = offT + k;
                        ul.addElement(seq.sid, flatIdx, it.utility, rutilFlat[flatIdx]);
                    }
                }
            }
        }

        // Update the Layer 1 pruning counter.
        for (int i = 0; i <= maxItemId; i++) {
            if (globalItemSWU[i] > 0 && globalItemSWU[i] < threshold) prunedL1++;
        }

        long tScan = (RunIsolation.cpuTimeNs() - startScanNs) / 1_000_000L;

        long startMiningNs = RunIsolation.cpuTimeNs();
        String outFileName = "out/EHAUSM_Remining_" + datasetName + "_B" + batchId + ".txt";
        new File("out").mkdirs();

        List<PatternKey> seeds = new ArrayList<>();
        for (Map.Entry<PatternKey, UtilityList> entry : historicalULs.entrySet()) {
            seeds.add(entry.getKey());
        }
        seeds.sort(Comparator.comparingInt(a -> a.items[0]));

        BufferedWriter writer = null;
        try {
            if (this.enableIO) {
                writer = new BufferedWriter(new FileWriter(outFileName), 65536);
                writer.write("# EHAUSM-Remining: oracle baseline (re-mines the cumulative database).\n");
                writer.write("# Threshold: " + threshold + "\n\n");
            }

            for (PatternKey alpha : seeds) {
                miningDFS(alpha, historicalULs.get(alpha), threshold, writer);
            }
        } catch (IOException e) {
            e.printStackTrace();
        } finally {
            if (writer != null) {
                try { writer.close(); } catch (IOException ignored) {}
            }
        }

        long tMining = (RunIsolation.cpuTimeNs() - startMiningNs) / 1_000_000L;
        return buildRunResult(batchId, startTimeNs, tScan, tMining, 1.0);
    }

    private void miningDFS(PatternKey alpha, UtilityList ul, double threshold, BufferedWriter writer) throws IOException {
        candidateCount++;

        long[] ev = ul.evaluate();
        long totalIutil = ev[0];
        long exactPEAU = ev[1]; // Tight PEAU computed by the utility list.
        int support = (int) ev[2];

        totalExactUtility += totalIutil;
        totalPEAU += exactPEAU;

        double reqHAUSP = threshold * ul.itemSize;
        double reqExtend = threshold * (ul.itemSize + 1);

        if (totalIutil >= reqHAUSP) {
            hauspCount++;
            if (this.enableIO && writer != null) {
                try {
                    double actualAU = (double) totalIutil / ul.itemSize;
                    writer.write(alpha.toString() + "\t"
                            + String.format(Locale.US, "%.2f", actualAU) + "\t"
                            + support + "\n");
                } catch (Exception ignored) {}
            }
        }

        // Layer 2: prune by the exact PEAU bound.
        if (exactPEAU < reqExtend) {
            prunedL2++;
            return;
        }

        ul.trimToSize();
        generateExtensions(alpha, ul, threshold, writer);
    }

    private void generateExtensions(PatternKey alpha, UtilityList ul, double threshold, BufferedWriter writer) throws IOException {
        Int2ObjectOpenHashMap<UtilityList> iExMap = new Int2ObjectOpenHashMap<>();
        Int2ObjectOpenHashMap<UtilityList> sExMap = new Int2ObjectOpenHashMap<>();

        int size = ul.eLocs.size();
        int p = 0;

        // Compute the SWU floor for the next extension length.
        double minSWU = threshold * (ul.itemSize + 1);

        while (p < size) {
            // eLocs packs (sid, flatIdx).
            int sid = ul.getSid(p);
            int startP = p;
            while (p < size && ul.getSid(p) == sid) {
                p++;
            }
            int endP = p;

            Sequence seq = seqById.get(sid);
            if (seq == null) continue;
            int n = seq.itemsets.size();
            // 1-D flat rutil.
            long[] rutilFlat = globalRutilTables.get(sid);

            // Reuse the cached offsets instead of rebuilding them per (sid, DFS node).
            int[] offsets = globalOffsets.get(sid);
            // flatToTid lets us derive tid in O(1).
            int[] flatToTid = globalFlatToTid.get(sid);
            int totalItems = (n > 0) ? offsets[n - 1] + seq.itemsets.get(n - 1).items.size() : 0;

            if (totalItems > maxK_buffer.length) {
                int oldLen = maxK_buffer.length;
                int newLen = totalItems + 2000;
                maxK_buffer = Arrays.copyOf(maxK_buffer, newLen);
                modifiedK = Arrays.copyOf(modifiedK, newLen);
                for(int i = oldLen; i < newLen; i++) maxK_buffer[i] = -1L;
            }
            if (n > maxT_buffer.length) {
                int oldLen = maxT_buffer.length;
                int newLen = n + 500;
                maxT_buffer = Arrays.copyOf(maxT_buffer, newLen);
                modifiedT = Arrays.copyOf(modifiedT, newLen);
                for(int i = oldLen; i < newLen; i++) maxT_buffer[i] = -1L;
            }

            int modTCount = 0;
            int modKCount = 0;

            for (int i = startP; i < endP; i++) {
                // (flatIdx, derived tid) instead of storing eTids/eIdxs explicitly.
                int flatIdx = ul.getFlatIdx(i);
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

            long maxSPrefix = -1;

            for (int t = 0; t < n; t++) {
                if (maxT_buffer[t] > maxSPrefix) maxSPrefix = maxT_buffer[t];

                Itemset is = seq.itemsets.get(t);
                long currentMaxI = -1;

                for (int k = 0; k < is.items.size(); k++) {
                    int flatIdx = offsets[t] + k;
                    if (maxK_buffer[flatIdx] > currentMaxI) currentMaxI = maxK_buffer[flatIdx];

                    ItemQ it = is.items.get(k);

                    // Length-aware Layer 1 SWU pruning.
                    if (it.id >= globalItemSWU.length || globalItemSWU[it.id] < minSWU) continue;

                    if (currentMaxI != -1L) {
                        final int fid = it.id;
                        UtilityList childUl = iExMap.computeIfAbsent(fid, key -> new UtilityList(ul.itemSize + 1));
                        // addElement(sid, flatIdx, iutil, rutil).
                        childUl.addElement(sid, flatIdx, currentMaxI + it.utility, rutilFlat[flatIdx]);
                    }

                    if (t > 0 && maxSPrefix != -1L) {
                        final int fid = it.id;
                        UtilityList childUl = sExMap.computeIfAbsent(fid, key -> new UtilityList(ul.itemSize + 1));
                        childUl.addElement(sid, flatIdx, maxSPrefix + it.utility, rutilFlat[flatIdx]);
                    }
                }
            }

            for (int i = 0; i < modTCount; i++) maxT_buffer[modifiedT[i]] = -1L;
            for (int i = 0; i < modKCount; i++) maxK_buffer[modifiedK[i]] = -1L;
        }

        int[] iKeys = iExMap.keySet().toIntArray();
        Arrays.sort(iKeys);
        for (int key : iKeys) {
            UtilityList childUl = iExMap.remove(key);
            miningDFS(alpha.iExtend(key), childUl, threshold, writer);
        }

        int[] sKeys = sExMap.keySet().toIntArray();
        Arrays.sort(sKeys);
        for (int key : sKeys) {
            UtilityList childUl = sExMap.remove(key);
            miningDFS(alpha.sExtend(key), childUl, threshold, writer);
        }
    }

    // Build rutil in a 1-D layout (index: flatIdx = offsets[t] + k).
    private long[] getRUtilTableFlat(Sequence seq, int[] offsets, int totalItems) {
        int n = seq.itemsets.size();
        long[] table = new long[totalItems];
        long running = 0;
        for (int t = n - 1; t >= 0; t--) {
            Itemset is = seq.itemsets.get(t);
            int m = is.items.size();
            int off = offsets[t];
            for (int k = m - 1; k >= 0; k--) {
                table[off + k] = running;
                running += is.items.get(k).utility;
            }
        }
        return table;
    }

    private RunResult buildRunResult(int bId, long startNs, long tScan,
                                     long tMining, double dRatio) {
        RunResult res = new RunResult();
        res.timestamp = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date());
        res.algorithm = "EHAUSM-R";
        res.dataset = datasetName;
        res.batchID = bId;
        res.minUtil = minUtilPercentage;
        res.mu = 0.0;
        res.deltaRatio = dRatio;
        res.totalDBUtility = totalDBUtility;
        res.cumulativeDBSize = globalDatabase.size();
        res.tScan = tScan;
        res.tMining = tMining;
        res.tTotal = (RunIsolation.cpuTimeNs() - startNs) / 1_000_000L;

        res.numCand = candidateCount;
        res.numPrunedL1 = prunedL1;
        res.numPrunedL2 = prunedL2;
        res.numPrunedL3 = 0;

        res.ratioTightnessPEAU = (totalPEAU == 0) ? 0.0 : (double) totalExactUtility / totalPEAU;
        res.ratioTightnessIAUUB = 0.0;
        res.ratioTightnessMFUUB = 0.0;

        res.hauspFound = hauspCount;
        res.shausActive = 0;

        // System.gc() removed: stop-the-world pauses bias tTotal.
        res.memPeak = (Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory()) / (1024.0 * 1024.0);
        return res;
    }
}