import java.util.ArrayList;
import java.util.List;

/**
 * Runs HAUSP-UB in top-k mode over a database split into consecutive batches and prints, after
 * every batch, the exact k-th value and every pattern of the result with its utility and length.
 * It drives {@link HAUSP_UB} through the same public calls the runners use; analysis/verify_topk.py
 * compares its output with a top-k computed from the definitions.
 *
 * <pre>
 * java -cp build/incremental-hausp-mining-1.0.0.jar TopKProbe \
 *      &lt;eui&gt; &lt;seq&gt; &lt;properties&gt; &lt;k&gt; &lt;warm|cold|mu=X&gt; &lt;batch sizes, e.g. 3,1,1&gt; [keys] [arm]
 * </pre>
 *
 * <p>{@code mu=X} runs the threshold mode at minUtil X instead of top-k and prints its patterns.
 * {@code keys} is a comma-separated key per line of the sequence file (default: the line number); a
 * line whose key appeared on an earlier line carries itemsets appended to that sequence, which is
 * how the mixed update model is fed. {@code arm} names the arm (default HAUSP-UB[noEUCS]); use
 * {@code -} to keep a default.
 *
 * <p>Output, one fact per line:
 * <pre>
 * batch &lt;b&gt; kth_util &lt;u&gt; kth_len &lt;l&gt; result &lt;n&gt; lists &lt;c&gt; nodes &lt;r&gt;
 * pattern &lt;b&gt; &lt;pattern&gt; &lt;utility&gt; &lt;length&gt;
 * </pre>
 */
public final class TopKProbe {

    private TopKProbe() { }

    public static void main(String[] args) {
        if (args.length < 6 || args.length > 8
                || !(args[4].equals("warm") || args[4].equals("cold") || args[4].startsWith("mu="))) {
            System.err.println("usage: TopKProbe <eui> <seq> <properties> <k> <warm|cold|mu=X> <batch sizes> [keys] [arm]");
            System.exit(2);
        }
        List<Sequence> db = QSDB_Parser.loadDB(args[0], args[1]);
        if (args.length >= 7 && !args[6].equals("-")) {
            String[] keys = args[6].split(",");
            if (keys.length != db.size()) {
                System.err.println(keys.length + " keys for " + db.size() + " lines");
                System.exit(2);
            }
            for (int i = 0; i < keys.length; i++) db.get(i).sid = Integer.parseInt(keys[i].trim());
        }
        String arm = args.length == 8 && !args[7].equals("-") ? args[7] : "HAUSP-UB[noEUCS]";
        boolean threshold = args[4].startsWith("mu=");
        int k = Integer.parseInt(args[3]);
        String[] tok = args[5].split(",");
        int[] sizes = new int[tok.length];
        int sum = 0;
        for (int i = 0; i < tok.length; i++) {
            sizes[i] = Integer.parseInt(tok[i].trim());
            sum += sizes[i];
        }
        if (sum != db.size()) {
            System.err.println("batch sizes add up to " + sum + " but the database holds " + db.size() + " sequences");
            System.exit(2);
        }
        HAUSP_UB alg = HAUSP_UB.fromArmName(arm, args[2]);
        alg.setConfig(threshold ? Double.parseDouble(args[4].substring(3)) : 0.0);
        alg.topK = threshold ? 0 : k;
        alg.warmStart = args[4].equals("warm");
        alg.enableIO = threshold;
        int start = 0;
        for (int b = 0; b < sizes.length; b++) {
            List<Sequence> batch = new ArrayList<>(db.subList(start, start + sizes[b]));
            start += sizes[b];
            RunResult r = alg.processBatch(batch, b);
            long[] kth = alg.topKValue();
            System.out.println("batch " + b + " kth_util " + kth[0] + " kth_len " + kth[1] + " result " + r.hauspFound
                    + " lists " + r.numCand + " nodes " + r.numRecursed + " appended " + alg.appendedLastBatch
                    + " sequences " + r.cumulativeDBSize);
            if (threshold) {
                java.io.File dump = new java.io.File("out/HAUSP_" + new java.io.File(propsDataset(args[2])).getName()
                        .replace("_seq.txt", "") + "_B" + b + "_mu" + alg.minUtilPercentage + ".txt");
                try (java.io.BufferedReader br = new java.io.BufferedReader(new java.io.FileReader(dump))) {
                    String line;
                    while ((line = br.readLine()) != null) {
                        int tab = line.indexOf('\t');
                        if (tab > 0) System.out.println("pattern " + b + " " + line.substring(0, tab) + " - -");
                    }
                } catch (java.io.IOException e) {
                    System.err.println("no pattern dump " + dump);
                    System.exit(3);
                }
                continue;
            }
            for (HAUSP_UB.TopKEntry e : alg.topKResult()) {
                System.out.println("pattern " + b + " " + render(e.pattern) + " " + e.util + " " + e.len);
            }
        }
    }

    private static String propsDataset(String props) {
        java.util.Properties p = new java.util.Properties();
        try (java.io.FileReader fr = new java.io.FileReader(props)) { p.load(fr); } catch (java.io.IOException e) { return "unknown_seq.txt"; }
        return p.getProperty("dataset.path", "unknown_seq.txt");
    }

    private static String render(int[] p) {
        StringBuilder sb = new StringBuilder("<(");
        for (int i = 0; i < p.length; i++) {
            if (p[i] == -1) sb.append(")(");
            else {
                if (i > 0 && p[i - 1] != -1) sb.append(",");
                sb.append(p[i]);
            }
        }
        return sb.append(")>").toString();
    }
}
