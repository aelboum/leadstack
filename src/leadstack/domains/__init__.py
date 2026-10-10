"""LeadStack business domains (CRM, sales, ...), one subpackage per domain.

No domain exists yet -- the first arrives with the CRM vertical slice. This
package exists now so the architecture contracts in `pyproject.toml` bind
from the first domain module onward: domain code never imports SQLAlchemy
or psycopg directly (database access goes through `infra.db`), and never
imports HTTP frameworks or process entrypoints.
"""
