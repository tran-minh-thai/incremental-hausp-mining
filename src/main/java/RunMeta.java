import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.lang.management.ManagementFactory;
import java.net.InetAddress;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.concurrent.TimeUnit;

/**
 * Provenance of the current JVM run, stamped into every result file.
 *
 * <p>Every CSV written by {@link CSVLogger} starts with (or, when appending
 * to an existing file, receives once per JVM) a comment line of the form
 * <pre>
 *   # run_id=20260904-0715 git=0cefc9f jvm=26.0.1 heap=24g host=machine tree=clean cmd=--exp 1 ...
 * </pre>
 * so that a file produced by several sessions still tells which rows came
 * from which run. Readers must ignore lines starting with {@code #}.
 * The same run identifier is also written per row in the {@code RunID}
 * column, which is what makes merged files traceable cell by cell.
 */
public final class RunMeta {

    private RunMeta() {}

    /** Identifier of this JVM invocation: local wall-clock at start, minute resolution. */
    public static final String RUN_ID = new SimpleDateFormat("yyyyMMdd-HHmm").format(new Date());

    /** Short commit hash of the working tree the JVM was started in; "unknown" outside a git checkout. */
    public static final String GIT = git("rev-parse", "--short", "HEAD");

    /** "clean" or "MODIFIED(n)" where n is the number of paths reported by git status. */
    public static final String TREE = treeState();

    public static final String JVM = System.getProperty("java.version", "unknown");

    /** The -Xmx value given on the command line, or the JVM's maximum heap when none was given. */
    public static final String HEAP = heap();

    public static final String HOST = host();

    /** Command-line arguments of the launcher; set once by {@code ExperimentLauncher.main}. */
    public static volatile String COMMAND = "";

    /** The provenance line written at the top of every result file (without trailing newline). */
    public static String headerLine() {
        return "# run_id=" + RUN_ID + " git=" + GIT + " jvm=" + JVM + " heap=" + HEAP
                + " host=" + HOST + " tree=" + TREE + " cmd=" + COMMAND;
    }

    private static String git(String... args) {
        String[] cmd = new String[args.length + 1];
        cmd[0] = "git";
        System.arraycopy(args, 0, cmd, 1, args.length);
        try {
            Process p = new ProcessBuilder(cmd)
                    .directory(new File(System.getProperty("user.dir")))
                    .redirectErrorStream(true)
                    .start();
            StringBuilder sb = new StringBuilder();
            try (BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                String line;
                while ((line = br.readLine()) != null) {
                    if (sb.length() > 0) sb.append('\n');
                    sb.append(line);
                }
            }
            if (!p.waitFor(5, TimeUnit.SECONDS) || p.exitValue() != 0) return null;
            return sb.toString().trim();
        } catch (Exception e) {
            return null;
        }
    }

    private static String treeState() {
        String status = git("status", "--porcelain");
        if (status == null) return "unknown";
        if (status.isEmpty()) return "clean";
        return "MODIFIED(" + status.split("\n").length + ")";
    }

    private static String heap() {
        try {
            for (String a : ManagementFactory.getRuntimeMXBean().getInputArguments()) {
                if (a.startsWith("-Xmx")) return a.substring(4);
            }
        } catch (Exception ignored) { /* fall through */ }
        long max = Runtime.getRuntime().maxMemory();
        return String.format("%.1fg(max)", max / (1024.0 * 1024.0 * 1024.0));
    }

    private static String host() {
        try {
            return InetAddress.getLocalHost().getHostName();
        } catch (Exception e) {
            String h = System.getenv("HOSTNAME");
            return h == null ? "unknown" : h;
        }
    }
}
