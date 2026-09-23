from dataclasses import dataclass

from .errors import QueryError


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    position: int


def tokenize(sql: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
            continue

        start = index + 1
        if char.isalpha() or char == "_":
            index += 1
            while index < length and (sql[index].isalnum() or sql[index] == "_"):
                index += 1
            tokens.append(Token("IDENT", sql[start - 1 : index].lower(), start))
        elif char.isdigit():
            index += 1
            while index < length and sql[index].isdigit():
                index += 1
            tokens.append(Token("NUMBER", int(sql[start - 1 : index]), start))
        elif char == "'":
            index += 1
            characters: list[str] = []
            closed = False
            while index < length:
                current = sql[index]
                if current == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        characters.append("'")
                        index += 2
                    else:
                        index += 1
                        closed = True
                        break
                characters.append(current)
                index += 1
            if not closed:
                raise QueryError("SYNTAX_ERROR", start, "unterminated string literal")
            tokens.append(Token("STRING", "".join(characters), start))
        elif char == "=":
            tokens.append(Token("OP", "=", start))
            index += 1
        elif char == "!":
            if index + 1 < length and sql[index + 1] == "=":
                tokens.append(Token("OP", "!=", start))
                index += 2
            else:
                raise QueryError("SYNTAX_ERROR", start, "unexpected character")
        elif char == "<":
            if index + 1 < length and sql[index + 1] == "=":
                tokens.append(Token("OP", "<=", start))
                index += 2
            else:
                tokens.append(Token("OP", "<", start))
                index += 1
        elif char == ">":
            if index + 1 < length and sql[index + 1] == "=":
                tokens.append(Token("OP", ">=", start))
                index += 2
            else:
                tokens.append(Token("OP", ">", start))
                index += 1
        elif char in "(),*.-":
            tokens.append(Token("PUNCT", char, start))
            index += 1
        else:
            raise QueryError("SYNTAX_ERROR", start, "unexpected character")

    tokens.append(Token("EOF", None, max(length, 1)))
    return tokens
