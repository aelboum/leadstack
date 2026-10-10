# Phase 5 audit report: sales pipelines and deals

Date: 2026-10-09

## 1. Verdict

**PASS WITH LIMITATIONS.**

- Pipelines, stages and deals use currency-safe money and are tested
  through the HTTP stack, with a basic pipeline board in the frontend.
- An independent review found 1 high, 4 medium and 5 low issues. All were
  fixed.
- Limitations are listed in §6.

## 2. Scope completed

**Money** (`domains/money.py`)
- Integer minor units plus an explicit ISO 4217 currency, using ISO
  exponents (MAD and EUR have 2 decimals, TND and KWD have 3, XOF and JPY
  have 0).
- Input is an ASCII decimal string or an integer. Floats, exponent
  notation, non-ASCII digits and excess precision are rejected.
- Deal amounts are never negative.
- Currencies are never mixed.

**Pipelines**
- Default stages are named in the workspace's locale (fr/ar/en).
- Custom stages are validated: at least one open stage, at most one won
  stage and one lost stage, unique names, probability between 0 and 100.
- Limit of 20 pipelines, serialized with an advisory lock.
- A pipeline that holds deals cannot be deleted. The pipeline row lock
  serializes deletion against concurrent deal inserts.

**Deals**
- The stage must belong to the deal's pipeline (composite foreign key, and
  checked in code).
- Status and `closed_at` are derived from the stage kind.
- Every move is recorded in the stage history.
- Contact and company links stay within the workspace.
- The owner must be an active member.
- Partial money updates: sending only `amount` uses the stored currency;
  sending only `currency` re-expresses the stored amount and refuses any
  loss of precision.
- Deals can be filtered by pipeline, stage, status, owner (`owner=me`) and
  contact, with keyset pagination.

**Reporting**
- Per-stage counts, totals and probability-weighted totals (rounded
  half-even), one figure per currency.
- Aggregated in SQL and returned as exact decimal strings, so browsers
  never lose precision.

**Frontend**
- Pipeline board: columns with counts and totals per currency, a stage
  move selector, cursor pagination up to 500 deals, and `Intl` money
  formatting from exact strings with Latin digits.

## 3. Files and migrations

- **Migration** `0005_sales`:
  - tables: `pipelines`, `pipeline_stages`, `deals`, `deal_stage_history`;
  - forced RLS;
  - composite foreign keys;
  - CHECK constraints: money pair, non-negative amount, closed-at
    consistency.
  - Downgrading drops sales data.
- **Code:**
  - `domains/{money,sales/*}.py`
  - `routes/sales.py`
  - `db_types.py`, the only reviewed import of SQLAlchemy types outside
    `infra.db`. The import contracts carry an explicit `ignore_imports`
    entry for it.
  - `frontend/components/DealsBoard.tsx`

## 4. Tests

- **Unit:** `test_money.py` (20 tests).
- **Integration:** `test_sales.py` (24 tests). Covers:
  - localized defaults;
  - stage validation;
  - lifecycle and history;
  - amount validation across exponents;
  - stage-in-pipeline integrity;
  - per-currency summary;
  - deletion rules;
  - isolation;
  - filters and pagination;
  - a 10^14-minor-unit amount;
  - negative and non-ASCII amounts;
  - partial money updates;
  - precision-losing currency changes.
- **Frontend:** `money.test.ts`, including totals above 2^53.

## 5. Independent review and resolution

| ID | Finding | Resolution |
|---|---|---|
| H1 | BIGINT mapped as Integer: amounts above 2^31−1 caused 500s (SQLAlchemy 2.1 renders `::INTEGER`) | `BigInteger`/`Date` come from `leadstack/db_types.py`; regression test with 10^14 minor units; upstream proposal P10 |
| M1 | Negative amounts | Rejected in code and by a CHECK constraint |
| M2 | Pipeline delete raced deal create | Pipeline row lock (`FOR UPDATE` / `FOR SHARE`); integrity errors mapped by constraint name |
| M3 | Totals could exceed JavaScript's safe integers | Totals returned as decimal strings |
| M4 | PATCH with only an amount was rejected | Supported, with a precision check on currency changes |
| L1 | Pipeline limit race | Advisory lock |
| L2 | Non-ASCII digits accepted | ASCII-only pattern |
| L3 | DATE mapped as DateTime | `Date` type |
| L4 | Unknown currency defaulted to 2 decimals in the UI | UI formats exact strings and no longer needs exponents |
| L5 | Board counts could disagree with the summary | Counts come from the summary; board paginates |

## 6. Limitations

- The board loads at most 500 deals per pipeline. Counts and totals are
  always complete.
- No drag-and-drop on the board; stages are changed with a selector.
- No currency conversion, by design: reports are per currency.
- Sales-triggered automation is not part of this phase. It comes with
  workflow automation (Phase 7).

## 7. Repository state

Untracked only. No commit, push or deployment. SaaS-OS untouched.
