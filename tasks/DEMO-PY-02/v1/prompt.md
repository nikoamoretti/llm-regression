`slugify` in `src/slug.py` leaves repeated and trailing hyphens in its output.

Required behavior:

- `slugify(text)` lowercases `text` and replaces every run of characters other than ASCII letters and digits with a single hyphen.
- The result never starts or ends with a hyphen.
- Text with no letters or digits gives an empty string.

Do not add dependencies.
