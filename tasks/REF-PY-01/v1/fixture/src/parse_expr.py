from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Number:
    value: int


@dataclass(frozen=True)
class Unary:
    op: str
    expr: object


@dataclass(frozen=True)
class Binary:
    op: str
    left: object
    right: object


TOKEN = re.compile(r"\s*(\d+|[()+*/-])")


def tokenize(source: str) -> list[tuple[str, str]]:
    tokens = []
    index = 0
    while index < len(source):
        match = TOKEN.match(source, index)
        if not match:
            raise ValueError(f"bad token at {index}: {source[index:]!r}")
        text = match.group(1)
        kind = "NUM" if text.isdigit() else text
        tokens.append((kind, text))
        index = match.end()
    tokens.append(("EOF", ""))
    return tokens


class Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.index]

    def eat(self, kind: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if kind is not None and token[0] != kind:
            raise ValueError(f"expected {kind}, got {token}")
        self.index += 1
        return token

    def parse(self) -> object:
        expr = self.expr()
        self.eat("EOF")
        return expr

    def expr(self) -> object:
        node = self.term()
        while self.peek()[0] in {"+", "-"}:
            op = self.eat()[0]
            node = Binary(op, node, self.term())
        return node

    def term(self) -> object:
        node = self.factor()
        while self.peek()[0] in {"*", "/"}:
            op = self.eat()[0]
            node = Binary(op, node, self.factor())
        return node

    def factor(self) -> object:
        kind, text = self.peek()
        if kind == "-":
            self.eat("-")
            return Unary("-", self.factor())
        if kind == "NUM":
            self.eat("NUM")
            return Number(int(text))
        if kind == "(":
            self.eat("(")
            node = self.expr()
            self.eat(")")
            return node
        raise ValueError(f"unexpected {kind}")


def parse(source: str) -> object:
    return Parser(tokenize(source)).parse()
