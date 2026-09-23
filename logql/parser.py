"""语法分析：token 流 -> AST。只做语法，不做字段/类型检查。"""

from .errors import QueryError
from .lexer import EOF, IDENT, INT, OP, PUNCT, STR, tokenize

AGG_FUNCS = ("count", "sum", "min", "max", "avg")
COMPARE_OPS = ("=", "!=", "<", "<=", ">", ">=")


class SelectItem:
    """选择列表中的一项：普通字段或聚合调用。"""

    __slots__ = ("kind", "name", "name_pos", "arg", "arg_pos", "alias")

    def __init__(self, kind, name, name_pos, arg=None, arg_pos=None, alias=None):
        self.kind = kind          # "field" 或 "agg"
        self.name = name          # 字段名或函数名（小写）
        self.name_pos = name_pos
        self.arg = arg            # agg 的参数字段名；count(*) 时为 None
        self.arg_pos = arg_pos
        self.alias = alias        # AS 别名（小写），没有则为 None

    @property
    def header(self):
        if self.alias is not None:
            return self.alias
        return self.name


class Compare:
    __slots__ = ("field", "field_pos", "op", "lit_kind", "lit_value", "lit_pos")

    def __init__(self, field, field_pos, op, lit_kind, lit_value, lit_pos):
        self.field = field            # 字段名（小写）
        self.field_pos = field_pos
        self.op = op
        self.lit_kind = lit_kind      # "int" 或 "string"
        self.lit_value = lit_value
        self.lit_pos = lit_pos


class And:
    __slots__ = ("left", "right")

    def __init__(self, left, right):
        self.left = left
        self.right = right


class Or:
    __slots__ = ("left", "right")

    def __init__(self, left, right):
        self.left = left
        self.right = right


class Not:
    __slots__ = ("operand",)

    def __init__(self, operand):
        self.operand = operand


class OrderItem:
    __slots__ = ("name", "pos", "desc")

    def __init__(self, name, pos, desc):
        self.name = name
        self.pos = pos
        self.desc = desc


class Query:
    __slots__ = ("select", "where", "group_by", "order_by", "limit")

    def __init__(self, select, where, group_by, order_by, limit):
        self.select = select
        self.where = where            # None 或条件表达式树
        self.group_by = group_by      # [(字段名, pos), ...]
        self.order_by = order_by      # [OrderItem, ...]
        self.limit = limit            # None 或非负 int


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

    @property
    def cur(self):
        return self.tokens[self.i]

    def advance(self):
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def syntax(self, message, tok=None):
        tok = tok if tok is not None else self.cur
        raise QueryError("SYNTAX_ERROR", tok.pos, message)

    def is_keyword(self, word):
        tok = self.cur
        return tok.kind == IDENT and tok.value.upper() == word

    def expect_keyword(self, word):
        if not self.is_keyword(word):
            self.syntax("这里应该是关键字 {}".format(word))
        return self.advance()

    def expect_punct(self, ch):
        tok = self.cur
        if tok.kind != PUNCT or tok.value != ch:
            self.syntax("这里应该是 '{}'".format(ch))
        return self.advance()

    def expect_ident(self, what):
        tok = self.cur
        if tok.kind != IDENT:
            self.syntax("这里应该是{}".format(what))
        return self.advance()

    # ---- 顶层结构 ----

    def parse_query(self):
        self.expect_keyword("SELECT")
        select = self.parse_select_list()
        self.expect_keyword("FROM")
        table = self.expect_ident("表名 logs")
        if table.value.upper() != "LOGS":
            raise QueryError("SYNTAX_ERROR", table.pos,
                             "只支持表 logs，不认识 {!r}".format(table.value))
        where = None
        group_by = []
        order_by = []
        limit = None
        if self.is_keyword("WHERE"):
            self.advance()
            where = self.parse_or()
        if self.is_keyword("GROUP"):
            self.advance()
            self.expect_keyword("BY")
            group_by = self.parse_field_list()
        if self.is_keyword("ORDER"):
            self.advance()
            self.expect_keyword("BY")
            order_by = self.parse_order_list()
        if self.is_keyword("LIMIT"):
            self.advance()
            tok = self.cur
            if tok.kind != INT:
                raise QueryError("BAD_LIMIT", tok.pos,
                                 "LIMIT 后面必须是非负整数")
            limit = self.advance().value
        if self.cur.kind != EOF:
            self.syntax("查询结束后还有多余内容")
        return Query(select, where, group_by, order_by, limit)

    def parse_select_list(self):
        items = [self.parse_select_item()]
        while self.cur.kind == PUNCT and self.cur.value == ",":
            self.advance()
            items.append(self.parse_select_item())
        return items

    def parse_select_item(self):
        tok = self.expect_ident("字段名或聚合函数")
        name = tok.value.lower()
        if self.cur.kind == PUNCT and self.cur.value == "(":
            if name not in AGG_FUNCS:
                raise QueryError("UNKNOWN_FUNCTION", tok.pos,
                                 "不认识的聚合函数 {!r}".format(tok.value))
            self.advance()
            arg = None
            arg_pos = None
            if self.cur.kind == PUNCT and self.cur.value == "*":
                star = self.advance()
                if name != "count":
                    self.syntax("只有 count 可以用 * 作参数", star)
            else:
                arg_tok = self.expect_ident("聚合参数（字段名）")
                arg = arg_tok.value.lower()
                arg_pos = arg_tok.pos
            self.expect_punct(")")
            item = SelectItem("agg", name, tok.pos, arg, arg_pos)
        else:
            item = SelectItem("field", name, tok.pos)
        if self.is_keyword("AS"):
            self.advance()
            alias_tok = self.expect_ident("别名")
            item.alias = alias_tok.value.lower()
        return item

    def parse_field_list(self):
        fields = [self.parse_field_ref()]
        while self.cur.kind == PUNCT and self.cur.value == ",":
            self.advance()
            fields.append(self.parse_field_ref())
        return fields

    def parse_field_ref(self):
        tok = self.expect_ident("字段名")
        return (tok.value.lower(), tok.pos)

    def parse_order_list(self):
        items = [self.parse_order_item()]
        while self.cur.kind == PUNCT and self.cur.value == ",":
            self.advance()
            items.append(self.parse_order_item())
        return items

    def parse_order_item(self):
        tok = self.expect_ident("排序字段或别名")
        desc = False
        if self.is_keyword("ASC"):
            self.advance()
        elif self.is_keyword("DESC"):
            self.advance()
            desc = True
        return OrderItem(tok.value.lower(), tok.pos, desc)

    # ---- WHERE 条件：NOT > AND > OR ----

    def parse_or(self):
        node = self.parse_and()
        while self.is_keyword("OR"):
            self.advance()
            node = Or(node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.is_keyword("AND"):
            self.advance()
            node = And(node, self.parse_not())
        return node

    def parse_not(self):
        if self.is_keyword("NOT"):
            self.advance()
            return Not(self.parse_not())
        return self.parse_atom()

    def parse_atom(self):
        if self.cur.kind == PUNCT and self.cur.value == "(":
            self.advance()
            node = self.parse_or()
            self.expect_punct(")")
            return node
        field_tok = self.expect_ident("字段名")
        op_tok = self.cur
        if op_tok.kind != OP:
            self.syntax("这里应该是比较运算符（= != < <= > >=）")
        self.advance()
        lit_tok = self.cur
        if lit_tok.kind == INT:
            self.advance()
            lit_kind, lit_value = "int", lit_tok.value
        elif lit_tok.kind == STR:
            self.advance()
            lit_kind, lit_value = "string", lit_tok.value
        else:
            self.syntax("比较右边应该是非负整数或单引号字符串")
        return Compare(field_tok.value.lower(), field_tok.pos, op_tok.value,
                       lit_kind, lit_value, lit_tok.pos)


def parse(text):
    """解析查询文本，返回 Query；语法错误抛 QueryError。"""
    return _Parser(tokenize(text)).parse_query()
