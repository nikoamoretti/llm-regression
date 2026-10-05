from __future__ import annotations

from ast_nodes import Binary, Number, Unary
from lexer import Token, tokenize


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> Token:
        return self.tokens[self.index]

    def eat(self, kind: str | None = None) -> Token:
        token = self.peek()
        if kind is not None and token.kind != kind:
            raise ValueError(f"expected {kind}, got {token}")
        self.index += 1
        return token

    def parse(self) -> object:
        expr = self.expr()
        self.eat("EOF")
        return expr

    def expr(self) -> object:
        node = self.term()
        while self.peek().kind in {"+", "-"}:
            op = self.eat().kind
            node = Binary(op, node, self.term())
        return node

    def term(self) -> object:
        node = self.factor()
        while self.peek().kind in {"*", "/"}:
            op = self.eat().kind
            node = Binary(op, node, self.factor())
        return node

    def factor(self) -> object:
        token = self.peek()
        if token.kind == "-":
            self.eat("-")
            return Unary("-", self.factor())
        if token.kind == "NUM":
            self.eat("NUM")
            return Number(int(token.text))
        if token.kind == "(":
            self.eat("(")
            node = self.expr()
            self.eat(")")
            return node
        raise ValueError(f"unexpected {token.kind}")


def parse(source: str) -> object:
    return Parser(tokenize(source)).parse()
