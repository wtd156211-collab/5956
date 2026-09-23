"""查询错误的统一表示。"""


class QueryError(Exception):
    """解析、检查或执行查询时发生的可预期错误。

    code: README 中定义的错误码，如 SYNTAX_ERROR。
    pos: 出错字符在查询语句里的下标，从 1 开始（按 Unicode 码点计）。
    message: 给人看的说明文字。
    """

    def __init__(self, code, pos, message):
        super().__init__(message)
        self.code = code
        self.pos = pos
        self.message = message

    def to_line(self):
        return "error,{},{},{}".format(self.code, self.pos, self.message)
