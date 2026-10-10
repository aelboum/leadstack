"""Guards on the LeadStack-owned deployment files (deploy/, ADR-0009).

These are static checks: they do not prove a deployment works, only that
the security-relevant choices in the files cannot be weakened silently."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
COMPOSE = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
BACKEND = (DEPLOY / "Dockerfile.backend").read_text(encoding="utf-8")
FRONTEND = (DEPLOY / "Dockerfile.frontend").read_text(encoding="utf-8")
PIN = "b126f1ad0c9cf4173ada15e20b14850d9e600c14"  # pragma: allowlist secret (a commit id)


def test_only_the_edge_publishes_ports() -> None:
    published = {name for name, service in SERVICES.items() if service.get("ports")}
    assert published == {"edge"}


def test_data_store_tls_is_required_unless_the_operator_opts_out() -> None:
    """SaaS-OS requires TLS in production; the files must never ship the
    opt-out as a default (decision D14)."""
    env = SERVICES["api"]["environment"]
    assert env["ENVIRONMENT"] == "production"
    assert env["DB_REQUIRE_TLS"] == "${DB_REQUIRE_TLS:-true}"
    assert env["REDIS_REQUIRE_TLS"] == "${REDIS_REQUIRE_TLS:-true}"
    example = (DEPLOY / "production.env.example").read_text(encoding="utf-8")
    active = [line for line in example.splitlines() if line and not line.startswith("#")]
    assert not any(line.startswith(("DB_REQUIRE_TLS", "REDIS_REQUIRE_TLS")) for line in active)


def test_only_the_migration_job_receives_owner_credentials() -> None:
    holders = {
        name
        for name, service in SERVICES.items()
        if "MIGRATIONS_DATABASE_URL" in (service.get("environment") or {})
    }
    assert holders == {"migrate"}
    assert SERVICES["migrate"]["restart"] == "no"
    for name in ("api", "worker"):
        assert (
            SERVICES[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
        )


def test_forwarded_headers_are_trusted_from_the_edge_only() -> None:
    edge_address = SERVICES["edge"]["networks"]["front"]["ipv4_address"]
    assert SERVICES["api"]["environment"]["FORWARDED_ALLOW_IPS"] == edge_address
    assert "*" not in edge_address
    # No default in the image: starting without an explicit allowlist fails.
    assert "${FORWARDED_ALLOW_IPS:?" in BACKEND
    assert "--proxy-headers" in BACKEND
    assert re.search(r"forwarded-allow-ips[ =]\*", BACKEND) is None


def test_application_containers_are_hardened() -> None:
    for name in ("api", "worker", "migrate", "frontend"):
        service = SERVICES[name]
        assert service["read_only"] is True, name
        assert service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
    assert re.search(r"^USER leadstack$", BACKEND, re.M)
    assert re.search(r"^USER node$", FRONTEND, re.M)


def test_build_token_is_a_secret_never_an_argument_or_layer() -> None:
    assert "--mount=type=secret,id=saas_os_token" in BACKEND
    assert re.search(r"^\s*(ARG|ENV)\s+.*TOKEN", BACKEND, re.M | re.I) is None
    assert COMPOSE["secrets"]["saas_os_token"] == {"environment": "SAAS_OS_READ_TOKEN"}
    for name, service in SERVICES.items():
        assert "SAAS_OS_READ_TOKEN" not in str(service.get("environment", "")), name


def test_image_installs_exactly_the_locked_dependencies() -> None:
    assert BACKEND.count("uv sync --frozen --no-dev") == 2
    assert PIN in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "npm ci --ignore-scripts" in FRONTEND


def test_required_settings_have_no_silent_defaults() -> None:
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    for name in (
        "LEADSTACK_VERSION",
        "LEADSTACK_DOMAIN",
        "LEADSTACK_FORM_TOKEN_SECRET",
        "LEADSTACK_LINK_SIGNING_SECRET",
        "OIDC_ISSUER_URL",
    ):
        assert f"${{{name}:?" in text, name


def test_internet_facing_containers_cannot_reach_the_data_stores() -> None:
    """Jobs are deserialized by the worker: write access to Redis from the
    edge or the frontend would become code execution next to the database
    credentials."""

    def networks(name: str) -> set[str]:
        return set(SERVICES[name]["networks"])

    assert COMPOSE["networks"]["data"] == {"internal": True}
    assert networks("postgres") == networks("redis") == {"data"}
    assert "data" not in networks("edge") and "data" not in networks("frontend")
    assert networks("api") == {"front", "data"}
    assert networks("migrate") == {"data"}
    assert "--requirepass" in str(SERVICES["redis"]["command"])
    assert "REDIS_PASSWORD is required" in str(SERVICES["redis"]["command"])


def test_bundled_data_stores_are_optional_and_urls_overridable() -> None:
    """Decision D14 option (a): external TLS data stores must be usable
    without editing the compose file."""
    assert SERVICES["postgres"]["profiles"] == SERVICES["redis"]["profiles"] == ["bundled"]
    env = SERVICES["api"]["environment"]
    assert env["DATABASE_URL"].startswith("${DATABASE_URL:-")
    assert env["REDIS_URL"].startswith("${REDIS_URL:-")
    assert SERVICES["migrate"]["environment"]["MIGRATIONS_DATABASE_URL"].startswith(
        "${MIGRATIONS_DATABASE_URL:-"
    )
    for name in ("api", "worker"):
        assert SERVICES[name]["depends_on"]["redis"]["required"] is False
    assert SERVICES["migrate"]["depends_on"]["postgres"]["required"] is False


def test_database_becomes_healthy_only_after_the_init_scripts() -> None:
    # The init-time server listens on the Unix socket only: check over TCP.
    assert "pg_isready -h 127.0.0.1" in str(SERVICES["postgres"]["healthcheck"]["test"])


def test_operational_commands_never_rerun_the_migration_job() -> None:
    for name in ("deployment.md", "runbook.md", "backup-restore.md"):
        text = (ROOT / "docs" / "operations" / name).read_text(encoding="utf-8")
        assert "run --rm api" not in text, name
        # Every real `run` command line (shell lines, not prose) skips dependencies.
        commands = [
            line
            for line in text.splitlines()
            if line.lstrip().startswith(("docker compose", "dc ")) and " run " in line
        ]
        assert all("--no-deps" in line for line in commands), (name, commands)


def test_example_environment_contains_placeholders_only() -> None:
    example = (DEPLOY / "production.env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if any(word in key for word in ("PASSWORD", "SECRET", "TOKEN")):
            assert value == "", key


def test_edge_hides_health_endpoints_and_request_uris_from_logs() -> None:
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "@health path /healthz /readyz" in caddy
    for field in ("request>uri", "request>headers>Referer", "resp_headers>Location"):
        assert f"{field} delete" in caddy, field  # link tokens travel in paths
    assert "Strict-Transport-Security" in caddy  # API responses too, not only pages
    next_config = (ROOT / "frontend" / "next.config.ts").read_text(encoding="utf-8")
    assert 'source: "/:kind(c|u)/:token"' in next_config and '"no-referrer"' in next_config
    assert "header_up X-Forwarded-For {remote_host}" in caddy  # never client-supplied


def test_database_roles_are_restricted() -> None:
    script = (DEPLOY / "postgres" / "10-roles.sh").read_text(encoding="utf-8")
    roles = [line for line in script.splitlines() if line.startswith("CREATE ROLE")]
    assert len(roles) == 2
    assert all("NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE" in line for line in roles)
    assert "must be set" in script  # empty passwords are refused
    assert "REVOKE ALL ON DATABASE" in script


def test_production_secrets_file_is_ignored_by_git_and_docker() -> None:
    """deploy/production.env holds real passwords and keys on the host. The
    target repository is public: a plain `git add .` must never pick it up."""
    import shutil
    import subprocess

    git = shutil.which("git")
    assert git is not None
    for path, expected in (
        ("deploy/production.env", 0),  # ignored
        ("frontend/.env.local", 0),
        ("deploy/production.env.example", 1),  # the template is tracked
        (".env.example", 1),
    ):
        result = subprocess.run([git, "check-ignore", "-q", path], cwd=ROOT, check=False)
        assert result.returncode == expected, path
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "deploy/production.env" in dockerignore and "**/.env" in dockerignore
