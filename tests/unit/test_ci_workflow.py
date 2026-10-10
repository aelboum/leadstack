"""The CI workflow keeps its required gates, pins every action to an
immutable commit, and never embeds a credential."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_required_jobs_and_gates_exist() -> None:
    jobs = _workflow()["jobs"]
    assert {"quality", "integration", "frontend", "deploy", "security"} <= set(jobs)
    runs = "\n".join(step.get("run", "") for job in jobs.values() for step in job["steps"])
    for gate in (
        "uv lock --check",
        "uv sync --locked",
        "ruff check",
        "ruff format --check",
        "pyright",
        "lint-imports",
        "pytest -m integration",
        "pip-audit",
        "detect-secrets-hook",
        "npm ci",
        "npm run typecheck",
        "npm test",
        "npm run build",
        "npm audit --omit=dev --audit-level=high",
        "docker compose -f deploy/compose.yaml",
        "--profile bundled config --quiet",
        "caddy validate",
        "docker build -f deploy/Dockerfile.frontend",
    ):
        assert gate in runs, gate
    assert jobs["integration"]["env"]["LEADSTACK_REQUIRE_INTEGRATION"] == "1"


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    uses = [
        step["uses"]
        for job in _workflow()["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]
    assert uses
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), uses


def test_no_credentials_in_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_", text)
    assert "${{ secrets.SAAS_OS_READ_TOKEN }}" in text  # the only source of the token


def test_dependency_token_reaches_only_the_jobs_that_resolve_it() -> None:
    """The frontend and deploy jobs run third-party build tooling: they
    must never receive the SaaS-OS read token."""
    workflow = _workflow()
    assert not any(key.startswith("GIT_CONFIG") for key in workflow.get("env", {}))
    for name, job in workflow["jobs"].items():
        has_token = "SAAS_OS_READ_TOKEN }}" in str(job.get("env", {}))
        assert has_token == (name in {"quality", "integration", "security"}), name
    for name in ("frontend", "deploy"):
        assert "secrets." not in yaml.safe_dump(workflow["jobs"][name]), name
