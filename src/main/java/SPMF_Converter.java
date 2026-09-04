import java.io.*;
import java.util.*;

/**
 * Converts sequence datasets from the SPMF format (item IDs only) into the
 * quantitative QSDB format used by the experiments (quantities + external
 * utilities). This is the copy that produced the files under datasets/.
 *
 * Input (SPMF), one sequence per line: "item1 item2 -1 item3 -1 -2",
 * where a non-negative integer is an item ID, "-1" closes an itemset and
 * "-2" closes the sequence.
 *
 * Output, two files per dataset:
 *   1. NAME_seq.txt (QSDB): "item1[q1] item2[q2] -1 item3[q3] -1 -2";
 *      quantities follow a weighted mixture: 70% in [1,2], 20% in [3,5],
 *      10% in [6,10].
 *   2. NAME_eui.txt: "itemID:profit"; profits follow a log-normal
 *      distribution exp(N(2.5, 1.0)) rounded and clipped to [1, 1000].
 *
 * Seeding. A SINGLE java.util.Random(42) is held in a static field and shared
 * by every file of a batch, so the bytes a file receives depend on which files
 * were converted before it and in what order. Only the first file of a batch
 * (or a file converted alone) equals the output of a per-file-seeded
 * generator. This was verified on 2026-09-04 against the per-file-seeded
 * converter published at github.com/tran-minh-thai/convert-spmf-to-quantitative
 * and the huspm-datasets v1.1 release: BIBLE, BMS1 and C8T1S5I8N5K under
 * datasets/ are value-identical to that release (only the _eui comment line
 * differs), while FIFA, KOSARAK, LEVIATHAN and SIGN are not, because they were
 * converted after other files in the original batch. Those four files can
 * therefore not be regenerated from the raw SPMF data with a per-file seed;
 * they are pinned by datasets/MANIFEST.sha256 and distributed verbatim.
 *
 * For new datasets use the per-file-seeded converter linked above; this file
 * is kept only to document how the committed data were produced.
 */
public class SPMF_Converter {

    // Shared across the whole batch (see the class comment): output depends on order.
    private static final Random random = new Random(42);

    /** Output directory for the converted files. */
    private static final String OUTPUT_DIR = "datasets";

    /** Directory holding the raw SPMF exports. */
    private static final String SOURCE_DIR = "datasets";

    public static void main(String[] args) {
        // SPMF exports of the original conversion batch (order matters, see class comment).
        // BIBLE and C8T1S5I8N5K were converted in separate runs and are not in this list.
        String[] inputFiles = {
                "BMS1_SPMF.txt",
                "E_SHOP.txt",
                "KOSARAK.txt",
                "ONLINE_RETAIL_II_ALL.txt",
                "FIFA.txt",
                "LEVIATHAN.txt",
                "SIGN.txt",
                "ONLINE_RETAIL_II_BEST.txt"
        };

        File dir = new File(OUTPUT_DIR);
        if (!dir.exists()) dir.mkdirs();

        System.out.println("========== BATCH CONVERSION START ==========");
        System.out.println("[*] Random seed = 42 (deterministic output)");

        for (String fileName : inputFiles) {
            String fullInputPath = SOURCE_DIR.isEmpty()
                    ? fileName
                    : SOURCE_DIR + File.separator + fileName;
            processSingleFile(fullInputPath, fileName);
        }

        System.out.println("==================================================");
    }

    /**
     * Converts one SPMF file into the two QSDB files (_seq.txt + _eui.txt).
     *
     * @param fullPath     full path of the SPMF input file
     * @param originalName original file name (used to derive the output names)
     */
    private static void processSingleFile(String fullPath, String originalName) {
        File inputFile = new File(fullPath);
        if (!inputFile.exists()) {
            System.err.println("[!] ERROR: file not found: "
                    + inputFile.getAbsolutePath());
            return;
        }

        String baseName = originalName.replace(".txt", "");
        String seqOut = OUTPUT_DIR + File.separator + baseName + "_seq.txt";
        String euiOut = OUTPUT_DIR + File.separator + baseName + "_eui.txt";

        // TreeSet collects the distinct item IDs in sorted order.
        Set<Integer> itemRegistry = new TreeSet<>();

        try (BufferedReader br = new BufferedReader(new FileReader(inputFile));
             BufferedWriter bw = new BufferedWriter(new FileWriter(seqOut))) {

            String line;
            while ((line = br.readLine()) != null) {
                // Skip blank lines and comments.
                if (line.isEmpty() || line.startsWith("#") || line.startsWith("@")) continue;

                String[] tokens = line.trim().split("\\s+");
                for (String t : tokens) {
                    try {
                        int id = Integer.parseInt(t);
                        if (id >= 0) {
                            // Item token: draw the quantity from the weighted
                            // mixture 70% -> 1-2, 20% -> 3-5, 10% -> 6-10.
                            int r = random.nextInt(100);
                            int q;
                            if (r < 70) q = random.nextInt(2) + 1;       // 1-2
                            else if (r < 90) q = random.nextInt(3) + 3;  // 3-5
                            else q = random.nextInt(5) + 6;              // 6-10

                            bw.write(id + "[" + q + "] ");
                            itemRegistry.add(id); // register the item
                        } else {
                            // Separator: -1 closes an itemset, -2 closes the sequence.
                            bw.write(id + " ");
                        }
                    } catch (NumberFormatException e) {
                        // Ignore non-numeric tokens, if any.
                    }
                }
                bw.write("\n");
            }

            // Generate the external-utility file.
            generateEUI(euiOut, itemRegistry);
            System.out.println("[OK] converted: " + originalName
                    + " (" + itemRegistry.size() + " items)");

        } catch (IOException e) {
            System.err.println("[ERROR] " + originalName + ": " + e.getMessage());
        }
    }

    /**
     * Generates the external-utility (EUI) file for all items: profits follow
     * a log-normal distribution exp(N(2.5, 1.0)), rounded to a long and
     * clipped to [1, 1000]; the ":" separator matches example_eui.txt.
     *
     * @param outputPath output path of the EUI file
     * @param items      distinct item IDs in sorted order
     */
    private static void generateEUI(String outputPath, Set<Integer> items) throws IOException {
        try (BufferedWriter writer = new BufferedWriter(new FileWriter(outputPath))) {
            writer.write("# ItemID:Profit (Log-Normal Distribution seed=42)\n");
            for (int id : items) {
                // Log-normal draw: most values fall in 5-30, a few reach 500-1000.
                double logNormal = Math.exp(random.nextGaussian() * 1.0 + 2.5);

                // Clip to [1, 1000].
                long profit = Math.round(Math.max(1, Math.min(1000, logNormal)));
                writer.write(id + ":" + profit + "\n");
            }
        }
    }
}