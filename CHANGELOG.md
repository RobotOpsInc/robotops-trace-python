# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-26

### Added

- Initial package scaffold for `robotops-trace` — the Python tracing SDK core
  (the SDK half of the RobotOps "SDK + carrier" distributed-tracing model).
- **SDK core implementation (ROB-420).** A real, thin ergonomic layer over the
  `opentelemetry-sdk`, mirroring the proven C++ core (ROB-419):
  - Lifecycle: `init(Config | None)` / `shutdown()` / `force_flush()` — idempotent
    global state, never raising into user code, with full env overrides
    (`ROBOTOPS_SERVICE_NAME`, `ROBOTOPS_OTLP_ENDPOINT`, `ROBOTOPS_TRACE_ENABLED`,
    `ROBOTOPS_TRACE_MAX_QUEUE`, `ROBOTOPS_TRACE_MAX_BATCH`,
    `ROBOTOPS_TRACE_SCHEDULE_DELAY_MS`).
  - `@robotops.trace` decorator for sync **and** `async def` functions (async
    spans nest across `await`).
  - `robotops.span()` usable as both a sync (`with`) and async (`async with`)
    context manager, yielding a `Span` handle
    (`set_attribute` / `add_event` / `set_status` / `context`).
  - Context helpers: `current_span()`, `capture_context()` / `attach()` for
    thread-pool / executor context carry (contextvars-backed).
  - W3C Trace Context cross-process carrier: `inject_traceparent()` /
    `extract_traceparent()`.
  - Public types: `Config`, `SpanKind`, `StatusCode`, `Span`, `SpanContext`.
  - Export over **OTLP/HTTP (protobuf)** to `<endpoint>/v1/traces` via a
    `BatchSpanProcessor`; an OTel `SpanExporter` can be injected through
    `Config.exporter` (e.g. `InMemorySpanExporter` for tests).
  - Zero-impact guarantee: disabled (`ROBOTOPS_TRACE_ENABLED=0`) or
    uninitialized => decorator and context managers are no-ops; a failed export
    never raises into user code.
  - 10-case test suite mirroring the C++ core (root/nested/sibling spans,
    attributes/status/events, async nesting, thread-pool carry, W3C round-trip,
    disabled no-op, idempotent lifecycle).
- `pyproject.toml` (hatchling build backend), OpenTelemetry dependencies
  (`opentelemetry-api`, `opentelemetry-sdk`,
  `opentelemetry-exporter-otlp-proto-http` — proto-http specifically, to avoid
  pulling in gRPC), and a `dev` extra (pytest, ruff, mypy).
- CI/CD for the PyPI lane: branch-name validation, matrix CI (Python
  3.10/3.11/3.12), version-check against the PyPI JSON API, and OIDC
  Trusted-Publishing release workflows (PyPI for `main`, TestPyPI for
  `development`).

[Unreleased]: https://github.com/RobotOpsInc/robotops-trace-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RobotOpsInc/robotops-trace-python/releases/tag/v0.1.0
