// SPDX-License-Identifier: MIT
import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/**
 * Exactness of an incremental arm under the mixed update model, checked batch by batch against an independent
 * re-mining of the accumulated database.
 *
 * <p>The sequence file holds one line per (key, batch): a line whose key appeared on an earlier line carries the
 * itemsets appended to that key's sequence. The arm under test receives the lines batch by batch through
 * {@link HAUSP_UB#processBatch}, exactly as the runners feed it. The reference is {@link EHAUSM_Remining}, a separate
 * implementation, run from a fresh instance after every batch on the accumulated database built here: the file is
 * parsed a second time and the lines of each key are concatenated in arrival order. The two runs share only the
 * parser and the item classes. Both write their pattern dumps (pattern, average utility to two decimals, support);
 * the sets of dump lines are compared.
 *
 * <pre>
 * java -cp build/incremental-hausp-mining-1.0.0.jar ExactnessProbe \
 *      &lt;eui&gt; &lt;seq&gt; &lt;properties&gt; &lt;xi&gt; &lt;batch sizes&gt; &lt;@keys file&gt; &lt;arm&gt; &lt;fault or -&gt;
 * </pre>
 *
 * <p>Output, one line per batch, then at most five differing dump lines per side:
 * <pre>
 * batch &lt;b&gt; lines &lt;n&gt; appended &lt;a&gt; sequences &lt;s&gt; total_utility &lt;U&gt; arm &lt;p&gt; reference &lt;q&gt; only_arm &lt;x&gt; only_reference &lt;y&gt;
 * diff &lt;b&gt; arm|reference &lt;dump line&gt;
 * </pre>
 * The working directory receives out/; run it in a directory of its own.
 */
public final class ExactnessProbe {

    private ExactnessProbe() { }

    public static void main(String[] args) throws IOException {
        if (args.length != 8 || !args[5].startsWith("@")) {
            System.err.println("usage: ExactnessProbe <eui> <seq> <properties> <xi> <batch sizes> <@keys> <arm> <fault|->");
            System.exit(2);
        }
        double xi = Double.parseDouble(args[3]);
        int[] keys = readKeys(args[5].substring(1));
        List<Sequence> forArm = QSDB_Parser.loadDB(args[0], args[1]);
        List<Sequence> forRef = QSDB_Parser.loadDB(args[0], args[1]);
        if (keys.length != forArm.size() || keys.length != forRef.size()) {
            System.err.println(keys.length + " keys for " + forArm.size() + " lines");
            System.exit(2);
        }
        for (int i = 0; i < keys.length; i++) {
            forArm.get(i).sid = keys[i];
            forRef.get(i).sid = keys[i];
        }
        String[] tok = args[4].split(",");
        int[] sizes = new int[tok.length];
        int sum = 0;
        for (int i = 0; i < tok.length; i++) sum += sizes[i] = Integer.parseInt(tok[i].trim());
        if (sum != keys.length) {
            System.err.println("batch sizes add up to " + sum + " but the file holds " + keys.length + " lines");
            System.exit(2);
        }
        HAUSP_UB.FAULT = args[7].equals("-") ? null : args[7];
        HAUSP_UB arm = HAUSP_UB.fromArmName(args[6], args[2]);
        arm.setConfig(xi);
        arm.topK = 0;
        arm.enableIO = true;

        Map<Integer, Sequence> accumulated = new LinkedHashMap<>();
        File out = new File("out");
        int start = 0;
        int failures = 0;
        // -Dhausp.reuseReference=true: one reference object for every batch, as a campaign runner uses it
        boolean reuse = Boolean.getBoolean("hausp.reuseReference");
        EHAUSM_Remining kept = null;
        for (int b = 0; b < sizes.length; b++) {
            List<Sequence> batch = new ArrayList<>(forArm.subList(start, start + sizes[b]));
            int appended = 0;
            for (Sequence part : forRef.subList(start, start + sizes[b])) {
                Sequence old = accumulated.get(part.sid);
                if (old == null) {
                    accumulated.put(part.sid, copy(part, null));
                } else {
                    appended++;
                    accumulated.put(part.sid, copy(part, old));
                }
            }
            start += sizes[b];
            clear(out);
            arm.processBatch(batch, b);
            EHAUSM_Remining ref = reuse && kept != null ? kept : new EHAUSM_Remining(args[2]);
            kept = ref;
            ref.setConfig(xi);
            ref.enableIO = true;
            List<Sequence> db = new ArrayList<>(accumulated.values());
            ref.processBatch(db, b);
            long total = 0;
            for (Sequence s : db) total += s.totalUtility;
            TreeSet<String> a = dump(out, "HAUSP_", b);
            TreeSet<String> r = dump(out, "EHAUSM_Remining_", b);
            TreeSet<String> onlyA = new TreeSet<>(a);
            onlyA.removeAll(r);
            TreeSet<String> onlyR = new TreeSet<>(r);
            onlyR.removeAll(a);
            System.out.println("batch " + b + " lines " + sizes[b] + " appended " + appended + " sequences " + db.size()
                    + " total_utility " + total + " arm " + a.size() + " reference " + r.size()
                    + " only_arm " + onlyA.size() + " only_reference " + onlyR.size());
            final int b0 = b;
            onlyA.stream().limit(5).forEach(l -> System.out.println("diff " + b0 + " arm " + l));
            onlyR.stream().limit(5).forEach(l -> System.out.println("diff " + b0 + " reference " + l));
            if (!onlyA.isEmpty() || !onlyR.isEmpty()) failures++;
        }
        System.exit(failures == 0 ? 0 : 1);
    }

    /** A new sequence holding the itemsets of {@code head} (if any) followed by those of {@code tail}. */
    private static Sequence copy(Sequence tail, Sequence head) {
        Sequence s = new Sequence(tail.sid);
        if (head != null) for (Itemset is : head.itemsets) s.addItemset(is);
        for (Itemset is : tail.itemsets) s.addItemset(is);
        return s;
    }

    private static int[] readKeys(String path) throws IOException {
        List<Integer> k = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(new FileReader(path))) {
            String line;
            while ((line = br.readLine()) != null) {
                for (String t : line.split(",")) if (!t.isBlank()) k.add(Integer.parseInt(t.trim()));
            }
        }
        return k.stream().mapToInt(Integer::intValue).toArray();
    }

    private static void clear(File out) {
        File[] f = out.listFiles();
        if (f != null) for (File x : f) if (!x.delete()) throw new IllegalStateException("cannot delete " + x);
    }

    /** The pattern lines of the one dump file a run wrote for batch {@code b}; it is an error if there is not exactly one. */
    private static TreeSet<String> dump(File out, String prefix, int b) throws IOException {
        File[] f = out.listFiles((d, n) -> n.startsWith(prefix) && n.contains("_B" + b + "_"));
        if (f == null || f.length != 1) {
            throw new IllegalStateException((f == null ? 0 : f.length) + " dump files " + prefix + "*_B" + b + "_* in " + out);
        }
        TreeSet<String> lines = new TreeSet<>();
        try (BufferedReader br = new BufferedReader(new FileReader(f[0]))) {
            String line;
            while ((line = br.readLine()) != null) if (line.startsWith("<(")) lines.add(line);
        }
        return lines;
    }
}
