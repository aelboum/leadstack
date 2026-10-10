"""SaaS-OS is consumed as the built package from the approved, immutable
commit -- not an editable install, not a source checkout, not a moving
branch -- and no dependency URL carries credentials."""

from __future__ import annotations

import json
import re
import sysconfig
import tomllib
from importlib.metadata import distribution
from pathlib import Path

import api
import contracts
import control_plane
import core
import infra

REPO_ROOT = Path(__file__).resolve().parents[2]
APPROVED_PIN = "b126f1ad0c9cf4173ada15e20b14850d9e600c14"  # pragma: allowlist secret (commit SHA)
SAAS_OS_REPO = "https://github.com/aelboum/saas-os.git"
_CREDENTIALS_IN_URL = re.compile(r"[a-z+]+://[^/\s\"'@]+@", re.IGNORECASE)


def _saas_os_requirement() -> str:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    (requirement,) = [d for d in pyproject["project"]["dependencies"] if d.startswith("saas-os")]
    return requirement


def test_pyproject_pins_exact_commit() -> None:
    assert _saas_os_requirement() == f"saas-os @ git+{SAAS_OS_REPO}@{APPROVED_PIN}"


def test_lockfile_resolves_the_same_commit() -> None:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    (package,) = [p for p in lock["package"] if p["name"] == "saas-os"]
    assert package["source"]["git"] == f"{SAAS_OS_REPO}?rev={APPROVED_PIN}#{APPROVED_PIN}"


def test_installed_distribution_is_the_pinned_commit_and_not_editable() -> None:
    raw = distribution("saas-os").read_text("direct_url.json")
    assert raw is not None, "saas-os was not installed from a direct (Git) reference"
    direct_url = json.loads(raw)

    assert direct_url["url"] == SAAS_OS_REPO
    assert direct_url["vcs_info"]["commit_id"] == APPROVED_PIN
    assert not direct_url.get("dir_info", {}).get("editable", False)


def test_platform_packages_resolve_from_site_packages() -> None:
    site_packages = Path(sysconfig.get_paths()["purelib"]).resolve()
    for package in (api, core, infra, control_plane, contracts):
        location = Path(package.__file__ or "").resolve()
        assert location.is_relative_to(site_packages), f"{package.__name__} from {location}"


def test_no_credentials_in_dependency_manifests() -> None:
    for name in ("pyproject.toml", "uv.lock"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert not _CREDENTIALS_IN_URL.search(text), f"credential-bearing URL in {name}"
