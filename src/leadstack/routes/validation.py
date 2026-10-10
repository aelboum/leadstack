"""Request-body conventions shared by every LeadStack route.

`StrictModel`:
- rejects unknown fields (no mass assignment);
- rejects control characters in any string (a NUL byte cannot be stored by
  PostgreSQL and would otherwise surface as a 500; other C0 controls have no
  place in names, emails or notes). Tab, line feed and carriage return are
  allowed so multi-line notes work.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def has_control_characters(value: str) -> bool:
    return bool(_CONTROL.search(value))


def _check(value: Any) -> None:
    if isinstance(value, str):
        if has_control_characters(value):
            raise ValueError("control characters are not allowed")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check(key)
            _check(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check(item)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _no_control_characters(cls, data: Any) -> Any:
        _check(data)
        return data
