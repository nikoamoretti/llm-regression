use std::collections::HashMap;

pub struct Catalog {
    items: HashMap<String, Item>,
}

#[derive(Clone, Debug)]
pub struct Item {
    pub sku: String,
    pub list_cents: u32,
    pub discount_bps: u32,
}

impl Catalog {
    pub fn demo() -> Self {
        let mut items = HashMap::new();
        items.insert(
            "WIDE".into(),
            Item {
                sku: "WIDE".into(),
                list_cents: 1000,
                discount_bps: 2500,
            },
        );
        items.insert(
            "NARROW".into(),
            Item {
                sku: "NARROW".into(),
                list_cents: 400,
                discount_bps: 0,
            },
        );
        Self { items }
    }

    pub fn get(&self, sku: &str) -> Option<&Item> {
        self.items.get(&sku.to_ascii_uppercase())
    }
}
