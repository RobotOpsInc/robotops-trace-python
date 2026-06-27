# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Unix-domain-socket transport for the OTLP/HTTP exporter (ROB-441).** The
  exporter now speaks OTLP/HTTP (protobuf) over a **Unix-domain socket** as well
  as TCP, selected by the endpoint scheme — the shared transport contract with
  the C++ exporter and the agent receiver:
  - `ROBOTOPS_OTLP_ENDPOINT=unix:///abs/path` (or `Config.endpoint`) ⇒ UDS;
    `http://host:port` ⇒ TCP loopback. Both POST a protobuf body to `/v1/traces`.
  - **Default endpoint changed** from `http://127.0.0.1:4318` to
    `unix:///run/robotops/trace.sock`. Deployments that relied on the TCP default
    must set `ROBOTOPS_OTLP_ENDPOINT=http://127.0.0.1:4318` explicitly.
  - The UDS path mounts a tiny `requests` transport adapter (an `AF_UNIX`
    connection over the `requests`/`urllib3` the OTLP HTTP exporter already
    pulls in) onto the exporter's `requests.Session`; the stock OTel
    `OTLPSpanExporter` still drives the protobuf encode + POST. **No new
    third-party dependency.** The ROB-440 bounded `export_timeout_ms` applies to
    the UDS transport too (the adapter sets the socket timeout).
  - Zero-robot-impact preserved: a missing socket (agent down / not mounted)
    fails best-effort on the `BatchSpanProcessor` background thread and never
    blocks or raises into user code, exactly like the TCP path.
  - New tests prove a **real UDS round-trip** (a throwaway HTTP server bound to a
    Unix socket receives a decodable OTLP `ExportTraceServiceRequest` carrying
    the emitted spans), TCP fallback still delivers, and socket-absent runs at
    ~no-op speed with bounded flush/shutdown. (Container run: 418-byte OTLP
    protobuf delivered over both UDS and TCP; 2000 traced calls in ~90 ms against
    an absent socket.)
- **Zero-Robot-Impact Invariant test suite + hardening (ROB-440).** Empirical
  proof that a tracing failure never crashes, throws into, or blocks the host
  (the ROB-418 invariant):
  - **Fault-injection tests** point the *real* OTLP/HTTP exporter at a dead
    (connection-refused) endpoint **and** a slow one (a socket server that
    accepts then never replies) and assert that N decorated/`span()` calls run
    at ~no-op speed (export rides the `BatchSpanProcessor` background thread, so
    the caller never touches the network), that no tracing exception reaches the
    caller, and that `force_flush()`/`shutdown()` return within their bound even
    while the carrier is wedged. (Container run: 2000 traced calls in ~90 ms vs.
    ~19 ms disabled baseline, against a 1.5 s export timeout.)
  - **Exception-passthrough tests** prove a user exception raised in a `@trace`
    function or a `with robotops.span()` block (sync + async) propagates
    unchanged while the span still closes with ERROR status and a recorded
    `exception` event.
  - **No-op-when-disabled tests** prove `ROBOTOPS_TRACE_ENABLED=0`/uninitialized
    runs the user code, produces no spans, never raises, and still propagates
    user exceptions.
- New `Config.export_timeout_ms` (env `ROBOTOPS_TRACE_EXPORT_TIMEOUT_MS`,
  default 10000): a **bounded per-export network timeout** passed to the
  OTLP/HTTP exporter so a stuck carrier can't keep the background export thread
  (and thus a flush/shutdown) blocked on OpenTelemetry's default 10 s timeout +
  retry backoff.

### Fixed

- `force_flush(timeout=...)` and `shutdown()` are now **hard-bounded** in
  wall-clock time. The underlying OpenTelemetry `BatchProcessor.force_flush`
  ignores its timeout (it blocks on an unconditional full-queue drain) and
  `shutdown` only honours a 30 s default; both could wedge the caller against a
  slow/unreachable carrier. The flush/shutdown now run on a daemon thread joined
  with the requested bound, so the host is never blocked past the timeout (the
  SDK's export worker is itself a daemon, so any abandoned in-flight export dies
  with the process). `shutdown()` gains an optional `timeout` parameter
  (default 10 s).

- Env-default auto-init (ROB-421), the Datadog `-javaagent` model: set
  `ROBOTOPS_TRACE_AUTOINIT=1` once in the launch environment and every Python
  process auto-initializes tracing with zero per-process code. Shipped as a
  `robotops_autoinit.pth` startup hook (force-included into the wheel at the
  site-packages root) that imports the new `robotops._autoinit` module at
  interpreter startup; the module calls `robotops.init()` when the env var is
  truthy (`1`/`true`/`yes`/`on`) and is a no-op otherwise. Importing the hook
  never raises, so auto-init can't break process startup.

### Changed

- `robotops.init()` is now explicitly idempotent (early-returns once
  initialized), so an explicit call after env-default auto-init is a safe
  override and never double-initializes.

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
