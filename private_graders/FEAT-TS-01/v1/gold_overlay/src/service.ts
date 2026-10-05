export type Item = { id: string; name: string };

export type Page = {
  items: Item[];
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
};

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

export function listItems(options: { page?: number; pageSize?: number } = {}): Page {
  const page = options.page ?? 1;
  const pageSize = options.pageSize ?? 10;
  const total = ITEMS.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (page < 1 || pageSize < 1) {
    return { items: [], page, pageSize, total, totalPages };
  }
  const start = (page - 1) * pageSize;
  const items = ITEMS.slice(start, start + pageSize);
  return { items, page, pageSize, total, totalPages };
}
