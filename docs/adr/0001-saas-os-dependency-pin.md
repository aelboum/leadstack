# ADR-0001: SaaS-OS dependency pin and upgrade process

Status: Accepted (approved as decision D3, 2026-10-09)

## Context

LeadStack is an independent product that consumes SaaS-OS
(https://github.com/aelboum/saas-os) as an external package. SaaS-OS's own
ADR-0015 (Accepted) defines the supported consumption model: a versioned
Python package named `saas-os`, consumed initially as a Git dependency
pinned to an exact commit SHA rather than a mutable tag. SaaS-OS has no
release tags, no GitHub releases and no package index as of 2026-10-09, and
the repository is private.

## Decision

- LeadStack declares exactly one SaaS-OS requirement in `pyproject.toml`:

  `saas-os @ git+https://github.com/aelboum/saas-os.git@b126f1ad0c9cf4173ada15e20b14850d9e600c14`

  This is the head of SaaS-OS `main` at the time of the bootstrap audit
  (commit dated 2026-09-26).
- `uv.lock` records the same commit and is the authoritative resolution.
  CI installs with `uv sync --locked` and fails if the lock is stale.
- The dependency URL never carries credentials. Local development
  authenticates through the developer's Git credential helper. CI uses a
  read-only `SAAS_OS_READ_TOKEN` secret injected as process-scoped Git
  configuration (`.github/workflows/ci.yml`).
- Editable installs, local-path installs and branch references of SaaS-OS
  are not permitted. `tests/unit/test_dependency_pin.py` asserts the pin,
  the lock, the installed commit, a non-editable install, site-packages
  resolution, and credential-free manifests.
- No SaaS-OS source code is copied into this repository.

## Upgrade process

1. Select a SaaS-OS commit (or a release tag's commit, once tags exist) and
   review its changes: migrations, public interfaces LeadStack uses
   (see `docs/architecture/saas-os-integration.md`), security-relevant
   changes and dependency changes.
2. Change the SHA in `pyproject.toml`, `APPROVED_PIN` in
   `tests/unit/test_dependency_pin.py`, and this ADR's decision section, in
   one reviewed change. Run `uv lock`.
3. Run the full gate: unit, integration (which applies SaaS-OS migrations
   then LeadStack's to a fresh database), pyright, import contracts,
   pip-audit, detect-secrets.
4. Deploy order: install the new package, `leadstack-migrate upgrade`
   (SaaS-OS history first, then LeadStack's), then restart the app and worker.
5. Record the change and its compatibility notes in the change report.

A pin change is never made silently or as a side effect of another task.

## Alternatives considered

- Mutable tag or branch reference: rejected; not reproducible and open to
  tag mutation.
- Vendoring or a submodule: rejected; forbidden by the product/platform
  boundary and by SaaS-OS ADR-0015.
- Private package index: not available; SaaS-OS has not adopted one.

## Consequences

- Builds need read access to a private repository; a missing credential
  fails dependency resolution, with no fallback.
- SaaS-OS is pre-1.0 and makes no interface-stability promise, so every
  upgrade must be reviewed against the interfaces LeadStack uses.
- `saas-os` itself is not on PyPI, so pip-audit cannot audit it directly;
  its dependencies are audited through the lock.

## Security implications

Pinning to an immutable SHA prevents tag-mutation supply-chain substitution.
The read-only CI credential must be scoped to Contents: read on the SaaS-OS
repository only.

## Reversal

Moving to a tag or a package index later changes only how the dependency is
declared; the import surface (`api.*`, `core.*`, `infra.*`,
`control_plane.*`) stays the same.
