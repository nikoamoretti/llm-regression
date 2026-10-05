#[derive(Clone, Debug)]
pub struct Price {
    pub list_cents: u32,
    pub discount_bps: u32,
}

pub fn effective_price(price: &Price) -> u32 {
    let discount = price.list_cents as u64 * price.discount_bps as u64 / 10_000;
    price.list_cents.saturating_sub(discount as u32)
}

pub fn list_price(price: &Price) -> u32 {
    price.list_cents
}
