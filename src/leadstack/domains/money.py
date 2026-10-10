"""Currency-safe money.

Amounts are integers in the currency's minor unit (ISO 4217 exponent),
always paired with an explicit ISO 4217 code. No binary floating point is
ever used: input is a decimal *string* (or an integer of major units), and
precision beyond the currency's minor unit is rejected rather than rounded.
Different currencies are never added together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from leadstack.domains.regional import CURRENCY_MINOR_UNITS, validate_currency

# 10^15 minor units: far above any realistic deal, far below int64 overflow.
MAX_MINOR = 10**15
# Aggregates (report totals) may exceed a single amount's bound.
MAX_TOTAL_MINOR = 10**24
_DECIMAL = re.compile(r"-?[0-9]{1,18}(\.[0-9]{1,6})?")


class InvalidAmountError(ValueError):
    code = "invalid_amount"


@dataclass(frozen=True, order=True)
class Money:
    currency: str
    amount_minor: int

    def __post_init__(self) -> None:
        validate_currency(self.currency)
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise InvalidAmountError()
        if not -MAX_TOTAL_MINOR < self.amount_minor < MAX_TOTAL_MINOR:
            raise InvalidAmountError()

    @classmethod
    def parse(cls, amount: str | int, currency: str) -> Money:
        """`"1250.50"` MAD -> 125050 minor units. Rejects floats, NaN,
        exponents beyond the range, and excess decimal places."""
        currency = validate_currency(currency)
        if isinstance(amount, bool) or not isinstance(amount, (str, int)):
            raise InvalidAmountError()
        text = str(amount).strip()
        if not _DECIMAL.fullmatch(text):
            # ASCII digits only: Decimal would also accept Arabic-Indic or
            # full-width digits, exponents and underscores.
            raise InvalidAmountError()
        try:
            value = Decimal(text)
        except InvalidOperation:
            raise InvalidAmountError() from None
        if not value.is_finite():
            raise InvalidAmountError()
        exponent = CURRENCY_MINOR_UNITS[currency]
        scaled = value.scaleb(exponent)
        if scaled != scaled.to_integral_value():
            raise InvalidAmountError()  # more decimals than the currency has
        minor = int(scaled)
        if not -MAX_MINOR < minor < MAX_MINOR:
            raise InvalidAmountError()
        return cls(currency=currency, amount_minor=minor)

    @property
    def exponent(self) -> int:
        return CURRENCY_MINOR_UNITS[self.currency]

    def decimal_string(self) -> str:
        """Canonical major-unit string with exactly the currency's decimals."""
        value = Decimal(self.amount_minor).scaleb(-self.exponent)
        return f"{value:.{self.exponent}f}"


def total_by_currency(amounts: list[Money]) -> dict[str, int]:
    """Sum per currency; never converts or mixes currencies."""
    totals: dict[str, int] = {}
    for money in amounts:
        totals[money.currency] = totals.get(money.currency, 0) + money.amount_minor
    return totals
