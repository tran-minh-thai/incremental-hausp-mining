/**
 * A single item occurrence inside an {@link Itemset}, with its quantity
 * (internal utility) and its precomputed utility
 * {@code utility = quantity × externalUtility}.
 */
public class ItemQ {

    /** Item identifier. */
    public int id;

    /** Internal utility (quantity) of the item in this itemset. */
    public int quantity;

    /** {@code quantity × externalUtility}; {@code long} to avoid overflow. */
    public long utility;

    public ItemQ(int id, int quantity, long utility) {
        this.id = id;
        this.quantity = quantity;
        this.utility = utility;
    }
}
