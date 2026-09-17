import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Reader for the quantitative sequential database format used in this project.
 *
 * <p>Two files describe each dataset.
 *
 * <p>The <em>external-utility</em> file lists one item per line as
 * {@code itemID:profit} (the comma {@code ,} is accepted as an alternative
 * separator); lines starting with {@code #} or {@code @} are treated as
 * comments.
 *
 * <p>The <em>sequence</em> file lists one quantitative sequence per line. Each
 * token is either {@code itemID[quantity]}, {@code -1} to close the current
 * itemset, or {@code -2} to close the sequence. Internal utility is computed
 * as {@code quantity × externalUtility} during parsing.
 *
 * <p>Sequence identifiers are assigned automatically and are zero-based.
 */
public class QSDB_Parser {

    /** Parse a single {@code itemID[quantity]} token; defaults to quantity 1 if no brackets. */
    /**
     * Append {@code itemset} to {@code seq} and return a fresh one to fill.
     *
     * <p>INVARIANT: items inside an itemset are stored in increasing id order. The miner decides
     * the legality of an I-extension by POSITION, the extending item sitting at a later flat
     * position of the same itemset, and that test equals "greater than every item of the parent's
     * last itemset" only while this order holds. Remove the sort and the search both admits
     * illegal candidates and misses legal ones.
     *
     * <p>Every path that closes an itemset goes through here, so the order cannot depend on how
     * the line happened to end.
     */
    private static Itemset closeItemset(Sequence seq, Itemset itemset) {
        if (!itemset.items.isEmpty()) {
            itemset.items.sort(Comparator.comparingInt(a -> a.id));
            seq.addItemset(itemset);
        }
        return new Itemset();
    }

    private static ItemQ parseItem(String token, Map<Integer, Long> euiTable) {
        try {
            int bracketStart = token.indexOf('[');
            int bracketEnd = token.indexOf(']');

            if (bracketStart == -1 || bracketEnd == -1) {
                int itemId = Integer.parseInt(token);
                if (itemId < 0) return null;
                long profit = euiTable.getOrDefault(itemId, 1L);
                return new ItemQ(itemId, 1, profit);
            }

            int itemId = Integer.parseInt(token.substring(0, bracketStart));
            int quantity = Integer.parseInt(token.substring(bracketStart + 1, bracketEnd));
            long profit = euiTable.getOrDefault(itemId, 1L);

            long utility = (long) quantity * profit;
            return new ItemQ(itemId, quantity, utility);

        } catch (NumberFormatException e) {
            System.err.println("[QSDB_Parser] Cannot parse token: " + token);
            return null;
        }
    }

    /** Load the entire database, reading the EUI file inline. */
    public static List<Sequence> loadDB(String euiPath, String seqPath) {
        List<Sequence> database = new ArrayList<>();
        Map<Integer, Long> euiTable = new HashMap<>();

        try (BufferedReader br = new BufferedReader(new FileReader(euiPath))) {
            String line;
            while ((line = br.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty() || line.startsWith("#") || line.startsWith("@")) continue;
                String[] parts = line.split("[:\\s\\t,]+");
                if (parts.length >= 2) {
                    euiTable.put(Integer.parseInt(parts[0]), Long.parseLong(parts[1]));
                }
            }
        } catch (IOException e) {
            System.err.println("[QSDB_Parser] Error reading EUI file: " + e.getMessage());
            return database;
        }

        try (BufferedReader br = new BufferedReader(new FileReader(seqPath))) {
            String line;
            int sid = 0;
            while ((line = br.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty() || line.startsWith("#") || line.startsWith("@")) continue;

                Sequence seq = new Sequence(sid++);
                Itemset currentItemset = new Itemset();
                String[] tokens = line.split("\\s+");

                for (String token : tokens) {
                    if (token.equals("-1")) {
                        currentItemset = closeItemset(seq, currentItemset);
                    } else if (token.equals("-2")) {
                        break;
                    } else {
                        ItemQ item = parseItem(token, euiTable);
                        if (item != null) {
                            currentItemset.items.add(item);
                        }
                    }
                }
                // A line whose last itemset is not closed by -1, or that carries no -2 at all,
                // used to lose that itemset or the whole sequence. Close it here, once: the
                // helper appends only a non-empty itemset, so a well-formed line is unaffected.
                closeItemset(seq, currentItemset);
                if (!seq.itemsets.isEmpty()) {
                    database.add(seq);
                }
            }
        } catch (IOException e) {
            System.err.println("[QSDB_Parser] Error reading sequence file: " + e.getMessage());
        }

        return database;
    }

    /**
     * Load the database and split it into consecutive batches according to the
     * supplied size ratios. The final batch absorbs any remainder so that the
     * sum of returned batch sizes always equals the total database size.
     */
    public static List<List<Sequence>> loadDBByRatios(String euiPath, String seqPath, double[] ratios) {
        List<Sequence> allSequences = loadDB(euiPath, seqPath);
        if (allSequences == null || allSequences.isEmpty()) return new ArrayList<>();

        int totalSize = allSequences.size();
        List<List<Sequence>> batches = new ArrayList<>();

        int currentIndex = 0;
        for (int i = 0; i < ratios.length; i++) {
            int batchSize = (i == ratios.length - 1)
                    ? (totalSize - currentIndex)
                    : (int) Math.round(totalSize * ratios[i]);

            List<Sequence> batch = new ArrayList<>();
            for (int j = 0; j < batchSize && currentIndex < totalSize; j++) {
                batch.add(allSequences.get(currentIndex++));
            }
            batches.add(batch);
        }
        return batches;
    }
}
