// SPDX-License-Identifier: MIT
import java.io.BufferedWriter;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Experiment 15: maintenance cost under the mixed append model, without any search.
 *
 * <p>Input of one dataset and schedule: the directory datasets/maint/&lt;NAME&gt;-&lt;SCHEDULE&gt;/ with db_eui.txt,
 * db_seq.txt (one line per sequence part, batch after batch), keys.txt (the customer key of each line) and sizes.txt
 * (lines per batch, comma separated). A part whose key is already stored appends to that customer's sequence. The four
 * files must match datasets/maint/MANIFEST.sha256, else the run is refused: the data are not redistributable, so the
 * manifest is what ties a row to its input.
 *
 * <p>Each arm runs in maintenance-only mode (see {@code HAUSP_UB.maintenanceOnly}): after every batch the maintained
 * structures are up to date and every root list readable, and no search runs. One JVM measures one repetition (flag
 * {@code --rep k}); the campaign driver runs the repetitions as separate commands. Per batch the row records the
 * maintenance phase (System.nanoTime), the collector time over it, the bytes allocated by the thread, the positions
 * written, the root-list entries made readable, and the live heap after a forced collection taken outside the phase.
 * The minimum-utility fraction only sets which items are promising, a step of the phase that is cheap; it is fixed at
 * {@link #MU} for every arm.
 */
public class Experiment15Runner {
    static final double MU = 0.01;
    static final String HEADER = "dataset,schedule,arm,rep,batch,lines,appended,sequences,positions_written,"
            + "root_entries,maint_ns,gc_ms,alloc_bytes,heap_live_bytes,status";

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP15;
        int rep = 1;
        for (int i = 0; i + 1 < args.length; i++) if (args[i].equals("--rep")) rep = Integer.parseInt(args[i + 1]);
        String outDir = spec.outputDir();
        new File(outDir).mkdirs();
        File csv = new File(outDir, spec.logFileName);
        String[] arms = ExperimentConfig.filteredAlgos(spec);
        for (ExperimentConfig.DatasetRun run : ExperimentConfig.filteredRuns(spec)) {
            Path dir = Paths.get(run.dataset.seqPath).getParent();
            verifyInputs(dir);
            String name = dir.getFileName().toString();
            int dash = name.lastIndexOf('-');
            String dataset = name.substring(0, dash), schedule = name.substring(dash + 1);
            String[] keys = new String(Files.readAllBytes(dir.resolve("keys.txt")), StandardCharsets.UTF_8).trim().split("\\s+");
            String[] tok = new String(Files.readAllBytes(dir.resolve("sizes.txt")), StandardCharsets.UTF_8).trim().split(",");
            int[] sizes = new int[tok.length];
            for (int i = 0; i < tok.length; i++) sizes[i] = Integer.parseInt(tok[i].trim());
            for (String arm : arms) {
                List<Sequence> db = QSDB_Parser.loadDB(dir.resolve("db_eui.txt").toString(), dir.resolve("db_seq.txt").toString());
                if (db.size() != keys.length) throw new IllegalStateException(dir + ": " + db.size() + " lines, " + keys.length + " keys");
                int total = 0;
                for (int s : sizes) total += s;
                if (total != db.size()) throw new IllegalStateException(dir + ": batch sizes add up to " + total + ", lines " + db.size());
                for (int i = 0; i < keys.length; i++) db.get(i).sid = Integer.parseInt(keys[i]);
                File props = File.createTempFile("maint", ".properties");
                props.deleteOnExit();
                Files.write(props.toPath(), ("dataset.path=" + dir.resolve("db_seq.txt").toString().replace('\\', '/')
                        + "\nminUtil.percentage=" + MU + "\n").getBytes(StandardCharsets.UTF_8));
                HAUSP_UB alg = HAUSP_UB.fromArmName(arm, props.getPath());
                alg.setConfig(MU);
                alg.enableIO = false;
                alg.maintenanceOnly = true;
                int start = 0;
                java.util.Set<String> seen = new java.util.HashSet<>();
                for (int b = 0; b < sizes.length; b++) {
                    // parts whose customer is already stored, counted from the input (the same for every arm;
                    // the rebuild arm itself treats every sequence as new)
                    int appended = 0;
                    for (int j = start; j < start + sizes[b]; j++) if (seen.contains(keys[j])) appended++;
                    for (int j = start; j < start + sizes[b]; j++) seen.add(keys[j]);
                    List<Sequence> batch = new ArrayList<>(db.subList(start, start + sizes[b]));
                    start += sizes[b];
                    RunResult r = alg.processBatch(batch, b);
                    long heap = liveHeap();
                    String row = String.join(",", dataset, schedule, arm, Integer.toString(rep), Integer.toString(b),
                            Integer.toString(sizes[b]), Integer.toString(appended), Long.toString(r.cumulativeDBSize),
                            Long.toString(alg.positionsWrittenLastBatch), Long.toString(alg.lastMaterializedEntries),
                            Long.toString(alg.lastMaintNanos), Long.toString(alg.lastGcMillis), Long.toString(alg.lastAllocatedBytes),
                            Long.toString(heap), "OK");
                    try (BufferedWriter w = CSVLogger.openForAppend(csv, HEADER)) {
                        w.write(row);
                        w.newLine();
                    }
                    System.out.println("[exp15] " + row);
                }
            }
        }
    }

    /** Live heap after two forced collections; called between batches, outside the timed phase. */
    static long liveHeap() {
        Runtime rt = Runtime.getRuntime();
        System.gc();
        System.gc();
        return rt.totalMemory() - rt.freeMemory();
    }

    /** Refuses unless every input file of {@code dir} has the digest that datasets/maint/MANIFEST.sha256 records. */
    static void verifyInputs(Path dir) throws IOException {
        Path manifest = dir.getParent().resolve("MANIFEST.sha256");
        if (!Files.exists(manifest)) throw new IllegalStateException("refusing: " + manifest + " is missing");
        Map<String, String> want = new HashMap<>();
        for (String line : Files.readAllLines(manifest, StandardCharsets.UTF_8)) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            String[] f = line.split("\\s+", 2);
            want.put(f[1].replace("*", "").trim(), f[0]);
        }
        String set = dir.getFileName().toString();
        for (String f : new String[]{"db_eui.txt", "db_seq.txt", "keys.txt", "sizes.txt"}) {
            Path p = dir.resolve(f);
            String key = set + "/" + f;
            if (!want.containsKey(key)) throw new IllegalStateException("refusing: " + manifest + " has no line for " + key);
            if (!Files.exists(p)) throw new IllegalStateException("refusing: " + p + " is missing");
            String got = sha256(p);
            if (!got.equals(want.get(key))) {
                throw new IllegalStateException("refusing: " + p + " has sha256 " + got + ", the manifest records " + want.get(key));
            }
        }
    }

    static String sha256(Path p) throws IOException {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            try (java.io.InputStream in = Files.newInputStream(p)) {
                byte[] buf = new byte[1 << 20];
                int n;
                while ((n = in.read(buf)) > 0) md.update(buf, 0, n);
            }
            StringBuilder sb = new StringBuilder();
            for (byte x : md.digest()) sb.append(String.format(Locale.ROOT, "%02x", x));
            return sb.toString();
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
