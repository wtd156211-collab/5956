from dataclasses import dataclass


@dataclass
class QueryError(Exception):
    code: str
    position: int
    message: str

    def __str__(self) -> str:
        return self.message
