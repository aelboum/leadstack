"""Regional validation, the role/permission matrix, and pagination cursors."""

from __future__ import annotations

import pytest

from leadstack.domains import regional
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces.service import InvalidCursorError
from leadstack.routes.pagination import paginate


@pytest.mark.parametrize("value", ["fr", "ar", "en"])
def test_supported_locales(value: str) -> None:
    assert regional.validate_locale(value) == value


@pytest.mark.parametrize("value", ["", "FR", "fr-MA", "de"])
def test_unsupported_locales(value: str) -> None:
    with pytest.raises(regional.RegionalValueError):
        regional.validate_locale(value)


def test_time_zones_use_the_iana_database() -> None:
    assert regional.validate_time_zone("Africa/Casablanca") == "Africa/Casablanca"
    assert regional.validate_time_zone("Europe/Paris") == "Europe/Paris"
    for bad in ("", "GMT+1", "africa/casablanca", "Mars/Olympus"):
        with pytest.raises(regional.RegionalValueError):
            regional.validate_time_zone(bad)


def test_currency_minor_units_follow_iso_4217() -> None:
    assert regional.CURRENCY_MINOR_UNITS["MAD"] == 2
    assert regional.CURRENCY_MINOR_UNITS["TND"] == 3
    assert regional.CURRENCY_MINOR_UNITS["XOF"] == 0
    with pytest.raises(regional.RegionalValueError):
        regional.validate_currency("mad")


def test_country_shape() -> None:
    assert regional.validate_country("MA") == "MA"
    for bad in ("ma", "MAR", "", "1A"):
        with pytest.raises(regional.RegionalValueError):
            regional.validate_country(bad)


def test_arabic_is_the_only_rtl_locale() -> None:
    assert [loc for loc in sorted(regional.SUPPORTED_LOCALES) if regional.is_rtl(loc)] == ["ar"]


def test_roles_are_strictly_nested() -> None:
    owner, admin, member = (perms.ROLE_PERMISSIONS[r] for r in perms.ROLES)
    assert member < admin < owner
    assert owner - admin == {perms.OWNERSHIP_MANAGE}


def test_member_cannot_invite_or_manage_or_read_audit() -> None:
    member = perms.ROLE_PERMISSIONS[perms.ROLE_MEMBER]
    for forbidden in (
        perms.MEMBERS_INVITE,
        perms.MEMBERS_MANAGE,
        perms.AUDIT_READ,
        perms.WORKSPACE_UPDATE,
        perms.CLIENTS_CREATE,
        perms.PLATFORM_ROLE_ASSIGN,
        perms.PLATFORM_INVITATION_CREATE,
    ):
        assert forbidden not in member


def test_pagination_walks_every_item_once() -> None:
    items = list(range(7))
    seen, cursor = [], None
    while True:
        page, cursor = paginate(items, 3, cursor)
        seen.extend(page)
        if cursor is None:
            break
    assert seen == items


@pytest.mark.parametrize("cursor", ["garbage", "bzotMQ", "eDox", "!!"])
def test_malformed_cursors_are_rejected(cursor: str) -> None:
    with pytest.raises(InvalidCursorError):
        paginate([1, 2, 3], 1, cursor)
