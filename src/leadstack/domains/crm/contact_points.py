"""Normalization of contact points: phone numbers and email addresses.

Phones are stored as E.164 (`+212612345678`) alongside the user's original
input. Numbers without a country prefix are interpreted against the
workspace's country; if the workspace has none, an international prefix is
required. No country is assumed.
"""

from __future__ import annotations

import re

import phonenumbers


class InvalidPhoneError(ValueError):
    code = "invalid_phone"


class InvalidEmailError(ValueError):
    code = "invalid_email"


MAX_PHONE_INPUT = 32
MAX_EMAIL = 254
# Deliberately conservative syntax check; deliverability is not verified here.
_EMAIL = re.compile(r"^[^\s@\x00-\x1f\x7f]{1,64}@[^\s@\x00-\x1f\x7f]+\.[^\s@.\x00-\x1f\x7f]{2,}$")


def normalize_phone(raw: str, default_region: str | None) -> str:
    """Return the E.164 form of `raw` or raise `InvalidPhoneError`."""
    text = raw.strip()
    if not text or len(text) > MAX_PHONE_INPUT:
        raise InvalidPhoneError()
    try:
        parsed = phonenumbers.parse(text, None if text.startswith("+") else default_region)
    except phonenumbers.NumberParseException:
        raise InvalidPhoneError() from None
    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneError()
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(raw: str) -> str:
    """Trimmed, with the domain lower-cased (the local part is preserved:
    it is case-sensitive by standard, even if rarely in practice)."""
    text = raw.strip()
    if len(text) > MAX_EMAIL or not _EMAIL.fullmatch(text):
        raise InvalidEmailError()
    local, domain = text.rsplit("@", 1)
    return f"{local}@{domain.lower()}"
