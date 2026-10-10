"""Static database-session discipline.

1. Tenant scope (LeadStack's equivalent of SaaS-OS's ADR-0022 check, which
   the package does not ship). Row-level security returns *zero rows* when
   no tenant is set, so an unscoped session hides bugs instead of failing.
   LeadStack code opens sessions with `infra.db.tenant_session_scope()`; an
   unscoped `session_scope()` (or a raw engine) is allowed only at the
   reviewed call sites listed here.

2. No nested pool checkouts. Inside an open `with *session_scope(...)`
   block, code must not call helpers that open *another* pooled connection
   (authorization checks, membership lookups, the workspace-country lookup).
   Each such request would hold one connection while waiting for a second;
   under concurrency the pool is exhausted and every request times out
   (Phase 3 review finding H1).

Aliased imports (`from infra.db import session_scope as s`) are resolved.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "leadstack"

# (module path relative to src/leadstack, enclosing function) -> reason
ALLOWED_UNSCOPED_SESSIONS = {
    ("domains/workspaces/service.py", "_index_workspace"): "user-keyed index (ADR-0004)",
    ("domains/workspaces/service.py", "list_my_workspaces"): "user-keyed index (ADR-0004)",
    ("domains/workspaces/purge.py", "purge_tenant_data"): "user-keyed index, tenant-filtered",
    ("admin.py", "known_workspace_ids"): "operator command reading the user-keyed index",
    ("domains/forms/service.py", "_resolve"): "public form-key index (ADR-0005)",
    ("domains/scheduling/service.py", "_resolve"): "public booking-key index (ADR-0008)",
    ("worker.py", "main"): "get_engine() for the SaaS-OS database-role guard",
}

UNSCOPED_FACTORIES = {"session_scope", "get_session_factory", "get_engine"}
SESSION_SCOPES = {"session_scope", "tenant_session_scope"}
# Helpers that check out their own pooled connection.
CONNECTION_OPENERS = {
    "authorize",
    "_holds",
    "can",
    "is_active_member",
    "workspace_country",
    "get_membership",
    "get_tenant",
    "list_tenant_members",
    "list_membership_roles",
    "list_roles",
    "ensure_workspace_roles",
}


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> original name for names imported from anywhere."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names[alias.asname or alias.name] = alias.name
    return names


def _called(node: ast.Call, aliases: dict[str, str]) -> str:
    target = node.func
    name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
    return aliases.get(name, name)


def _walk(tree: ast.AST, aliases: dict[str, str]):
    """Yield (call_name, enclosing_function, inside_open_session) for every call."""
    results: list[tuple[str, str, bool]] = []

    def visit(node: ast.AST, function: str, in_session: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function, in_session = node.name, False
        if isinstance(node, ast.With):
            opens = False
            for item in node.items:
                visit(item.context_expr, function, in_session)
                expr = item.context_expr
                if isinstance(expr, ast.Call) and _called(expr, aliases) in SESSION_SCOPES:
                    opens = True
            for statement in node.body:
                visit(statement, function, in_session or opens)
            return
        if isinstance(node, ast.Call):
            results.append((_called(node, aliases), function, in_session))
        for child in ast.iter_child_nodes(node):
            visit(child, function, in_session)

    visit(tree, "<module>", False)
    return results


def _modules():
    for path in SRC.rglob("*.py"):
        if "migrations" in path.parts or path.name == "migrate.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        yield path.relative_to(SRC).as_posix(), _walk(tree, _aliases(tree))


def test_unscoped_database_sessions_only_at_reviewed_call_sites() -> None:
    violations = [
        f"{module}:{function} calls {called}()"
        for module, calls in _modules()
        for called, function, _ in calls
        if called in UNSCOPED_FACTORIES and (module, function) not in ALLOWED_UNSCOPED_SESSIONS
    ]
    assert violations == []


def test_allowlist_has_no_stale_entries() -> None:
    present = {
        (module, function)
        for module, calls in _modules()
        for called, function, _ in calls
        if called in UNSCOPED_FACTORIES
    }
    assert set(ALLOWED_UNSCOPED_SESSIONS) <= present


def _transitive_openers(modules: list[tuple[str, list[tuple[str, str, bool]]]]) -> set[str]:
    """Any LeadStack function that (directly or indirectly) opens a session
    or calls a known opener is itself an opener. Computed to a fixpoint, so
    wrapping a lookup in a helper cannot hide it from the check."""
    openers = set(CONNECTION_OPENERS) | SESSION_SCOPES | {"record", "_audit"}
    changed = True
    while changed:
        changed = False
        for _, calls in modules:
            for called, function, _ in calls:
                if called in openers and function not in openers and function != "<module>":
                    openers.add(function)
                    changed = True
    return openers


def test_no_connection_opening_helpers_inside_open_sessions() -> None:
    modules = list(_modules())
    openers = _transitive_openers(modules) - SESSION_SCOPES
    violations = [
        f"{module}:{function} calls {called}() inside an open session"
        for module, calls in modules
        for called, function, in_session in calls
        if in_session and called in openers
    ]
    assert violations == []


def test_wrapped_openers_are_detected() -> None:
    known = _transitive_openers(list(_modules()))
    for helper in ("_check_assignee", "_prepare_contact", "_phone_query", "tags_of", "_audit"):
        assert helper in known, helper


def test_detector_sees_nested_checkouts_and_aliases() -> None:
    sample = ast.parse(
        "from infra.db import tenant_session_scope as tss, session_scope as raw\n"
        "def f(t):\n"
        "    with tss(t) as s:\n"
        "        x = authorize(1, t, 2)\n"
        "    raw()\n"
    )
    calls = _walk(sample, _aliases(sample))
    assert ("authorize", "f", True) in calls
    assert ("session_scope", "f", False) in calls
    assert ("tenant_session_scope", "f", False) in calls
