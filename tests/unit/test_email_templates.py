"""Email texts exist for every supported locale and keep their placeholders."""

from __future__ import annotations

import re

import pytest

from leadstack.domains import regional
from leadstack.domains.messaging.templates import consent_texts


@pytest.mark.parametrize("locale", sorted(regional.SUPPORTED_LOCALES))
def test_every_locale_has_complete_texts(locale: str) -> None:
    texts = consent_texts(locale)
    assert "{workspace}" in texts.confirm_subject and "{workspace}" in texts.confirm_intro
    for value in vars(texts).values():
        assert value.strip() and "\n" not in value and "\r" not in value
    if locale != "en":
        assert texts != consent_texts("en")


def test_arabic_texts_are_arabic_and_unknown_locales_fall_back() -> None:
    assert re.search("[\u0600-\u06ff]", consent_texts("ar").confirm_intro)
    assert consent_texts("xx") == consent_texts("en")
    assert consent_texts(None) == consent_texts("en")
