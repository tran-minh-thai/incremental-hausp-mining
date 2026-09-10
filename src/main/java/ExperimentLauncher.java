import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Single command-line entry point that dispatches to the experiment runners
 * in a shared JVM.
 *
 * <pre>
 *   java ExperimentLauncher                         # the eight experiments of the paper
 *   java ExperimentLauncher --exp 1                 # only Experiment 1
 *   java ExperimentLauncher --exp 1,3,5             # selected experiments
 *   java ExperimentLauncher --exp 10 --repeats 3    # opt-in study (9, 10, 11)
 *   java ExperimentLauncher --dump-config json      # print ExperimentConfig as JSON and exit
 * </pre>
 *
 * Options (all optional):
 * <ul>
 *   <li>{@code --repeats N}: trials per configuration (default 3).</li>
 *   <li>{@code --repeats-min-seconds S}: raise the trial count of configurations
 *       whose first trial finished in under S seconds (15 under 1 s, 10 under 10 s, 5 under 120 s).</li>
 *   <li>{@code --dataset a,b}: dataset short names to run.</li>
 *   <li>{@code --algo A,B}: arm names to run, exactly as written in the CSV.</li>
 *   <li>{@code --k 10,100}: batch counts for Experiments 7 and 11.</li>
 *   <li>{@code --results-dir DIR}: root of the result CSVs (default {@code results}).</li>
 *   <li>{@code --timeout MIN}: per-batch time limit in minutes.</li>
 *   <li>{@code --resume}: skip configurations already present in the CSV.</li>
 * </ul>
 */
public final class ExperimentLauncher {

    public static void main(String[] args) throws Exception {
        RunMeta.COMMAND = String.join(" ", args);

        if (hasFlag(args, "--print-header")) {
            System.out.println(CSVLogger.CSV_HEADER);
            System.exit(0);
        }
        if (hasFlag(args, "--dump-config")) {
            System.out.print(ExperimentConfig.toJson());
            System.exit(0);
        }

        List<Integer> targets = parseTargets(args);
        ExperimentConfig.REPEATS = parseRepeats(args, ExperimentConfig.REPEATS);
        ExperimentConfig.REPEATS_MIN_SECONDS = parseDouble(args, "--repeats-min-seconds", 0.0);
        parseListFlag(args, "--dataset", ExperimentConfig.DATASET_FILTER, true);
        parseListFlag(args, "--algo", ExperimentConfig.ALGO_FILTER, false);
        parseKFilter(args);
        String resultsDir = parseString(args, "--results-dir", null);
        if (resultsDir != null && !resultsDir.isEmpty()) {
            ExperimentConfig.RESULTS_DIR = resultsDir.replaceAll("[/\\\\]+$", "");
        }
        ExperimentConfig.RESUME = hasFlag(args, "--resume");
        ExperimentConfig.PROFILE_PHASES = hasFlag(args, "--profile-phases");
        if (ExperimentConfig.PROFILE_PHASES) {
            System.out.println("[launcher] --profile-phases: per-node phase timers ON; runtimes of this run are NOT comparable across arms");
        }
        String memMode = parseString(args, "--mem-mode", "used");
        if (memMode.equals("live")) {
            ExperimentConfig.MEM_MODE_LIVE = true;
        } else if (!memMode.equals("used")) {
            System.err.println("[launcher] --mem-mode must be 'used' or 'live'; got " + memMode);
            System.exit(1);
        }
        if (ExperimentConfig.MEM_MODE_LIVE && (resultsDir == null || !resultsDir.contains("mem"))) {
            System.err.println("[launcher] --mem-mode live requires a dedicated --results-dir containing 'mem' "
                    + "(its runtimes include forced collections and must never mix with timing runs)");
            System.exit(1);
        }
        ExperimentConfig.TIMEOUT_OVERRIDE_MIN = parseLong(args, "--timeout", 0);

        if (targets.isEmpty()) {
            printUsage();
            System.exit(1);
        }
        refuseMeasurementWhereDisabled();

        System.out.println("[launcher] run id              : " + RunMeta.RUN_ID);
        System.out.println("[launcher] provenance          : " + RunMeta.headerLine());
        System.out.println("[launcher] experiments to run  : " + targets);
        System.out.println("[launcher] repeats per config   : " + ExperimentConfig.REPEATS);
        if (ExperimentConfig.REPEATS_MIN_SECONDS > 0) {
            System.out.println("[launcher] adaptive repeats     : configs under " + ExperimentConfig.REPEATS_MIN_SECONDS
                    + " s get 15 (<1 s) / 10 (<10 s) / 5 (<120 s) trials");
        }
        System.out.println("[launcher] results directory   : " + ExperimentConfig.RESULTS_DIR);
        if (!ExperimentConfig.DATASET_FILTER.isEmpty()) {
            System.out.println("[launcher] dataset filter       : " + ExperimentConfig.DATASET_FILTER);
        }
        if (!ExperimentConfig.ALGO_FILTER.isEmpty()) {
            System.out.println("[launcher] arm filter           : " + ExperimentConfig.ALGO_FILTER);
        }
        if (!ExperimentConfig.K_FILTER.isEmpty()) {
            System.out.println("[launcher] K filter             : " + ExperimentConfig.K_FILTER);
        }
        if (ExperimentConfig.RESUME) {
            System.out.println("[launcher] resume mode          : on (skipping configs already in CSV)");
        }
        if (ExperimentConfig.TIMEOUT_OVERRIDE_MIN > 0) {
            System.out.println("[launcher] timeout override     : " + ExperimentConfig.TIMEOUT_OVERRIDE_MIN + " min");
        }
        if (ExperimentConfig.MEM_MODE_LIVE) {
            System.out.println("[launcher] memory mode          : live (forced full GC every " + MemorySampler.INTERVAL_MS
                    + " ms; runtimes of this run are NOT timing measurements)");
        }
        if (!"clean".equals(RunMeta.TREE)) {
            System.out.println("[launcher] WARNING: working tree is " + RunMeta.TREE
                    + "; the commit hash in the provenance line does not describe the code that runs.");
        }

        int failures = 0;
        for (int id : targets) {
            ExperimentConfig.ExperimentSpec spec = ExperimentConfig.getById(id);
            String[] arms = ExperimentConfig.filteredAlgos(spec);
            System.out.println();
            System.out.println("------------------------------------------------------------");
            System.out.println(" Experiment " + id + ": " + spec.title);
            System.out.println(" Datasets   : " + datasetNames(spec));
            System.out.println(" Algorithms : " + Arrays.toString(arms));
            System.out.println(" Output     : " + spec.outputDir() + "/" + spec.logFileName);
            System.out.println("------------------------------------------------------------");
            System.out.println();

            if (arms.length == 0) {
                System.err.println("[launcher] Experiment " + id + ": no arm of " + Arrays.toString(spec.algorithms)
                        + " matches --algo " + ExperimentConfig.ALGO_FILTER + "; nothing to run.");
                continue;
            }

            try {
                switch (id) {
                    case 1: Experiment1Runner.main(args); break;
                    case 2: Experiment2Runner.main(args); break;
                    case 3: Experiment3Runner.main(args); break;
                    case 4: Experiment4Runner.main(args); break;
                    case 5: Experiment5Runner.main(args); break;
                    case 6: Experiment6Runner.main(args); break;
                    case 7: Experiment7Runner.main(args); break;
                    case 8: Experiment8Runner.main(args); break;
                    case 9:
                        Experiment1Runner.SPEC_OVERRIDE = ExperimentConfig.EXP9;
                        try { Experiment1Runner.main(args); } finally { Experiment1Runner.SPEC_OVERRIDE = null; }
                        break;
                    case 10:
                        Experiment3Runner.SPEC_OVERRIDE = ExperimentConfig.EXP10;
                        Experiment3Runner.MU_SWEEP = ExperimentConfig.MU_SWEEP;
                        Experiment3Runner.DELTAS_OVERRIDE = new double[]{0.20};
                        try { Experiment3Runner.main(args); } finally {
                            Experiment3Runner.SPEC_OVERRIDE = null;
                            Experiment3Runner.MU_SWEEP = null;
                            Experiment3Runner.DELTAS_OVERRIDE = null;
                        }
                        break;
                    case 11:
                        Experiment7Runner.SPEC_OVERRIDE = ExperimentConfig.EXP11;
                        Experiment7Runner.SCHEDULE = ExperimentConfig.SCHEDULE_WARM20;
                        try { Experiment7Runner.main(args); } finally {
                            Experiment7Runner.SPEC_OVERRIDE = null;
                            Experiment7Runner.SCHEDULE = ExperimentConfig.SCHEDULE_EQUAL;
                        }
                        break;
                    default: throw new IllegalArgumentException("Unknown experiment id: " + id);
                }
            } catch (Exception | Error e) {
                failures++;
                System.err.println("[launcher] Experiment " + id + " failed: " + e);
                e.printStackTrace();
            }
        }
        System.out.println();
        if (failures == 0) {
            System.out.println("[launcher] All requested experiments finished.");
        } else {
            System.err.println("[launcher] " + failures + " experiment(s) FAILED; see the stack traces above.");
        }
        // Runners leave no daemon-less threads behind on the normal path, but a failure that
        // escapes a runner before its executor is shut down would keep the JVM alive forever
        // and stall a runbook silently. Exit explicitly, with a non-zero code on failure.
        System.exit(failures == 0 ? 0 : 2);
    }

    /**
     * Measurement runs are started by the user from their own terminal, never
     * in a restricted environment (the session that edits the code). The
     * restricted shell carries the HAUSP_NO_MEASURE environment variable; when it is
     * present, only the toy dataset or a results-probe directory is allowed.
     * The check stands before the run rather than in a note, because notes
     * were ignored.
     */
    private static void refuseMeasurementWhereDisabled() {
        if (System.getenv("HAUSP_NO_MEASURE") == null) return;
        boolean toyOnly = !ExperimentConfig.DATASET_FILTER.isEmpty()
                && ExperimentConfig.DATASET_FILTER.stream().allMatch(d -> d.equals(ExperimentConfig.EXAMPLE.name));
        boolean probeDir = new java.io.File(ExperimentConfig.RESULTS_DIR).getName().startsWith("results-probe");
        if (toyOnly || probeDir) return;
        System.err.println("[launcher] REFUSED: environment variable HAUSP_NO_MEASURE is set, i.e. this JVM was started from an");
        System.err.println("[launcher] restricted environment. Measurement runs on real datasets are launched by the user from");
        System.err.println("[launcher] their own terminal. Allowed from a session: --dataset example, or --results-dir results-probe*.");
        System.err.println("[launcher] (dataset filter = " + ExperimentConfig.DATASET_FILTER + ", results dir = " + ExperimentConfig.RESULTS_DIR + ")");
        System.exit(3);
    }

    private static List<Integer> parseTargets(String[] args) {
        List<Integer> ids = new ArrayList<>();
        String spec = null;
        for (int i = 0; i < args.length; i++) {
            if ("--exp".equals(args[i]) && i + 1 < args.length) {
                spec = args[i + 1];
                break;
            }
        }
        if (spec == null || spec.equalsIgnoreCase("all")) {
            for (ExperimentConfig.ExperimentSpec s : ExperimentConfig.ALL_EXPERIMENTS) ids.add(s.id);
            return ids;
        }
        for (String tok : spec.split(",")) {
            tok = tok.trim();
            if (tok.isEmpty()) continue;
            try { ids.add(Integer.parseInt(tok)); }
            catch (NumberFormatException e) {
                System.err.println("[launcher] Ignoring non-integer --exp value: " + tok);
            }
        }
        return ids;
    }

    private static String parseString(String[] args, String flag, String fallback) {
        for (int i = 0; i < args.length - 1; i++) {
            if (flag.equals(args[i])) return args[i + 1].trim();
        }
        return fallback;
    }

    private static long parseLong(String[] args, String flag, long fallback) {
        for (int i = 0; i < args.length - 1; i++) {
            if (flag.equals(args[i])) {
                try { return Long.parseLong(args[i + 1].trim()); }
                catch (NumberFormatException e) {
                    System.err.println("[launcher] cannot parse " + flag + " " + args[i + 1] + "; using " + fallback);
                }
            }
        }
        return fallback;
    }

    private static double parseDouble(String[] args, String flag, double fallback) {
        for (int i = 0; i < args.length - 1; i++) {
            if (flag.equals(args[i])) {
                try { return Double.parseDouble(args[i + 1].trim()); }
                catch (NumberFormatException e) {
                    System.err.println("[launcher] cannot parse " + flag + " " + args[i + 1] + "; using " + fallback);
                }
            }
        }
        return fallback;
    }

    private static boolean hasFlag(String[] args, String flag) {
        for (String a : args) if (flag.equals(a)) return true;
        return false;
    }

    private static void parseListFlag(String[] args, String flag, java.util.Set<String> into, boolean lowerCase) {
        for (int i = 0; i < args.length - 1; i++) {
            if (flag.equals(args[i])) {
                for (String tok : args[i + 1].split(",")) {
                    String norm = lowerCase ? tok.trim().toLowerCase() : tok.trim();
                    if (!norm.isEmpty()) into.add(norm);
                }
                return;
            }
        }
    }

    private static void parseKFilter(String[] args) {
        for (int i = 0; i < args.length - 1; i++) {
            if ("--k".equals(args[i])) {
                for (String tok : args[i + 1].split(",")) {
                    tok = tok.trim();
                    if (tok.isEmpty()) continue;
                    try {
                        int k = Integer.parseInt(tok);
                        if (k >= 1) ExperimentConfig.K_FILTER.add(k);
                        else System.err.println("[launcher] ignoring --k value " + tok + " (must be >= 1)");
                    } catch (NumberFormatException e) {
                        System.err.println("[launcher] ignoring non-integer --k value: " + tok);
                    }
                }
                return;
            }
        }
    }

    private static int parseRepeats(String[] args, int fallback) {
        for (int i = 0; i < args.length - 1; i++) {
            if ("--repeats".equals(args[i])) {
                try {
                    int n = Integer.parseInt(args[i + 1]);
                    if (n >= 1) return n;
                    System.err.println("[launcher] --repeats must be >= 1; using " + fallback);
                } catch (NumberFormatException e) {
                    System.err.println("[launcher] cannot parse --repeats " + args[i + 1] + "; using " + fallback);
                }
            }
        }
        return fallback;
    }

    private static String datasetNames(ExperimentConfig.ExperimentSpec spec) {
        StringBuilder sb = new StringBuilder("[");
        List<ExperimentConfig.DatasetRun> runs = ExperimentConfig.filteredRuns(spec);
        for (int i = 0; i < runs.size(); i++) {
            if (i > 0) sb.append(", ");
            sb.append(runs.get(i).dataset.name);
        }
        return sb.append("]").toString();
    }

    private static void printUsage() {
        System.out.println("HAUSP-UB experiment launcher");
        System.out.println("Usage:");
        System.out.println("  java ExperimentLauncher                              run every experiment (E1..E8)");
        System.out.println("  java ExperimentLauncher --exp 1                      run a single experiment");
        System.out.println("  java ExperimentLauncher --exp 9|10|11                opt-in studies: attribution, pre-large mu sweep, warm-start K");
        System.out.println("  java ExperimentLauncher --exp 1,3,5                  run a comma-separated subset");
        System.out.println("  java ExperimentLauncher --exp all                    same as the default");
        System.out.println("  java ExperimentLauncher --exp 1 --repeats 5          five independent trials per config");
        System.out.println("  java ExperimentLauncher --exp 1 --repeats-min-seconds 10   more trials for configs under 10 s");
        System.out.println("  java ExperimentLauncher --exp 5 --dataset bible      restrict to one dataset");
        System.out.println("  java ExperimentLauncher --exp 2 --dataset sign,bms1_spmf  comma-separated dataset filter");
        System.out.println("  java ExperimentLauncher --exp 2 --algo HAUSP-UB-L1,HAUSP-UB  restrict to named arms");
        System.out.println("  java ExperimentLauncher --exp 7 --k 10,100           restrict Experiment 7/11 to given K");
        System.out.println("  java ExperimentLauncher --exp 1 --results-dir results-2026-09  write CSVs under another root");
        System.out.println("  java ExperimentLauncher --exp all --resume           skip configs already present in CSV");
        System.out.println("  java ExperimentLauncher --exp all --timeout 30       override per-batch timeout (minutes)");
        System.out.println("  java ExperimentLauncher --exp 4 --mem-mode live --results-dir results-2026-09/mem   live-heap memory run");
        System.out.println("  java ExperimentLauncher --dump-config json           print the experiment declaration as JSON");
        System.out.println("  java ExperimentLauncher --print-header               print the CSV column header of this build");
        System.out.println("  java ExperimentLauncher --exp 1 --profile-phases      per-node Layer-2/3 timers on (profiling only, not for cross-arm timing)");
        System.out.println();
        System.out.println("All experimental parameters are declared in ExperimentConfig.java.");
        System.out.println("Dataset short names: bible, bms1_spmf, fifa, kosarak, leviathan, sign, syn_c8t1s5i8n5k, example");
    }
}
