from __future__ import annotations

import re
from typing import NamedTuple

TOKEN = re.compile(r"\s*(\d+|[()+*/-])")


class Token(NamedTuple):
    kind: str
    text: str


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    while index < len(source):
        match = TOKEN.match(source, index)
        if not match:
            raise ValueError(f"bad token at {index}: {source[index:]!r}")
        text = match.group(1)
        kind = "NUM" if text.isdigit() else text
        tokens.append(Token(kind, text))
        index = match.end()
    tokens.append(Token("EOF", ""))
    return tokens
