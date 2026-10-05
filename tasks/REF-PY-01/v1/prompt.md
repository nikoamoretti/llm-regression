`src/parse_expr.py` is a single-file arithmetic parser. Split it without changing parse behavior.

Required structure:

- `src/ast_nodes.py` must define `Number`, `Unary`, and `Binary`
- `src/lexer.py` must define `Token` and `tokenize`
- `src/parser.py` must define `parse`
- `src/parse_expr.py` may re-export `parse` so existing imports keep working

Preserve operator precedence: `*` and `/` bind tighter than `+` and `-`. Unary minus is supported. Do not change the accepted language.
