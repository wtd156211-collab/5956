from __future__ import annotations

import csv
from decimal import Decimal, ROUND_HALF_UP
from functools import cmp_to_key
import io
import os
from typing import Iterable, Iterator

from . import ast
from .errors import QueryError
from .parser import parse_query
from .schema import FIELDNAMES, SCHEMA
from .semantic import validate_query


UNKNOWN = object()


def compare_values(left, right) -> int:
    if left is None:
        return 0 if right is None else -1
    if right is None:
        return 1
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def compare_order(left, right, descending: bool) -> int:
    result = compare_values(left, right)
    if descending and result != 0:
        return -result
    return result


class Row:
    __slots__ = ("raw", "converted")

    def __init__(self, raw: dict[str, str]):
        self.raw = raw
        self.converted: dict[str, object] = {}

    def get(self, name: str):
        if name in self.converted:
            return self.converted[name]

        text = self.raw[name]
        if text == "":
            value = None
        elif SCHEMA[name] == "int":
            value = int(text)
        else:
            value = text
        self.converted[name] = value
        return value


def evaluate_condition(condition: ast.Condition, row: Row):
    if isinstance(condition, ast.Not):
        value = evaluate_condition(condition.operand, row)
        if value is UNKNOWN:
            return UNKNOWN
        return not value

    if isinstance(condition, ast.BooleanOp):
        left = evaluate_condition(condition.left, row)
        if condition.operator == "AND":
            if left is False:
                return False
            right = evaluate_condition(condition.right, row)
            if right is False:
                return False
            if left is UNKNOWN or right is UNKNOWN:
                return UNKNOWN
            return bool(left and right)

        if left is True:
            return True
        right = evaluate_condition(condition.right, row)
        if right is True:
            return True
        if left is UNKNOWN or right is UNKNOWN:
            return UNKNOWN
        return bool(left or right)

    left = row.get(condition.field.name)
    if left is None:
        return UNKNOWN
    right = condition.literal.value
    result = compare_values(left, right)
    operators = {
        "=": result == 0,
        "!=": result != 0,
        "<": result < 0,
        "<=": result <= 0,
        ">": result > 0,
        ">=": result >= 0,
    }
    return operators[condition.operator]


def rows_match(query: ast.Query, rows: Iterable[Row]) -> Iterable[Row]:
    if query.where is None:
        return rows

    def matching() -> Iterable[Row]:
        for row in rows:
            if evaluate_condition(query.where, row) is True:
                yield row

    return matching()


def initial_state():
    return [0, 0, None, None]


def update_state(state: list[object], item: ast.SelectItem, row: Row) -> None:
    if item.argument is None:
        return
    value = row.get(item.argument.name)
    if value is None:
        return

    state[0] = int(state[0]) + 1
    if item.name in {"count"}:
        return

    if item.name in {"sum", "avg"}:
        number = int(value)
        state[1] = int(state[1]) + number

    if item.name in {"min", "max", "avg"}:
        comparable = int(value) if SCHEMA[item.argument.name] == "int" else value
        current_min = state[2]
        current_max = state[3]
        if current_min is None or compare_values(comparable, current_min) < 0:
            state[2] = comparable
        if current_max is None or compare_values(comparable, current_max) > 0:
            state[3] = comparable


def aggregate_value(item: ast.SelectItem, state: list[object], row_count: int):
    if item.name == "count":
        if item.argument is None:
            return row_count
        return state[0]

    if item.name == "sum":
        return state[1]
    if item.name == "min":
        return state[2]
    if item.name == "max":
        return state[3]

    non_null_count = int(state[0])
    if non_null_count == 0:
        return None
    average = Decimal(int(state[1])) / Decimal(non_null_count)
    return average.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def format_value(value) -> str:
    if value is None:
        return "null"
    return str(value)


def projection_values(query: ast.Query, row: Row) -> list[object]:
    return [row.get(item.name or "") for item in query.select]


def sort_rows_by_order(
    rows: list[tuple[int, list[object], dict[str, object]]],
    query: ast.Query,
) -> None:
    if not query.order_by:
        rows.sort(key=lambda entry: entry[0])
        return

    selected_index: dict[str, int] = {}
    for index, item in enumerate(query.select):
        selected_index.setdefault(item.output_name, index)

    def comparator(left_entry, right_entry) -> int:
        _, left, _ = left_entry
        _, right, _ = right_entry
        for order in query.order_by:
            selected = selected_index.get(order.name)
            if selected is not None:
                left_value = left[selected]
                right_value = right[selected]
            else:
                left_value = left_entry[2][order.name]
                right_value = right_entry[2][order.name]
            result = compare_order(left_value, right_value, order.descending)
            if result != 0:
                return result
        left_index = left_entry[0]
        right_index = right_entry[0]
        return -1 if left_index < right_index else 1

    rows.sort(key=cmp_to_key(comparator))


def group_key_values(group_fields: list[ast.Field], row: Row) -> tuple[object, ...]:
    return tuple(row.get(field.name) for field in group_fields)


def compare_group_keys(left: tuple[object, ...], right: tuple[object, ...]) -> int:
    for left_value, right_value in zip(left, right):
        result = compare_values(left_value, right_value)
        if result != 0:
            return result
    return 0


def grouped_rows(query: ast.Query, rows: Iterable[Row]) -> list[tuple[tuple[object, ...], list[object]]]:
    states: dict[tuple[object, ...], list[list[object]]] = {}
    ordered_keys: list[tuple[object, ...]] = []
    counts: dict[tuple[object, ...], int] = {}
    aggregate_items = [item for item in query.select if item.kind == "aggregate"]

    for row in rows:
        key = group_key_values(query.group_by, row)
        if key not in states:
            states[key] = [initial_state() for _ in aggregate_items]
            ordered_keys.append(key)
            counts[key] = 0
        counts[key] += 1
        for state, item in zip(states[key], aggregate_items):
            update_state(state, item, row)

    ordered_keys.sort(key=cmp_to_key(compare_group_keys))

    field_values: dict[str, object]
    result_rows: list[tuple[tuple[object, ...], list[object]]] = []
    for key in ordered_keys:
        field_values = {field.name: value for field, value in zip(query.group_by, key)}
        values: list[object] = []
        aggregate_index = 0
        for item in query.select:
            if item.kind == "field":
                values.append(field_values[item.name or ""])
            else:
                state = states[key][aggregate_index]
                aggregate_index += 1
                values.append(aggregate_value(item, state, counts[key]))
        result_rows.append((key, values))
    return result_rows


def ungrouped_aggregate_row(query: ast.Query, rows: Iterable[Row]) -> list[object]:
    aggregate_items = [item for item in query.select if item.kind == "aggregate"]
    states = [initial_state() for _ in aggregate_items]
    count = 0
    for row in rows:
        count += 1
        for state, item in zip(states, aggregate_items):
            update_state(state, item, row)

    values: list[object] = []
    for item, state in zip(aggregate_items, states):
        values.append(aggregate_value(item, state, count))
    return values


def sort_output_rows(
    query: ast.Query,
    rows: list[tuple[tuple[object, ...], list[object]]] | list[list[object]],
) -> list[list[object]]:
    if not query.order_by:
        return [entry[1] if isinstance(entry, tuple) else entry for entry in rows]

    selected_index: dict[str, int] = {}
    for index, item in enumerate(query.select):
        selected_index.setdefault(item.output_name, index)

    if query.group_by:
        group_key_index = {field.name: index for index, field in enumerate(query.group_by)}

        def group_key(entry: tuple[tuple[object, ...], list[object]], order: ast.OrderItem):
            if order.name in selected_index:
                return entry[1][selected_index[order.name]]
            return entry[0][group_key_index[order.name]]

    else:
        def group_key(entry: tuple[tuple[object, ...], list[object]], order: ast.OrderItem):
            return entry[1][selected_index[order.name]]

    def comparator(left_entry: tuple[tuple[object, ...], list[object]], right_entry: tuple[tuple[object, ...], list[object]]) -> int:
        left = left_entry[1]
        right = right_entry[1]
        for order in query.order_by:
            result = compare_order(
                group_key(left_entry, order),
                group_key(right_entry, order),
                order.descending,
            )
            if result != 0:
                return result
        if isinstance(left_entry, tuple) and isinstance(right_entry, tuple):
            return compare_group_keys(left_entry[0], right_entry[0])
        return 0

    return [entry[1] for entry in sorted(rows, key=cmp_to_key(comparator))]


def read_data_rows(path: str | os.PathLike[str]) -> Iterator[Row]:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(FIELDNAMES):
            raise ValueError("CSV header does not match the fixed logs schema")
        for raw in reader:
            yield Row(raw)


def execute_query(sql: str, data_path: str | os.PathLike[str]) -> str:
    query = parse_query(sql)
    validate_query(query)

    headers = [item.output_name for item in query.select]
    output = io.StringIO()
    writer = csv.writer(
        output,
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writerow(headers)

    if query.limit == 0:
        return output.getvalue()

    has_aggregate = any(item.kind == "aggregate" for item in query.select)

    if query.group_by:
        result_rows = grouped_rows(query, rows_match(query, read_data_rows(data_path)))
        result_rows = sort_output_rows(query, result_rows)
        if query.limit is not None:
            result_rows = result_rows[: query.limit]
        for row in result_rows:
            writer.writerow([format_value(value) for value in row])
    elif has_aggregate:
        result_rows = [ungrouped_aggregate_row(query, rows_match(query, read_data_rows(data_path)))]
        result_rows = sort_output_rows(query, result_rows)
        if query.limit is not None:
            result_rows = result_rows[: query.limit]
        for row in result_rows:
            writer.writerow([format_value(value) for value in row])
    else:
        collected: list[tuple[int, list[object], dict[str, object]]] = []
        needs_materialize = bool(query.order_by)
        output_names = {item.output_name for item in query.select}
        unselected_order_fields = [
            order.name for order in query.order_by if order.name not in output_names
        ]
        row_number = 0
        for row in rows_match(query, read_data_rows(data_path)):
            values = projection_values(query, row)
            if needs_materialize:
                order_values = {
                    name: row.get(name) for name in unselected_order_fields
                }
                collected.append((row_number, values, order_values))
            else:
                writer.writerow([format_value(value) for value in values])
                if query.limit is not None and row_number + 1 >= query.limit:
                    row_number += 1
                    break
            row_number += 1

        if needs_materialize:
            sort_rows_by_order(collected, query)
            if query.limit is not None:
                collected = collected[: query.limit]
            for _, values, _ in collected:
                writer.writerow([format_value(value) for value in values])

    return output.getvalue()
