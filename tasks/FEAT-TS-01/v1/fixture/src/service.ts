export type Item = { id: string; name: string };

const ITEMS: Item[] = [
  { id: "1", name: "alpha" },
  { id: "2", name: "bravo" },
  { id: "3", name: "charlie" },
  { id: "4", name: "delta" },
  { id: "5", name: "echo" },
];

export function allItems(): Item[] {
  return ITEMS.slice();
}

export function listItems(): Item[] {
  return allItems();
}
