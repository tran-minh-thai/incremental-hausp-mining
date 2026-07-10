import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Single command-line entry point that dispatches to one or more of
 * {@code Experiment1Runner} through {@code Experiment6Runner} in a shared JVM.
 *
 * <pre>
 *   java ExperimentLauncher                  # all six experiments
 *   java ExperimentLauncher --exp 1          # only Experiment 1
 *   java ExperimentLauncher --exp 1,3,5      # selected experiments
 *   java ExperimentLauncher --exp all        # same as no arguments
 * </pre>
 */
public final class ExperimentLauncher {

    public static void main(String[] args) throws Exception {
        List<Integer> targets = parseTargets(args);
        ExperimentConfig.REPEATS = parseRepeats(args, ExperimentConfig.REPEATS);
        parseDatasetFilter(args);
        ExperimentConfig.RESUME = hasFlag(args, "--resume");
        ExperimentConfig.TIMEOUT_OVERRIDE_MIN = parseLong(args, "--timeout", 0);

        if (targets.isEmpty()) {
            printUsage();
            System.exit(1);
        }

        System.out.println("[launcher] experiments to run : " + targets);
        System.out.println("[launcher] repeats per config  : " + ExperimentConfig.REPEATS);
        if (!ExperimentConfig.DATASET_FILTER.isEmpty()) {
            System.out.println("[launcher] dataset filter      : " + ExperimentConfig.DATASET_FILTER);
        }
        if (ExperimentConfig.RESUME) {
            System.out.println("[launcher] resume mode         : on (skipping configs already in CSV)");
        }
        if (ExperimentConfig.TIMEOUT_OVERRIDE_MIN > 0) {
            System.out.println("[launcher] timeout override    : " + ExperimentConfig.TIMEOUT_OVERRIDE_MIN + " min");
        }

        for (int id : targets) {
            ExperimentConfig.ExperimentSpec spec = ExperimentConfig.getById(id);
            System.out.println();
            System.out.println("------------------------------------------------------------");
            System.out.println(" Experiment " + id + ": " + spec.title);
            System.out.println(" Datasets   : " + datasetNames(spec));
            System.out.println(" Algorithms : " + Arrays.toString(spec.algorithms));
            System.out.println(" Output     : " + spec.outputDir + "/" + spec.logFileName);
            System.out.println("------------------------------------------------------------");
            System.out.println();

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
                    default: throw new IllegalArgumentException("Unknown experiment id: " + id);
                }
            } catch (Exception e) {
                System.err.println("[launcher] Experiment " + id + " failed: " + e);
                e.printStackTrace();
            }
        }
        System.out.println();
        System.out.println("[launcher] All requested experiments finished.");
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
            for (int i = 1; i <= ExperimentConfig.ALL_EXPERIMENTS.length; i++) ids.add(i);
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

    private static boolean hasFlag(String[] args, String flag) {
        for (String a : args) if (flag.equals(a)) return true;
        return false;
    }

    private static void parseDatasetFilter(String[] args) {
        for (int i = 0; i < args.length - 1; i++) {
            if ("--dataset".equals(args[i])) {
                for (String tok : args[i + 1].split(",")) {
                    String norm = tok.trim().toLowerCase();
                    if (!norm.isEmpty()) ExperimentConfig.DATASET_FILTER.add(norm);
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
        for (int i = 0; i < spec.runs.size(); i++) {
            if (i > 0) sb.append(", ");
            sb.append(spec.runs.get(i).dataset.name);
        }
        return sb.append("]").toString();
    }

    private static void printUsage() {
        System.out.println("HAUSP-UB experiment launcher");
        System.out.println("Usage:");
        System.out.println("  java ExperimentLauncher                              run every experiment (E1..E8)");
        System.out.println("  java ExperimentLauncher --exp 1                      run a single experiment");
        System.out.println("  java ExperimentLauncher --exp 1,3,5                  run a comma-separated subset");
        System.out.println("  java ExperimentLauncher --exp all                    same as the default");
        System.out.println("  java ExperimentLauncher --exp 1 --repeats 5          five independent trials per config");
        System.out.println("  java ExperimentLauncher --exp 5 --dataset bible      restrict to one dataset");
        System.out.println("  java ExperimentLauncher --exp 2 --dataset sign,bms1  comma-separated dataset filter");
        System.out.println("  java ExperimentLauncher --exp all --resume           skip configs already present in CSV");
        System.out.println("  java ExperimentLauncher --exp all --timeout 30       override per-config timeout (minutes)");
        System.out.println();
        System.out.println("All experimental parameters are declared in ExperimentConfig.java.");
        System.out.println("Dataset short names: bible, bms1_spmf, fifa, kosarak, leviathan, sign");
    }
}
