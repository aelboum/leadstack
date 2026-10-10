"""Custom-field definitions and value validation (pure, no I/O).

Values are stored in `contacts.custom_fields` (a JSON object keyed by the
definition `key`). Numbers are stored as canonical decimal *strings* so no
binary floating point is ever involved; dates as ISO `YYYY-MM-DD`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

FIELD_TYPES = ("text", "number", "date", "boolean", "select")
MAX_DEFINITIONS = 50
MAX_TEXT = 1000
MAX_OPTIONS = 50
MAX_OPTION_LENGTH = 100
MAX_NUMBER_TEXT = 64
MAX_NUMBER_DIGITS = 28  # Decimal's default context precision
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CustomFieldError(ValueError):
    code = "invalid_custom_field"


class UnknownCustomFieldError(CustomFieldError):
    code = "unknown_custom_field"


class InvalidCustomFieldValueError(CustomFieldError):
    code = "invalid_custom_field_value"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    field_type: str
    options: tuple[str, ...] | None = None


def validate_definition(
    key: str, label: str, field_type: str, options: list[str] | None
) -> tuple[str, str, str, list[str] | None]:
    if not _KEY.fullmatch(key):
        raise CustomFieldError()
    label = label.strip()
    if not label or len(label) > 100:
        raise CustomFieldError()
    if field_type not in FIELD_TYPES:
        raise CustomFieldError()
    if field_type == "select":
        cleaned = [o.strip() for o in (options or [])]
        if (
            not cleaned
            or len(cleaned) > MAX_OPTIONS
            or any(not o or len(o) > MAX_OPTION_LENGTH for o in cleaned)
            or len(set(cleaned)) != len(cleaned)
        ):
            raise CustomFieldError()
        return key, label, field_type, cleaned
    if options:
        raise CustomFieldError()
    return key, label, field_type, None


def _value(spec: FieldSpec, value: object) -> object:
    if spec.field_type == "text":
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            raise InvalidCustomFieldValueError()
        return value.strip()
    if spec.field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            # Floats are rejected on purpose: send numbers as strings or integers.
            raise InvalidCustomFieldValueError()
        text = str(value).strip()
        if len(text) > MAX_NUMBER_TEXT:
            raise InvalidCustomFieldValueError()
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise InvalidCustomFieldValueError() from None
        if (
            not number.is_finite()
            or abs(number.adjusted()) > 15
            or len(number.as_tuple().digits) > MAX_NUMBER_DIGITS
        ):
            # Rejected rather than silently rounded.
            raise InvalidCustomFieldValueError()
        return format(number.normalize(), "f")
    if spec.field_type == "date":
        if not isinstance(value, str):
            raise InvalidCustomFieldValueError()
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise InvalidCustomFieldValueError() from None
    if spec.field_type == "boolean":
        if not isinstance(value, bool):
            raise InvalidCustomFieldValueError()
        return value
    if spec.options is None or value not in spec.options:
        raise InvalidCustomFieldValueError()
    return value


def apply_values(
    specs: Mapping[str, FieldSpec], current: Mapping[str, object], changes: Mapping[str, object]
) -> dict[str, object]:
    """Merge `changes` into `current`. A `None` value removes the field.
    Keys without a definition are rejected; stored keys whose definition
    was deleted are dropped."""
    result = visible_values(specs, current)
    for key, value in changes.items():
        spec = specs.get(key)
        if spec is None:
            raise UnknownCustomFieldError()
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _value(spec, value)
    return result


def visible_values(
    specs: Mapping[str, FieldSpec], stored: Mapping[str, object]
) -> dict[str, object]:
    """Stored values that still have a definition *and* satisfy it (a
    definition recreated with another type, or a removed select option,
    never resurfaces an old value)."""
    visible: dict[str, object] = {}
    for key, value in stored.items():
        spec = specs.get(key)
        if spec is None:
            continue
        try:
            if _value(spec, value) == value:
                visible[key] = value
        except CustomFieldError:
            continue
    return visible
