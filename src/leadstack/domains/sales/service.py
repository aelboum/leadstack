"""Pipelines, stages and deals.

- Money is integer minor units plus an explicit ISO 4217 currency
  (`domains.money`). Reports total per currency and never mix currencies.
- A deal's stage must belong to its pipeline (enforced by a composite
  foreign key as well as here). Moving a deal records history and derives
  the deal status from the stage kind (open/won/lost).
- Same session discipline as the CRM: lookups that need their own pooled
  connection (authorization, membership, workspace settings) run before a
  session is opened.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from core import audit_log
from core.audit_log import ActorType, AuditOutcome
from infra.db import (
    Integer,
    IntegrityError,
    acquire_tenant_advisory_lock,
    select,
    sum_,
    tenant_session_scope,
)

from leadstack.domains.automation import events
from leadstack.domains.crm.models import Company, Contact
from leadstack.domains.money import Money
from leadstack.domains.sales.models import Deal, DealStageHistory, Pipeline, PipelineStage
from leadstack.domains.workspaces import permissions as perms
from leadstack.domains.workspaces.service import authorize, is_active_member

MAX_PAGE = 100
MAX_STAGES = 20
MAX_PIPELINES = 20
PIPELINES_LOCK_KEY = "leadstack.pipelines"

# Default stage names for a new pipeline, in the workspace's locale.
DEFAULT_STAGES: dict[str, list[tuple[str, int, str]]] = {
    "en": [("Lead", 10, "open"), ("Qualified", 30, "open"), ("Proposal", 60, "open"),
           ("Negotiation", 80, "open"), ("Won", 100, "won"), ("Lost", 0, "lost")],
    "fr": [("Prospect", 10, "open"), ("Qualifié", 30, "open"), ("Proposition", 60, "open"),
           ("Négociation", 80, "open"), ("Gagné", 100, "won"), ("Perdu", 0, "lost")],
    "ar": [("عميل محتمل", 10, "open"), ("مؤهل", 30, "open"), ("عرض", 60, "open"),
           ("تفاوض", 80, "open"), ("ربح", 100, "won"), ("خسارة", 0, "lost")],
}  # fmt: skip


class SalesError(Exception):
    code = "sales_error"


class PipelineNotFoundError(SalesError):
    code = "pipeline_not_found"


class StageNotFoundError(SalesError):
    code = "stage_not_found"


class DealNotFoundError(SalesError):
    code = "deal_not_found"


class DuplicatePipelineError(SalesError):
    code = "pipeline_exists"


class PipelineInUseError(SalesError):
    code = "pipeline_in_use"


class InvalidStagesError(SalesError):
    code = "invalid_stages"


class InvalidDealError(SalesError):
    code = "invalid_deal"


class InvalidDealOwnerError(SalesError):
    code = "invalid_owner"


class UnknownReferenceError(SalesError):
    code = "unknown_reference"


class InvalidCursorError(SalesError):
    code = "invalid_cursor"


class LimitReachedError(SalesError):
    code = "limit_reached"


# --- Views ----------------------------------------------------------------------


@dataclass(frozen=True)
class StageView:
    stage_id: uuid.UUID
    name: str
    position: int
    probability: int
    kind: str


@dataclass(frozen=True)
class PipelineView:
    pipeline_id: uuid.UUID
    name: str
    stages: tuple[StageView, ...]


@dataclass(frozen=True)
class DealView:
    deal_id: uuid.UUID
    pipeline_id: uuid.UUID
    stage_id: uuid.UUID
    title: str
    amount: str | None
    currency: str | None
    amount_minor: int | None
    contact_id: uuid.UUID | None
    company_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None
    expected_close_date: date | None
    status: str
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DealPage:
    items: tuple[DealView, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class HistoryView:
    from_stage_id: uuid.UUID | None
    to_stage_id: uuid.UUID
    changed_by_user_id: uuid.UUID
    changed_at: datetime


@dataclass(frozen=True)
class StageSummary:
    stage_id: uuid.UUID
    name: str
    kind: str
    deal_count: int
    # Exact decimal strings per currency (JSON numbers would lose precision
    # beyond 2^53 in browsers).
    totals: dict[str, str] = field(default_factory=dict)
    weighted: dict[str, str] = field(default_factory=dict)


def _stage_view(row: PipelineStage) -> StageView:
    return StageView(row.id, row.name, row.position, row.probability, row.kind)


def _deal_view(row: Deal) -> DealView:
    money = (
        Money(currency=row.currency, amount_minor=row.amount_minor)
        if row.currency is not None and row.amount_minor is not None
        else None
    )
    close = row.expected_close_date
    if isinstance(close, datetime):
        close = close.date()
    return DealView(
        deal_id=row.id,
        pipeline_id=row.pipeline_id,
        stage_id=row.stage_id,
        title=row.title,
        amount=money.decimal_string() if money else None,
        currency=row.currency,
        amount_minor=row.amount_minor,
        contact_id=row.contact_id,
        company_id=row.company_id,
        owner_user_id=row.owner_user_id,
        expected_close_date=close,
        status=row.status,
        closed_at=row.closed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audit(tenant_id, actor_id, action: str, resource: str, rid, metadata=None) -> None:
    audit_log.record(
        tenant_id=tenant_id,
        actor_type=ActorType.USER,
        actor_user_id=actor_id,
        action=action,
        resource_type=resource,
        resource_id=str(rid),
        outcome=AuditOutcome.SUCCESS,
        metadata=metadata,
    )


# --- Pipelines ------------------------------------------------------------------


def _lock_pipeline(
    session, tenant_id: uuid.UUID, pipeline_id: uuid.UUID, *, exclusive: bool
) -> None:
    """Row lock on the pipeline: deleting takes FOR UPDATE, adding or moving
    deals takes FOR SHARE, so a deletion and a concurrent deal insert are
    serialized instead of racing to an integrity error."""
    statement = select(Pipeline.id).where(
        Pipeline.tenant_id == tenant_id, Pipeline.id == pipeline_id
    )
    statement = statement.with_for_update(read=not exclusive)
    if session.execute(statement).scalar_one_or_none() is None:
        raise PipelineNotFoundError()


def _load_pipeline(session, tenant_id: uuid.UUID, pipeline_id: uuid.UUID) -> PipelineView:
    pipeline = session.execute(
        select(Pipeline).where(Pipeline.tenant_id == tenant_id, Pipeline.id == pipeline_id)
    ).scalar_one_or_none()
    if pipeline is None:
        raise PipelineNotFoundError()
    stages = session.execute(
        select(PipelineStage)
        .where(PipelineStage.tenant_id == tenant_id, PipelineStage.pipeline_id == pipeline_id)
        .order_by(PipelineStage.position, PipelineStage.id)
    ).scalars()
    return PipelineView(pipeline.id, pipeline.name, tuple(_stage_view(s) for s in stages))


def _validate_stages(stages: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    if not 2 <= len(stages) <= MAX_STAGES:
        raise InvalidStagesError()
    cleaned = []
    for stage in stages:
        name = str(stage.get("name", "")).strip()
        probability = stage.get("probability")
        kind = stage.get("kind", "open")
        if not name or len(name) > 100 or kind not in ("open", "won", "lost"):
            raise InvalidStagesError()
        if isinstance(probability, bool) or not isinstance(probability, int):
            raise InvalidStagesError()
        if not 0 <= probability <= 100:
            raise InvalidStagesError()
        cleaned.append((name, probability, kind))
    kinds = [kind for _, _, kind in cleaned]
    if "open" not in kinds or kinds.count("won") > 1 or kinds.count("lost") > 1:
        raise InvalidStagesError()
    if len({name.lower() for name, _, _ in cleaned}) != len(cleaned):
        raise InvalidStagesError()
    return cleaned


def create_pipeline(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    name: str,
    stages: list[dict[str, Any]] | None,
    locale: str,
) -> PipelineView:
    """`stages=None` uses the default stages in the workspace locale."""
    authorize(actor_id, tenant_id, perms.PIPELINES_MANAGE)
    name = name.strip()
    if not name or len(name) > 100:
        raise InvalidStagesError()
    cleaned = (
        _validate_stages(stages)
        if stages is not None
        else DEFAULT_STAGES.get(locale, DEFAULT_STAGES["en"])
    )
    try:
        with tenant_session_scope(tenant_id) as session:
            acquire_tenant_advisory_lock(session, tenant_id, PIPELINES_LOCK_KEY)
            count = len(
                session.execute(select(Pipeline.id).where(Pipeline.tenant_id == tenant_id)).all()
            )
            if count >= MAX_PIPELINES:
                raise LimitReachedError()
            pipeline = Pipeline(tenant_id=tenant_id, name=name)
            session.add(pipeline)
            session.flush()
            for position, (stage_name, probability, kind) in enumerate(cleaned):
                session.add(
                    PipelineStage(
                        tenant_id=tenant_id,
                        pipeline_id=pipeline.id,
                        name=stage_name,
                        position=position,
                        probability=probability,
                        kind=kind,
                    )
                )
            session.flush()
            view = _load_pipeline(session, tenant_id, pipeline.id)
    except IntegrityError:
        raise DuplicatePipelineError() from None
    _audit(tenant_id, actor_id, "leadstack.pipeline.created", "leadstack.pipeline",
           view.pipeline_id, {"stages": len(view.stages)})  # fmt: skip
    return view


def list_pipelines(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> list[PipelineView]:
    authorize(actor_id, tenant_id, perms.DEALS_READ)
    with tenant_session_scope(tenant_id) as session:
        ids = session.execute(
            select(Pipeline.id).where(Pipeline.tenant_id == tenant_id).order_by(Pipeline.created_at)
        ).scalars()
        return [_load_pipeline(session, tenant_id, pid) for pid in list(ids)]


def get_pipeline(actor_id: uuid.UUID, tenant_id: uuid.UUID, pipeline_id: uuid.UUID) -> PipelineView:
    authorize(actor_id, tenant_id, perms.DEALS_READ)
    with tenant_session_scope(tenant_id) as session:
        return _load_pipeline(session, tenant_id, pipeline_id)


def delete_pipeline(actor_id: uuid.UUID, tenant_id: uuid.UUID, pipeline_id: uuid.UUID) -> None:
    """Only an empty pipeline can be deleted (its deals must be moved or
    deleted first) -- deals are never removed as a side effect."""
    authorize(actor_id, tenant_id, perms.PIPELINES_MANAGE)
    try:
        _delete_pipeline(tenant_id, pipeline_id)
    except IntegrityError:
        raise PipelineInUseError() from None  # a deal was added concurrently
    _audit(tenant_id, actor_id, "leadstack.pipeline.deleted", "leadstack.pipeline", pipeline_id)


def _delete_pipeline(tenant_id: uuid.UUID, pipeline_id: uuid.UUID) -> None:
    with tenant_session_scope(tenant_id) as session:
        _lock_pipeline(session, tenant_id, pipeline_id, exclusive=True)
        in_use = session.execute(
            select(Deal.id)
            .where(Deal.tenant_id == tenant_id, Deal.pipeline_id == pipeline_id)
            .limit(1)
        ).first()
        if in_use is not None:
            raise PipelineInUseError()
        session.delete(session.get(Pipeline, pipeline_id))


# --- Deals ----------------------------------------------------------------------


UNSET: Any = object()


def _prepare_deal(tenant_id: uuid.UUID, fields: dict[str, Any]) -> dict[str, Any]:
    """Validation that needs no session (or needs its own connection)."""
    values: dict[str, Any] = {}
    if "title" in fields:
        title = (fields["title"] or "").strip()
        if not title or len(title) > 200:
            raise InvalidDealError()
        values["title"] = title
    if "amount" in fields or "currency" in fields:
        # Resolved against the stored deal by _apply_money() (a PATCH may
        # send only one of the two).
        values["_money"] = (fields.get("amount", UNSET), fields.get("currency", UNSET))
    if "expected_close_date" in fields:
        values["expected_close_date"] = fields["expected_close_date"]
    if "owner_user_id" in fields:
        owner = fields["owner_user_id"]
        if owner is not None and not is_active_member(tenant_id, owner):
            raise InvalidDealOwnerError()
        values["owner_user_id"] = owner
    for name in ("contact_id", "company_id"):
        if name in fields:
            values[name] = fields[name]
    return values


def _apply_money(values: dict[str, Any], current: Deal | None) -> None:
    """Turn the requested amount/currency into minor units.

    - both given: parsed together;
    - only `amount`: interpreted in the deal's current currency;
    - only `currency`: the stored amount is re-expressed in the new currency
      (refused if that would lose precision, e.g. 10.50 MAD -> XOF);
    - explicit nulls clear both; a deal amount is never negative.
    """
    if "_money" not in values:
        return
    amount, currency = values.pop("_money")
    stored_currency = current.currency if current is not None else None
    stored_amount = (
        Money(currency=stored_currency, amount_minor=current.amount_minor).decimal_string()
        if current is not None and stored_currency is not None and current.amount_minor is not None
        else None
    )
    amount = stored_amount if amount is UNSET else amount
    currency = stored_currency if currency is UNSET else currency
    if amount is None and currency is None:
        values["amount_minor"] = values["currency"] = None
        return
    if amount is None or currency is None:
        raise InvalidDealError()  # money always travels with its currency
    money = Money.parse(amount, str(currency))
    if money.amount_minor < 0:
        raise InvalidDealError()
    values["amount_minor"], values["currency"] = money.amount_minor, money.currency


def _check_links(session, tenant_id: uuid.UUID, values: dict[str, Any]) -> None:
    for name, model in (("contact_id", Contact), ("company_id", Company)):
        target = values.get(name)
        if target is not None:
            found = session.execute(
                select(model.id).where(model.tenant_id == tenant_id, model.id == target)
            ).scalar_one_or_none()
            if found is None:
                raise UnknownReferenceError()


def _stage(session, tenant_id, pipeline_id, stage_id) -> PipelineStage:
    stage = session.execute(
        select(PipelineStage).where(
            PipelineStage.tenant_id == tenant_id,
            PipelineStage.pipeline_id == pipeline_id,
            PipelineStage.id == stage_id,
        )
    ).scalar_one_or_none()
    if stage is None:
        raise StageNotFoundError()
    return stage


def _status_for(stage: PipelineStage) -> tuple[str, datetime | None]:
    if stage.kind == "open":
        return "open", None
    return stage.kind, datetime.now(UTC)


def create_deal(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    fields: dict[str, Any],
) -> DealView:
    authorize(actor_id, tenant_id, perms.DEALS_CREATE)
    if "title" not in fields:
        raise InvalidDealError()
    values = _prepare_deal(tenant_id, fields)
    try:
        with tenant_session_scope(tenant_id) as session:
            _lock_pipeline(session, tenant_id, pipeline_id, exclusive=False)
            _apply_money(values, None)
            pipeline = _load_pipeline(session, tenant_id, pipeline_id)
            stage_id = fields.get("stage_id") or next(
                s.stage_id for s in pipeline.stages if s.kind == "open"
            )
            stage = _stage(session, tenant_id, pipeline_id, stage_id)
            _check_links(session, tenant_id, values)
            status, closed_at = _status_for(stage)
            row = Deal(
                tenant_id=tenant_id,
                pipeline_id=pipeline_id,
                stage_id=stage.id,
                status=status,
                closed_at=closed_at,
                created_by_user_id=actor_id,
                **values,
            )
            session.add(row)
            session.flush()
            session.add(
                DealStageHistory(
                    tenant_id=tenant_id,
                    deal_id=row.id,
                    from_stage_id=None,
                    to_stage_id=stage.id,
                    changed_by_user_id=actor_id,
                )
            )
            session.flush()
            session.refresh(row)
            view = _deal_view(row)
    except IntegrityError as exc:
        raise _integrity(exc) from None
    _audit(tenant_id, actor_id, "leadstack.deal.created", "leadstack.deal", view.deal_id)
    return view


def _integrity(exc: IntegrityError) -> SalesError:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    name = getattr(diag, "constraint_name", None) or ""
    if name == "fk_deals_stage_in_pipeline":
        return StageNotFoundError()
    if name in ("fk_deals_contact_id", "fk_deals_company_id"):
        return UnknownReferenceError()
    return InvalidDealError()


def _deal_row(session, tenant_id: uuid.UUID, deal_id: uuid.UUID, *, lock: bool = False) -> Deal:
    statement = select(Deal).where(Deal.tenant_id == tenant_id, Deal.id == deal_id)
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).scalar_one_or_none()
    if row is None:
        raise DealNotFoundError()
    return row


def get_deal(actor_id: uuid.UUID, tenant_id: uuid.UUID, deal_id: uuid.UUID) -> DealView:
    authorize(actor_id, tenant_id, perms.DEALS_READ)
    with tenant_session_scope(tenant_id) as session:
        return _deal_view(_deal_row(session, tenant_id, deal_id))


def update_deal(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, deal_id: uuid.UUID, fields: dict[str, Any]
) -> DealView:
    authorize(actor_id, tenant_id, perms.DEALS_UPDATE)
    values = _prepare_deal(tenant_id, fields)
    try:
        with tenant_session_scope(tenant_id) as session:
            row = _deal_row(session, tenant_id, deal_id, lock=True)
            _apply_money(values, row)
            _check_links(session, tenant_id, values)
            for name, value in values.items():
                setattr(row, name, value)
            if values:
                row.updated_at = datetime.now(UTC)
            session.flush()
            session.refresh(row)
            view = _deal_view(row)
    except IntegrityError as exc:
        raise _integrity(exc) from None
    if values:
        changed = sorted({"amount" if k in ("amount_minor", "currency") else k for k in values})
        _audit(tenant_id, actor_id, "leadstack.deal.updated", "leadstack.deal", deal_id,
               {"fields": changed})  # fmt: skip
    return view


def move_deal(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, deal_id: uuid.UUID, stage_id: uuid.UUID
) -> DealView:
    """Move to another stage of the same pipeline, recording history."""
    authorize(actor_id, tenant_id, perms.DEALS_UPDATE)
    with tenant_session_scope(tenant_id) as session:
        row = _deal_row(session, tenant_id, deal_id, lock=True)
        _lock_pipeline(session, tenant_id, row.pipeline_id, exclusive=False)
        stage = _stage(session, tenant_id, row.pipeline_id, stage_id)
        previous = row.stage_id
        if previous != stage.id:
            row.stage_id = stage.id
            row.status, row.closed_at = _status_for(stage)
            row.updated_at = datetime.now(UTC)
            session.add(
                DealStageHistory(
                    tenant_id=tenant_id,
                    deal_id=row.id,
                    from_stage_id=previous,
                    to_stage_id=stage.id,
                    changed_by_user_id=actor_id,
                )
            )
            events.emit(
                session,
                tenant_id,
                events.DEAL_STAGE_CHANGED,
                row.id,
                {
                    "pipeline_id": str(row.pipeline_id),
                    "from_stage_id": str(previous),
                    "to_stage_id": str(stage.id),
                    "status": row.status,
                },
            )
        session.flush()
        session.refresh(row)
        view = _deal_view(row)
    if previous != stage_id:
        _audit(tenant_id, actor_id, "leadstack.deal.stage_changed", "leadstack.deal", deal_id,
               {"from_stage_id": str(previous), "to_stage_id": str(stage_id),
                "status": view.status})  # fmt: skip
    return view


def delete_deal(actor_id: uuid.UUID, tenant_id: uuid.UUID, deal_id: uuid.UUID) -> None:
    authorize(actor_id, tenant_id, perms.DEALS_DELETE)
    with tenant_session_scope(tenant_id) as session:
        session.delete(_deal_row(session, tenant_id, deal_id, lock=True))
    _audit(tenant_id, actor_id, "leadstack.deal.deleted", "leadstack.deal", deal_id)


def deal_history(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, deal_id: uuid.UUID
) -> list[HistoryView]:
    authorize(actor_id, tenant_id, perms.DEALS_READ)
    with tenant_session_scope(tenant_id) as session:
        _deal_row(session, tenant_id, deal_id)
        rows = session.execute(
            select(DealStageHistory)
            .where(DealStageHistory.tenant_id == tenant_id, DealStageHistory.deal_id == deal_id)
            .order_by(DealStageHistory.changed_at, DealStageHistory.id)
            .limit(500)
        ).scalars()
        return [
            HistoryView(r.from_stage_id, r.to_stage_id, r.changed_by_user_id, r.changed_at)
            for r in rows
        ]


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps({"c": created_at.isoformat(), "i": str(row_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        data = json.loads(base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()))
        created_at, row_id = datetime.fromisoformat(data["c"]), uuid.UUID(data["i"])
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise InvalidCursorError() from None
    if created_at.tzinfo is None:
        raise InvalidCursorError()
    return created_at, row_id


def list_deals(
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    pipeline_id: uuid.UUID | None = None,
    stage_id: uuid.UUID | None = None,
    status: str | None = None,
    owner_user_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> DealPage:
    authorize(actor_id, tenant_id, perms.DEALS_READ)
    limit = max(1, min(limit, MAX_PAGE))
    statement = select(Deal).where(Deal.tenant_id == tenant_id)
    for column, value in (
        (Deal.pipeline_id, pipeline_id),
        (Deal.stage_id, stage_id),
        (Deal.owner_user_id, owner_user_id),
        (Deal.contact_id, contact_id),
    ):
        if value is not None:
            statement = statement.where(column == value)
    if status is not None:
        if status not in ("open", "won", "lost"):
            raise InvalidDealError()
        statement = statement.where(Deal.status == status)
    if cursor:
        created_at, row_id = _decode_cursor(cursor)
        statement = statement.where(
            (Deal.created_at < created_at) | ((Deal.created_at == created_at) & (Deal.id < row_id))
        )
    statement = statement.order_by(Deal.created_at.desc(), Deal.id.desc()).limit(limit + 1)
    with tenant_session_scope(tenant_id) as session:
        rows = list(session.execute(statement).scalars())
        more, rows = len(rows) > limit, rows[:limit]
        views = tuple(_deal_view(r) for r in rows)  # while the rows are still bound
    next_cursor = (
        _encode_cursor(views[-1].created_at, views[-1].deal_id) if more and views else None
    )
    return DealPage(items=views, next_cursor=next_cursor)


# --- Reporting ------------------------------------------------------------------


def pipeline_summary(
    actor_id: uuid.UUID, tenant_id: uuid.UUID, pipeline_id: uuid.UUID
) -> list[StageSummary]:
    """Per stage: deal count, totals per currency, and probability-weighted
    totals per currency (rounded half-even to the minor unit). Currencies
    are never converted or mixed."""
    authorize(actor_id, tenant_id, perms.DEALS_READ)
    with tenant_session_scope(tenant_id) as session:
        pipeline = _load_pipeline(session, tenant_id, pipeline_id)
        # Aggregated in the database: one row per (stage, currency).
        rows = session.execute(
            select(
                Deal.stage_id,
                Deal.currency,
                sum_(Deal.id.is_not(None).cast(Integer)),  # deal count
                sum_(Deal.amount_minor),
            )
            .where(Deal.tenant_id == tenant_id, Deal.pipeline_id == pipeline_id)
            .group_by(Deal.stage_id, Deal.currency)
        ).all()
    summaries = []
    for stage in pipeline.stages:
        mine = [r for r in rows if r[0] == stage.stage_id]
        count = sum(int(r[2]) for r in mine)
        totals: dict[str, int] = {
            str(r[1]): int(r[3]) for r in mine if r[1] is not None and r[3] is not None
        }
        weighted = {
            currency: int(
                (Decimal(total) * stage.probability / 100).quantize(
                    Decimal(1), rounding=ROUND_HALF_EVEN
                )
            )
            for currency, total in totals.items()
        }

        def as_text(minor: dict[str, int]) -> dict[str, str]:
            return {
                currency: Money(currency=currency, amount_minor=value).decimal_string()
                for currency, value in minor.items()
            }

        summaries.append(
            StageSummary(
                stage.stage_id, stage.name, stage.kind, count, as_text(totals), as_text(weighted)
            )
        )
    return summaries
