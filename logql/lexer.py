"""词法分析：把查询文本切成 token 流。

token 的 pos 是 1 起始的码点下标，指向 token 的第一个字符；
EOF token 的 pos 是 len(text) + 1。
"""

from .errors import QueryError

# token 种类
IDENT = "IDENT"      # 标识符 / 关键字（是否关键字由 parser 判断，大小写不敏感）
INT = "INT"          # 非负整数字面量
STR = "STR"          # 字符串字面量（已处理 '' 转义）
OP = "OP"            # = != < <= > >=
PUNCT = "PUNCT"      # ( ) , *
EOF = "EOF"

_PUNCT = set("(),*")


class Token:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind, value, pos):
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self):
        return "Token({!r}, {!r}, {})".format(self.kind, self.value, self.pos)


def tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c.isalpha() or c == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(Token(IDENT, text[i:j], i + 1))
            i = j
            continue
        if c.isdigit():
            j = i + 1
            while j < n and text[j].isdigit():
                j += 1
            tokens.append(Token(INT, int(text[i:j]), i + 1))
            i = j
            continue
        if c == "'":
            start = i
            j = i + 1
            buf = []
            while True:
                if j >= n:
                    raise QueryError("SYNTAX_ERROR", start + 1, "字符串字面量没有闭合")
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    j += 1
                    break
                buf.append(text[j])
                j += 1
            tokens.append(Token(STR, "".join(buf), start + 1))
            i = j
            continue
        if c in "=<>":
            if c == "<" and i + 1 < n and text[i + 1] == "=":
                tokens.append(Token(OP, "<=", i + 1))
                i += 2
                continue
            if c == ">" and i + 1 < n and text[i + 1] == "=":
                tokens.append(Token(OP, ">=", i + 1))
                i += 2
                continue
            if c == "<":
                tokens.append(Token(OP, "<", i + 1))
            elif c == ">":
                tokens.append(Token(OP, ">", i + 1))
            else:
                tokens.append(Token(OP, "=", i + 1))
            i += 1
            continue
        if c == "!":
            if i + 1 < n and text[i + 1] == "=":
                tokens.append(Token(OP, "!=", i + 1))
                i += 2
                continue
            raise QueryError("SYNTAX_ERROR", i + 1, "不认识的字符 '!'" )
        if c in _PUNCT:
            tokens.append(Token(PUNCT, c, i + 1))
            i += 1
            continue
        raise QueryError("SYNTAX_ERROR", i + 1, "不认识的字符 {!r}".format(c))
    # EOF 位置指向最后一个非空白字符之后，忽略查询末尾的空白（如文件尾换行）
    eof_pos = len(text.rstrip(" \t\r\n")) + 1
    tokens.append(Token(EOF, None, eof_pos))
    return tokens
