"""执行器：单趟流式扫描 CSV，分组聚合，稳定排序，格式化输出。"""

import csv
import operator
from decimal import Decimal, ROUND_HALF_UP

from .checker import SCHEMA, check
from .errors import QueryError
from .parser import And, Compare, Not, Or, parse

_FIELD_NAMES = list(SCHEMA)           # 固定的列顺序
_INDEX = {name: i for i, name in enumerate(_FIELD_NAMES)}

_OPS = {
    "=": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


# ---- 条件编译：三值逻辑 + 短路 ----

def _compile_condition(node):
    """把条件树编译成 row -> True/False/None 的闭包。

    None 表示“未知”（三值逻辑）。AND 左边为 False、OR 左边为 True 时
    不再计算右边，实现短路。
    """
    if isinstance(node, Compare):
        i = _INDEX[node.field]
        op = _OPS[node.op]
        lit = node.lit_value

        def cmp(row, i=i, op=op, lit=lit):
            v = row[i]
            if v is None:
                return None
            return op(v, lit)

        return cmp
    if isinstance(node, And):
        left = _compile_condition(node.left)
        right = _compile_condition(node.right)

        def and_(row):
            lv = left(row)
            if lv is False:
                return False
            rv = right(row)
            if rv is False:
                return False
            if lv is None or rv is None:
                return None
            return True

        return and_
    if isinstance(node, Or):
        left = _compile_condition(node.left)
        right = _compile_condition(node.right)

        def or_(row):
            lv = left(row)
            if lv is True:
                return True
            rv = right(row)
            if rv is True:
                return True
            if lv is None or rv is None:
                return None
            return False

        return or_
    if isinstance(node, Not):
        operand = _compile_condition(node.operand)

        def not_(row):
            v = operand(row)
            if v is None:
                return None
            return not v

        return not_
    raise AssertionError("未知的条件节点 {!r}".format(node))


# ---- 聚合 ----

def _compile_agg(item):
    """返回 (init, update, finalize) 三元组。

    init 是累加槽的初始值（标量槽包成 [v]，avg 是 [total, n]）；
    update(slot, row) 累积一行；finalize(slot) 算出输出值。
    null 一律跳过（count(*) 除外）。
    """
    name = item.name
    i = _INDEX[item.arg] if item.arg is not None else None
    if name == "count":
        if i is None:
            return [0], lambda slot, row: slot.__setitem__(0, slot[0] + 1), \
                lambda slot: slot[0]

        def upd_count(slot, row):
            if row[i] is not None:
                slot[0] += 1
        return [0], upd_count, lambda slot: slot[0]
    if name == "sum":
        def upd_sum(slot, row):
            v = row[i]
            if v is not None:
                slot[0] += v
        return [0], upd_sum, lambda slot: slot[0]
    if name == "min":
        def upd_min(slot, row):
            v = row[i]
            if v is not None and (slot[0] is None or v < slot[0]):
                slot[0] = v
        return [None], upd_min, lambda slot: slot[0]
    if name == "max":
        def upd_max(slot, row):
            v = row[i]
            if v is not None and (slot[0] is None or v > slot[0]):
                slot[0] = v
        return [None], upd_max, lambda slot: slot[0]
    if name == "avg":
        def upd_avg(slot, row):
            v = row[i]
            if v is not None:
                slot[0] += v
                slot[1] += 1

        def fin_avg(slot):
            if slot[1] == 0:
                return None
            return (Decimal(slot[0]) / Decimal(slot[1])).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP)

        return [0, 0], upd_avg, fin_avg
    raise AssertionError("未知的聚合函数 {!r}".format(name))


# ---- 数据读取 ----

def _iter_rows(csv_path):
    """流式读取 CSV，按固定列顺序产出类型化后的行 tuple。"""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        col_of = {name: i for i, name in enumerate(header)}
        order = [col_of[name] for name in _FIELD_NAMES]
        kinds = [SCHEMA[name] for name in _FIELD_NAMES]
        for raw in reader:
            row = []
            append = row.append
            for pos, kind in zip(order, kinds):
                cell = raw[pos]
                if cell == "":
                    append(None)
                elif kind == "int":
                    append(int(cell))
                else:
                    append(cell)
            yield tuple(row)


# ---- 排序 ----

def _sort_key_value(v):
    # null 视为最小：(False, None) 排在所有 (True, value) 之前
    return (v is not None, v)


def _apply_order(rows, order_cols):
    """稳定多键排序。order_cols 是 [(列下标, desc), ...]。"""
    for col, desc in reversed(order_cols):
        rows.sort(key=lambda r, col=col: _sort_key_value(r[col]), reverse=desc)


# ---- 执行 ----

def _resolve_order_columns(query, grouped):
    """把 ORDER BY 项解析成排序键来源。

    先按别名匹配，再按选择项的名字（字段名/函数名）匹配，最后按表字段
    匹配（投影查询取该列，分组查询只允许取分组键）。
    返回 [(kind, ref, desc), ...]：kind 为 "col"（输出列）/"key"（分组键）
    /"row"（源行字段）。
    """
    alias_cols = {}
    name_cols = {}
    for i, item in enumerate(query.select):
        if item.alias is not None and item.alias not in alias_cols:
            alias_cols[item.alias] = i
        if item.name not in name_cols:
            name_cols[item.name] = i
    group_pos = {name: k for k, (name, _) in enumerate(query.group_by)}
    result = []
    for item in query.order_by:
        if item.name in alias_cols:
            src = ("col", alias_cols[item.name])
        elif item.name in name_cols:
            src = ("col", name_cols[item.name])
        elif item.name in group_pos:
            src = ("key", group_pos[item.name])
        elif not grouped and item.name in SCHEMA:
            src = ("row", _INDEX[item.name])
        else:
            raise QueryError(
                "UNKNOWN_FIELD", item.pos,
                "ORDER BY 里引用了不存在的字段或别名 {!r}".format(item.name))
        result.append((src[0], src[1], item.desc))
    return result


def _run_grouped(query, csv_path, where, aggs):
    """返回 [(输出行 list, 分组键 tuple), ...]，按分组键升序。"""
    group_idx = [_INDEX[name] for name, _ in query.group_by]
    select_fields = [(out_i, _INDEX[item.name])
                     for out_i, item in enumerate(query.select)
                     if item.kind == "field"]
    compiled = [_compile_agg(item) for item in aggs]
    agg_out = [out_i for out_i, item in enumerate(query.select) if item.kind == "agg"]

    groups = {}
    if query.group_by:
        for row in _iter_rows(csv_path):
            if where is not None and where(row) is not True:
                continue
            key = tuple(row[i] for i in group_idx)
            entry = groups.get(key)
            if entry is None:
                entry = [row, [list(init) for init, _, _ in compiled]]
                groups[key] = entry
            slots = entry[1]
            for k in range(len(compiled)):
                compiled[k][1](slots[k], row)
    else:
        # 无 GROUP BY 的全表聚合：只有一个组，即使零行也要输出一行
        slots = [list(init) for init, _, _ in compiled]
        entry = [None, slots]
        groups[()] = entry
        for row in _iter_rows(csv_path):
            if where is not None and where(row) is not True:
                continue
            if entry[0] is None:
                entry[0] = row
            for k in range(len(compiled)):
                compiled[k][1](slots[k], row)

    # 分组输出顺序：分组键升序，null 最前；不依赖字典遍历顺序
    keys = sorted(groups, key=lambda k: tuple(_sort_key_value(v) for v in k))
    rows_out = []
    for key in keys:
        first_row, slots = groups[key]
        out = [None] * len(query.select)
        for out_i, field_i in select_fields:
            out[out_i] = first_row[field_i] if first_row is not None else None
        for slot, out_i, (_, _, finalize) in zip(slots, agg_out, compiled):
            out[out_i] = finalize(slot)
        rows_out.append((out, key))
    return rows_out


def _run_projection(query, csv_path, where):
    """返回 [(输出行 list, 源行 tuple), ...]，保持输入顺序。"""
    cols = [_INDEX[item.name] for item in query.select]
    rows_out = []
    for row in _iter_rows(csv_path):
        if where is not None and where(row) is not True:
            continue
        rows_out.append(([row[i] for i in cols], row))
    return rows_out


def _execute(query, csv_path):
    where = _compile_condition(query.where) if query.where is not None else None
    aggs = [item for item in query.select if item.kind == "agg"]
    grouped = bool(query.group_by) or bool(aggs)

    if grouped:
        pairs = _run_grouped(query, csv_path, where, aggs)
    else:
        pairs = _run_projection(query, csv_path, where)

    order_spec = _resolve_order_columns(query, grouped)
    n_select = len(query.select)
    rows = []
    for out, extra in pairs:
        full = list(out)
        for kind, ref, _ in order_spec:
            if kind != "col":
                full.append(extra[ref])
        rows.append(full)
    sort_cols = []
    hidden_base = n_select
    for kind, ref, desc in order_spec:
        if kind == "col":
            sort_cols.append((ref, desc))
        else:
            sort_cols.append((hidden_base, desc))
            hidden_base += 1
    if sort_cols:
        _apply_order(rows, sort_cols)
    if query.limit is not None:
        rows = rows[:query.limit]
    return [r[:n_select] for r in rows]


def _format_value(v):
    if v is None:
        return "null"
    if isinstance(v, (int, Decimal)):
        return str(v)
    return v


def execute(query, csv_path):
    """执行已检查的查询，返回 CSV 文本（含表头，行尾 LF）。"""
    rows = _execute(query, csv_path)
    lines = [",".join(item.header for item in query.select)]
    for row in rows:
        lines.append(",".join(_format_value(v) for v in row))
    return "\n".join(lines) + "\n"


def run(query_text, csv_path):
    """解析 -> 检查 -> 执行，返回输出文本；错误抛 QueryError。"""
    query = parse(query_text)
    check(query)
    return execute(query, csv_path)
