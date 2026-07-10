import java.util.ArrayList;
import java.util.List;

/**
 * One sequence in a quantitative sequential database: an ordered list of
 * itemsets with its accumulated sequence utility (sum of internal utilities of
 * all items).
 */
public class Sequence {

    /** Sequence identifier (zero- or one-based, dataset-dependent). */
    public int sid;

    /** Itemsets in temporal order. */
    public List<Itemset> itemsets;

    /** Sequence utility {@code SU(s) = Σ utility(item)} over all items in {@code s}. */
    public long totalUtility;

    public Sequence(int sid) {
        this.sid = sid;
        this.itemsets = new ArrayList<>();
        this.totalUtility = 0;
    }

    /** Append an itemset and accumulate its item utilities into {@link #totalUtility}. */
    public void addItemset(Itemset itemset) {
        this.itemsets.add(itemset);
        for (ItemQ item : itemset.items) {
            this.totalUtility += item.utility;
        }
    }
}
