from . import ast
from .errors import QueryError
from .schema import SCHEMA


def unknown_field(position: int, name: str) -> None:
    raise QueryError("UNKNOWN_FIELD", position, f"unknown field '{name}'")


def validate_select_items(query: ast.Query) -> None:
    for item in query.select:
        if item.kind == "field":
            if item.name not in SCHEMA:
                unknown_field(item.position, item.name or "")
            continue

        function_name = item.name or ""
        if function_name not in {"count", "sum", "min", "max", "avg"}:
            raise QueryError(
                "UNKNOWN_FUNCTION",
                item.position,
                f"unknown aggregate function '{function_name}'",
            )

        if item.argument is None:
            continue
        if item.argument.name not in SCHEMA:
            unknown_field(item.argument.position, item.argument.name)

        field_type = SCHEMA[item.argument.name]
        if function_name in {"sum", "avg"} and field_type != "int":
            raise QueryError(
                "TYPE_MISMATCH",
                item.position,
                f"{function_name.upper()} requires an integer field",
            )


def validate_where(query: ast.Query) -> None:
    def validate_condition(condition: ast.Condition) -> None:
        if isinstance(condition, ast.Not):
            validate_condition(condition.operand)
        elif isinstance(condition, ast.BooleanOp):
            validate_condition(condition.left)
            validate_condition(condition.right)
        else:
            if condition.field.name not in SCHEMA:
                unknown_field(condition.field.position, condition.field.name)
            if SCHEMA[condition.field.name] != condition.literal.type:
                raise QueryError(
                    "TYPE_MISMATCH",
                    condition.literal.position,
                    "comparison values must have the same type",
                )

    if query.where is not None:
        validate_condition(query.where)


def validate_order(query: ast.Query) -> None:
    aliases: dict[str, ast.SelectItem] = {}
    for item in query.select:
        if item.alias is not None and item.alias not in aliases:
            aliases[item.alias] = item

    group_names = {field.name for field in query.group_by}
    has_aggregate = any(item.kind == "aggregate" for item in query.select)

    for order in query.order_by:
        if order.name in aliases:
            continue

        if order.name not in SCHEMA:
            unknown_field(order.position, order.name)

        if query.group_by:
            if order.name not in group_names:
                raise QueryError(
                    "SYNTAX_ERROR",
                    order.position,
                    "ORDER BY field must be a GROUP BY field or selected alias",
                )
        elif has_aggregate:
            raise QueryError(
                "SYNTAX_ERROR",
                order.position,
                "aggregate queries can only be ordered by selected aliases",
            )


def validate_query(query: ast.Query) -> None:
    validate_select_items(query)

    if query.where is not None:
        validate_where(query)

    for field in query.group_by:
        if field.name not in SCHEMA:
            unknown_field(field.position, field.name)

    field_items = [item for item in query.select if item.kind == "field"]
    has_aggregate = any(item.kind == "aggregate" for item in query.select)

    if has_aggregate and field_items and not query.group_by:
        first = field_items[0]
        raise QueryError(
            "MIXED_WITHOUT_GROUP_BY",
            first.position,
            "fields and aggregates cannot be mixed without GROUP BY",
        )

    if query.group_by:
        group_names = {field.name for field in query.group_by}
        for item in field_items:
            if item.name not in group_names:
                raise QueryError(
                    "SYNTAX_ERROR",
                    item.position,
                    "selected field must appear in GROUP BY",
                )

    validate_order(query)
