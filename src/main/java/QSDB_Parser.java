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

    /** Parse an EUI file into an item-id to external-utility table. */
    public static Map<Integer, Long> parseEUITable(String euiPath) throws IOException {
        Map<Integer, Long> euiTable = new HashMap<>();

        try (BufferedReader br = new BufferedReader(new FileReader(euiPath))) {
            String line;
            while ((line = br.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty() || line.startsWith("#") || line.startsWith("@")) continue;

                String[] parts;
                if (line.contains(":")) {
                    parts = line.split(":");
                } else if (line.contains(",")) {
                    parts = line.split(",");
                } else {
                    System.err.println("[QSDB_Parser] Malformed EUI line, ignored: " + line);
                    continue;
                }

                if (parts.length >= 2) {
                    try {
                        int itemId = Integer.parseInt(parts[0].trim());
                        long profit = Long.parseLong(parts[1].trim());
                        euiTable.put(itemId, profit);
                    } catch (NumberFormatException e) {
                        System.err.println("[QSDB_Parser] Cannot parse EUI line: " + line);
                    }
                }
            }
        }

        System.out.println("[QSDB_Parser] Loaded EUI: " + euiTable.size() + " items");
        return euiTable;
    }

    /**
     * Parse the sequence file into a list of {@link Sequence}s using the given
     * external-utility table. Items inside an itemset are sorted by id.
     */
    public static List<Sequence> parseDatabase(String seqPath, Map<Integer, Long> euiTable)
            throws IOException {

        List<Sequence> database = new ArrayList<>();
        int sidCounter = 0;

        try (BufferedReader br = new BufferedReader(new FileReader(seqPath))) {
            String line;
            while ((line = br.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty() || line.startsWith("#") || line.startsWith("@")) continue;

                Sequence seq = new Sequence(sidCounter++);
                Itemset currentItemset = new Itemset();
                String[] tokens = line.split("\\s+");

                for (String token : tokens) {
                    token = token.trim();
                    if (token.isEmpty()) continue;

                    if (token.equals("-1")) {
                        currentItemset.items.sort(Comparator.comparingInt(a -> a.id));
                        seq.addItemset(currentItemset);
                        currentItemset = new Itemset();
                    } else if (token.equals("-2")) {
                        if (!currentItemset.items.isEmpty()) {
                            seq.addItemset(currentItemset);
                        }
                        break;
                    } else {
                        ItemQ item = parseItem(token, euiTable);
                        if (item != null) {
                            currentItemset.addItem(item);
                        }
                    }
                }

                if (!currentItemset.items.isEmpty()) {
                    seq.addItemset(currentItemset);
                }

                if (!seq.itemsets.isEmpty()) {
                    database.add(seq);
                }
            }
        }

        System.out.println("[QSDB_Parser] Loaded database: " + database.size() + " sequences");
        return database;
    }

    /** Parse a single {@code itemID[quantity]} token; defaults to quantity 1 if no brackets. */
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
                        currentItemset.items.sort(Comparator.comparingInt(a -> a.id));
                        seq.addItemset(currentItemset);
                        currentItemset = new Itemset();
                    } else if (token.equals("-2")) {
                        database.add(seq);
                        break;
                    } else {
                        ItemQ item = parseItem(token, euiTable);
                        if (item != null) {
                            currentItemset.items.add(item);
                        }
                    }
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
