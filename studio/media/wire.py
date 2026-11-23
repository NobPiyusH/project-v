"""JSON contract with the media workers: camelCase keys, unknown fields ignored (a newer worker must never wedge a workflow)."""

from __future__ import annotations

import dataclasses
import types
import typing
from decimal import Decimal
from typing import Any, TypeVar

T = TypeVar("T")


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p.capitalize() for p in rest)


def to_json(obj: Any) -> Any:
    """Dataclass (or list/dict of them) to plain JSON-ready data with camelCase keys. Decimal becomes a number."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {camel(f.name): to_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_json(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def from_json(cls: type[T], data: dict[str, Any]) -> T:
    hints = typing.get_type_hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        key = camel(f.name)
        if key in data:
            kwargs[f.name] = _convert(hints[f.name], data[key])
    return cls(**kwargs)


def _convert(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in typing.get_args(tp) if a is not type(None)]
        return _convert(inner[0], value) if len(inner) == 1 else value
    if origin is list:
        (item,) = typing.get_args(tp)
        return [_convert(item, v) for v in value]
    if dataclasses.is_dataclass(tp):
        return from_json(tp, value)
    if tp is Decimal:
        return Decimal(repr(value) if isinstance(value, float) else str(value))
    if tp is float and isinstance(value, int):
        return float(value)
    return value
