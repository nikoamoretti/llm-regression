`page_count` in `src/pagination.py` undercounts when the last page is only partly full, and `page_items` returns the wrong slice.

Required behavior:

- `page_count(total, per_page)` is the number of pages needed to show `total` items, `per_page` at a time. A partly full last page still counts. Zero items is zero pages.
- `page_items(items, page, per_page)` returns the items on 1-based page `page`. A page past the end is an empty list.
- Both raise `ValueError` when `per_page` is less than 1 or `page` is less than 1.

Do not add dependencies.
