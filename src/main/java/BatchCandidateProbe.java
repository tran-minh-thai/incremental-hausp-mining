import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Feasibility probe for an update of the result set that does not run the whole search again
 * (opened 2026-10-16). It counts; nothing it prints is a timing a table reads.
 *
 * <p>The update rests on two properties that follow from the batch-additive decomposition, for
 * insertions of new sequences with nonnegative utilities. A HAUSP of DB^(b-1) stays one at batch b
 * exactly when its stored evalIutil plus its evalIutil on the batch reaches theta(b) |alpha|. A
 * pattern that is not a HAUSP of DB^(b-1) and is one of DB^(b) must satisfy evalIutil on the batch
 * &gt; minUtil * totalU(batch) * |alpha|, i.e. be a HAUSP of the batch alone at the same relative
 * threshold. Every item of a HAUSP of length at least 2 of DB^(b) has SWU over DB^(b) of at least
 * 2 theta(b). So the candidates for promotion at batch b are the HAUSPs of the batch, restricted to
 * those items, at threshold minUtil * totalU(batch), where totalU is that of the unrestricted batch.
 *
 * <p>For a database split as the runners split it (consecutive batches in file order, sizes
 * round(N * ratio), the last one taking the remainder) into K equal batches, and one batch b &gt;= 1,
 * this prints one line with the size of that candidate set and of the search that finds it. It
 * mines the restricted batch with the full algorithm, so the candidate set is exact for the stated
 * threshold.
 *
 * <pre>
 * java -cp build/incremental-hausp-mining-1.0.0.jar BatchCandidateProbe \
 *      &lt;eui&gt; &lt;seq&gt; &lt;properties&gt; &lt;minUtil&gt; &lt;K&gt; &lt;b&gt; [verify]
 * </pre>
 *
 * <p>With {@code verify} it also runs the incremental miner over batches 0..b with pattern dumps
 * and checks that every pattern of length at least 2 reported at batch b and not at batch b-1 is
 * among the candidates; it prints how many such patterns it could compare, since a check over none
 * checks nothing. Dumps go to ./out/, so run it from a scratch directory, on small databases.
 */
public final class BatchCandidateProbe {

    private BatchCandidateProbe() { }

    public static void main(String[] args) throws Exception {
        if (args.length < 6 || args.length > 7 || (args.length == 7 && !args[6].equals("verify"))) {
            System.err.println("usage: BatchCandidateProbe <eui> <seq> <properties> <minUtil> <K> <b> [verify]");
            System.exit(2);
        }
        double minUtil = Double.parseDouble(args[3]);
        int k = Integer.parseInt(args[4]);
        int b = Integer.parseInt(args[5]);
        boolean verify = args.length == 7;
        if (k < 2 || b < 1 || b >= k) {
            System.err.println("need K >= 2 and 1 <= b < K");
            System.exit(2);
        }
        List<List<Sequence>> batches = QSDB_Parser.loadDBByRatios(args[0], args[1], ExperimentConfig.equalSchedule(k));
        if (batches.size() != k) {
            System.err.println("the database split into " + batches.size() + " batches, not " + k);
            System.exit(2);
        }

        long totalU = 0;
        Map<Integer, Long> swu = new HashMap<>();
        for (int i = 0; i <= b; i++) {
            for (Sequence s : batches.get(i)) {
                totalU += s.totalUtility;
                Set<Integer> seen = new HashSet<>();
                for (Itemset is : s.itemsets) {
                    for (ItemQ q : is.items) {
                        if (seen.add(q.id)) swu.merge(q.id, s.totalUtility, Long::sum);
                    }
                }
            }
        }
        double theta = minUtil * totalU;
        Set<Integer> allowed = new HashSet<>();
        for (Map.Entry<Integer, Long> e : swu.entrySet()) {
            if (e.getValue() >= 2 * theta) allowed.add(e.getKey());
        }

        List<Sequence> delta = batches.get(b);
        long deltaU = 0, keptU = 0, keptItems = 0, deltaItems = 0;
        List<Sequence> restricted = new ArrayList<>();
        for (Sequence s : delta) {
            deltaU += s.totalUtility;
            Sequence r = new Sequence(s.sid);
            for (Itemset is : s.itemsets) {
                Itemset ri = new Itemset();
                for (ItemQ q : is.items) {
                    deltaItems++;
                    if (allowed.contains(q.id)) {
                        ri.addItem(new ItemQ(q.id, q.quantity, q.utility));
                        keptItems++;
                    }
                }
                if (!ri.items.isEmpty()) r.addItemset(ri);
            }
            if (!r.itemsets.isEmpty()) {
                restricted.add(r);
                keptU += r.totalUtility;
            }
        }
        double thetaDelta = minUtil * deltaU;

        String line = "probe K=" + k + " b=" + b + " theta=" + theta + " theta_delta=" + thetaDelta
                + " items_seen=" + swu.size() + " items_allowed=" + allowed.size()
                + " delta_sequences=" + delta.size() + " delta_items=" + deltaItems
                + " restricted_sequences=" + restricted.size() + " restricted_items=" + keptItems;
        if (restricted.isEmpty() || keptU == 0) {
            System.out.println(line + " candidates=0 cand_lists=0 cand_recursed=0 cand_cpu_ms=0");
            return;
        }
        HAUSP_UB miner = HAUSP_UB.fromArmName("HAUSP-UB[noEUCS]", deltaProperties(args[2]));
        miner.setConfig(thetaDelta / keptU);    // its threshold is minUtil x totalU of what it mines
        miner.enableIO = verify;
        RunResult r = miner.processBatch(restricted, 0);
        System.out.println(line + " candidates=" + r.hauspFound + " cand_lists=" + r.numCand
                + " cand_recursed=" + r.numRecursed + " cand_cpu_ms=" + r.tTotal);
        System.out.flush();

        if (verify) verify(batches, b, minUtil, args[2], thetaDelta / keptU);
    }

    /** A properties file naming a different dataset, so that the batch miner's dump does not take
     *  the name of the incremental miner's. */
    private static String deltaProperties(String props) throws IOException {
        java.util.Properties p = new java.util.Properties();
        try (FileReader fr = new FileReader(props)) { p.load(fr); }
        String path = p.getProperty("dataset.path", "unknown_seq.txt");
        p.setProperty("dataset.path", path.replace("_seq.txt", "") + "-batchonly_seq.txt");
        File f = File.createTempFile("probe-batch", ".properties");
        f.deleteOnExit();
        try (java.io.FileWriter fw = new java.io.FileWriter(f)) { p.store(fw, null); }
        return f.getPath();
    }

    private static void verify(List<List<Sequence>> batches, int b, double minUtil, String props, double deltaMu)
            throws IOException {
        HAUSP_UB inc = HAUSP_UB.fromArmName("HAUSP-UB[noEUCS]", props);
        inc.setConfig(minUtil);
        inc.enableIO = true;
        for (int i = 0; i <= b; i++) inc.processBatch(new ArrayList<>(batches.get(i)), i);
        java.util.Properties p = new java.util.Properties();
        try (FileReader fr = new FileReader(props)) { p.load(fr); }
        String name = new File(p.getProperty("dataset.path", "unknown")).getName().replace("_seq.txt", "");
        Set<String> before = patterns("out/HAUSP_" + name + "_B" + (b - 1) + "_mu" + minUtil + ".txt");
        Set<String> after = patterns("out/HAUSP_" + name + "_B" + b + "_mu" + minUtil + ".txt");
        Set<String> cand = patterns("out/HAUSP_" + name + "-batchonly_B0_mu" + deltaMu + ".txt");
        int promoted = 0, missing = 0;
        String firstMissing = "";
        for (String a : after) {
            if (before.contains(a) || items(a) < 2) continue;
            promoted++;
            if (!cand.contains(a)) {
                missing++;
                if (firstMissing.isEmpty()) firstMissing = a;
            }
        }
        System.out.println("verify b=" + b + " hausp_before=" + before.size() + " hausp_after=" + after.size()
                + " promoted_len2plus=" + promoted + " candidates=" + cand.size() + " missing=" + missing
                + (missing > 0 ? " first_missing=" + firstMissing : ""));
    }

    private static Set<String> patterns(String path) throws IOException {
        Set<String> out = new HashSet<>();
        File f = new File(path);
        if (!f.exists()) throw new IOException("pattern dump " + path + " was not written");
        try (BufferedReader br = new BufferedReader(new FileReader(f))) {
            String line;
            while ((line = br.readLine()) != null) {
                int tab = line.indexOf('\t');
                if (tab > 0) out.add(line.substring(0, tab));
            }
        }
        return out;
    }

    /** Number of items in a pattern written as {@code <(a,b)(c)>}. */
    private static int items(String pattern) {
        int n = 0;
        boolean inToken = false;
        for (char c : pattern.toCharArray()) {
            boolean digit = Character.isDigit(c);
            if (digit && !inToken) n++;
            inToken = digit;
        }
        return n;
    }
}
