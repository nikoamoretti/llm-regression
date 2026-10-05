import { handleList } from "./api.ts";

export function renderList(query: Record<string, string> = {}): string {
  const { body } = handleList(query);
  const names = body.items.map((item: { name: string }) => item.name).join(", ");
  return `Items: ${names}`;
}
