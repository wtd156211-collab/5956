"""命令行入口：python -m logql <查询文件|-> [--csv 数据文件]"""

import argparse
import sys

from .errors import QueryError
from .executor import run


def main(argv=None):
    parser = argparse.ArgumentParser(prog="logql")
    parser.add_argument("query", help="查询语句文件路径，'-' 表示从标准输入读")
    parser.add_argument("--csv", default="samples/logs.csv",
                        help="日志数据 CSV，默认 samples/logs.csv")
    args = parser.parse_args(argv)
    if args.query == "-":
        text = sys.stdin.read()
    else:
        with open(args.query, encoding="utf-8") as f:
            text = f.read()
    try:
        out = run(text, args.csv)
    except QueryError as e:
        sys.stdout.write(e.to_line() + "\n")
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
