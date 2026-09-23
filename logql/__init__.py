"""logql：只读日志查询引擎。"""

from .errors import QueryError
from .executor import run

__all__ = ["QueryError", "run"]
