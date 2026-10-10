"""LeadStack's migration history is linear, lives in the installed package,
and is distinct from SaaS-OS's own history."""

from __future__ import annotations

from alembic.script import ScriptDirectory
from infra.db.migration_runner import core_head_revision

from leadstack.migrate import build_alembic_config


def test_history_is_linear_with_expected_base() -> None:
    script = ScriptDirectory.from_config(build_alembic_config())

    assert len(script.get_heads()) == 1
    base_revisions = [rev for rev in script.walk_revisions() if rev.down_revision is None]
    assert [rev.revision for rev in base_revisions] == ["0001_leadstack_schema"]


def test_product_revisions_never_collide_with_platform_revisions() -> None:
    script = ScriptDirectory.from_config(build_alembic_config())

    product = {rev.revision for rev in script.walk_revisions()}
    assert core_head_revision() not in product
