from . import ast
from .errors import QueryError
from .lexer import Token, tokenize


CLAUSES = {"group", "order", "limit"}


class Parser:
    def __init__(self, sql: str):
        self.sql = sql
        self.tokens = tokenize(sql)
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def peek(self, offset: int = 1) -> Token:
        return self.tokens[min(self.index + offset, len(self.tokens) - 1)]

    def advance(self) -> Token:
        token = self.current
        if token.kind != "EOF":
            self.index += 1
        return token

    def syntax_error(self, position: int, message: str) -> None:
        raise QueryError("SYNTAX_ERROR", position, message)

    def expect_keyword(self, word: str) -> Token:
        token = self.current
        if token.kind != "IDENT" or token.value != word:
            self.syntax_error(token.position, f"expected {word.upper()}")
        return self.advance()

    def expect_punctuation(self, mark: str) -> Token:
        token = self.current
        if token.kind != "PUNCT" or token.value != mark:
            self.syntax_error(token.position, f"expected '{mark}'")
        return self.advance()

    def is_keyword(self, word: str) -> bool:
        token = self.current
        return token.kind == "IDENT" and token.value == word

    def at_clause_boundary(self) -> bool:
        token = self.current
        return token.kind == "EOF" or (token.kind == "IDENT" and token.value in CLAUSES)

    def parse_identifier_field(self) -> ast.Field:
        token = self.current
        if token.kind != "IDENT":
            self.syntax_error(token.position, "expected field name")
        self.advance()
        if self.current.kind == "PUNCT" and self.current.value == "(":
            self.syntax_error(token.position, "nested functions are not supported")
        return ast.Field(str(token.value), token.position)

    def parse_select_item(self) -> ast.SelectItem:
        token = self.current
        if token.kind != "IDENT":
            self.syntax_error(token.position, "expected field or aggregate")
        self.advance()

        if self.current.kind == "PUNCT" and self.current.value == "(":
            function_name = str(token.value)
            self.advance()
            argument: ast.Field | None = None
            if self.current.kind == "PUNCT" and self.current.value == "*":
                star = self.advance()
                if function_name not in {"count", "sum", "min", "max", "avg"}:
                    raise QueryError(
                        "UNKNOWN_FUNCTION",
                        token.position,
                        f"unknown aggregate function '{function_name}'",
                    )
                if function_name != "count":
                    self.syntax_error(star.position, "only COUNT supports *")
            else:
                argument = self.parse_identifier_field()
            self.expect_punctuation(")")
            item = ast.SelectItem(
                kind="aggregate",
                position=token.position,
                name=function_name,
                argument=argument,
            )
        else:
            item = ast.SelectItem(kind="field", position=token.position, name=str(token.value))

        if self.is_keyword("as"):
            self.advance()
            alias = self.current
            if alias.kind != "IDENT":
                self.syntax_error(alias.position, "expected alias")
            item = ast.SelectItem(
                kind=item.kind,
                position=item.position,
                name=item.name,
                alias=str(alias.value).lower(),
                argument=item.argument,
            )
            self.advance()
        return item

    def parse_select_list(self) -> list[ast.SelectItem]:
        items: list[ast.SelectItem] = []
        while True:
            items.append(self.parse_select_item())
            if not (self.current.kind == "PUNCT" and self.current.value == ","):
                return items
            comma = self.advance()
            if self.at_clause_boundary() or self.is_keyword("from"):
                self.syntax_error(comma.position, "trailing comma is not allowed")

    def parse_comparison(self) -> ast.Condition:
        field = self.parse_identifier_field()
        operator_token = self.current
        if operator_token.kind != "OP":
            self.syntax_error(operator_token.position, "expected comparison operator")
        operator = str(operator_token.value)
        self.advance()

        literal_token = self.current
        if literal_token.kind == "NUMBER":
            literal = ast.Literal(int(literal_token.value), "int", literal_token.position)
        elif literal_token.kind == "STRING":
            literal = ast.Literal(str(literal_token.value), "string", literal_token.position)
        else:
            self.syntax_error(literal_token.position, "expected non-negative integer or string")
        self.advance()
        return ast.Comparison(field, operator, literal)

    def parse_condition_atom(self) -> ast.Condition:
        if self.is_keyword("not"):
            token = self.advance()
            if self.at_clause_boundary():
                self.syntax_error(token.position, "NOT is missing a condition")
            return ast.Not(self.parse_condition_atom())

        if self.current.kind == "PUNCT" and self.current.value == "(":
            self.advance()
            condition = self.parse_or()
            self.expect_punctuation(")")
            return condition

        return self.parse_comparison()

    def parse_and(self) -> ast.Condition:
        condition = self.parse_condition_atom()
        while self.is_keyword("and"):
            token = self.advance()
            if self.at_clause_boundary() or self.is_keyword("or"):
                self.syntax_error(token.position, "AND is missing a right condition")
            condition = ast.BooleanOp("AND", condition, self.parse_condition_atom())
        return condition

    def parse_or(self) -> ast.Condition:
        condition = self.parse_and()
        while self.is_keyword("or"):
            token = self.advance()
            if self.at_clause_boundary() or self.is_keyword("and"):
                self.syntax_error(token.position, "OR is missing a right condition")
            condition = ast.BooleanOp("OR", condition, self.parse_and())
        return condition

    def parse_where(self) -> ast.Condition:
        condition = self.parse_or()
        if not self.at_clause_boundary():
            self.syntax_error(self.current.position, "unexpected content in WHERE")
        return condition

    def parse_field_list(self) -> list[ast.Field]:
        fields: list[ast.Field] = []
        while True:
            fields.append(self.parse_identifier_field())
            if not (self.current.kind == "PUNCT" and self.current.value == ","):
                return fields
            comma = self.advance()
            if self.at_clause_boundary():
                self.syntax_error(comma.position, "trailing comma is not allowed")

    def parse_order_list(self) -> list[ast.OrderItem]:
        items: list[ast.OrderItem] = []
        while True:
            token = self.current
            if token.kind != "IDENT":
                self.syntax_error(token.position, "expected ORDER BY field or alias")
            self.advance()
            descending = False
            if self.is_keyword("asc"):
                self.advance()
            elif self.is_keyword("desc"):
                descending = True
                self.advance()
            items.append(ast.OrderItem(str(token.value).lower(), token.position, descending))

            if not (self.current.kind == "PUNCT" and self.current.value == ","):
                return items
            comma = self.advance()
            if self.at_clause_boundary():
                self.syntax_error(comma.position, "trailing comma is not allowed")

    def parse_limit(self) -> tuple[int, int]:
        token = self.current
        if token.kind != "NUMBER":
            raise QueryError("BAD_LIMIT", token.position, "LIMIT must be a non-negative integer")
        value = int(token.value)
        if value < 0:
            raise QueryError("BAD_LIMIT", token.position, "LIMIT must be non-negative")
        if self.peek().kind == "PUNCT" and self.peek().value in (".", "-"):
            raise QueryError("BAD_LIMIT", token.position, "LIMIT must be a non-negative integer")
        self.advance()
        return value, token.position

    def parse(self) -> ast.Query:
        self.expect_keyword("select")
        if self.is_keyword("from"):
            self.syntax_error(self.current.position, "SELECT list is empty")
        select = self.parse_select_list()
        self.expect_keyword("from")

        table = self.current
        if table.kind != "IDENT" or table.value != "logs":
            self.syntax_error(table.position, "only FROM logs is supported")
        self.advance()

        where = None
        group_by: list[ast.Field] = []
        order_by: list[ast.OrderItem] = []
        limit = None
        limit_position = None

        if self.is_keyword("where"):
            self.advance()
            if self.at_clause_boundary():
                self.syntax_error(self.current.position, "WHERE is missing a condition")
            where = self.parse_where()

        if self.is_keyword("group"):
            self.advance()
            self.expect_keyword("by")
            if self.at_clause_boundary():
                self.syntax_error(self.current.position, "GROUP BY is empty")
            group_by = self.parse_field_list()

        if self.is_keyword("order"):
            self.advance()
            self.expect_keyword("by")
            if self.at_clause_boundary():
                self.syntax_error(self.current.position, "ORDER BY is empty")
            order_by = self.parse_order_list()

        if self.is_keyword("limit"):
            self.advance()
            limit, limit_position = self.parse_limit()

        if self.current.kind != "EOF":
            self.syntax_error(self.current.position, "unexpected trailing content")

        return ast.Query(select, where, group_by, order_by, limit, limit_position)


def parse_query(sql: str) -> ast.Query:
    return Parser(sql).parse()
