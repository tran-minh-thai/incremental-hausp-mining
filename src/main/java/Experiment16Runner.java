// SPDX-License-Identifier: MIT

import java.io.BufferedWriter;
import java.io.File;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryPoolMXBean;
import java.lang.management.MemoryType;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Experiment 16: end-to-end cost of one batch under the mixed append model (maintenance and search), per arm.
 *
 * <p>Inputs are those of experiment 15 (datasets/maint/&lt;NAME&gt;-&lt;SCHEDULE&gt;/, checked against the manifest). One JVM
 * runs one arm, one cell (dataset, schedule, threshold) and one repetition: {@code --algo ARM --dataset name-sched
 * --xi X --rep k}. The input file is read once, before any batch, for every arm.
 *
 * <p>Arms:
 * <ul>
 *   <li>{@code HAUSP-UB[...]}: the arm names of {@link HAUSP_UB#fromArmName}. The batch goes to {@code processBatch};
 *       the maintenance phase (from the start of the batch to the start of the search) and the search phase are timed
 *       inside it with System.nanoTime.</li>
 *   <li>{@code Remine-PEAU}: {@link EHAUSM_Remining}, a re-miner with its own search (the PEAU bound), given the
 *       accumulated database at every batch. Joining a customer's stored and appended parts is done before the clock,
 *       as for the rebuild arm of HAUSP-UB: it stands for receiving the accumulated database, like reading a file.
 *       The building of its structures and its search are timed inside it with System.nanoTime. It keeps nothing
 *       between batches.</li>
 * </ul>
 *
 * <p>Per batch the row records both phases and the collector time over each, the patterns found, the candidates, the
 * peak heap use over the batch (sum of the peak usage of the heap pools, reset before the batch, so an upper bound of
 * the true peak) and the live heap after the batch (two forced collections, outside the clock).
 *
 * <p>{@code --whole}: one batch only, the accumulated database after the last batch (each customer's parts joined, in
 * arrival order), given to the arm as new sequences; the schedule column reads ALL. This measures the search on the
 * final database once, for the study of the search organization.
 *
 * <p>Time limit: a batch that runs longer than {@code --limit-s} (default 1800) gets a row with status OT, written by
 * a watchdog thread, and the JVM halts; the later batches of the cell have no row.
 */
public class Experiment16Runner {
    static final String HEADER = "dataset,schedule,xi,arm,rep,batch,lines,appended,sequences,patterns,candidates,"
            + "maint_ns,maint_gc_ms,search_ns,search_gc_ms,heap_peak_bytes,heap_live_bytes,status";
    static final String REMINE = "Remine-PEAU";
    private static final Object WRITE_LOCK = new Object();

    public static void main(String[] args) throws Exception {
        ExperimentConfig.ExperimentSpec spec = ExperimentConfig.EXP16;
        int rep = 1;
        double xi = Double.NaN;
        double limitS = 1800;
        boolean whole = false;
        for (String a : args) if (a.equals("--whole")) whole = true;
        for (int i = 0; i + 1 < args.length; i++) {
            if (args[i].equals("--rep")) rep = Integer.parseInt(args[i + 1]);
            if (args[i].equals("--xi")) xi = Double.parseDouble(args[i + 1]);
            if (args[i].equals("--limit-s")) limitS = Double.parseDouble(args[i + 1]);
        }
        if (Double.isNaN(xi) || xi <= 0) throw new IllegalArgumentException("experiment 16 needs --xi X (X > 0)");
        String outDir = spec.outputDir();
        new File(outDir).mkdirs();
        File csv = new File(outDir, spec.logFileName);
        String[] arms = ExperimentConfig.filteredAlgos(spec);
        List<ExperimentConfig.DatasetRun> runs = ExperimentConfig.filteredRuns(spec);
        if (arms.length != 1 || runs.size() != 1) {
            throw new IllegalArgumentException("experiment 16 runs one arm on one cell per JVM: got " + arms.length
                    + " arm(s) and " + runs.size() + " dataset(s); give --algo and --dataset");
        }
        String arm = arms[0];
        Path dir = Paths.get(runs.get(0).dataset.seqPath).getParent();
        Experiment15Runner.verifyInputs(dir);
        String name = dir.getFileName().toString();
        int dash = name.lastIndexOf('-');
        String dataset = name.substring(0, dash), schedule = name.substring(dash + 1);
        String[] keys = new String(Files.readAllBytes(dir.resolve("keys.txt")), StandardCharsets.UTF_8).trim().split("\\s+");
        String[] tok = new String(Files.readAllBytes(dir.resolve("sizes.txt")), StandardCharsets.UTF_8).trim().split(",");
        int[] sizes = new int[tok.length];
        int total = 0;
        for (int i = 0; i < tok.length; i++) total += sizes[i] = Integer.parseInt(tok[i].trim());
        List<Sequence> db = QSDB_Parser.loadDB(dir.resolve("db_eui.txt").toString(), dir.resolve("db_seq.txt").toString());
        if (db.size() != keys.length || total != keys.length) {
            throw new IllegalStateException(dir + ": " + db.size() + " lines, " + keys.length + " keys, sizes add up to " + total);
        }
        for (int i = 0; i < keys.length; i++) db.get(i).sid = Integer.parseInt(keys[i]);
        if (whole) {
            Map<Integer, Sequence> all = new LinkedHashMap<>();
            for (Sequence part : db) all.put(part.sid, join(all.get(part.sid), part));
            db = new ArrayList<>(all.values());
            sizes = new int[]{db.size()};
            schedule = "ALL";
        }
        File props = File.createTempFile("e2e", ".properties");
        props.deleteOnExit();
        Files.write(props.toPath(), ("dataset.path=" + dir.resolve("db_seq.txt").toString().replace('\\', '/')
                + "\nminUtil.percentage=" + xi + "\n").getBytes(StandardCharsets.UTF_8));

        HAUSP_UB ub = null;
        EHAUSM_Remining re = null;
        if (arm.equals(REMINE)) {
            re = new EHAUSM_Remining(props.getPath());
            re.setConfig(xi);
            re.enableIO = false;
        } else {
            ub = HAUSP_UB.fromArmName(arm, props.getPath());
            ub.setConfig(xi);
            ub.topK = 0;
            ub.enableIO = false;
        }
        List<MemoryPoolMXBean> heapPools = new ArrayList<>();
        for (MemoryPoolMXBean p : ManagementFactory.getMemoryPoolMXBeans()) if (p.getType() == MemoryType.HEAP) heapPools.add(p);

        Map<Integer, Sequence> accumulated = new LinkedHashMap<>();
        Set<Integer> seen = new HashSet<>();
        String xiText = Double.toString(xi);
        int start = 0;
        for (int b = 0; b < sizes.length; b++) {
            int appended = 0;
            for (int j = start; j < start + sizes[b]; j++) if (seen.contains(db.get(j).sid)) appended++;
            for (int j = start; j < start + sizes[b]; j++) seen.add(db.get(j).sid);
            List<Sequence> batch = new ArrayList<>(db.subList(start, start + sizes[b]));
            List<Sequence> cumulative = null;
            if (re != null) {                                   // before the clock: the accumulated database
                for (Sequence part : batch) {
                    Sequence old = accumulated.get(part.sid);
                    accumulated.put(part.sid, join(old, part));
                }
                cumulative = new ArrayList<>(accumulated.values());
            }
            start += sizes[b];
            final String okPrefix = String.join(",", dataset, schedule, xiText, arm, Integer.toString(rep),
                    Integer.toString(b), Integer.toString(sizes[b]), Integer.toString(appended));
            AtomicBoolean finished = new AtomicBoolean(false);
            Thread watchdog = watchdog(csv, okPrefix, seen.size(), limitS, finished);
            for (MemoryPoolMXBean p : heapPools) p.resetPeakUsage();
            RunResult r;
            long maintNs, maintGc, searchNs, searchGc;
            if (re != null) {
                long gc0 = HAUSP_UB.gcMillis();
                r = re.processBatch(cumulative, b);
                long gc1 = HAUSP_UB.gcMillis();
                maintNs = re.lastBuildNanos;
                searchNs = re.lastSearchNanos;
                maintGc = -1;                                   // the re-miner reports the collector time of the whole batch
                searchGc = gc1 - gc0;
            } else {
                r = ub.processBatch(batch, b);
                maintNs = ub.lastMaintNanos;
                maintGc = ub.lastGcMillis;
                searchNs = ub.lastSearchNanos;
                searchGc = ub.lastSearchGcMillis;
            }
            synchronized (WRITE_LOCK) { finished.set(true); }   // from here the watchdog writes nothing
            watchdog.interrupt();
            long peak = 0;
            for (MemoryPoolMXBean p : heapPools) peak += p.getPeakUsage().getUsed();
            long live = Experiment15Runner.liveHeap();
            String row = String.join(",", okPrefix, Integer.toString(seen.size()), Long.toString(r.hauspFound),
                    Long.toString(r.numCand), Long.toString(maintNs), Long.toString(maintGc), Long.toString(searchNs),
                    Long.toString(searchGc), Long.toString(peak), Long.toString(live), "OK");
            write(csv, row);
            System.out.println("[exp16] " + row);
        }
    }

    private static Sequence join(Sequence head, Sequence tail) {
        Sequence s = new Sequence(tail.sid);
        if (head != null) for (Itemset is : head.itemsets) s.addItemset(is);
        for (Itemset is : tail.itemsets) s.addItemset(is);
        return s;
    }

    private static void write(File csv, String row) {
        synchronized (WRITE_LOCK) {
            try (BufferedWriter w = CSVLogger.openForAppend(csv, HEADER)) {
                w.write(row);
                w.newLine();
            } catch (IOException e) {
                throw new IllegalStateException("cannot write " + csv, e);
            }
        }
    }

    /** A daemon that, if not interrupted within the limit, writes the OT row of the batch and halts the JVM. */
    private static Thread watchdog(File csv, String prefix, int sequences, double limitS, AtomicBoolean finished) {
        Thread t = new Thread(() -> {
            try {
                Thread.sleep((long) (limitS * 1000));
            } catch (InterruptedException e) {
                return;
            }
            String row = String.join(",", prefix, Integer.toString(sequences), "-1", "-1", "-1", "-1", "-1", "-1", "-1",
                    "-1", "OT");
            synchronized (WRITE_LOCK) {
                if (finished.get()) return;
                try (BufferedWriter w = CSVLogger.openForAppend(csv, HEADER)) {
                    w.write(row);
                    w.newLine();
                } catch (IOException ignored) {
                    // the JVM halts either way; the campaign driver sees a command without its rows
                }
                System.out.println("[exp16] " + row);
                System.out.flush();
                Runtime.getRuntime().halt(0);
            }
        }, "exp16-watchdog");
        t.setDaemon(true);
        t.start();
        return t;
    }
}
