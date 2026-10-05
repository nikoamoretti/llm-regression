import { listItems } from "./service.ts";

export function handleList(query: Record<string, string> = {}) {
  const page = Number(query.page ?? 1);
  const pageSize = Number(query.pageSize ?? 10);
  const body = listItems({ page, pageSize });
  return { status: 200, body };
}
