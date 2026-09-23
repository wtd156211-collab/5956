"""语义检查：在读数据之前完成所有字段存在性与类型检查。"""

from .errors import QueryError
from .parser import And, Compare, Not, Or

# 固定表结构：字段名 -> 类型（"int" 或 "string"）
SCHEMA = {
    "ts": "string",
    "level": "string",
    "service": "string",
    "region": "string",
    "status": "int",
    "latency_ms": "int",
}


def _check_condition(node):
    if isinstance(node, Compare):
        ftype = SCHEMA.get(node.field)
        if ftype is None:
            raise QueryError("UNKNOWN_FIELD", node.field_pos,
                             "WHERE 里引用了不存在的字段 {!r}".format(node.field))
        if ftype != node.lit_kind:
            raise QueryError(
                "TYPE_MISMATCH", node.lit_pos,
                "字段 {} 是 {} 类型，不能和 {} 字面量比较".format(
                    node.field, ftype, node.lit_kind))
        return
    if isinstance(node, (And, Or)):
        _check_condition(node.left)
        _check_condition(node.right)
        return
    if isinstance(node, Not):
        _check_condition(node.operand)
        return
    raise AssertionError("未知的条件节点 {!r}".format(node))


def check(query):
    """对解析后的 Query 做全量检查，任何错误都在执行前抛出。"""
    has_field = False
    has_agg = False
    for item in query.select:
        if item.kind == "field":
            has_field = True
            if item.name not in SCHEMA:
                raise QueryError("UNKNOWN_FIELD", item.name_pos,
                                 "选择列表里引用了不存在的字段 {!r}".format(item.name))
        else:
            has_agg = True
            if item.arg is not None:
                atype = SCHEMA.get(item.arg)
                if atype is None:
                    raise QueryError(
                        "UNKNOWN_FIELD", item.arg_pos,
                        "聚合参数里引用了不存在的字段 {!r}".format(item.arg))
                if item.name in ("sum", "avg") and atype != "int":
                    raise QueryError(
                        "TYPE_MISMATCH", item.name_pos,
                        "{} 只能用在整数列上，{} 是 {} 类型".format(
                            item.name, item.arg, atype))
    if query.where is not None:
        _check_condition(query.where)
    if has_field and has_agg and not query.group_by:
        first_field = next(it for it in query.select if it.kind == "field")
        raise QueryError("MIXED_WITHOUT_GROUP_BY", first_field.name_pos,
                         "选择列表里既有字段又有聚合，必须加 GROUP BY")
    for name, pos in query.group_by:
        if name not in SCHEMA:
            raise QueryError("UNKNOWN_FIELD", pos,
                             "GROUP BY 里引用了不存在的字段 {!r}".format(name))
    # ORDER BY 的可解析性依赖执行期的别名表，这里先检查“既不是别名也不是
    # 已知字段”的情况，真正的列定位在执行时完成。
    alias_names = {it.header for it in query.select}
    field_names = {it.name for it in query.select if it.kind == "field"}
    group_fields = {name for name, _ in query.group_by}
    for item in query.order_by:
        if (item.name not in alias_names
                and item.name not in field_names
                and item.name not in group_fields
                and item.name not in SCHEMA):
            raise QueryError("UNKNOWN_FIELD", item.pos,
                             "ORDER BY 里引用了不存在的字段或别名 {!r}".format(item.name))
