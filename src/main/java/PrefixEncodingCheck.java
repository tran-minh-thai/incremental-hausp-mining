import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.TreeMap;

/**
 * Checks the in-place append of stored sequences (arm option {@code prefix}) batch by batch against
 * the definitions, on a database fed under the mixed update model.
 *
 * <pre>
 * java -cp build/incremental-hausp-mining-1.0.0.jar PrefixEncodingCheck \
 *      &lt;eui&gt; &lt;seq&gt; &lt;properties&gt; &lt;mu&gt; &lt;batch sizes&gt; &lt;keys|@keyfile&gt; [arm] [fault] [eucs pairs]
 * </pre>
 *
 * <p>After every batch:
 * <ol>
 * <li><b>invariance</b>: every position stored before the batch (item, utility, itemset index, pre) and
 *     every itemset stored before it (offset, size) is unchanged;</li>
 * <li><b>sequences</b>: for every key, the stored positions equal the parts that arrived for it joined
 *     in order (read again from the file, so no object is shared with the miner), u(S) equals their
 *     total, and u(S) - pre[k] - utils[k] equals the remaining utility at k computed from scratch;</li>
 * <li><b>swu</b>: the SWU of every item and totalU equal their definitions;</li>
 * <li><b>eucs</b> (when the arm keeps it): self, sequence-order and same-itemset EUCS of every pair
 *     (all pairs when the database has at most 200 items, otherwise the pairs of a seeded sample of
 *     sequences, {@code eucs pairs} sequences) equal their definitions;</li>
 * <li><b>roots</b>: the single-item list the search reads for every item equals the definition, entry
 *     by entry (sequences in id order, positions in increasing order), and so do its sums of maxima
 *     (utility, utility plus remaining utility, support).</li>
 * </ol>
 * Each check prints how many values it compared. Exit code 1 on any disagreement. {@code fault}
 * injects one of {@code HAUSP_UB.FAULT}'s faults; {@code -} for none.
 */
public final class PrefixEncodingCheck {

    private PrefixEncodingCheck() { }

    private static final String[] CHECKS = {"invariance", "sequences", "swu", "eucs", "roots"};
    private static final long[] compared = new long[CHECKS.length];
    private static final long[] wrong = new long[CHECKS.length];
    private static final List<String> examples = new ArrayList<>();

    private static void cmp(int c, boolean ok, String what) {
        compared[c]++;
        if (!ok) {
            wrong[c]++;
            if (examples.size() < 10) examples.add(CHECKS[c] + ": " + what);
        }
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 6) {
            System.err.println("usage: PrefixEncodingCheck <eui> <seq> <properties> <mu> <batch sizes> <keys|@file> [arm] [fault] [eucs pairs]");
            System.exit(2);
        }
        List<Sequence> db = QSDB_Parser.loadDB(args[0], args[1]);
        List<Sequence> mine = QSDB_Parser.loadDB(args[0], args[1]);   // independent copy for the expected values
        int[] keys = readKeys(args[5], db.size());
        for (int i = 0; i < keys.length; i++) db.get(i).sid = keys[i];
        String arm = args.length >= 7 && !args[6].equals("-") ? args[6] : "HAUSP-UB[prefix]";
        HAUSP_UB.FAULT = args.length >= 8 && !args[7].equals("-") ? args[7] : null;
        int eucsSample = args.length >= 9 ? Integer.parseInt(args[8]) : 2000;
        String[] tok = args[4].split(",");
        HAUSP_UB alg = HAUSP_UB.fromArmName(arm, args[2]);
        alg.setConfig(Double.parseDouble(args[3]));
        alg.topK = 0;
        alg.enableIO = false;

        Map<Integer, List<Itemset>> joined = new LinkedHashMap<>();
        int start = 0;
        long appendedBatches = 0, written = 0, arrivedTotal = 0;
        for (int b = 0; b < tok.length; b++) {
            int size = Integer.parseInt(tok[b].trim());
            // snapshot of every stored sequence before the batch
            Map<Integer, long[][]> snap = new HashMap<>();
            for (int key : joined.keySet()) {
                int sid = alg.internalId(key);
                int len = alg.positions(sid), nIs = alg.itemsets(sid);
                long[][] s = new long[6][];
                s[0] = toLong(Arrays.copyOf(alg.posItems(sid), len));
                s[1] = Arrays.copyOf(alg.posUtils(sid), len);
                s[2] = toLong(Arrays.copyOf(alg.posTids(sid), len));
                s[3] = Arrays.copyOf(alg.posPre(sid), len);
                s[4] = toLong(Arrays.copyOf(alg.isetOffsets(sid), nIs));
                s[5] = toLong(Arrays.copyOf(alg.isetSizes(sid), nIs));
                snap.put(key, s);
            }
            List<Sequence> batch = new ArrayList<>(db.subList(start, start + size));
            for (int i = start; i < start + size; i++) {
                joined.computeIfAbsent(keys[i], x -> new ArrayList<>()).addAll(mine.get(i).itemsets);
            }
            start += size;
            alg.processBatch(batch, b);
            if (alg.appendedLastBatch > 0) appendedBatches++;
            long arrived = 0;
            for (int i = start - size; i < start; i++) for (Itemset is : mine.get(i).itemsets) arrived += is.items.size();
            written += alg.positionsWrittenLastBatch;
            arrivedTotal += arrived;

            for (Map.Entry<Integer, long[][]> e : snap.entrySet()) {
                int sid = alg.internalId(e.getKey());
                long[][] s = e.getValue();
                long[][] now = {toLong(alg.posItems(sid)), alg.posUtils(sid), toLong(alg.posTids(sid)), alg.posPre(sid),
                                toLong(alg.isetOffsets(sid)), toLong(alg.isetSizes(sid))};
                for (int a = 0; a < 6; a++) {
                    for (int k = 0; k < s[a].length; k++) {
                        cmp(0, now[a][k] == s[a][k], "key " + e.getKey() + " array " + a + " index " + k);
                    }
                }
            }
            checkState(alg, joined, eucsSample, b);
        }
        System.out.println("prefix_check arm " + arm + " fault " + (HAUSP_UB.FAULT == null ? "-" : HAUSP_UB.FAULT)
                + " batches " + tok.length + " batches_with_appends " + appendedBatches
                + " positions_arrived " + arrivedTotal + " positions_written " + written
                + " run_merges " + alg.rootRunMerges);
        boolean ok = true;
        for (int c = 0; c < CHECKS.length; c++) {
            System.out.println("check " + CHECKS[c] + " compared " + compared[c] + " wrong " + wrong[c]);
            ok &= wrong[c] == 0;
        }
        for (String x : examples) System.out.println("  example " + x);
        System.out.println(ok ? "PASS" : "FAIL");
        System.exit(ok ? 0 : 1);
    }

    private static void checkState(HAUSP_UB alg, Map<Integer, List<Itemset>> joined, int eucsSample, int b) {
        Map<Integer, Long> swu = new HashMap<>();
        // item -> sid -> positions, kept in sid order for the root comparison
        Map<Integer, TreeMap<Integer, List<long[]>>> occ = new HashMap<>();
        long total = 0;
        List<int[]> seqTids = new ArrayList<>();
        List<int[]> seqIds = new ArrayList<>();
        List<Long> seqUs = new ArrayList<>();
        for (Map.Entry<Integer, List<Itemset>> e : joined.entrySet()) {
            int sid = alg.internalId(e.getKey());
            List<Itemset> its = e.getValue();
            int len = 0;
            for (Itemset is : its) len += is.items.size();
            int[] ids = new int[len], tids = new int[len];
            long[] uts = new long[len];
            int[] off = new int[its.size()], sz = new int[its.size()];
            int p = 0;
            for (int t = 0; t < its.size(); t++) {
                off[t] = p;
                sz[t] = its.get(t).items.size();
                for (ItemQ it : its.get(t).items) { ids[p] = it.id; uts[p] = it.utility; tids[p] = t; p++; }
            }
            long u = 0;
            for (long x : uts) u += x;
            total += u;
            cmp(1, alg.positions(sid) == len, "key " + e.getKey() + " positions " + alg.positions(sid) + " vs " + len);
            cmp(1, alg.itemsets(sid) == its.size(), "key " + e.getKey() + " itemsets");
            cmp(1, alg.sequenceUtility(sid) == u, "key " + e.getKey() + " u(S) " + alg.sequenceUtility(sid) + " vs " + u);
            int n = Math.min(len, alg.positions(sid));
            long rest = u;
            for (int k = 0; k < n; k++) {
                rest -= uts[k];
                cmp(1, alg.posItems(sid)[k] == ids[k] && alg.posUtils(sid)[k] == uts[k] && alg.posTids(sid)[k] == tids[k],
                        "key " + e.getKey() + " position " + k);
                long rutil = alg.sequenceUtility(sid) - alg.posPre(sid)[k] - alg.posUtils(sid)[k];
                cmp(1, rutil == rest, "key " + e.getKey() + " remaining utility at " + k + ": " + rutil + " vs " + rest);
            }
            for (int t = 0; t < Math.min(its.size(), alg.itemsets(sid)); t++) {
                cmp(1, alg.isetOffsets(sid)[t] == off[t] && alg.isetSizes(sid)[t] == sz[t], "key " + e.getKey() + " itemset " + t);
            }
            java.util.HashSet<Integer> distinct = new java.util.HashSet<>();
            for (int k = 0; k < len; k++) {
                distinct.add(ids[k]);
                occ.computeIfAbsent(ids[k], x -> new TreeMap<>()).computeIfAbsent(sid, x -> new ArrayList<>())
                        .add(new long[]{k, uts[k], u - (k == 0 ? 0 : sumTo(uts, k)) - uts[k]});
            }
            for (int it : distinct) swu.merge(it, u, Long::sum);
            seqIds.add(ids);
            seqTids.add(tids);
            seqUs.add(u);
        }
        cmp(2, alg.totalUtility() == total, "batch " + b + " totalU " + alg.totalUtility() + " vs " + total);
        for (Map.Entry<Integer, Long> e : swu.entrySet()) {
            cmp(2, alg.swu(e.getKey()) == e.getValue(), "item " + e.getKey() + " SWU " + alg.swu(e.getKey()) + " vs " + e.getValue());
        }
        if (alg.eucsOn()) checkEucs(alg, joined, seqIds, seqTids, seqUs, swu.size(), eucsSample, b);
        for (Map.Entry<Integer, TreeMap<Integer, List<long[]>>> e : occ.entrySet()) {
            long[] got = alg.rootSnapshot(e.getKey());
            long iu = 0, mf = 0, sup = 0;
            List<long[]> want = new ArrayList<>();
            for (Map.Entry<Integer, List<long[]>> s : e.getValue().entrySet()) {
                long mi = Long.MIN_VALUE, mm = Long.MIN_VALUE;
                for (long[] o : s.getValue()) {
                    want.add(new long[]{s.getKey(), o[0], o[1]});
                    mi = Math.max(mi, o[1]);
                    mm = Math.max(mm, o[1] + o[2]);
                }
                iu += mi;
                mf += mm;
                sup++;
            }
            cmp(4, got[0] == iu && got[1] == mf && got[2] == sup,
                    "item " + e.getKey() + " sums (" + got[0] + "," + got[1] + "," + got[2] + ") vs (" + iu + "," + mf + "," + sup + ")");
            boolean same = (got.length - 3) / 3 == want.size();
            for (int i = 0; same && i < want.size(); i++) {
                same = got[3 + 3 * i] == want.get(i)[0] && got[4 + 3 * i] == want.get(i)[1] && got[5 + 3 * i] == want.get(i)[2];
            }
            cmp(4, same, "item " + e.getKey() + " entries");
        }
    }

    private static long sumTo(long[] a, int k) {
        long s = 0;
        for (int i = 0; i < k; i++) s += a[i];
        return s;
    }

    private static void checkEucs(HAUSP_UB alg, Map<Integer, List<Itemset>> joined, List<int[]> seqIds,
                                  List<int[]> seqTids, List<Long> seqUs, int nItems, int sample, int b) {
        // pairs to check
        java.util.TreeSet<Integer> items = new java.util.TreeSet<>();
        for (int[] ids : seqIds) for (int x : ids) items.add(x);
        List<int[]> pairs = new ArrayList<>();
        if (items.size() <= 200) {
            for (int a : items) for (int c : items) pairs.add(new int[]{a, c});
        } else {
            Random rng = new Random(20261111L + b);
            java.util.HashSet<Long> seen = new java.util.HashSet<>();
            for (int t = 0; t < sample && !seqIds.isEmpty(); t++) {
                int[] ids = seqIds.get(rng.nextInt(seqIds.size()));
                if (ids.length == 0) continue;
                int a = ids[rng.nextInt(ids.length)], c = ids[rng.nextInt(ids.length)];
                if (seen.add(((long) a << 32) | (c & 0xFFFFFFFFL))) pairs.add(new int[]{a, c});
            }
            // and pairs of an item with one it never meets, which must stay 0
            Integer[] all = items.toArray(new Integer[0]);
            for (int t = 0; t < sample / 4; t++) pairs.add(new int[]{all[rng.nextInt(all.length)], all[rng.nextInt(all.length)]});
        }
        // expected values over every sequence, from first/last itemset indices and itemset membership
        Map<Long, long[]> want = new HashMap<>();
        for (int[] pr : pairs) want.put(((long) pr[0] << 32) | (pr[1] & 0xFFFFFFFFL), new long[3]);
        java.util.HashSet<Integer> needed = new java.util.HashSet<>();
        for (int[] pr : pairs) { needed.add(pr[0]); needed.add(pr[1]); }
        for (int s = 0; s < seqIds.size(); s++) {
            int[] ids = seqIds.get(s), tids = seqTids.get(s);
            long u = seqUs.get(s);
            Map<Integer, int[]> fl = new HashMap<>();
            Map<Integer, java.util.HashSet<Integer>> isetsOf = new HashMap<>();
            for (int k = 0; k < ids.length; k++) {
                if (!needed.contains(ids[k])) continue;
                int[] f = fl.computeIfAbsent(ids[k], x -> new int[]{Integer.MAX_VALUE, -1});
                f[0] = Math.min(f[0], tids[k]);
                f[1] = Math.max(f[1], tids[k]);
                isetsOf.computeIfAbsent(ids[k], x -> new java.util.HashSet<>()).add(tids[k]);
            }
            for (int[] pr : pairs) {
                int[] fa = fl.get(pr[0]), fb = fl.get(pr[1]);
                if (fa == null || fb == null) continue;
                long[] w = want.get(((long) pr[0] << 32) | (pr[1] & 0xFFFFFFFFL));
                if (pr[0] == pr[1]) {
                    if (fa[0] < fa[1]) w[0] += u;
                } else {
                    if (fa[0] < fb[1]) w[1] += u;
                    java.util.HashSet<Integer> common = new java.util.HashSet<>(isetsOf.get(pr[0]));
                    common.retainAll(isetsOf.get(pr[1]));
                    if (!common.isEmpty()) w[2] += u;
                }
            }
        }
        java.util.HashSet<Long> done = new java.util.HashSet<>();
        for (int[] pr : pairs) {
            long key = ((long) pr[0] << 32) | (pr[1] & 0xFFFFFFFFL);
            if (!done.add(key)) continue;
            long[] w = want.get(key);
            if (pr[0] == pr[1]) {
                cmp(3, alg.selfEucs(pr[0]) == w[0], "self " + pr[0] + ": " + alg.selfEucs(pr[0]) + " vs " + w[0]);
            } else {
                cmp(3, alg.sEucs(pr[0], pr[1]) == w[1], "seq " + pr[0] + "," + pr[1] + ": " + alg.sEucs(pr[0], pr[1]) + " vs " + w[1]);
                cmp(3, alg.iEucs(pr[0], pr[1]) == w[2], "iset " + pr[0] + "," + pr[1] + ": " + alg.iEucs(pr[0], pr[1]) + " vs " + w[2]);
            }
        }
    }

    private static long[] toLong(int[] a) {
        long[] o = new long[a.length];
        for (int i = 0; i < a.length; i++) o[i] = a[i];
        return o;
    }

    private static int[] readKeys(String arg, int n) throws IOException {
        String text;
        if (arg.startsWith("@")) {
            StringBuilder sb = new StringBuilder();
            try (BufferedReader br = new BufferedReader(new FileReader(arg.substring(1)))) {
                String line;
                while ((line = br.readLine()) != null) sb.append(line.trim()).append(',');
            }
            text = sb.toString();
        } else {
            text = arg;
        }
        String[] t = Arrays.stream(text.split(",")).filter(x -> !x.isEmpty()).toArray(String[]::new);
        if (t.length != n) {
            System.err.println(t.length + " keys for " + n + " lines");
            System.exit(2);
        }
        int[] k = new int[n];
        for (int i = 0; i < n; i++) k[i] = Integer.parseInt(t[i].trim());
        return k;
    }
}
