import argparse
import csv
import io
import sys

from .errors import QueryError
from .executor import execute_query


def error_line(error: QueryError) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow(["error", error.code, str(error.position), error.message])
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a read-only logs query.")
    parser.add_argument("data", help="path to logs.csv")
    parser.add_argument("query", nargs="?", help="path to a query file")
    parser.add_argument("--sql", help="query text")
    arguments = parser.parse_args(argv)

    if arguments.sql is not None and arguments.query is not None:
        parser.error("use either a query file path or --sql, not both")
    if arguments.sql is None and arguments.query is None:
        parser.error("a query file path or --sql is required")

    if arguments.sql is not None:
        sql = arguments.sql
    else:
        with open(arguments.query, "r", encoding="utf-8") as handle:
            sql = handle.read()

    try:
        output = execute_query(sql, arguments.data)
    except QueryError as error:
        print(error_line(error))
        return 1

    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
