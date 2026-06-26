# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-26

### Added

- Initial package scaffold for `robotops-trace` — the Python tracing SDK core
  (the SDK half of the RobotOps "SDK + carrier" distributed-tracing model).
- Stub public API: `robotops.init()`, the `@robotops.trace` decorator, and the
  `robotops.span()` context manager. Bodies are import-safe no-ops pending the
  real implementation (ROB-420).
- `pyproject.toml` (hatchling build backend), OpenTelemetry dependencies, and a
  `dev` extra (pytest, ruff, mypy).
- CI/CD for the PyPI lane: branch-name validation, matrix CI (Python
  3.10/3.11/3.12), version-check against the PyPI JSON API, and OIDC
  Trusted-Publishing release workflows (PyPI for `main`, TestPyPI for
  `development`).

[Unreleased]: https://github.com/RobotOpsInc/robotops-trace-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RobotOpsInc/robotops-trace-python/releases/tag/v0.1.0
