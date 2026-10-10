"""Architecture boundaries are enforced on every test run, not only in CI."""

from __future__ import annotations

import shutil
import subprocess
import sysconfig
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_import_contracts_hold() -> None:
    executable = shutil.which("lint-imports", path=sysconfig.get_path("scripts"))
    assert executable, "import-linter is not installed (uv sync --locked)"

    result = subprocess.run(
        [executable, "--no-cache"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 kept, 0 broken" in result.stdout
