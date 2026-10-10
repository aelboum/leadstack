"""Phase 5: pipelines, stages and deals with currency-safe money."""

from __future__ import annotations

import uuid

import pytest
from core import audit_log

from tests.integration.test_crm import _companies, _contacts
from tests.integration.test_workspaces import _code, _create, _invite_and_accept

pytestmark = pytest.mark.integration


def _w(tenant: str) -> str:
    return f"/v1/workspaces/{tenant}"


@pytest.fixture
def sales(api, make_user):
    owner, member = make_user(), make_user()
    tenant = _create(api, owner)["tenant_id"]  # locale fr
    _invite_and_accept(api, owner, tenant, member, "member")
    pipeline = api.post(f"{_w(tenant)}/pipelines", json={"name": "Ventes"}, headers=owner.headers)
    assert pipeline.status_code == 201, pipeline.text
    return owner, member, tenant, pipeline.json()


def _stage(pipeline: dict, kind: str, index: int = 0) -> str:
    return [s for s in pipeline["stages"] if s["kind"] == kind][index]["stage_id"]


def test_default_pipeline_uses_workspace_locale(sales) -> None:
    _, _, _, pipeline = sales
    names = [s["name"] for s in pipeline["stages"]]
    assert names == ["Prospect", "Qualifié", "Proposition", "Négociation", "Gagné", "Perdu"]
    assert [s["kind"] for s in pipeline["stages"]][-2:] == ["won", "lost"]


def test_custom_stages_are_validated(api, sales) -> None:
    owner, member, tenant, _ = sales
    url = f"{_w(tenant)}/pipelines"
    for bad in (
        [{"name": "Only", "probability": 10}],  # fewer than two
        [
            {"name": "A", "probability": 10, "kind": "won"},
            {"name": "B", "probability": 0, "kind": "lost"},
        ],
        [{"name": "A", "probability": 10}, {"name": "a", "probability": 20}],  # duplicate names
        [{"name": "A", "probability": 101}, {"name": "B", "probability": 0}],
        [{"name": "A", "probability": 5.5}, {"name": "B", "probability": 0}],
    ):
        response = api.post(
            url, json={"name": str(uuid.uuid4()), "stages": bad}, headers=owner.headers
        )
        assert response.status_code == 422, bad
    assert api.post(url, json={"name": "Ventes"}, headers=owner.headers).status_code == 409
    assert api.post(url, json={"name": "x"}, headers=member.headers).status_code == 403


def test_deal_money_and_lifecycle(api, sales) -> None:
    owner, member, tenant, pipeline = sales
    pid = pipeline["pipeline_id"]
    contact = api.post(_contacts(tenant), json={"first_name": "Omar"}, headers=owner.headers).json()
    company = api.post(_companies(tenant), json={"name": "Riad"}, headers=owner.headers).json()

    created = api.post(
        f"{_w(tenant)}/pipelines/{pid}/deals",
        json={"title": "Site web", "amount": "12500.50", "currency": "MAD",
              "contact_id": contact["contact_id"], "company_id": company["company_id"],
              "owner_user_id": str(member.user_id), "expected_close_date": "2026-12-31"},
        headers=member.headers,
    )  # fmt: skip
    assert created.status_code == 201, created.text
    deal = created.json()
    assert (deal["amount"], deal["currency"], deal["amount_minor"]) == ("12500.50", "MAD", 1250050)
    assert deal["stage_id"] == _stage(pipeline, "open") and deal["status"] == "open"
    assert deal["expected_close_date"] == "2026-12-31"

    won = api.post(
        f"{_w(tenant)}/deals/{deal['deal_id']}/stage",
        json={"stage_id": _stage(pipeline, "won")},
        headers=member.headers,
    ).json()
    assert won["status"] == "won" and won["closed_at"] is not None
    reopened = api.post(
        f"{_w(tenant)}/deals/{deal['deal_id']}/stage",
        json={"stage_id": _stage(pipeline, "open", 2)},
        headers=member.headers,
    ).json()
    assert reopened["status"] == "open" and reopened["closed_at"] is None

    history = api.get(
        f"{_w(tenant)}/deals/{deal['deal_id']}/history", headers=member.headers
    ).json()
    assert [h["from_stage_id"] for h in history] == [
        None,
        deal["stage_id"],
        _stage(pipeline, "won"),
    ]
    events = {e.action for e in audit_log.list(uuid.UUID(tenant), limit=100)}
    assert "leadstack.deal.stage_changed" in events

    assert (
        api.delete(f"{_w(tenant)}/deals/{deal['deal_id']}", headers=member.headers).status_code
        == 403
    )
    assert (
        api.delete(f"{_w(tenant)}/deals/{deal['deal_id']}", headers=owner.headers).status_code
        == 204
    )


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"amount": "10.005", "currency": "MAD"}, 422),  # beyond 2 decimals
        ({"amount": 10.5, "currency": "MAD"}, 422),  # JSON float
        ({"amount": "10"}, 422),  # amount without currency
        ({"currency": "MAD"}, 422),  # currency without amount
        ({"amount": "10", "currency": "XXX"}, 422),
        ({"amount": "1.5", "currency": "XOF"}, 422),  # zero-decimal currency
        ({"amount": "0.125", "currency": "TND"}, 201),  # three-decimal currency
        ({"amount": 15000, "currency": "XOF"}, 201),  # integer major units
    ],
)
def test_deal_amount_validation(api, sales, body, status) -> None:
    owner, _, tenant, pipeline = sales
    response = api.post(
        f"{_w(tenant)}/pipelines/{pipeline['pipeline_id']}/deals",
        json={"title": "D"} | body,
        headers=owner.headers,
    )
    assert response.status_code == status, response.text


def test_stage_must_belong_to_the_deals_pipeline(api, sales) -> None:
    owner, _, tenant, pipeline = sales
    other = api.post(
        f"{_w(tenant)}/pipelines", json={"name": "Autre"}, headers=owner.headers
    ).json()
    deal = api.post(
        f"{_w(tenant)}/pipelines/{pipeline['pipeline_id']}/deals",
        json={"title": "D"},
        headers=owner.headers,
    ).json()

    wrong_create = api.post(
        f"{_w(tenant)}/pipelines/{pipeline['pipeline_id']}/deals",
        json={"title": "D", "stage_id": other["stages"][0]["stage_id"]},
        headers=owner.headers,
    )
    wrong_move = api.post(
        f"{_w(tenant)}/deals/{deal['deal_id']}/stage",
        json={"stage_id": other["stages"][0]["stage_id"]},
        headers=owner.headers,
    )

    assert (wrong_create.status_code, _code(wrong_create)) == (404, "stage_not_found")
    assert (wrong_move.status_code, _code(wrong_move)) == (404, "stage_not_found")


def test_summary_totals_per_currency_never_mixed(api, sales) -> None:
    owner, _, tenant, pipeline = sales
    pid = pipeline["pipeline_id"]
    first = pipeline["stages"][0]
    for amount, currency in (("100.00", "MAD"), ("50.25", "MAD"), ("30", "EUR"), (None, None)):
        body = {"title": "D"} | ({"amount": amount, "currency": currency} if amount else {})
        api.post(f"{_w(tenant)}/pipelines/{pid}/deals", json=body, headers=owner.headers)

    summary = api.get(f"{_w(tenant)}/pipelines/{pid}/summary", headers=owner.headers).json()
    stage = next(s for s in summary if s["stage_id"] == first["stage_id"])

    assert stage["deal_count"] == 4
    # Exact decimal strings per currency.
    assert stage["totals"] == {"MAD": "150.25", "EUR": "30.00"}
    # Probability 10%: 1502.5 minor -> 1502 (half-even), 300 -> 300
    assert stage["weighted"] == {"MAD": "15.02", "EUR": "3.00"}
    assert all(s["deal_count"] == 0 for s in summary if s["stage_id"] != first["stage_id"])


def test_pipeline_with_deals_cannot_be_deleted(api, sales) -> None:
    owner, _, tenant, pipeline = sales
    pid = pipeline["pipeline_id"]
    deal = api.post(
        f"{_w(tenant)}/pipelines/{pid}/deals", json={"title": "D"}, headers=owner.headers
    ).json()

    blocked = api.delete(f"{_w(tenant)}/pipelines/{pid}", headers=owner.headers)
    api.delete(f"{_w(tenant)}/deals/{deal['deal_id']}", headers=owner.headers)
    allowed = api.delete(f"{_w(tenant)}/pipelines/{pid}", headers=owner.headers)

    assert (blocked.status_code, _code(blocked)) == (409, "pipeline_in_use")
    assert allowed.status_code == 204


def test_sales_isolation(api, sales, make_user) -> None:
    owner, _, tenant, pipeline = sales
    other_owner = make_user()
    other = _create(api, other_owner, display_name="Other")["tenant_id"]
    deal = api.post(
        f"{_w(tenant)}/pipelines/{pipeline['pipeline_id']}/deals",
        json={"title": "Secret"},
        headers=owner.headers,
    ).json()
    foreign_contact = api.post(
        _contacts(other), json={"first_name": "F"}, headers=other_owner.headers
    ).json()

    assert (
        api.get(f"{_w(other)}/deals/{deal['deal_id']}", headers=other_owner.headers).status_code
        == 404
    )
    assert (
        api.get(
            f"{_w(other)}/pipelines/{pipeline['pipeline_id']}", headers=other_owner.headers
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"{_w(other)}/pipelines/{pipeline['pipeline_id']}/deals",
            json={"title": "x"},
            headers=other_owner.headers,
        ).status_code
        == 404
    )
    assert api.get(f"{_w(other)}/deals", headers=other_owner.headers).json()["items"] == []
    linked = api.patch(
        f"{_w(tenant)}/deals/{deal['deal_id']}",
        json={"contact_id": foreign_contact["contact_id"]},
        headers=owner.headers,
    )
    assert (linked.status_code, _code(linked)) == (422, "unknown_reference")


def test_deal_list_filters_and_pagination(api, sales) -> None:
    owner, member, tenant, pipeline = sales
    pid = pipeline["pipeline_id"]
    for i in range(5):
        body = {"title": f"D{i}"} | ({"owner_user_id": str(member.user_id)} if i % 2 else {})
        api.post(f"{_w(tenant)}/pipelines/{pid}/deals", json=body, headers=owner.headers)

    mine = api.get(f"{_w(tenant)}/deals", params={"owner": "me"}, headers=member.headers).json()
    assert sorted(d["title"] for d in mine["items"]) == ["D1", "D3"]
    seen, cursor = [], None
    while True:
        params = {"limit": 2, "pipeline_id": pid} | ({"cursor": cursor} if cursor else {})
        page = api.get(f"{_w(tenant)}/deals", params=params, headers=owner.headers).json()
        seen += [d["deal_id"] for d in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5
    open_only = api.get(
        f"{_w(tenant)}/deals", params={"status": "won"}, headers=owner.headers
    ).json()
    assert open_only["items"] == []


def test_large_amounts_survive_bigint_storage(api, sales) -> None:
    """Above 2^31-1 minor units (Phase 5 review H1: Integer binds failed)."""
    owner, _, tenant, pipeline = sales
    pid = pipeline["pipeline_id"]
    big = "999999999999.99"  # 10^14 - 1 minor units
    deal = api.post(
        f"{_w(tenant)}/pipelines/{pid}/deals",
        json={
            "title": "Tower",
            "amount": big,
            "currency": "MAD",
            "expected_close_date": "2027-01-15",
        },
        headers=owner.headers,
    )
    assert deal.status_code == 201, deal.text
    assert deal.json()["amount"] == big
    again = api.get(f"{_w(tenant)}/deals/{deal.json()['deal_id']}", headers=owner.headers).json()
    assert again["amount_minor"] == 99999999999999 and again["expected_close_date"] == "2027-01-15"
    api.post(
        f"{_w(tenant)}/pipelines/{pid}/deals",
        json={"title": "Tower 2", "amount": big, "currency": "MAD"},
        headers=owner.headers,
    )
    summary = api.get(f"{_w(tenant)}/pipelines/{pid}/summary", headers=owner.headers).json()
    assert summary[0]["totals"] == {"MAD": "1999999999999.98"}


@pytest.mark.parametrize("amount", ["-10", "١٢٣", "\uff11\uff12\uff13", "1e3", "1_000"])
def test_deal_amount_rejects_negative_and_non_ascii(api, sales, amount) -> None:
    owner, _, tenant, pipeline = sales
    response = api.post(
        f"{_w(tenant)}/pipelines/{pipeline['pipeline_id']}/deals",
        json={"title": "D", "amount": amount, "currency": "MAD"},
        headers=owner.headers,
    )
    assert response.status_code == 422


def test_partial_money_updates(api, sales) -> None:
    owner, _, tenant, pipeline = sales
    deal = api.post(
        f"{_w(tenant)}/pipelines/{pipeline['pipeline_id']}/deals",
        json={"title": "D", "amount": "10.50", "currency": "MAD"},
        headers=owner.headers,
    ).json()
    url = f"{_w(tenant)}/deals/{deal['deal_id']}"

    amount_only = api.patch(url, json={"amount": "200"}, headers=owner.headers).json()
    lossy = api.patch(url, json={"currency": "XOF"}, headers=owner.headers)
    exact = api.patch(url, json={"currency": "XOF"}, headers=owner.headers)
    cleared = api.patch(url, json={"amount": None, "currency": None}, headers=owner.headers).json()
    orphan = api.patch(url, json={"amount": "5"}, headers=owner.headers)

    assert (amount_only["amount"], amount_only["currency"]) == ("200.00", "MAD")
    assert exact.status_code == 200 and exact.json()["amount"] == "200"  # 200.00 MAD -> 200 XOF
    assert lossy.status_code == 200  # 200.00 has no fraction, so it is exact too
    assert (cleared["amount"], cleared["currency"]) == (None, None)
    assert orphan.status_code == 422  # no currency to interpret it in


def test_currency_change_that_would_lose_precision_is_refused(api, sales) -> None:
    owner, _, tenant, pipeline = sales
    deal = api.post(
        f"{_w(tenant)}/pipelines/{pipeline['pipeline_id']}/deals",
        json={"title": "D", "amount": "10.50", "currency": "MAD"},
        headers=owner.headers,
    ).json()

    response = api.patch(
        f"{_w(tenant)}/deals/{deal['deal_id']}", json={"currency": "XOF"}, headers=owner.headers
    )

    assert (response.status_code, _code(response)) == (422, "invalid_amount")
