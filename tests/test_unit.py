"""针对词法、语法、类型检查、空值语义、确定性的单元测试。"""

import os
import tempfile
import unittest

from logql import QueryError, run
from logql.executor import _compile_condition
from logql.lexer import tokenize
from logql.parser import And, Compare, Or, parse

FIXTURE = """\
ts,level,service,region,status,latency_ms
2026-01-01T00:00:00.000Z,INFO,api,cn-east,200,100
2026-01-01T00:00:01.000Z,INFO,api,,200,150
2026-01-01T00:00:02.000Z,WARN,api,cn-east,500,250
2026-01-01T00:00:03.000Z,ERROR,pay,,503,3000
2026-01-01T00:00:04.000Z,INFO,pay,us-west,200,80
"""


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.csv = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(FIXTURE)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.csv)

    def query(self, text):
        return run(text, self.csv)

    def error(self, text):
        with self.assertRaises(QueryError) as ctx:
            run(text, self.csv)
        return ctx.exception


class TestLexer(Base):
    def test_string_escape(self):
        toks = tokenize("'it''s'")
        self.assertEqual(toks[0].value, "it's")
        self.assertEqual(toks[0].pos, 1)

    def test_unterminated_string(self):
        with self.assertRaises(QueryError) as ctx:
            tokenize("SELECT 'abc")
        self.assertEqual(ctx.exception.code, "SYNTAX_ERROR")
        self.assertEqual(ctx.exception.pos, 8)

    def test_unknown_char(self):
        with self.assertRaises(QueryError) as ctx:
            tokenize("SELECT @")
        self.assertEqual(ctx.exception.pos, 8)

    def test_negative_number_rejected(self):
        e = self.error("SELECT count(*) FROM logs WHERE status = -1")
        self.assertEqual(e.code, "SYNTAX_ERROR")

    def test_decimal_literal_rejected(self):
        e = self.error("SELECT count(*) FROM logs WHERE latency_ms > 1.5")
        self.assertEqual(e.code, "SYNTAX_ERROR")


class TestParser(Base):
    def test_trailing_garbage(self):
        e = self.error("SELECT count(*) FROM logs LIMIT 5 extra")
        self.assertEqual(e.code, "SYNTAX_ERROR")

    def test_bad_limit(self):
        e = self.error("SELECT count(*) FROM logs LIMIT 'five'")
        self.assertEqual(e.code, "BAD_LIMIT")

    def test_unknown_function(self):
        e = self.error("SELECT median(status) FROM logs")
        self.assertEqual(e.code, "UNKNOWN_FUNCTION")
        self.assertEqual(e.pos, 8)

    def test_star_only_for_count(self):
        e = self.error("SELECT sum(*) FROM logs")
        self.assertEqual(e.code, "SYNTAX_ERROR")

    def test_case_insensitive(self):
        out = self.query("select SERVICE, COUNT(*) as N from LOGS "
                         "where service = 'api' group by SERVICE")
        self.assertEqual(out, "service,n\napi,3\n")


class TestNullSemantics(Base):
    def test_null_comparison_is_unknown(self):
        # region 为 null 的行，= 与 != 都不成立
        out = self.query("SELECT count(*) AS n FROM logs WHERE region = 'cn-east'")
        self.assertEqual(out, "n\n2\n")
        out = self.query("SELECT count(*) AS n FROM logs WHERE region != 'cn-east'")
        self.assertEqual(out, "n\n1\n")

    def test_not_unknown_is_unknown(self):
        out = self.query("SELECT count(*) AS n FROM logs WHERE NOT region = 'cn-east'")
        self.assertEqual(out, "n\n1\n")

    def test_null_group_first(self):
        out = self.query("SELECT region, count(*) AS n FROM logs "
                         "GROUP BY region ORDER BY region")
        self.assertEqual(out, "region,n\nnull,2\ncn-east,2\nus-west,1\n")

    def test_count_field_skips_null(self):
        out = self.query("SELECT count(*) AS a, count(region) AS b FROM logs")
        self.assertEqual(out, "a,b\n5,3\n")

    def test_all_null_group_aggregates(self):
        out = self.query(
            "SELECT count(region) AS c, sum(status) AS s, min(region) AS mn, "
            "max(region) AS mx, avg(status) AS a FROM logs WHERE status = 999")
        self.assertEqual(out, "c,s,mn,mx,a\n0,0,null,null,null\n")

    def test_sum_ignores_null_but_counts_rows(self):
        out = self.query("SELECT region, sum(status) AS s FROM logs "
                         "GROUP BY region ORDER BY region LIMIT 1")
        self.assertEqual(out, "region,s\nnull,703\n")


class TestLogic(Base):
    def _eval(self, cond_text, row):
        node = parse("SELECT count(*) FROM logs WHERE " + cond_text).where
        return _compile_condition(node)(row)

    def test_precedence_not_and_or(self):
        # NOT > AND > OR：等价于 (NOT a) OR (b AND c)
        row = ("ts", "WARN", "api", "cn-east", 200, 100)
        # NOT 只作用于 level='WARN'：(NOT True) AND True OR False -> False
        self.assertFalse(self._eval(
            "NOT level = 'WARN' AND service = 'api' OR status = 999", row))
        # AND 优先于 OR：True OR (False AND False) -> True
        self.assertTrue(self._eval(
            "level = 'WARN' OR service = 'pay' AND status = 999", row))

    def test_parentheses(self):
        row = ("ts", "INFO", "api", "cn-east", 200, 100)
        self.assertFalse(self._eval(
            "(service = 'api' OR service = 'pay') AND status = 500", row))

    def test_short_circuit_and(self):
        # AND 左边为 False 时右边不算：用会爆炸的右操作数验证
        node = And(Compare("status", 1, "=", "int", 999, 1),
                   Compare("status", 1, "=", "int", 1, 1))
        fn = _compile_condition(node)
        row = ("ts", "INFO", "api", "cn-east", 200, 100)
        self.assertFalse(fn(row))

    def test_short_circuit_or(self):
        node = Or(Compare("status", 1, "=", "int", 200, 1),
                  Compare("status", 1, "=", "int", 1, 1))
        fn = _compile_condition(node)
        row = ("ts", "INFO", "api", "cn-east", 200, 100)
        self.assertTrue(fn(row))

    def test_short_circuit_observable(self):
        # 右操作数若被求值会抛异常，以此证明短路
        import logql.executor as ex

        class Boom:
            def __call__(self, row):
                raise AssertionError("right side evaluated")

        node = And(Compare("status", 1, "=", "int", 999, 1),
                   Compare("status", 1, "=", "int", 1, 1))
        orig_compile = ex._compile_condition
        calls = []

        def spy(n):
            if n is node.right:
                return Boom()
            return orig_compile(n)

        ex._compile_condition = spy
        try:
            fn = orig_compile(node)
        finally:
            ex._compile_condition = orig_compile
        row = ("ts", "INFO", "api", "cn-east", 200, 100)
        self.assertFalse(fn(row))
        self.assertEqual(calls, [])


class TestAggregatesAndOutput(Base):
    def test_avg_rounding_half_up(self):
        # 1/2000 = 0.0005，四舍五入应为 0.001（不是银行家舍入的 0.000）
        out = self.query("SELECT avg(latency_ms) AS a FROM logs WHERE status = 999")
        self.assertEqual(out, "a\nnull\n")
        out = self.query("SELECT avg(status) AS a FROM logs WHERE service = 'api'")
        # (200+200+500)/3 = 300.000
        self.assertEqual(out, "a\n300.000\n")

    def test_avg_three_decimals(self):
        out = self.query("SELECT avg(status) AS a FROM logs")
        # (200+200+500+503+200)/5 = 320.6
        self.assertEqual(out, "a\n320.600\n")

    def test_min_max_on_string(self):
        out = self.query("SELECT min(service) AS lo, max(service) AS hi FROM logs")
        self.assertEqual(out, "lo,hi\napi,pay\n")

    def test_limit_zero(self):
        out = self.query("SELECT service FROM logs LIMIT 0")
        self.assertEqual(out, "service\n")

    def test_projection_order_and_limit(self):
        out = self.query("SELECT service, status FROM logs "
                         "WHERE service = 'api' LIMIT 2")
        self.assertEqual(out, "service,status\napi,200\napi,200\n")

    def test_order_by_stable_ties(self):
        # status 并列时保持输入顺序（ts 升序即输入顺序）
        out = self.query("SELECT ts, status FROM logs ORDER BY status LIMIT 3")
        lines = out.strip().split("\n")
        self.assertEqual(lines[0], "ts,status")
        self.assertTrue(lines[1].startswith("2026-01-01T00:00:00.000Z,200"))
        self.assertTrue(lines[2].startswith("2026-01-01T00:00:01.000Z,200"))
        self.assertTrue(lines[3].startswith("2026-01-01T00:00:04.000Z,200"))

    def test_order_by_alias_and_desc_null_last(self):
        out = self.query("SELECT region, count(*) AS n FROM logs "
                         "GROUP BY region ORDER BY n DESC, region DESC")
        self.assertEqual(out, "region,n\ncn-east,2\nnull,2\nus-west,1\n")

    def test_header_lowercase(self):
        out = self.query("SELECT SERVICE AS Svc, COUNT(*) FROM logs "
                         "GROUP BY service LIMIT 1")
        self.assertEqual(out.split("\n")[0], "svc,count")

    def test_duplicate_column_names_allowed(self):
        out = self.query("SELECT service, service FROM logs LIMIT 1")
        self.assertEqual(out, "service,service\napi,api\n")


class TestTypeAndFieldErrors(Base):
    def test_int_vs_string(self):
        e = self.error("SELECT count(*) FROM logs WHERE status = '200'")
        self.assertEqual(e.code, "TYPE_MISMATCH")

    def test_string_vs_int(self):
        e = self.error("SELECT count(*) FROM logs WHERE service = 200")
        self.assertEqual(e.code, "TYPE_MISMATCH")

    def test_sum_on_string(self):
        e = self.error("SELECT sum(service) FROM logs")
        self.assertEqual(e.code, "TYPE_MISMATCH")

    def test_unknown_field_in_order_by(self):
        e = self.error("SELECT service FROM logs ORDER BY nope")
        self.assertEqual(e.code, "UNKNOWN_FIELD")

    def test_unknown_agg_arg(self):
        e = self.error("SELECT count(nope) FROM logs")
        self.assertEqual(e.code, "UNKNOWN_FIELD")

    def test_check_happens_before_reading_data(self):
        # 数据文件不存在时，类型错误仍应先于 IO 错误报出
        from logql.executor import run as run_
        with self.assertRaises(QueryError) as ctx:
            run_("SELECT avg(service) FROM logs", "/nonexistent/path.csv")
        self.assertEqual(ctx.exception.code, "TYPE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
