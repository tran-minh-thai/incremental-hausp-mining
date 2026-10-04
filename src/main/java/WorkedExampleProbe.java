import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.List;

/**
 * Runs the presented arm of HAUSP-UB on a small database split into batches, and prints what a
 * check of the worked example needs: the accumulated total utility and the pattern count after
 * every batch, and the evaluated values of chosen single-item roots after the last batch.
 *
 * <p>It drives {@link HAUSP_UB} through the same public calls the experiment runners use and reads
 * the private root array by reflection, so nothing on the timed code path changes. The miner writes
 * its pattern files to {@code ./out/}, so run this from a scratch directory.
 *
 * <pre>
 * java -cp build/incremental-hausp-mining-1.0.0.jar WorkedExampleProbe \
 *      &lt;eui file&gt; &lt;seq file&gt; &lt;properties file&gt; &lt;minUtil&gt; &lt;batch sizes, e.g. 4,1&gt; &lt;root item ids, e.g. 1&gt;
 * </pre>
 *
 * <p>Output lines, one fact each:
 * <pre>
 * batch &lt;b&gt; total_utility &lt;u&gt; hausp &lt;n&gt;
 * root &lt;item id&gt; evalIutil &lt;v&gt; evalMFUUB &lt;w&gt;
 * </pre>
 */
public final class WorkedExampleProbe {

    private WorkedExampleProbe() { }

    public static void main(String[] args) throws Exception {
        if (args.length != 6) {
            System.err.println("usage: WorkedExampleProbe <eui> <seq> <properties> <minUtil> <batch sizes> <root item ids>");
            System.exit(2);
        }
        List<Sequence> db = QSDB_Parser.loadDB(args[0], args[1]);
        double minUtil = Double.parseDouble(args[3]);
        String[] sizeTokens = args[4].split(",");
        int[] sizes = new int[sizeTokens.length];
        int sum = 0;
        for (int i = 0; i < sizes.length; i++) {
            sizes[i] = Integer.parseInt(sizeTokens[i].trim());
            sum += sizes[i];
        }
        if (sum != db.size()) {
            System.err.println("batch sizes add up to " + sum + " but the database holds " + db.size() + " sequences");
            System.exit(2);
        }

        HAUSP_UB alg = HAUSP_UB.fromArmName("HAUSP-UB[noEUCS]", args[2]);
        alg.setConfig(minUtil);
        alg.enableIO = true;
        int start = 0;
        for (int b = 0; b < sizes.length; b++) {
            List<Sequence> batch = new ArrayList<>(db.subList(start, start + sizes[b]));
            start += sizes[b];
            RunResult r = alg.processBatch(batch, b);
            System.out.println("batch " + b + " total_utility " + r.totalDBUtility + " hausp " + r.hauspFound);
        }

        Field rootsField = HAUSP_UB.class.getDeclaredField("globalAUDULs");
        rootsField.setAccessible(true);
        Object[] roots = (Object[]) rootsField.get(alg);
        for (String token : args[5].split(",")) {
            int itemId = Integer.parseInt(token.trim());
            HAUSP_UB.AUDUL root = itemId < roots.length ? (HAUSP_UB.AUDUL) roots[itemId] : null;
            if (root == null) {
                System.out.println("root " + itemId + " absent");
                continue;
            }
            root.evaluate();
            System.out.println("root " + itemId + " evalIutil " + root.evalIutil + " evalMFUUB " + root.evalMFUUB);
        }
    }
}
