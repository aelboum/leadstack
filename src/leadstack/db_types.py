"""SQL types and functions that SaaS-OS's `infra.db` does not re-export.

Domain code may not import SQLAlchemy (import contract in pyproject.toml);
`infra.db` exposes the ORM primitives domains need, but not `BigInteger` or
`Date`. Mapping a BIGINT column as `Integer` is wrong with SQLAlchemy 2.1's
psycopg dialect: binds are rendered as `::INTEGER`, so values above 2^31-1
fail in PostgreSQL ("integer out of range"). This module is the single,
reviewed place where those two types come from; the contract allows exactly
this one edge (`leadstack.db_types -> sqlalchemy`).

Upstream proposal: export these from `infra.db` (docs/upstream/saas-os-proposals.md).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Date, func

# lower(): lets equality lookups use `lower(column)` functional indexes.
lower = func.lower

__all__ = ["BigInteger", "Date", "lower"]
