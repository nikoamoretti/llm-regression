import { listItems } from "./service.ts";

export function handleList(_query: Record<string, string> = {}) {
  const items = listItems();
  return { status: 200, body: { items } };
}
