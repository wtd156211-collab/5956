from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class Field:
    name: str
    position: int


@dataclass(frozen=True)
class Literal:
    value: Union[int, str]
    type: str
    position: int


@dataclass(frozen=True)
class Comparison:
    field: Field
    operator: str
    literal: Literal


@dataclass(frozen=True)
class BooleanOp:
    operator: str
    left: Any
    right: Any


@dataclass(frozen=True)
class Not:
    operand: Any


Condition = Union[Comparison, BooleanOp, Not]


@dataclass(frozen=True)
class SelectItem:
    kind: str
    position: int
    name: Optional[str] = None
    alias: Optional[str] = None
    argument: Optional[Field] = None

    @property
    def output_name(self) -> str:
        return self.alias if self.alias is not None else (self.name or "")


@dataclass(frozen=True)
class OrderItem:
    name: str
    position: int
    descending: bool = False


@dataclass(frozen=True)
class Query:
    select: list[SelectItem]
    where: Optional[Condition]
    group_by: list[Field]
    order_by: list[OrderItem]
    limit: Optional[int]
    limit_position: Optional[int]
