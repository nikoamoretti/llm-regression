Add 1-based pagination through the existing catalog stack.

Files:

- `src/service.ts` owns catalog records
- `src/api.ts` is the HTTP-ish handler
- `src/ui.ts` renders a page

Requirements:

- `listItems({ page, pageSize })` returns `{ items, page, pageSize, total, totalPages }`
- `page` is 1-based. Invalid pages (`0`, negative, past the last page) return an empty `items` array and the requested page number.
- `handleList` must accept `page` and `pageSize` query fields, defaulting to page 1 and pageSize 10.
- `renderList` must show `Page X of Y` and only the items for that page.

Do not add dependencies.
