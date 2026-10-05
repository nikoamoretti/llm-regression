#[cfg(test)]
mod tests {
    use checkout::quote;
    use inventory::Catalog;

    #[test]
    fn discounted_sku_uses_effective_price() {
        let catalog = Catalog::demo();
        assert_eq!(quote(&catalog, "wide", 2), Some(1500));
    }

    #[test]
    fn undiscounted_sku_unchanged() {
        let catalog = Catalog::demo();
        assert_eq!(quote(&catalog, "NARROW", 1), Some(400));
    }

    #[test]
    fn unknown_sku() {
        let catalog = Catalog::demo();
        assert_eq!(quote(&catalog, "nope", 1), None);
    }
}
