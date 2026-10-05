use inventory::Catalog;
use pricing::{list_price, Price};

pub fn quote(catalog: &Catalog, sku: &str, quantity: u32) -> Option<u32> {
    let item = catalog.get(sku)?;
    let price = Price {
        list_cents: item.list_cents,
        discount_bps: item.discount_bps,
    };
    // BUG: uses list price and ignores discounts.
    Some(quantity * list_price(&price))
}
