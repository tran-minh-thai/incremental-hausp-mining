import java.util.ArrayList;
import java.util.List;

/**
 * A set of items that occur in the same transaction within a sequence. Inside
 * the input files itemsets are delimited by {@code -1}; the entire sequence is
 * terminated by {@code -2}.
 */
public class Itemset {

    /**
     * Items in this itemset, kept sorted by item id in ascending order. The
     * ordering matters because i-extensions require the appended item to have
     * an id strictly greater than the last item of the parent pattern.
     */
    public List<ItemQ> items;

    public Itemset() {
        this.items = new ArrayList<>();
    }

    /**
     * Append an item. Callers are responsible for either inserting items in
     * ascending id order or sorting the list afterwards.
     */
    public void addItem(ItemQ item) {
        this.items.add(item);
    }
}
