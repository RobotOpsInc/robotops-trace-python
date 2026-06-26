# Open Items

Tracking the manual / cross-team items this scaffold cannot complete itself.

## 1. PyPI Trusted Publisher registration (BLOCKS first release)

**Owner: founder.** The OIDC release workflows (`release.yml` → PyPI,
`release-dev.yml` → TestPyPI) use **Trusted Publishing** and carry **no API
token**. Publishing will fail until a Trusted Publisher is registered manually
in the PyPI project settings.

Required, one-time:

- [ ] Create / confirm the `RobotOpsInc` **PyPI** org account.
- [ ] Reserve the `robotops-trace` project name on PyPI (and TestPyPI).
- [ ] Register a **PyPI** Trusted Publisher:
      owner `RobotOpsInc`, repo `robotops-trace-python`,
      workflow `release.yml`, environment `pypi`.
- [ ] Register a **TestPyPI** Trusted Publisher:
      owner `RobotOpsInc`, repo `robotops-trace-python`,
      workflow `release-dev.yml`, environment `testpypi`.
- [ ] Create the GitHub environments `pypi` and `testpypi` in repo settings
      (optionally with required reviewers).

This blocks the first Python *release*, not the build/CI.

## 2. Branch protection / rulesets

Per spec §2.1, apply the org branch rulesets (`development` accepts
`feature/* | fix/* | task/* | chore/* | <issue#>-*`; `main` accepts only
`development`; no direct push / force-push / deletion). Not enabled by this
scaffold — apply via `github_config/setup-branch-rulesets.sh` when ready.

## 3. Real SDK implementation (ROB-420)

`init()`, `@trace`, and `span()` are import-safe no-op stubs. The OTLP exporter,
contextvars propagation, and async capture/restore land in ROB-420.

## 4. Auto-init mechanics (ROB-421)

The `ROBOTOPS_TRACE_AUTOINIT` / `.pth` / sitecustomize auto-import hook is
documented in the README but not yet implemented.
