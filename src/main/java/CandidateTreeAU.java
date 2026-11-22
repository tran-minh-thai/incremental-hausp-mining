// SPDX-License-Identifier: MIT

import it.unimi.dsi.fastutil.ints.Int2ObjectMap;
import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.ints.IntArrayList;
import it.unimi.dsi.fastutil.ints.IntOpenHashSet;
import it.unimi.dsi.fastutil.longs.LongArrayList;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * A candidate pattern tree kept across batches, after IncUSP-Miner+ (Wang and Huang, "On Incremental High Utility
 * Sequential Pattern Mining", ACM TIST 9(5), article 55, 2018): USP-Miner (Algorithm 1) builds the tree on the first
 * batch, IncUSP-Miner+ (Algorithm 2, HandleCase-1, HandleCase-2, Strategies 1 to 6) maintains it on every later batch.
 * Inserted sequences and itemsets appended to stored sequences are both handled (the paper's appended sequences,
 * equation 16). Named by its behavior: it is an adaptation, not the published algorithm.
 *
 * <p>Implemented from the paper as written: the bounds PEU (Definition 14, 0 where the remaining utility is 0) and TSU
 * (Definitions 15 and 16), the node fields (Section 4.2.2: TSU, PEU, u, LMTSU, per sequence the extension positions with
 * u(t, p, s), and u, PEU, TSU of t in s), the node update by differences over the updated sequences (Section 4.3.1),
 * the skip of Strategy 1 and the scan scopes of Strategies 2 to 6.
 *
 * <p>Four changes, because the problem here is high average utility under a ratio threshold, minAU = xi times the
 * total utility of the accumulated database, which grows from batch to batch (utilities are non-negative), whereas
 * the paper keeps one absolute min_util:
 * <ol>
 *   <li>Length-aware comparisons. The utility bounds are unchanged; a pattern t is a result when u(t) &gt;= minAU |t|,
 *       a child t' of t is kept in the tree when TSU(t') &gt;= minAU (|t| + 1), and t is expanded when
 *       PEU(t) &gt;= minAU (|t| + 1). Every descendant of t' is at least as long as t', and the paper's Theorems 1 and
 *       2 bound its utility by TSU(t') and PEU(t), so its average utility is below minAU when the test fails. All
 *       children of t have the same length, so Strategies 3 and 5 compare LMTSU + PEU_LDB with one constant.</li>
 *   <li>An expanded flag on each node in place of the test PEU_D(t) &lt; min_util of Algorithm 2, line 6. With a
 *       growing threshold a node expanded earlier can fail line 4 later; its subtree is then dropped (it holds no
 *       result, by Theorem 1, and would no longer be updated) and the node is marked not expanded, so a later batch
 *       rebuilds it with USP-Miner. The paper's test would also rebuild it (its PEU_D is below the earlier, lower,
 *       threshold), so this change frees memory and is not meant to change the result; the fault paper_line6
 *       checks that.</li>
 *   <li>In the scan of Strategy 6 a child that is in the tree, has an instance in the updated sequences and is now
 *       below the threshold is dropped and counted in LMTSU. With a fixed threshold this cannot happen (TSU never
 *       decreases); kept, it would miss this batch's update and later updates would subtract stale values.</li>
 *   <li>The result is the list of nodes marked as results, filtered after every batch by u(t) &gt;= minAU |t| at the
 *       current threshold (a node untouched by the batch keeps its utility while the threshold rises).</li>
 * </ol>
 * Section 4.3 of the paper ("all high TSU sequences are buffered") and the proofs of Strategies 2 to 6 need the
 * invariant: LMTSU(t) is at least the TSU of every extension of t that has an instance in the database and is not in
 * the tree. The four changes keep it (a dropped child is counted in LMTSU, or its parent is no longer expanded).
 *
 * <p>Mode for the paper's running example ({@link #paperMode}): one absolute min_util, comparisons not length-aware,
 * as in the paper. On that example (Table 1, min_util = 50) every value of Figure 3 and of the text is reproduced
 * except LMTSU(&lt;a&gt;): the paper gives 21, the TSU of its only low i-extension [a, e] (Example 4.1, p. 13), but
 * the s-extension &lt;a, b&gt; is also low, with TSU 39 (s1 only: u(a, 1, s1) = 15, u(b, 3, s1) = 24, nothing after;
 * its utility is 39 too, so by Lemma 3 its TSU cannot be smaller). LMTSU is defined over all extension sequences
 * (Section 4.2.2, Definition 10), and Strategy 3 needs LMTSU(t) at least the TSU of every low extension, so this
 * implementation keeps 39. Counting i-extensions only would give the paper's 21 and make Strategy 3 unsound.
 *
 * <p>Timing: {@link #lastMaintNanos} covers joining the parts of the batch into the stored sequences (the arm keeps
 * its own copy of the accumulated database), {@link #lastSearchNanos} the pass over the tree (System.nanoTime).
 */
public class CandidateTreeAU {
    /**
     * Fault hook for the exactness check: {@code keep_low_child} (without change 3), {@code no_result_filter} (without
     * change 4), {@code paper_line6} (the paper's test PEU_D(t) &lt; threshold in place of the flag of change 2).
     */
    public static String FAULT = System.getProperty("hausp.ctFault");

    public boolean enableIO = false;
    public long lastMaintNanos = -1, lastSearchNanos = -1;
    /** Nodes built in the last batch, and drops caused by changes 2 and 3. */
    public long lastBuilt, lastDropped, lastLowChildDropped, unexpectedHighMissing;

    private final String datasetName;
    private double xi = 0.01;
    private boolean averageUtility = true;
    private long fixedMinUtil = -1;
    private double minAU;

    private final Int2ObjectOpenHashMap<Seq> db = new Int2ObjectOpenHashMap<>();
    private long totalUtility;
    private final Node root = new Node(null, -1, true);
    private final List<Node> results = new ArrayList<>();
    private IntOpenHashSet ldb = new IntOpenHashSet();

    // scratch of one scan; copied into a CandList before any recursive call
    private int[] seqStampI = new int[64], seqStampS = new int[64], aggStampI = new int[64], aggStampS = new int[64];
    private long[] ldbI = new long[64], ldbS = new long[64], allI = new long[64], allS = new long[64];
    private boolean[] inLdbI = new boolean[64], inLdbS = new boolean[64];
    private int seqStamp, aggStamp;

    public CandidateTreeAU(String datasetName) {
        this.datasetName = datasetName;
    }

    public void setConfig(double xi) {
        this.xi = xi;
    }

    /** The paper's setting: one absolute threshold on utility, comparisons not length-aware. */
    public void paperMode(long minUtil) {
        averageUtility = false;
        fixedMinUtil = minUtil;
    }

    // ------------------------------------------------------------------ data

    static final class Seq {
        final int[][] items;
        final long[][] util;
        /** ru[p][k]: utility of the items after the k-th item of itemset p, to the end of the sequence. */
        final long[][] ru;
        final long total;

        Seq(Seq head, Sequence tail) {
            int h = head == null ? 0 : head.items.length;
            int n = h + tail.itemsets.size();
            items = new int[n][];
            util = new long[n][];
            ru = new long[n][];
            for (int p = 0; p < h; p++) {
                items[p] = head.items[p];
                util[p] = head.util[p];
            }
            for (int p = h; p < n; p++) {
                List<ItemQ> is = tail.itemsets.get(p - h).items;
                items[p] = new int[is.size()];
                util[p] = new long[is.size()];
                for (int k = 0; k < is.size(); k++) {
                    items[p][k] = is.get(k).id;
                    util[p][k] = is.get(k).utility;
                    if (k > 0 && items[p][k] <= items[p][k - 1]) {
                        throw new IllegalStateException("itemset not strictly sorted in sequence " + tail.sid);
                    }
                }
            }
            long after = 0;
            for (int p = n - 1; p >= 0; p--) {
                ru[p] = new long[items[p].length];
                for (int k = items[p].length - 1; k >= 0; k--) {
                    ru[p][k] = after;
                    after += util[p][k];
                }
            }
            total = after;
        }

        int find(int p, int item) {
            return Arrays.binarySearch(items[p], item);
        }
    }

    /** t in one sequence s: extension positions in ascending order with u(t, p, s), and u(t, s), PEU(t, s), TSU(t, s). */
    static final class Occ {
        int[] pos;
        long[] up;
        long u, peu, tsu;
    }

    static final class Node {
        final Node parent;
        final int item;
        final boolean sExt;
        final int len;
        Int2ObjectOpenHashMap<Node> iKids, sKids;
        final Int2ObjectOpenHashMap<Occ> occ = new Int2ObjectOpenHashMap<>();
        long u, peu, tsu, lmtsu, prevPeu;
        boolean expanded, isResult, dead;

        Node(Node parent, int item, boolean sExt) {
            this.parent = parent;
            this.item = item;
            this.sExt = sExt;
            this.len = parent == null ? 0 : parent.len + 1;
        }

        Node kid(int item, boolean s) {
            Int2ObjectOpenHashMap<Node> m = s ? sKids : iKids;
            return m == null ? null : m.get(item);
        }

        void attach(Node c) {
            if (c.sExt) {
                if (sKids == null) sKids = new Int2ObjectOpenHashMap<>();
                sKids.put(c.item, c);
            } else {
                if (iKids == null) iKids = new Int2ObjectOpenHashMap<>();
                iKids.put(c.item, c);
            }
        }

        /** Pattern in the dump notation of the other arms: item ids, itemsets in parentheses. */
        String pattern() {
            List<Node> path = new ArrayList<>();
            for (Node n = this; n.parent != null; n = n.parent) path.add(n);
            StringBuilder sb = new StringBuilder("<");
            for (int j = path.size() - 1; j >= 0; j--) {
                Node n = path.get(j);
                if (j == path.size() - 1) sb.append('(');
                else if (n.sExt) sb.append(")(");
                else sb.append(',');
                sb.append(n.item);
            }
            return sb.append(")>").toString();
        }
    }

    /** Candidates of one scan, in a fixed order (i-extensions, then s-extensions, by item). */
    static final class CandList {
        final IntArrayList item = new IntArrayList();
        final it.unimi.dsi.fastutil.booleans.BooleanArrayList sExt = new it.unimi.dsi.fastutil.booleans.BooleanArrayList();
        final LongArrayList tsuLdb = new LongArrayList(), tsuAll = new LongArrayList();
        final it.unimi.dsi.fastutil.booleans.BooleanArrayList inLdb = new it.unimi.dsi.fastutil.booleans.BooleanArrayList();
    }

    // ---------------------------------------------------------- thresholds

    private double resultReq(int len) {
        return averageUtility ? minAU * len : fixedMinUtil;
    }

    /** Requirement on TSU of a child of length len, and on PEU of its parent (change 1). */
    private double keepReq(int len) {
        return averageUtility ? minAU * len : fixedMinUtil;
    }

    // ------------------------------------------------------------------ batch

    public RunResult processBatch(List<Sequence> batch, int batchId) {
        long t0 = System.nanoTime();
        ldb = new IntOpenHashSet(batch.size());
        for (Sequence part : batch) {
            Seq old = db.get(part.sid);
            Seq s = new Seq(old, part);
            totalUtility += s.total - (old == null ? 0 : old.total);
            db.put(part.sid, s);
            ldb.add(part.sid);
            for (int[] is : s.items) for (int it : is) grow(it);
        }
        minAU = xi * totalUtility;
        root.prevPeu = root.peu;
        root.peu = totalUtility;                                 // PEU of the empty sequence: the total utility
        long t1 = System.nanoTime();
        lastMaintNanos = t1 - t0;
        lastBuilt = lastDropped = lastLowChildDropped = 0;
        int[] ldbArr = ldb.toIntArray();
        Arrays.sort(ldbArr);
        RuntimeException[] err = new RuntimeException[1];
        Thread th = new Thread(null, () -> {
            try {
                inc(root, ldbArr);
            } catch (RuntimeException e) {
                err[0] = e;
            }
        }, "candidate-tree", 1L << 30);
        th.start();
        try {
            th.join();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        }
        if (err[0] != null) throw err[0];
        int kept = 0;
        for (int j = 0; j < results.size(); j++) {
            Node n = results.get(j);
            if (!n.dead && (n.u >= resultReq(n.len) || "no_result_filter".equals(FAULT))) results.set(kept++, n);
            else n.isResult = false;
        }
        results.subList(kept, results.size()).clear();
        lastSearchNanos = System.nanoTime() - t1;
        if (enableIO) dump(batchId);
        RunResult r = new RunResult();
        r.algorithm = "CandidateTree-AU";
        r.dataset = datasetName;
        r.batchID = batchId;
        r.hauspFound = results.size();
        r.numCand = lastBuilt;
        return r;
    }

    private void dump(int batchId) {
        new File("out").mkdirs();
        String f = "out/CandidateTree_" + datasetName + "_B" + batchId + "_mu" + xi + ".txt";
        try (BufferedWriter w = new BufferedWriter(new FileWriter(f))) {
            w.write("# CandidateTree-AU\n\n");
            for (Node n : results) {
                w.write(n.pattern() + "\t" + String.format(Locale.US, "%.2f", (double) n.u / n.len) + "\t" + n.occ.size()
                        + "\n");
            }
        } catch (IOException e) {
            throw new IllegalStateException("cannot write " + f, e);
        }
    }

    // ------------------------------------------------------- Algorithm 2

    /** IncUSP-Miner+(t); {@code ldbSids}: the updated sequences that contain t (all of them for the root). */
    private void inc(Node t, int[] ldbSids) {
        // Strategy 1: the t-projected database of LDB is empty or holds only empty sequences
        boolean any = false;
        for (int sid : ldbSids) {
            if (ruAtFirst(t, sid) > 0) {
                any = true;
                break;
            }
        }
        if (!any) return;
        double req = keepReq(t.len + 1);
        if (t.peu < req) {                                       // line 4
            if (t.expanded) dropKids(t);                         // change 2
            t.expanded = false;
            return;
        }
        boolean rebuild = "paper_line6".equals(FAULT) ? t.prevPeu < req : !t.expanded;
        if (rebuild) {                                           // line 6
            if (t.expanded) dropKids(t);
            uspMiner(t, sidsOf(t));
            return;
        }
        long peuLdb = 0;
        for (int sid : ldbSids) peuLdb += occOf(t, sid).peu;
        long emltsu = 0;
        List<Node> case1 = new ArrayList<>();
        IntArrayList case2Item = new IntArrayList();
        it.unimi.dsi.fastutil.booleans.BooleanArrayList case2S = new it.unimi.dsi.fastutil.booleans.BooleanArrayList();
        if (t.lmtsu == 0 || peuLdb + t.lmtsu < req) {          // Strategies 2 and 3: scan LDB only
            CandList cl = scan(t, ldbSids, null);
            for (int j = 0; j < cl.item.size(); j++) {
                int it = cl.item.getInt(j);
                boolean s = cl.sExt.getBoolean(j);
                Node c = t.kid(it, s);
                if (c != null) case1.add(c);
                else if (cl.tsuLdb.getLong(j) >= req) {
                    case2Item.add(it);
                    case2S.add(s);
                } else emltsu = Math.max(emltsu, cl.tsuLdb.getLong(j));
            }
            t.lmtsu += emltsu;                                   // Strategies 4 and 5
        } else {                                                 // Strategy 6: scan D'
            t.lmtsu = 0;
            CandList cl = scan(t, ldbSids, ndbSids(t));
            for (int j = 0; j < cl.item.size(); j++) {
                int it = cl.item.getInt(j);
                boolean s = cl.sExt.getBoolean(j);
                Node c = t.kid(it, s);
                boolean inL = cl.inLdb.getBoolean(j);
                long all = cl.tsuAll.getLong(j);
                if (all >= req) {
                    if (inL) {
                        if (c != null) case1.add(c);
                        else {
                            case2Item.add(it);
                            case2S.add(s);
                        }
                    } else if (c == null) {                     // cannot happen if the invariant holds
                        unexpectedHighMissing++;
                        case2Item.add(it);
                        case2S.add(s);
                    }
                } else {
                    if (inL && c != null && "keep_low_child".equals(FAULT)) lastLowChildDropped++;
                    else if (inL && c != null) {                 // change 3
                        drop(c);
                        lastLowChildDropped++;
                    }
                    emltsu = Math.max(emltsu, all);
                }
            }
            t.lmtsu = emltsu;
        }
        // HandleCase-1 for every child in the tree, then HandleCase-2 for every new one; the children of one node are
        // independent, so the order does not change the result. Each group's per-sequence values are computed in one
        // pass over the sequences (implementation, not in the paper).
        if (!case1.isEmpty()) {
            int[] items = new int[case1.size()];
            boolean[] sx = new boolean[case1.size()];
            for (int j = 0; j < items.length; j++) {
                items[j] = case1.get(j).item;
                sx[j] = case1.get(j).sExt;
            }
            Int2ObjectOpenHashMap<Occ>[] nw = childOccs(t, ldbSids, items, sx);
            for (int j = 0; j < items.length; j++) handleCase1(case1.get(j), nw[j], ldbSids);
        }
        if (!case2Item.isEmpty()) {
            for (Node c : buildAll(t, case2Item.toIntArray(), case2S.toBooleanArray(), sidsOf(t))) uspMiner(c, sidsOf(c));
        }
    }

    /** HandleCase-1: update c by differences over the updated sequences (equation 16), then IncUSP-Miner+(c). */
    private void handleCase1(Node c, Int2ObjectOpenHashMap<Occ> nw, int[] ldbSidsOfT) {
        IntArrayList cl = new IntArrayList();
        c.prevPeu = c.peu;
        for (int sid : ldbSidsOfT) {
            Occ n = nw.get(sid);
            Occ old = c.occ.get(sid);
            if (n == null) {
                if (old != null) throw new IllegalStateException("an appended sequence lost an instance of " + c.pattern());
                continue;
            }
            long ou = old == null ? 0 : old.u, op = old == null ? 0 : old.peu, ot = old == null ? 0 : old.tsu;
            c.u += n.u - ou;
            c.peu += n.peu - op;
            c.tsu += n.tsu - ot;
            c.occ.put(sid, n);
            cl.add(sid);
        }
        if (c.u >= resultReq(c.len)) markResult(c);
        inc(c, cl.toIntArray());
    }

    // ------------------------------------------------------- Algorithm 1

    private void uspMiner(Node t, int[] sids) {
        double req = keepReq(t.len + 1);
        if (t.peu < req) {
            t.expanded = false;
            return;
        }
        t.expanded = true;
        t.lmtsu = 0;
        CandList cl = scan(t, sids, null);
        IntArrayList items = new IntArrayList();
        it.unimi.dsi.fastutil.booleans.BooleanArrayList sx = new it.unimi.dsi.fastutil.booleans.BooleanArrayList();
        for (int j = 0; j < cl.item.size(); j++) {
            long tsu = cl.tsuLdb.getLong(j);
            if (tsu >= req) {
                items.add(cl.item.getInt(j));
                sx.add(cl.sExt.getBoolean(j));
            } else {
                t.lmtsu = Math.max(t.lmtsu, tsu);
            }
        }
        if (items.isEmpty()) return;
        for (Node c : buildAll(t, items.toIntArray(), sx.toBooleanArray(), sids)) uspMiner(c, sidsOf(c));
    }

    /** BuildNode for several children of t over the sequences {@code sids} (one pass), attached to t. */
    private List<Node> buildAll(Node t, int[] items, boolean[] sx, int[] sids) {
        Int2ObjectOpenHashMap<Occ>[] occs = childOccs(t, sids, items, sx);
        List<Node> out = new ArrayList<>(items.length);
        for (int j = 0; j < items.length; j++) {
            Node c = new Node(t, items[j], sx[j]);
            for (Int2ObjectMap.Entry<Occ> e : occs[j].int2ObjectEntrySet()) {
                Occ o = e.getValue();
                c.occ.put(e.getIntKey(), o);
                c.u += o.u;
                c.peu += o.peu;
                c.tsu += o.tsu;
            }
            c.prevPeu = c.peu;
            t.attach(c);
            lastBuilt++;
            if (c.u >= resultReq(c.len)) markResult(c);
            out.add(c);
        }
        return out;
    }

    private void markResult(Node c) {
        if (!c.isResult) {
            c.isResult = true;
            results.add(c);
        }
    }

    private void dropKids(Node t) {
        for (Int2ObjectOpenHashMap<Node> m : Arrays.asList(t.iKids, t.sKids)) {
            if (m == null) continue;
            for (Node c : m.values()) kill(c);
        }
        t.iKids = t.sKids = null;
        lastDropped++;
    }

    private void drop(Node c) {
        (c.sExt ? c.parent.sKids : c.parent.iKids).remove(c.item);
        kill(c);
    }

    private void kill(Node c) {
        c.dead = true;
        c.occ.clear();
        for (Int2ObjectOpenHashMap<Node> m : Arrays.asList(c.iKids, c.sKids)) {
            if (m == null) continue;
            for (Node k : m.values()) kill(k);
        }
        c.iKids = c.sKids = null;
    }

    // --------------------------------------------------- per-sequence values

    private Occ rootOcc(Seq s) {
        Occ o = new Occ();
        o.pos = new int[]{-1};
        o.up = new long[]{0};
        o.u = 0;
        o.peu = s.total;
        o.tsu = s.total;
        return o;
    }

    private Occ occOf(Node t, int sid) {
        return t == root ? rootOcc(db.get(sid)) : t.occ.get(sid);
    }

    private long ruAtFirst(Node t, int sid) {
        Seq s = db.get(sid);
        if (t == root) return s.total;
        Occ o = t.occ.get(sid);
        int p = o.pos[0];
        return s.ru[p][s.find(p, t.item)];
    }

    // scratch of childOccs: child index of a selected item, per kind (stamp-guarded)
    private int[] selStampI = new int[64], selStampS = new int[64], selIdxI = new int[64], selIdxS = new int[64];
    private int selStamp;

    /**
     * Per-sequence values of the children (items[j], sx[j]) of t in the sequences {@code sids}, by Theorems 3 and 4
     * and Definitions 14 to 16, in one pass over each sequence. result[j] maps a sequence to the child's values there;
     * a sequence without the child is absent.
     */
    @SuppressWarnings("unchecked")
    private Int2ObjectOpenHashMap<Occ>[] childOccs(Node t, int[] sids, int[] items, boolean[] sx) {
        int n = items.length;
        if (selStampI.length < seqStampI.length) {
            selStampI = Arrays.copyOf(selStampI, seqStampI.length);
            selStampS = Arrays.copyOf(selStampS, seqStampI.length);
            selIdxI = Arrays.copyOf(selIdxI, seqStampI.length);
            selIdxS = Arrays.copyOf(selIdxS, seqStampI.length);
        }
        selStamp++;
        boolean anyI = false;
        for (int j = 0; j < n; j++) {
            if (sx[j]) {
                selStampS[items[j]] = selStamp;
                selIdxS[items[j]] = j;
            } else {
                selStampI[items[j]] = selStamp;
                selIdxI[items[j]] = j;
                anyI = true;
            }
        }
        Int2ObjectOpenHashMap<Occ>[] out = new Int2ObjectOpenHashMap[n];
        IntArrayList[] pos = new IntArrayList[n];
        LongArrayList[] up = new LongArrayList[n], rus = new LongArrayList[n];
        long[] firstUtil = new long[n], firstRu = new long[n];
        for (int j = 0; j < n; j++) {
            out[j] = new Int2ObjectOpenHashMap<>();
            pos[j] = new IntArrayList(4);
            up[j] = new LongArrayList(4);
            rus[j] = new LongArrayList(4);
        }
        IntArrayList touched = new IntArrayList();
        for (int sid : sids) {
            Seq s = db.get(sid);
            Occ o = occOf(t, sid);
            if (anyI && t != root) {
                for (int jp = 0; jp < o.pos.length; jp++) {
                    int p = o.pos[jp];
                    int k = s.find(p, t.item);
                    for (int kk = k + 1; kk < s.items[p].length; kk++) {
                        int it = s.items[p][kk];
                        if (selStampI[it] != selStamp) continue;
                        int c = selIdxI[it];
                        if (pos[c].isEmpty()) {
                            touched.add(c);
                            firstUtil[c] = s.util[p][kk];
                            firstRu[c] = s.ru[p][kk];
                        }
                        pos[c].add(p);
                        up[c].add(o.up[jp] + s.util[p][kk]);
                        rus[c].add(s.ru[p][kk]);
                    }
                }
            }
            int jp = 0;
            long best = Long.MIN_VALUE;
            for (int q = o.pos[0] + 1; q < s.items.length; q++) {
                while (jp < o.pos.length && o.pos[jp] < q) best = Math.max(best, o.up[jp++]);
                for (int kk = 0; kk < s.items[q].length; kk++) {
                    int it = s.items[q][kk];
                    if (selStampS[it] != selStamp) continue;
                    int c = selIdxS[it];
                    if (pos[c].isEmpty()) {
                        touched.add(c);
                        firstUtil[c] = s.util[q][kk];
                        firstRu[c] = s.ru[q][kk];
                    }
                    pos[c].add(q);
                    up[c].add(best + s.util[q][kk]);
                    rus[c].add(s.ru[q][kk]);
                }
            }
            for (int ti = 0; ti < touched.size(); ti++) {
                int c = touched.getInt(ti);
                Occ x = new Occ();
                x.pos = pos[c].toIntArray();
                x.up = up[c].toLongArray();
                long u = Long.MIN_VALUE, peu = 0;
                for (int j = 0; j < x.pos.length; j++) {
                    u = Math.max(u, x.up[j]);
                    long ru = rus[c].getLong(j);
                    if (ru > 0) peu = Math.max(peu, x.up[j] + ru);     // Definition 14
                }
                x.u = u;
                x.peu = peu;
                x.tsu = o.u == o.up[0] ? o.up[0] + firstUtil[c] + firstRu[c] : o.peu;   // Definitions 15, 16
                out[c].put(sid, x);
                pos[c].clear();
                up[c].clear();
                rus[c].clear();
            }
            touched.clear();
        }
        return out;
    }

    /** Definitions 15 and 16: TSU of the extension of t (values o in s) whose smallest extension position is p1. */
    private static long tsuOf(Occ o, Seq s, int p1, int k1) {
        return o.u == o.up[0] ? o.up[0] + s.util[p1][k1] + s.ru[p1][k1] : o.peu;
    }

    /** TSU of every extension of t: over {@code ldbSids} (column tsuLdb), and over both lists (column tsuAll). */
    private CandList scan(Node t, int[] ldbSids, int[] ndbSids) {
        aggStamp++;
        IntArrayList dirtyI = new IntArrayList(), dirtyS = new IntArrayList();
        for (int pass = 0; pass < 2; pass++) {
            int[] sids = pass == 0 ? ldbSids : ndbSids;
            if (sids == null) continue;
            for (int sid : sids) {
                Seq s = db.get(sid);
                Occ o = occOf(t, sid);
                seqStamp++;
                if (t != root) {
                    for (int p : o.pos) {
                        int k = s.find(p, t.item);
                        for (int kk = k + 1; kk < s.items[p].length; kk++) {
                            int it = s.items[p][kk];
                            if (seqStampI[it] == seqStamp) continue;
                            seqStampI[it] = seqStamp;
                            long tsu = tsuOf(o, s, p, kk);
                            if (aggStampI[it] != aggStamp) {
                                aggStampI[it] = aggStamp;
                                ldbI[it] = allI[it] = 0;
                                inLdbI[it] = false;
                                dirtyI.add(it);
                            }
                            if (pass == 0) {
                                ldbI[it] += tsu;
                                inLdbI[it] = true;
                            }
                            allI[it] += tsu;
                        }
                    }
                }
                for (int q = o.pos[0] + 1; q < s.items.length; q++) {
                    for (int kk = 0; kk < s.items[q].length; kk++) {
                        int it = s.items[q][kk];
                        if (seqStampS[it] == seqStamp) continue;
                        seqStampS[it] = seqStamp;
                        long tsu = tsuOf(o, s, q, kk);
                        if (aggStampS[it] != aggStamp) {
                            aggStampS[it] = aggStamp;
                            ldbS[it] = allS[it] = 0;
                            inLdbS[it] = false;
                            dirtyS.add(it);
                        }
                        if (pass == 0) {
                            ldbS[it] += tsu;
                            inLdbS[it] = true;
                        }
                        allS[it] += tsu;
                    }
                }
            }
        }
        CandList cl = new CandList();
        int[] di = dirtyI.toIntArray(), ds = dirtyS.toIntArray();
        Arrays.sort(di);
        Arrays.sort(ds);
        for (int it : di) {
            cl.item.add(it);
            cl.sExt.add(false);
            cl.tsuLdb.add(ldbI[it]);
            cl.tsuAll.add(allI[it]);
            cl.inLdb.add(inLdbI[it]);
        }
        for (int it : ds) {
            cl.item.add(it);
            cl.sExt.add(true);
            cl.tsuLdb.add(ldbS[it]);
            cl.tsuAll.add(allS[it]);
            cl.inLdb.add(inLdbS[it]);
        }
        return cl;
    }

    private int[] sidsOf(Node t) {
        int[] a = t == root ? db.keySet().toIntArray() : t.occ.keySet().toIntArray();
        Arrays.sort(a);
        return a;
    }

    private int[] ndbSids(Node t) {
        IntArrayList a = new IntArrayList();
        for (int sid : sidsOf(t)) if (!ldb.contains(sid)) a.add(sid);
        return a.toIntArray();
    }

    private void grow(int item) {
        if (item < seqStampI.length) return;
        int n = Math.max(item + 1, seqStampI.length * 2);
        seqStampI = Arrays.copyOf(seqStampI, n);
        seqStampS = Arrays.copyOf(seqStampS, n);
        aggStampI = Arrays.copyOf(aggStampI, n);
        aggStampS = Arrays.copyOf(aggStampS, n);
        ldbI = Arrays.copyOf(ldbI, n);
        ldbS = Arrays.copyOf(ldbS, n);
        allI = Arrays.copyOf(allI, n);
        allS = Arrays.copyOf(allS, n);
        inLdbI = Arrays.copyOf(inLdbI, n);
        inLdbS = Arrays.copyOf(inLdbS, n);
    }

    // ------------------------------------------------ inspection for checks

    /** The node of a pattern in the dump notation, or null; for the check on the paper's example. */
    Node find(String pattern) {
        String body = pattern.substring(2, pattern.length() - 2);
        Node n = root;
        boolean firstSet = true;
        for (String set : body.split("\\)\\(")) {
            boolean first = true;
            for (String tok : set.split(",")) {
                int it = Integer.parseInt(tok.trim());
                boolean s = first;                  // first item of an itemset is an s-extension (root: also)
                n = n.kid(it, s || (firstSet && first));
                if (n == null) return null;
                first = false;
            }
            firstSet = false;
        }
        return n;
    }

    /** Values of a node for the check: u, PEU, TSU, LMTSU in the database, and u, PEU, TSU in sequence sid. */
    String describe(String pattern, int sid) {
        Node n = find(pattern);
        if (n == null) return pattern + " absent";
        Occ o = n.occ.get(sid);
        StringBuilder pos = new StringBuilder();
        if (o != null) for (int j = 0; j < o.pos.length; j++) pos.append(j == 0 ? "" : ";").append(o.pos[j] + 1).append(':').append(o.up[j]);
        return pattern + " u " + n.u + " peu " + n.peu + " tsu " + n.tsu + " lmtsu " + n.lmtsu
                + (o == null ? " sid " + sid + " absent" : " sid " + sid + " u " + o.u + " peu " + o.peu + " tsu " + o.tsu
                + " pos " + pos);
    }

    /**
     * The running example of the paper (Table 1), in its own setting (absolute min_util, utility).
     * Args: min_util, then pairs pattern:sid to describe after batch 0 (D) and after batch 1 (D'), separated by "--".
     * Items a..f are 1..6; Table 1(b) gives the external utilities.
     */
    public static void main(String[] args) {
        long minUtil = Long.parseLong(args[0]);
        long[] eu = {0, 3, 4, 1, 2, 1, 2};
        String[][] d = {
                {"1", "(1,5)(2,3)|(4,1)|(2,6)"},
                {"2", "(1,1)(3,1)|(1,4)(3,6)|(3,2)|(5,1)|(4,2)"},
                {"3", "(3,2)|(2,5)|(4,4)|(1,2)"},
                {"4", "(5,5)|(3,2)"},
                {"5", "(1,1)(3,1)|(1,5)(3,1)(5,1)"}};
        String[][] delta = {
                {"4", "(3,2)(4,6)|(6,5)|(1,2)(5,5)"},
                {"5", "(1,5)(3,1)(5,1)|(4,1)(5,2)"},
                {"6", "(6,2)|(2,1)|(5,4)|(4,2)|(3,1)"}};
        CandidateTreeAU m = new CandidateTreeAU("PAPER");
        m.paperMode(minUtil);
        int phase = 0;
        for (String[][] part : new String[][][]{d, delta}) {
            List<Sequence> batch = new ArrayList<>();
            for (String[] row : part) {
                Sequence s = new Sequence(Integer.parseInt(row[0]));
                for (String set : row[1].split("\\|")) {
                    Itemset is = new Itemset();
                    for (String it : set.substring(1, set.length() - 1).split("\\)\\(")) {
                        String[] f = it.split(",");
                        int id = Integer.parseInt(f[0]), q = Integer.parseInt(f[1]);
                        is.addItem(new ItemQ(id, q, q * eu[id]));
                    }
                    s.addItemset(is);
                }
                batch.add(s);
            }
            m.processBatch(batch, phase);
            System.out.println("phase " + phase + " results " + m.results.size() + " built " + m.lastBuilt);
            boolean mine = false;
            int seen = 0;
            for (int j = 1; j < args.length; j++) {
                if (args[j].equals("--")) {
                    seen++;
                    continue;
                }
                if (seen != phase) continue;
                String[] f = args[j].split(":");
                System.out.println("phase " + phase + " " + m.describe(f[0], Integer.parseInt(f[1])));
            }
            phase++;
        }
    }
}
