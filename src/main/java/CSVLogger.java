import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.Locale;

/**
 * Appends one CSV row per {@link RunResult}. The header is written the first
 * time the target file is created. Only the pre-large baseline records a
 * non-zero {@code mu}; the other algorithms always log {@code 0.0}.
 */
public class CSVLogger {
    private static final String CSV_HEADER =
            "Timestamp,Algorithm,Dataset,BatchID,RunIndex,MinUtil,mu,DeltaRatio," +
                    "TotalDBUtil,CumulativeDBSize," +
                    "tScan(ms),tMining(ms),tTotal(ms),tLayer1(ms),tLayer2(ms),tLayer3(ms)," +
                    "Cand,PrunedL1(SWU),PrunedL2(IAUUB),PrunedL3(MFUUB)," +
                    "TightnessPEAU,TightnessIAUUB,TightnessMFUUB,HAUSP,SHAUS,MemPeak(MB)," +
                    "PoolBorrows,PoolReuses,PoolPeakLive,AudulActive,Status";

    public static final String ALGO_EHAUSM_R     = "EHAUSM-R";
    public static final String ALGO_EHAUSM_I     = "EHAUSM-I";
    public static final String ALGO_PRE_HAUSPM   = "Pre-HAUSPM";
    public static final String ALGO_HAUSP_UB_L1  = "HAUSP-UB-L1";
    public static final String ALGO_HAUSP_UB_L1L3 = "HAUSP-UB-L1L3";
    public static final String ALGO_HAUSP_UB_A   = "HAUSP-UB*";
    public static final String ALGO_HAUSP_UB     = "HAUSP-UB";

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

    public static void logResult(String outputDir, String fileName, RunResult res) {
        File dir = new File(outputDir);
        if (!dir.exists()) dir.mkdirs();
        File file = new File(dir, fileName);
        boolean isNewFile = !file.exists();

        try (BufferedWriter bw = new BufferedWriter(new FileWriter(file, true))) {
            if (isNewFile) {
                bw.write(CSV_HEADER);
                bw.newLine();
            }
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
            sb.append(res.runStatus);
            bw.write(sb.toString());
            bw.newLine();
        } catch (IOException e) {
            e.printStackTrace();
        }
    }

    public static void logError(String outputDir, String fileName, String algorithm,
                                String dataset, int batchID, double minUtil,
                                double mu, double deltaRatio, String errorType) {
        File dir = new File(outputDir);
        if (!dir.exists()) dir.mkdirs();
        File file = new File(dir, fileName);
        boolean isNewFile = !file.exists();

        try (BufferedWriter bw = new BufferedWriter(new FileWriter(file, true))) {
            if (isNewFile) { bw.write(CSV_HEADER); bw.newLine(); }
            String timestamp = new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new java.util.Date());
            String line = String.format(Locale.US,
                    "%s,%s,%s,%d,0,%.6f,%.3f,%.3f,0,0,0,0,0,0,0,0,%s,%s,%s,%s,0.000000,0.000000,0.000000,0,0,0.00,0,0,0,0,%s",
                    timestamp, algorithm, dataset, batchID, minUtil, mu, deltaRatio,
                    errorType, errorType, errorType, errorType, errorType);
            bw.write(line);
            bw.newLine();
        } catch (IOException e) {
            e.printStackTrace();
        }
    }
}
