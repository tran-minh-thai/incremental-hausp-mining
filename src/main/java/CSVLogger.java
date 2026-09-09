import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * Appends one CSV row per {@link RunResult}.
 *
 * <p>File layout: the first line of a new file is the provenance comment of
 * {@link RunMeta#headerLine()} ({@code # run_id=... git=... jvm=... heap=...
 * host=...}), the second line is the column header. When a later JVM appends
 * to an existing file, its own provenance line is written once before its
 * first row, so a file assembled over several sessions records every run
 * that contributed to it. Readers must skip lines starting with {@code #}.
 *
 * <p>Only the pre-large baseline records a non-zero {@code mu}; the other
 * algorithms always log {@code 0.0}.
 */
public class CSVLogger {
    /** Column header. Columns after {@code Status} were added on 2026-09-03/04; older files lack them. */
    public static final String CSV_HEADER =
            "Timestamp,Algorithm,Dataset,BatchID,RunIndex,MinUtil,mu,DeltaRatio," +
                    "TotalDBUtil,CumulativeDBSize," +
                    "tScan(ms),tMining(ms),tTotal(ms),tLayer1(ms),tLayer2(ms),tLayer3(ms)," +
                    "Cand,PrunedL1(SWU),PrunedL2(IAUUB),PrunedL3(MFUUB)," +
                    "TightnessPEAU,TightnessIAUUB,TightnessMFUUB,HAUSP,SHAUS,MemPeak(MB)," +
                    "PoolBorrows,PoolReuses,PoolPeakLive,AudulActive,Status,Recursed," +
                    "ArmOrder,Schedule,PoolBytes,FlatBytes,EucsBytes,AudulRootBytes," +
                    "RescanTriggered,BufferUtil,BufferTested,SafetyBound,PrunedL3Node,PrunedL1Root,MemMode,MemLive(MB),MemRetained(MB),GcForced,RunID";

    /** Number of fields in {@link #CSV_HEADER}; every row must have exactly this many. */
    public static final int COLUMN_COUNT = CSV_HEADER.split(",").length;

    public static final String ALGO_EHAUSM_R     = "EHAUSM-R";
    public static final String ALGO_EHAUSM_I     = "EHAUSM-I";
    public static final String ALGO_PRE_HAUSPM   = "Pre-HAUSPM";
    public static final String ALGO_HAUSP_UB_L1  = "HAUSP-UB-L1";
    public static final String ALGO_HAUSP_UB_L1L3 = "HAUSP-UB-L1L3";
    public static final String ALGO_HAUSP_UB_A   = "HAUSP-UB*";
    public static final String ALGO_HAUSP_UB     = "HAUSP-UB";

    /** Files that already received this JVM's provenance line. */
    private static final Set<String> STAMPED = new HashSet<>();

    /**
     * Returns the value of {@code mu} that should appear in the CSV for the
     * given algorithm. Only Pre-HAUSPM uses {@code mu}; every other algorithm
     * is reported as {@code 0.0}.
     */
    public static double effectiveMu(String algorithm, double configuredMu) {
        if (algorithm == null) return configuredMu;
        if (algorithm.equals(ALGO_PRE_HAUSPM) || algorithm.equals("Pre-HUSPM-adapt")) {
            return configuredMu;
        }
        return 0.0;
    }

    /**
     * Opens {@code file} for appending and makes sure it carries a column
     * header and this JVM's provenance line. Shared with runners that write
     * their own schema (Experiment 6).
     *
     * @return a writer positioned after the header lines; the caller closes it.
     */
    public static synchronized BufferedWriter openForAppend(File file, String header) throws IOException {
        File dir = file.getAbsoluteFile().getParentFile();
        if (dir != null && !dir.exists()) dir.mkdirs();
        boolean isNewFile = !file.exists() || file.length() == 0;
        if (!isNewFile) {
            // Never append rows of one schema under the header of another: the file
            // would parse with shifted columns. Refuse and name both headers.
            String existing = firstHeader(file);
            if (existing != null && !existing.equals(header)) {
                throw new IllegalStateException("refusing to append to " + file + ": its column header differs from the "
                        + "current schema.\n  file   : " + existing + "\n  current: " + header
                        + "\n  Write to a new results directory (--results-dir) or migrate the file first.");
            }
        }
        BufferedWriter bw = new BufferedWriter(new FileWriter(file, true));
        String key = file.getAbsolutePath();
        if (isNewFile) {
            bw.write(RunMeta.headerLine());
            bw.newLine();
            bw.write(header);
            bw.newLine();
            STAMPED.add(key);
        } else if (!STAMPED.contains(key)) {
            bw.write(RunMeta.headerLine());
            bw.newLine();
            STAMPED.add(key);
        }
        return bw;
    }

    /** First non-comment line of an existing CSV, or null when the file has none. */
    private static String firstHeader(File file) throws IOException {
        try (java.io.BufferedReader br = new java.io.BufferedReader(new java.io.FileReader(file))) {
            String line;
            while ((line = br.readLine()) != null) {
                if (!line.isEmpty() && !line.startsWith("#")) return line.trim();
            }
        }
        return null;
    }

    public static synchronized void logResult(String outputDir, String fileName, RunResult res) {
        File file = new File(outputDir, fileName);
        try (BufferedWriter bw = openForAppend(file, CSV_HEADER)) {
            String row = formatRow(res);
            bw.write(row);
            bw.newLine();
        } catch (IOException e) {
            throw new IllegalStateException("cannot write result row to " + file, e);
        }
    }

    /** One CSV row for {@code res}, with exactly {@link #COLUMN_COUNT} fields. */
    static String formatRow(RunResult res) {
        StringBuilder sb = new StringBuilder();
        sb.append(res.timestamp).append(",");
        sb.append(res.algorithm).append(",");
        sb.append(res.dataset).append(",");
        sb.append(res.batchID).append(",");
        sb.append(res.runIndex).append(",");
        sb.append(String.format(Locale.US, "%.6f", res.minUtil)).append(",");
        sb.append(String.format(Locale.US, "%.3f", res.mu)).append(",");
        sb.append(String.format(Locale.US, "%.3f", res.deltaRatio)).append(",");
        sb.append(res.totalDBUtility).append(",");
        sb.append(res.cumulativeDBSize).append(",");
        sb.append(res.tScan).append(",");
        sb.append(res.tMining).append(",");
        sb.append(res.tTotal).append(",");
        sb.append(res.tLayer1).append(",");
        sb.append(res.tLayer2).append(",");
        sb.append(res.tLayer3).append(",");
        sb.append(res.numCand).append(",");
        sb.append(res.numPrunedL1).append(",");
        sb.append(res.numPrunedL2).append(",");
        sb.append(res.numPrunedL3).append(",");
        sb.append(String.format(Locale.US, "%.6f", res.ratioTightnessPEAU)).append(",");
        sb.append(String.format(Locale.US, "%.6f", res.ratioTightnessIAUUB)).append(",");
        sb.append(String.format(Locale.US, "%.6f", res.ratioTightnessMFUUB)).append(",");
        sb.append(res.hauspFound).append(",");
        sb.append(res.shausActive).append(",");
        sb.append(String.format(Locale.US, "%.2f", res.memPeak)).append(",");
        sb.append(res.poolBorrows).append(",");
        sb.append(res.poolReuses).append(",");
        sb.append(res.poolPeakLive).append(",");
        sb.append(res.audulActive).append(",");
        sb.append(res.runStatus).append(",");
        sb.append(res.numRecursed).append(",");
        sb.append(res.armOrder < 0 ? "" : Integer.toString(res.armOrder)).append(",");
        sb.append(res.schedule == null ? "" : res.schedule).append(",");
        sb.append(optional(res.poolBytes)).append(",");
        sb.append(optional(res.flatBytes)).append(",");
        sb.append(optional(res.eucsBytes)).append(",");
        sb.append(optional(res.audulRootBytes)).append(",");
        sb.append(res.rescanTriggered < 0 ? "" : Integer.toString(res.rescanTriggered)).append(",");
        sb.append(optional(res.bufferUtil)).append(",");
        sb.append(optional(res.bufferTested)).append(",");
        sb.append(Double.isNaN(res.safetyBound) ? "" : String.format(Locale.US, "%.0f", res.safetyBound)).append(",");
        sb.append(optional(res.numPrunedL3Node)).append(",");
        sb.append(optional(res.numPrunedL1Root)).append(",");
        sb.append(res.memMode == null ? "used" : res.memMode).append(",");
        sb.append(res.memLiveMB < 0 ? "" : String.format(Locale.US, "%.2f", res.memLiveMB)).append(",");
        sb.append(res.memRetainedMB < 0 ? "" : String.format(Locale.US, "%.2f", res.memRetainedMB)).append(",");
        sb.append(optional(res.gcForced)).append(",");
        sb.append(res.runId == null ? "" : res.runId);
        return sb.toString();
    }

    private static String optional(long v) {
        return v < 0 ? "" : Long.toString(v);
    }
}
