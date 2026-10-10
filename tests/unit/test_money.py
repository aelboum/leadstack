"""Money uses integer minor units with an explicit currency; no floats."""

from __future__ import annotations

import pytest

from leadstack.domains.money import MAX_MINOR, InvalidAmountError, Money, total_by_currency
from leadstack.domains.regional import RegionalValueError


@pytest.mark.parametrize(
    ("amount", "currency", "minor", "text"),
    [
        ("1250.50", "MAD", 125050, "1250.50"),
        ("1250.5", "MAD", 125050, "1250.50"),
        (1250, "MAD", 125000, "1250.00"),
        ("0.001", "TND", 1, "0.001"),  # three-decimal currency
        ("15000", "XOF", 15000, "15000"),  # zero-decimal currency
        ("-10.25", "EUR", -1025, "-10.25"),
        ("0", "USD", 0, "0.00"),
    ],
)
def test_parse_and_format(amount, currency, minor, text) -> None:
    money = Money.parse(amount, currency)
    assert money.amount_minor == minor
    assert money.decimal_string() == text


@pytest.mark.parametrize(
    "amount",
    ["1.005", "abc", "", "NaN", "Infinity", "1e3", "1_000", 12.5, True, None],
)
def test_rejects_imprecise_or_non_decimal_input(amount) -> None:
    with pytest.raises(InvalidAmountError):
        Money.parse(amount, "MAD")  # type: ignore[arg-type]


def test_rejects_zero_decimal_fractions_and_huge_values() -> None:
    with pytest.raises(InvalidAmountError):
        Money.parse("1.5", "XOF")
    with pytest.raises(InvalidAmountError):
        Money.parse(str(MAX_MINOR), "MAD")


def test_currency_is_required_and_validated() -> None:
    with pytest.raises(RegionalValueError):
        Money.parse("1", "XXX")
    with pytest.raises(RegionalValueError):
        Money(currency="mad", amount_minor=1)


def test_totals_never_mix_currencies() -> None:
    totals = total_by_currency(
        [Money.parse("10", "MAD"), Money.parse("5.50", "MAD"), Money.parse("3", "EUR")]
    )
    assert totals == {"MAD": 1550, "EUR": 300}
