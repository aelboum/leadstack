"""Regional configuration primitives: locales, time zones, currencies, countries.

Pure validation, no I/O. Nothing here assumes a particular country: every
workspace states its own locale, time zone and currency explicitly. The
initial market's values (French/Arabic, `Africa/Casablanca`, MAD) are simply
members of the supported sets.
"""

from __future__ import annotations

import re
import zoneinfo
from functools import lru_cache
from types import MappingProxyType

# Interface languages with translations. Arabic is right-to-left.
SUPPORTED_LOCALES: frozenset[str] = frozenset({"fr", "ar", "en"})
RTL_LOCALES: frozenset[str] = frozenset({"ar"})

# ISO 4217 code -> number of minor-unit digits (ISO 4217 "minor unit").
# An explicit allowlist: extending it is a reviewed change, never a guess.
CURRENCY_MINOR_UNITS: MappingProxyType[str, int] = MappingProxyType(
    {
        "MAD": 2,  # Moroccan dirham
        "EUR": 2,
        "USD": 2,
        "GBP": 2,
        "CAD": 2,
        "CHF": 2,
        "AED": 2,
        "SAR": 2,
        "QAR": 2,
        "EGP": 2,
        "DZD": 2,
        "XOF": 0,  # West African CFA franc
        "TND": 3,  # Tunisian dinar
        "KWD": 3,
        "JPY": 0,
    }
)

_COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")


class RegionalValueError(ValueError):
    """An unsupported locale, time zone, currency or country code."""

    def __init__(self, field: str, value: str) -> None:
        super().__init__(f"unsupported {field}: {value!r}")
        self.field = field


@lru_cache(maxsize=1)
def _available_time_zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def validate_locale(value: str) -> str:
    if value not in SUPPORTED_LOCALES:
        raise RegionalValueError("locale", value)
    return value


def validate_time_zone(value: str) -> str:
    """An IANA time zone name, as shipped in the tz database."""
    if value not in _available_time_zones():
        raise RegionalValueError("time_zone", value)
    return value


def validate_currency(value: str) -> str:
    if value not in CURRENCY_MINOR_UNITS:
        raise RegionalValueError("currency", value)
    return value


def validate_country(value: str) -> str:
    """An ISO 3166-1 alpha-2 code. Only the shape is checked here."""
    if not _COUNTRY_CODE.fullmatch(value):
        raise RegionalValueError("country", value)
    return value


def is_rtl(locale: str) -> bool:
    return locale in RTL_LOCALES
