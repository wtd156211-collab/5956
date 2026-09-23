import csv
import tempfile
import unittest
from pathlib import Path

from logquery import execute_query
from logquery import ast
from logquery.executor import Row, evaluate_condition
from logquery.errors import QueryError


HEADER = "ts,level,service,region,status,latency_ms\n"


def run_sql(sql: str, rows: list[str] | None = None) -> str:
    with tempfile.TemporaryDirectory() as directory:
        data_path = Path(directory) / "logs.csv"
        data_path.write_text(HEADER + "".join(rows or []), encoding="utf-8")
        return execute_query(sql, data_path)


def rows(csv_text: str) -> list[list[str]]:
    return list(csv.reader(csv_text.splitlines()))


class ParserErrorTest(unittest.TestCase):
    def assert_error(self, sql: str, code: str, position: int) -> None:
        with self.assertRaises(QueryError) as caught:
            execute_query(sql, "unused.csv")
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.position, position)

    def test_sample_error_positions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        expected = {
            "bad-1": ("SYNTAX_ERROR", 21),
            "bad-2": ("UNKNOWN_FIELD", 42),
            "bad-3": ("TYPE_MISMATCH", 42),
            "bad-4": ("MIXED_WITHOUT_GROUP_BY", 8),
            "bad-5": ("TYPE_MISMATCH", 8),
            "bad-6": ("SYNTAX_ERROR", 46),
            "bad-7": ("UNKNOWN_FIELD", 36),
        }
        for name, (code, position) in expected.items():
            sql = (root / "samples" / "queries" / f"{name}.sql").read_text(encoding="utf-8")
            self.assert_error(sql, code, position)

    def test_unterminated_string_position(self) -> None:
        self.assert_error(
            "SELECT count(*) FROM logs WHERE service = 'abc",
            "SYNTAX_ERROR",
            43,
        )

    def test_bad_limit_points_at_limit_value(self) -> None:
        self.assert_error(
            "SELECT ts FROM logs LIMIT abc",
            "BAD_LIMIT",
            27,
        )

    def test_unknown_function(self) -> None:
        self.assert_error(
            "SELECT median(status) FROM logs",
            "UNKNOWN_FUNCTION",
            8,
        )

    def test_unknown_star_function_precedes_star_syntax(self) -> None:
        self.assert_error(
            "SELECT median(*) FROM logs",
            "UNKNOWN_FUNCTION",
            8,
        )


class NullLogicTest(unittest.TestCase):
    def test_comparison_and_not_unknown_are_excluded(self) -> None:
        data = [
            "a,INFO,a,,200,10\n",
            "b,INFO,b,west,404,20\n",
        ]
        sql = "SELECT ts FROM logs WHERE NOT region = 'west'"
        output = rows(run_sql(sql, data))
        self.assertEqual(output, [["ts"]])

    def test_or_unknown_with_true_is_included(self) -> None:
        data = [
            "a,INFO,a,,500,10\n",
            "b,INFO,b,west,200,20\n",
        ]
        sql = "SELECT ts FROM logs WHERE region = 'west' OR status = 500"
        output = rows(run_sql(sql, data))
        self.assertEqual(output, [["ts"], ["a"], ["b"]])


class AggregationTest(unittest.TestCase):
    def test_null_group_and_aggregate_values(self) -> None:
        data = [
            "a,INFO,a,,200,\n",
            "b,INFO,b,west,404,20\n",
        ]
        sql = (
            "SELECT region, count(*), count(latency_ms), "
            "sum(latency_ms), min(latency_ms), max(latency_ms), avg(latency_ms) "
            "FROM logs GROUP BY region ORDER BY region"
        )
        output = rows(run_sql(sql, data))
        self.assertEqual(
            output,
            [
                ["region", "count", "count", "sum", "min", "max", "avg"],
                ["null", "1", "0", "0", "null", "null", "null"],
                ["west", "1", "1", "20", "20", "20", "20.000"],
            ],
        )

    def test_avg_half_up_rounding(self) -> None:
        data = [
            "a,INFO,a,west,200,1\n",
            "b,INFO,b,west,200,1\n",
            "c,INFO,c,west,200,2\n",
        ]
        output = rows(run_sql("SELECT avg(latency_ms) FROM logs", data))
        self.assertEqual(output, [["avg"], ["1.333"]])

    def test_string_min_max(self) -> None:
        data = [
            "2020,INFO,b,west,200,1\n",
            "2019,INFO,a,west,200,2\n",
        ]
        sql = "SELECT min(service), max(service) FROM logs"
        output = rows(run_sql(sql, data))
        self.assertEqual(output, [["min", "max"], ["a", "b"]])


class DeterminismTest(unittest.TestCase):
    def test_order_ties_keep_input_order(self) -> None:
        data = [
            "a,INFO,x,west,200,1\n",
            "b,INFO,x,west,200,2\n",
            "c,INFO,y,west,200,3\n",
        ]
        sql = "SELECT ts, service FROM logs ORDER BY service LIMIT 3"
        first = run_sql(sql, data)
        second = run_sql(sql, data)
        self.assertEqual(first, second)
        self.assertEqual(
            rows(first),
            [["ts", "service"], ["a", "x"], ["b", "x"], ["c", "y"]],
        )

    def test_order_by_unselected_field(self) -> None:
        data = [
            "a,INFO,a,west,200,10\n",
            "b,INFO,b,west,200,30\n",
            "c,INFO,c,west,200,20\n",
        ]
        sql = "SELECT ts FROM logs ORDER BY latency_ms"
        output = rows(run_sql(sql, data))
        self.assertEqual(output, [["ts"], ["a"], ["c"], ["b"]])

    def test_grouped_order_by_unselected_group_field(self) -> None:
        data = [
            "a,INFO,a,west,200,10\n",
            "b,INFO,b,east,200,30\n",
            "c,INFO,c,,200,20\n",
        ]
        sql = "SELECT count(*) AS n FROM logs GROUP BY region ORDER BY region DESC"
        output = rows(run_sql(sql, data))
        self.assertEqual(output, [["n"], ["1"], ["1"], ["1"]])

    def test_group_order_is_null_then_utf8_then_numeric(self) -> None:
        data = [
            "a,INFO,a,west,500,1\n",
            "b,INFO,b,,200,1\n",
            "c,INFO,c,east,100,1\n",
        ]
        sql = "SELECT region, status, count(*) FROM logs GROUP BY region, status"
        output = rows(run_sql(sql, data))
        self.assertEqual(
            output,
            [
                ["region", "status", "count"],
                ["null", "200", "1"],
                ["east", "100", "1"],
                ["west", "500", "1"],
            ],
        )


class ShortCircuitTest(unittest.TestCase):
    def test_semantic_check_runs_before_data_is_opened(self) -> None:
        with self.assertRaises(QueryError) as caught:
            execute_query(
                "SELECT count(*) FROM logs WHERE status = 'bad'",
                "/path/that/must/not/be/read.csv",
            )
        self.assertEqual(caught.exception.code, "TYPE_MISMATCH")

    def test_and_skips_right_after_false(self) -> None:
        condition = ast.BooleanOp(
            "AND",
            ast.Comparison(
                ast.Field("status", 1),
                "=",
                ast.Literal(500, "int", 11),
            ),
            ast.Comparison(
                ast.Field("missing", 20),
                "=",
                ast.Literal(1, "int", 30),
            ),
        )
        row = Row({"status": "200"})
        self.assertFalse(evaluate_condition(condition, row))

    def test_or_skips_right_after_true(self) -> None:
        condition = ast.BooleanOp(
            "OR",
            ast.Comparison(
                ast.Field("status", 1),
                "=",
                ast.Literal(200, "int", 11),
            ),
            ast.Comparison(
                ast.Field("missing", 20),
                "=",
                ast.Literal(1, "int", 30),
            ),
        )
        row = Row({"status": "200"})
        self.assertTrue(evaluate_condition(condition, row))


if __name__ == "__main__":
    unittest.main()
