import it.unimi.dsi.fastutil.ints.IntArrayList;
import it.unimi.dsi.fastutil.longs.Long2LongOpenHashMap;
import it.unimi.dsi.fastutil.longs.LongOpenHashSet;

import java.io.*;
import java.text.SimpleDateFormat;
import java.util.*;

/**
 * HAUSP-UB: the proposed algorithm for incremental high-average-utility
 * sequential pattern mining with a three-layer pruning architecture
 * (SWU at Layer 1, IAUUB at Layer 2, SeqMFUUB at Layer 3).
 *
 * <p>The IAUUB tightness metric is reported as a ratio of sums; the SeqMFUUB
 * tightness metric is reported as an average of ratios. A length-aware SWU
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
     * When {@code false}, the IAUUB filter at Layer 2 is bypassed so that every
     * extension survives Layer 2; only the global SWU filter and, if enabled,
     * Layer 3 still prune. Used by the ablation study.
     */
    public boolean enableLayer2IAUUB = true;

    /**
     * When {@code false}, the SeqMFUUB filter at Layer 3 is bypassed in
     * {@code miningDFS}. Used by the ablation study.
     */
    public boolean enableLayer3MFUUB = true;

    /** Cumulative CPU time spent in each pruning layer for the current batch (nanoseconds). */
    public long timeLayer1Ns = 0;
    public long timeLayer2Ns = 0;
    public long timeLayer3Ns = 0;
    public double minUtilPercentage;
    public long totalDBUtility = 0;

    private AUDUL[] globalAUDULs = new AUDUL[10000];
    private int[] itemToCompact;
    private int[] compactToItem;
    private int compactCount = 0;
    private Sequence[] dbArray;
    private int[][] flatItemIds;
    private long[][] flatItemUtils;
    private long[][] flatRutilFull;
    private int[][] flatToTid;
    private int[][] itemsetOffsets;
    private int[][] itemsetSizes;
    private String datasetName = "BMS1_SPMF";
    private long[] globalItemSWU = new long[10000];

    // KIẾN TRÚC LAI (HYBRID) CHO EUCS INCREMENTAL
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

    private long[][] estIExIutil = new long[200][];
    private long[][] estIExTotal = new long[200][];
    private long[][] estSExIutil = new long[200][];
    private long[][] estSExTotal = new long[200][];

    private long[][] iEucsCacheByDepth = new long[200][];
    private long[][] sEucsCacheByDepth = new long[200][];
    private int[][] eucsCacheDirtyByDepth = new int[200][];

    private long[] localMaxI_I = new long[10000];
    private long[] localMaxR_I = new long[10000];
    private long[] localMaxI_S = new long[10000];
    private long[] localMaxR_S = new long[10000];
    private boolean[] inLocalSeen = new boolean[10000];
    private int[] localSeenList = new int[10000];

    private long hauspCount, candidateCount;
    private long prunedL1, prunedL1_5, prunedL2, prunedL3, prunedL_TwoPass;
    private double peakMemory = 0;
    private int peakMemCounter = 0;

    private boolean[] swuSeenBuf;
    private int[] swuSeenListBuf;
    private int[] firstTidBuf;
    private int[] lastTidBuf;
    private boolean[] seenCBuf;
    private int[] uListBuf;

    // Tightness measurement.
    // Both IAUUB and MFUUB use an average of ratios over the same candidate
    // nodes: Tightness(UB) = (1/N) * Σ [ au(α) / UB_normalized(α) ], with
    // au(α) = evalIutil / |α| and UB_normalized(α) = UB_raw / |α|. The raw
    // upper bound is passed down by the parent through estIAUUB so that the
    // two ratios are evaluated on identical sets of N candidates.
    private double sumTightnessIAUUB = 0.0; // Σ [ au(α) / IAUUB_normalized(α) ]
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
        Arrays.fill(localMaxI_I, -1L);
        Arrays.fill(localMaxI_S, -1L);
        Arrays.fill(maxT_buffer, -1L);
        Arrays.fill(maxK_buffer, -1L);
    }

    public void setConfig(double minUtil) {
        this.minUtilPercentage = minUtil;
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

        if (globalItemSWU != null) Arrays.fill(globalItemSWU, 0);

        dbArray = null;
        flatItemIds = null;
        flatItemUtils = null;
        flatToTid = null;
        flatRutilFull = null;
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
        if (currentMemMB > peakMemory) peakMemory = currentMemMB;
    }

    private void forcePeakMemorySample() {
        long totalMem = Runtime.getRuntime().totalMemory();
        long freeMem = Runtime.getRuntime().freeMemory();
        double currentMemMB = (totalMem - freeMem) / 1024.0 / 1024.0;
        if (currentMemMB > peakMemory) peakMemory = currentMemMB;
    }

    public RunResult processBatch(List<Sequence> deltaBatch, int batchId) {
        if (batchId == 0) reset();
        hauspCount = 0; candidateCount = 0;
        prunedL1 = 0; prunedL1_5 = 0; prunedL2 = 0; prunedL3 = 0; prunedL_TwoPass = 0;
        timeLayer1Ns = 0; timeLayer2Ns = 0; timeLayer3Ns = 0;
        audulPool.resetCounters();

        sumTightnessIAUUB = 0.0;
        sumTightnessMFUUB = 0.0;
        countTightnessNode = 0;
        tightnessPEAU  = 0.0;
        tightnessIAUUB = 0.0;
        tightnessMFUUB = 0.0;

        // CPU time at nanosecond resolution, excluding GC pauses.
        long startTimeNs = RunIsolation.cpuTimeNs();
        peakMemory = 0;

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
        flatRutilFull = (flatRutilFull == null) ? new long[reqSid + 1000][] : (reqSid >= flatRutilFull.length ? Arrays.copyOf(flatRutilFull, reqSid + 1000) : flatRutilFull);
        itemsetOffsets = (itemsetOffsets == null) ? new int[reqSid + 1000][] : (reqSid >= itemsetOffsets.length ? Arrays.copyOf(itemsetOffsets, reqSid + 1000) : itemsetOffsets);
        itemsetSizes = (itemsetSizes == null) ? new int[reqSid + 1000][] : (reqSid >= itemsetSizes.length ? Arrays.copyOf(itemsetSizes, reqSid + 1000) : itemsetSizes);

        if (globalAUDULs.length <= maxItemIdEver) {
            int newSize = maxItemIdEver + 5000;
            globalAUDULs = Arrays.copyOf(globalAUDULs, newSize);
            globalItemSWU = Arrays.copyOf(globalItemSWU, newSize);

            int oldLen = localMaxI_I.length;
            localMaxI_I = Arrays.copyOf(localMaxI_I, newSize);
            localMaxR_I = Arrays.copyOf(localMaxR_I, newSize);
            localMaxI_S = Arrays.copyOf(localMaxI_S, newSize);
            localMaxR_S = Arrays.copyOf(localMaxR_S, newSize);
            inLocalSeen = Arrays.copyOf(inLocalSeen, newSize);
            localSeenList = Arrays.copyOf(localSeenList, newSize);

            for (int i = oldLen; i < newSize; i++) {
                localMaxI_I[i] = -1L;
                localMaxI_S[i] = -1L;
            }
        }

        int swuBufSize = maxItemIdEver + 1;
        if (swuSeenBuf == null || swuSeenBuf.length < swuBufSize) {
            swuSeenBuf = new boolean[swuBufSize + 1024];
            swuSeenListBuf = new int[swuBufSize + 1024];
        }
        long l1Start = RunIsolation.cpuTimeNs();
        for (Sequence seq : deltaBatch) {
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
            flatRutilFull[seq.sid] = rfFlat;
            itemsetOffsets[seq.sid] = offsets;
            itemsetSizes[seq.sid] = sizes;

            for (int i = 0; i < totalItems; i++) {
                int itemId = ids[i];
                if (globalAUDULs[itemId] == null) globalAUDULs[itemId] = audulPool.get(1);
                globalAUDULs[itemId].addElementLight(seq.sid, i, uts[i], rfFlat[i]);
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

        long startMiningNs = RunIsolation.cpuTimeNs();
        forcePeakMemorySample();

        try (BufferedWriter writer = enableIO ? new BufferedWriter(new FileWriter("out/HAUSP_" + datasetName + "_B" + batchId + ".txt")) : null) {
            for (int cId = 0; cId < compactCount; cId++) {
                int itemId = compactToItem[cId];
                AUDUL dul = globalAUDULs[itemId];
                if (dul != null) {
                    dul.evaluate();

                    boolean canBeHAUSP = dul.evalIutil >= threshold;
                    boolean canExtend = globalItemSWU[itemId] >= (threshold * 2);

                    if (!canBeHAUSP && !canExtend) {
                        prunedL1_5++;
                        continue;
                    }

                    currentPattern[0] = itemId;
                    // Level 1: IAUUB of a singleton item equals SWU(item),
                    // an upper bound on the total utility of every sequence containing it.
                    miningDFS(dul, (double) globalItemSWU[itemId], threshold, writer, 0, cId, 1);
                }
            }
        } catch (IOException e) {
            e.printStackTrace();
        }

        // Tightness aggregation: average of ratios over the shared N candidates.
        // Tightness(UB) = (1/N) * Σ [ au(α) / UB_normalized(α) ]
        // IAUUB and MFUUB share the candidate set, so the two metrics are directly comparable.
        this.tightnessPEAU  = 0.0;
        this.tightnessIAUUB = (countTightnessNode == 0) ? 0.0 : sumTightnessIAUUB / countTightnessNode;
        this.tightnessMFUUB = (countTightnessNode == 0) ? 0.0 : sumTightnessMFUUB / countTightnessNode;

        // ns → ms when exported to RunResult (keeps the long-milliseconds contract with CSVLogger)
        long tScanMs = (startMiningNs - startTimeNs) / 1_000_000L;
        long tMiningMs = (RunIsolation.cpuTimeNs() - startMiningNs) / 1_000_000L;
        return buildRunResult(batchId, startTimeNs, tScanMs, tMiningMs);
    }

    /**
     * DFS over the extensions of pattern α.
     *
     * @param dul       AU-DUL of α; the caller must invoke {@code evaluate()} beforehand.
     * @param estIAUUB  Raw IAUUB of α as computed by the parent in
     *                  {@code generateExtensions} ({@code Σ_seq max(iutil + rutil)},
     *                  not yet divided by |α|). At level 1 (singleton items) the
     *                  caller passes {@code globalItemSWU[itemId]}.
     * @param threshold  minUtil × totalDBUtil
     */
    private void miningDFS(AUDUL dul, double estIAUUB, double threshold, BufferedWriter writer, int depth, int lastCompactId, int patternLen) throws IOException {
        candidateCount++;
        updatePeakMemory();
        dul.evaluate();

        double reqHAUSP = threshold * dul.itemSize;
        double reqExtend = threshold * (dul.itemSize + 1);

        if (dul.evalIutil >= reqHAUSP) {
            hauspCount++;
            if (enableIO && writer != null) {
                writer.write(getPatternString(patternLen) + "\t"
                        + String.format(Locale.US, "%.2f", (double) dul.evalIutil / dul.itemSize)
                        + "\t" + dul.evalSupport + "\n");
            }
        }

        // Tightness: tightness(UB, α) = au(α) / UB_normalized(α) on the
        // shared candidate set, where
        //   au(α)             = evalIutil(α) / |α|
        //   IAUUB_normalized  = estIAUUB(α)  / |α|   (raw IAUUB from parent)
        //   MFUUB_normalized  = evalMFUUB(α) / |α|   (raw MFUUB at this node)
        // Both ratios share the same |α|, so the units match au(α).
        if (enableTightnessMetric && estIAUUB > 0 && dul.evalMFUUB > 0) {
            double au            = (double) dul.evalIutil / dul.itemSize;
            double iauubNorm     = estIAUUB              / dul.itemSize; // raw IAUUB / |α|
            double mfuubNorm     = (double) dul.evalMFUUB / dul.itemSize; // raw MFUUB / |α|
            sumTightnessIAUUB   += au / iauubNorm;
            sumTightnessMFUUB   += au / mfuubNorm;
            countTightnessNode++;
        }

        // Layer 3 (MFUUB): no descendant of α can be a HAUSP or a productive extension.
        if (enableLayer3MFUUB) {
            long l3Start = RunIsolation.cpuTimeNs();
            boolean pruneL3 = dul.evalMFUUB < reqExtend;
            timeLayer3Ns += RunIsolation.cpuTimeNs() - l3Start;
            if (pruneL3) {
                prunedL3++;
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
        ensureDFSArraysCapacity(depth);

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

            int[] items = flatItemIds[sid];
            long[] uts = flatItemUtils[sid];
            int[] tids = flatToTid[sid];
            long[] rfFlat = flatRutilFull[sid];
            int[] offsets = itemsetOffsets[sid];
            int[] sizes = itemsetSizes[sid];
            int n = sizes.length;
            int totalItems = items.length;

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
            int localSeenCount = 0;

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

                    long iEucsVal = iCache[cId];
                    long sEucsVal;
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

                    boolean finalValidI = validI && (iEucsVal >= threshold);
                    boolean finalValidS = validS && (sEucsVal >= threshold);
                    if (!finalValidI && !finalValidS) continue;

                    long util = uts[flatIdx];
                    long rf = rfFlat[flatIdx];

                    if (finalValidI) {
                        long iu = currentMaxI + util;
                        if (localMaxI_I[cId] == -1L) {
                            localMaxI_I[cId] = iu;
                            localMaxR_I[cId] = rf;
                            if (!inLocalSeen[cId]) {
                                inLocalSeen[cId] = true;
                                localSeenList[localSeenCount++] = cId;
                            }
                        } else {
                            if (iu > localMaxI_I[cId]) localMaxI_I[cId] = iu;
                            if (rf > localMaxR_I[cId]) localMaxR_I[cId] = rf;
                        }

                        AUDUL c = iExMap[cId];
                        if (c == null) {
                            c = audulPool.get(dul.itemSize + 1);
                            iExMap[cId] = c;
                            iDirty[iDirtyCount++] = cId;
                        }
                        c.addElementLight(sid, flatIdx, iu, rf);
                    }

                    if (finalValidS) {
                        long iu = maxS + util;
                        if (localMaxI_S[cId] == -1L) {
                            localMaxI_S[cId] = iu;
                            localMaxR_S[cId] = rf;
                            if (!inLocalSeen[cId]) {
                                inLocalSeen[cId] = true;
                                localSeenList[localSeenCount++] = cId;
                            }
                        } else {
                            if (iu > localMaxI_S[cId]) localMaxI_S[cId] = iu;
                            if (rf > localMaxR_S[cId]) localMaxR_S[cId] = rf;
                        }

                        AUDUL c = sExMap[cId];
                        if (c == null) {
                            c = audulPool.get(dul.itemSize + 1);
                            sExMap[cId] = c;
                            sDirty[sDirtyCount++] = cId;
                        }
                        c.addElementLight(sid, flatIdx, iu, rf);
                    }
                }
            }

            for (int i = 0; i < modTCount; i++) maxT_buffer[modifiedT[i]] = -1L;
            for (int i = 0; i < modKCount; i++) maxK_buffer[modifiedK[i]] = -1L;

            for (int i = 0; i < localSeenCount; i++) {
                int cId = localSeenList[i];
                if (localMaxI_I[cId] != -1L) {
                    estIExIutil[depth][cId] += localMaxI_I[cId];
                    estIExTotal[depth][cId] += localMaxI_I[cId] + localMaxR_I[cId];
                    localMaxI_I[cId] = -1L;
                }
                if (localMaxI_S[cId] != -1L) {
                    estSExIutil[depth][cId] += localMaxI_S[cId];
                    estSExTotal[depth][cId] += localMaxI_S[cId] + localMaxR_S[cId];
                    localMaxI_S[cId] = -1L;
                }
                inLocalSeen[cId] = false;
            }
        }

        for (int i = 0; i < cacheDirtyCount; i++) {
            int cid = cacheDirty[i];
            iCache[cid] = -1L;
            sCache[cid] = -1L;
        }

        // Layer 2 (IAUUB dual bound) — bypassed when enableLayer2IAUUB == false.
        long l2Start = RunIsolation.cpuTimeNs();
        double reqChildHAUSP  = threshold * (dul.itemSize + 1);
        double reqChildExtend = reqChildHAUSP + threshold;

        int newIDirtyCount = 0;
        for (int i = 0; i < iDirtyCount; i++) {
            int cId = iDirty[i];
            long estIutil = estIExIutil[depth][cId];
            long estTotal = estIExTotal[depth][cId];
            estIExIutil[depth][cId] = 0L;
            // estIExTotal[depth][cId] is kept; processRecurse passes it on to miningDFS.

            boolean canBeHAUSP = estIutil >= reqChildHAUSP;
            boolean canExtend  = estTotal >= reqChildExtend;

            if (!enableLayer2IAUUB || canBeHAUSP || canExtend) {
                iDirty[newIDirtyCount++] = cId;
            } else {
                estIExTotal[depth][cId] = 0L; // pruned: reset immediately
                audulPool.release(iExMap[cId]);
                iExMap[cId] = null;
                prunedL_TwoPass++;
            }
        }
        iDirtyCount = newIDirtyCount;

        int newSDirtyCount = 0;
        for (int i = 0; i < sDirtyCount; i++) {
            int cId = sDirty[i];
            long estIutil = estSExIutil[depth][cId];
            long estTotal = estSExTotal[depth][cId];
            estSExIutil[depth][cId] = 0L;
            // estSExTotal[depth][cId] is kept; processRecurse reads it later.

            boolean canBeHAUSP = estIutil >= reqChildHAUSP;
            boolean canExtend  = estTotal >= reqChildExtend;

            if (!enableLayer2IAUUB || canBeHAUSP || canExtend) {
                sDirty[newSDirtyCount++] = cId;
            } else {
                estSExTotal[depth][cId] = 0L; // pruned: reset immediately
                audulPool.release(sExMap[cId]);
                sExMap[cId] = null;
                prunedL_TwoPass++;
            }
        }
        sDirtyCount = newSDirtyCount;
        timeLayer2Ns += RunIsolation.cpuTimeNs() - l2Start;

        // estIExTotal/estSExTotal[depth][cId] hold the raw IAUUB of each child for miningDFS.
        processRecurse(iExMap, iDirty, iDirtyCount, true,  threshold, writer, depth, patternLen, lastCompactId, reqChildHAUSP, estIExTotal[depth]);
        processRecurse(sExMap, sDirty, sDirtyCount, false, threshold, writer, depth, patternLen, lastCompactId, reqChildHAUSP, estSExTotal[depth]);
        // Reset estTotal once processRecurse has consumed it.
        for (int i = 0; i < iDirtyCount; i++) estIExTotal[depth][iDirty[i]] = 0L;
        for (int i = 0; i < sDirtyCount; i++) estSExTotal[depth][sDirty[i]] = 0L;
    }

    private void generateExtensionsSparse(AUDUL dul, double threshold, BufferedWriter writer, int depth, int lastCompactId, int patternLen, double reqUtil) throws IOException {
        ensureDFSArraysCapacity(depth);

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

            int[] items = flatItemIds[sid];
            long[] uts = flatItemUtils[sid];
            int[] tids = flatToTid[sid];
            long[] rfFlat = flatRutilFull[sid];
            int[] offsets = itemsetOffsets[sid];
            int[] sizes = itemsetSizes[sid];
            int n = sizes.length;
            int totalItems = items.length;

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
            int localSeenCount = 0;

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

                    long iEucsVal = iCache[cId];
                    long sEucsVal;
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

                    boolean finalValidI = validI && (iEucsVal >= threshold);
                    boolean finalValidS = validS && (sEucsVal >= threshold);
                    if (!finalValidI && !finalValidS) continue;

                    long util = uts[flatIdx];
                    long rf = rfFlat[flatIdx];

                    if (finalValidI) {
                        long iu = currentMaxI + util;
                        if (localMaxI_I[cId] == -1L) {
                            localMaxI_I[cId] = iu;
                            localMaxR_I[cId] = rf;
                            if (!inLocalSeen[cId]) {
                                inLocalSeen[cId] = true;
                                localSeenList[localSeenCount++] = cId;
                            }
                        } else {
                            if (iu > localMaxI_I[cId]) localMaxI_I[cId] = iu;
                            if (rf > localMaxR_I[cId]) localMaxR_I[cId] = rf;
                        }

                        AUDUL c = iExMap[cId];
                        if (c == null) {
                            c = audulPool.get(dul.itemSize + 1);
                            iExMap[cId] = c;
                            iDirty[iDirtyCount++] = cId;
                        }
                        c.addElementLight(sid, flatIdx, iu, rf);
                    }

                    if (finalValidS) {
                        long iu = maxS + util;
                        if (localMaxI_S[cId] == -1L) {
                            localMaxI_S[cId] = iu;
                            localMaxR_S[cId] = rf;
                            if (!inLocalSeen[cId]) {
                                inLocalSeen[cId] = true;
                                localSeenList[localSeenCount++] = cId;
                            }
                        } else {
                            if (iu > localMaxI_S[cId]) localMaxI_S[cId] = iu;
                            if (rf > localMaxR_S[cId]) localMaxR_S[cId] = rf;
                        }

                        AUDUL c = sExMap[cId];
                        if (c == null) {
                            c = audulPool.get(dul.itemSize + 1);
                            sExMap[cId] = c;
                            sDirty[sDirtyCount++] = cId;
                        }
                        c.addElementLight(sid, flatIdx, iu, rf);
                    }
                }
            }

            for (int i = 0; i < modTCount; i++) maxT_buffer[modifiedT[i]] = -1L;
            for (int i = 0; i < modKCount; i++) maxK_buffer[modifiedK[i]] = -1L;

            for (int i = 0; i < localSeenCount; i++) {
                int cId = localSeenList[i];
                if (localMaxI_I[cId] != -1L) {
                    estIExIutil[depth][cId] += localMaxI_I[cId];
                    estIExTotal[depth][cId] += localMaxI_I[cId] + localMaxR_I[cId];
                    localMaxI_I[cId] = -1L;
                }
                if (localMaxI_S[cId] != -1L) {
                    estSExIutil[depth][cId] += localMaxI_S[cId];
                    estSExTotal[depth][cId] += localMaxI_S[cId] + localMaxR_S[cId];
                    localMaxI_S[cId] = -1L;
                }
                inLocalSeen[cId] = false;
            }
        }

        for (int i = 0; i < cacheDirtyCount; i++) {
            int cid = cacheDirty[i];
            iCache[cid] = -1L;
            sCache[cid] = -1L;
        }

        // Layer 2 (IAUUB dual bound) — bypassed when enableLayer2IAUUB == false.
        long l2Start = RunIsolation.cpuTimeNs();
        double reqChildHAUSP  = threshold * (dul.itemSize + 1);
        double reqChildExtend = reqChildHAUSP + threshold;

        int newIDirtyCount = 0;
        for (int i = 0; i < iDirtyCount; i++) {
            int cId = iDirty[i];
            long estIutil = estIExIutil[depth][cId];
            long estTotal = estIExTotal[depth][cId];
            estIExIutil[depth][cId] = 0L;
            // estIExTotal[depth][cId] is kept; processRecurse passes it on to miningDFS.

            boolean canBeHAUSP = estIutil >= reqChildHAUSP;
            boolean canExtend  = estTotal >= reqChildExtend;

            if (!enableLayer2IAUUB || canBeHAUSP || canExtend) {
                iDirty[newIDirtyCount++] = cId;
            } else {
                estIExTotal[depth][cId] = 0L; // pruned: reset immediately
                audulPool.release(iExMap[cId]);
                iExMap[cId] = null;
                prunedL_TwoPass++;
            }
        }
        iDirtyCount = newIDirtyCount;

        int newSDirtyCount = 0;
        for (int i = 0; i < sDirtyCount; i++) {
            int cId = sDirty[i];
            long estIutil = estSExIutil[depth][cId];
            long estTotal = estSExTotal[depth][cId];
            estSExIutil[depth][cId] = 0L;
            // estSExTotal[depth][cId] is kept; processRecurse reads it later.

            boolean canBeHAUSP = estIutil >= reqChildHAUSP;
            boolean canExtend  = estTotal >= reqChildExtend;

            if (!enableLayer2IAUUB || canBeHAUSP || canExtend) {
                sDirty[newSDirtyCount++] = cId;
            } else {
                estSExTotal[depth][cId] = 0L; // pruned: reset immediately
                audulPool.release(sExMap[cId]);
                sExMap[cId] = null;
                prunedL_TwoPass++;
            }
        }
        sDirtyCount = newSDirtyCount;
        timeLayer2Ns += RunIsolation.cpuTimeNs() - l2Start;

        // estIExTotal/estSExTotal[depth][cId] hold the raw IAUUB of each child for miningDFS.
        processRecurse(iExMap, iDirty, iDirtyCount, true,  threshold, writer, depth, patternLen, lastCompactId, reqChildHAUSP, estIExTotal[depth]);
        processRecurse(sExMap, sDirty, sDirtyCount, false, threshold, writer, depth, patternLen, lastCompactId, reqChildHAUSP, estSExTotal[depth]);
        // Reset estTotal once processRecurse has consumed it.
        for (int i = 0; i < iDirtyCount; i++) estIExTotal[depth][iDirty[i]] = 0L;
        for (int i = 0; i < sDirtyCount; i++) estSExTotal[depth][sDirty[i]] = 0L;
    }

    private void processRecurse(AUDUL[] mapArray, int[] dirtyList, int dirtyCount, boolean isI,
                                double threshold, BufferedWriter writer, int depth, int patternLen,
                                int lastCompactId, double reqChildHAUSP,
                                long[] estTotalArr) throws IOException {
        // estTotalArr[cId] holds the raw IAUUB (Σ_seq max(iutil+rutil)) computed
        // in generateExtensions; miningDFS reads it to evaluate the IAUUB tightness.
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
            child.evaluate();

            // Layer 3 (MFUUB) pruning of the child.
            boolean canBeHAUSP = child.evalIutil >= reqChildHAUSP;
            boolean canExtend  = child.evalMFUUB >= reqChildExtend;

            if (!canBeHAUSP && !canExtend) {
                prunedL3++;
                audulPool.release(child);
                continue;
            }

            int oldLen = patternLen;
            if (!isI) currentPattern[patternLen++] = -1;
            currentPattern[patternLen++] = origId;

            // estTotalArr[cId] is the raw IAUUB of the child; passed down to miningDFS for tightness.
            miningDFS(child, (double) estTotalArr[cId], threshold, writer, depth + 1, cId, patternLen);

            patternLen = oldLen;
            audulPool.release(child);
        }
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

    private void ensureDFSArraysCapacity(int depth) {
        if (depth >= iExArrays.length) {
            int newLen = depth + 64;
            iExArrays = Arrays.copyOf(iExArrays, newLen);
            sExArrays = Arrays.copyOf(sExArrays, newLen);
            iDirtyList = Arrays.copyOf(iDirtyList, newLen);
            sDirtyList = Arrays.copyOf(sDirtyList, newLen);
            estIExIutil = Arrays.copyOf(estIExIutil, newLen);
            estIExTotal = Arrays.copyOf(estIExTotal, newLen);
            estSExIutil = Arrays.copyOf(estSExIutil, newLen);
            estSExTotal = Arrays.copyOf(estSExTotal, newLen);
            iEucsCacheByDepth = Arrays.copyOf(iEucsCacheByDepth, newLen);
            sEucsCacheByDepth = Arrays.copyOf(sEucsCacheByDepth, newLen);
            eucsCacheDirtyByDepth = Arrays.copyOf(eucsCacheDirtyByDepth, newLen);
        }
        if (iExArrays[depth] == null || iExArrays[depth].length <= compactCount) {
            int size = compactCount + 100;
            iExArrays[depth] = new AUDUL[size];
            sExArrays[depth] = new AUDUL[size];
            iDirtyList[depth] = new int[size];
            sDirtyList[depth] = new int[size];
            estIExIutil[depth] = new long[size];
            estIExTotal[depth] = new long[size];
            estSExIutil[depth] = new long[size];
            estSExTotal[depth] = new long[size];
            iEucsCacheByDepth[depth] = new long[size];
            sEucsCacheByDepth[depth] = new long[size];
            eucsCacheDirtyByDepth[depth] = new int[size];
            Arrays.fill(iEucsCacheByDepth[depth], -1L);
            Arrays.fill(sEucsCacheByDepth[depth], -1L);
        }
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
        res.cumulativeDBSize = maxSidEver + 1;
        res.tScan = tScan;
        res.tMining = tMining;
        res.tTotal = (RunIsolation.cpuTimeNs() - startNs) / 1_000_000L;
        res.tLayer1 = timeLayer1Ns / 1_000_000L;
        res.tLayer2 = timeLayer2Ns / 1_000_000L;
        res.tLayer3 = timeLayer3Ns / 1_000_000L;
        res.numCand = candidateCount;
        res.hauspFound = hauspCount;
        res.memPeak = this.peakMemory;

        res.numPrunedL1 = prunedL1 + prunedL1_5;
        res.numPrunedL2 = prunedL_TwoPass + prunedL2;
        res.numPrunedL3 = prunedL3;

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

        public AUDUL get(int size) {
            borrows++;
            currentBorrowed++;
            if (currentBorrowed > peakBorrowed) peakBorrowed = currentBorrowed;
            if (top >= 0) {
                reuses++;
                AUDUL a = pool[top--];
                a.init(size);
                return a;
            }
            return new AUDUL(size);
        }

        public void release(AUDUL a) {
            if (a.locs.length > 8192) {
                a.locs = new long[8192];
                a.iutils = new long[8192];
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

        int count = 0;
        private int lastAggSid = -1;
        private long runMaxIutil = 0;
        private long runMaxMFUUB = 0;
        private long aggTotalIutil = 0;
        private long aggTotalMFUUB = 0;
        private int aggSupportCount = 0;

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
        }

        public void addElementLight(int sid, int flatIdx, long iu, long rf) {
            if (count >= locs.length) {
                int newCapacity = locs.length * 2;
                locs = Arrays.copyOf(locs, newCapacity);
                iutils = Arrays.copyOf(iutils, newCapacity);
            }

            locs[count] = ((long) sid << 32) | (flatIdx & 0xFFFFFFFFL);
            iutils[count++] = iu;

            // Compute PEAU/MFUUB in place using max(iutil + rutilFull).
            long currentMFUUB = iu + rf;

            if (sid != lastAggSid) {
                if (lastAggSid != -1) {
                    aggTotalIutil += runMaxIutil;
                    aggTotalMFUUB += runMaxMFUUB;
                    aggSupportCount++;
                }
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
                evalSupport = aggSupportCount + 1;
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