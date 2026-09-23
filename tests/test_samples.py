"""对照 samples/ 里的期望结果做端到端校验。"""

import os
import unittest

from logql import QueryError, run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERIES = os.path.join(ROOT, "samples", "queries")
EXPECTED = os.path.join(ROOT, "samples", "expected")
LOGS = os.path.join(ROOT, "samples", "logs.csv")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestGoodQueries(unittest.TestCase):
    def test_expected_output_byte_for_byte(self):
        for n in range(1, 7):
            name = "q-%d" % n
            with self.subTest(query=name):
                out = run(_read(os.path.join(QUERIES, name + ".sql")), LOGS)
                self.assertEqual(out, _read(os.path.join(EXPECTED, name + ".csv")))

    def test_deterministic_across_runs(self):
        text = _read(os.path.join(QUERIES, "q-2.sql"))
        self.assertEqual(run(text, LOGS), run(text, LOGS))


class TestBadQueries(unittest.TestCase):
    def test_error_code_and_position(self):
        for n in range(1, 8):
            name = "bad-%d" % n
            with self.subTest(query=name):
                with self.assertRaises(QueryError) as ctx:
                    run(_read(os.path.join(QUERIES, name + ".sql")), LOGS)
                expected = _read(os.path.join(EXPECTED, name + ".txt")).strip()
                _, code, pos = expected.split(",")[:3]
                self.assertEqual(ctx.exception.code, code)
                self.assertEqual(ctx.exception.pos, int(pos))


if __name__ == "__main__":
    unittest.main()
