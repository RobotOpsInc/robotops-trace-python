# ROB-420 — Python SDK Core: implementation spec

First cut of the Python tracing SDK core. Mirrors the **proven C++ API shape**
(ROB-419) so the two SDKs feel like one product, but builds on the mature
**opentelemetry-sdk** rather than reinventing span/context machinery.

## Locked decisions

1. **Build on `opentelemetry-sdk`** (it's the de-facto standard, transport-agnostic,
   and far less risky to depend on than otel-cpp). Our public API is a **thin
   ergonomic layer** (`robotops.init`, `@robotops.trace`, `robotops.span`) so users
   never have to touch raw OTel types — but can drop down to OTel if they want.
2. **Export = OTLP/HTTP** to `ROBOTOPS_OTLP_ENDPOINT` (default `http://127.0.0.1:4318`,
   `/v1/traces`), via `opentelemetry-exporter-otlp-proto-http` (HTTP/protobuf — NOT
   gRPC, matching the footprint-light HTTP decision). 
   - **Consequence for ROB-428:** the agent's `/v1/traces` receiver must accept BOTH
     `application/x-protobuf` (Python, here) AND `application/json` (the C++ core's
     hand-rolled exporter). The agent already depends on `opentelemetry-proto` (prost),
     so protobuf decode reuses existing types; JSON is the new path. Document this.
3. **Intra-process propagation = contextvars** (OTel's default context) — deterministic
   within a task/thread, automatic across `await`.
4. **Zero-impact:** disabled or uninitialized => decorator/context-manager are no-ops;
   a failed export never raises into user code.

## Public API (namespace/import `robotops`) — mirror C++ where it makes sense

```python
import robotops

robotops.init()                      # reads env; idempotent
robotops.init(robotops.Config(...))  # explicit override

@robotops.trace                      # span named after the function
def plan(): ...

@robotops.trace(name="grasp", kind=robotops.SpanKind.CLIENT,
                attributes={"robot.action.result": "SUCCEEDED"})
def grasp(): ...

@robotops.trace                      # works on async defs too (nests across await)
async def execute(): ...

with robotops.span("control") as s:  # sync context manager
    s.set_attribute("count", 7)
    s.add_event("grasp aborted")
    s.set_status(robotops.StatusCode.ERROR, "no plan")

async with robotops.span("io"):      # async context manager
    ...

span = robotops.current_span()       # handle; no-op-safe when no active span
ctx  = robotops.capture_context()    # snapshot for an executor hop
with robotops.attach(ctx):           # restore on another thread (capture/restore)
    ...

tp = robotops.inject_traceparent()           # W3C "00-...-...-01" (future carrier + OTel compat)
sc = robotops.extract_traceparent(tp)        # -> SpanContext

robotops.force_flush(timeout=5.0)
robotops.shutdown()
robotops.__version__                 # "0.1.0"
```

### Types
- `robotops.Config` — dataclass: `service_name`, `endpoint`, `enabled`,
  `resource_attributes: dict`, `max_queue`, `max_batch`, `schedule_delay_ms`,
  `exporter` (optional, for tests — inject an OTel `SpanExporter`). Env overrides:
  `ROBOTOPS_SERVICE_NAME`, `ROBOTOPS_OTLP_ENDPOINT`, `ROBOTOPS_TRACE_ENABLED`,
  `ROBOTOPS_TRACE_MAX_QUEUE`, `ROBOTOPS_TRACE_MAX_BATCH`,
  `ROBOTOPS_TRACE_SCHEDULE_DELAY_MS`.
- `robotops.SpanKind` (INTERNAL/SERVER/CLIENT/PRODUCER/CONSUMER) and
  `robotops.StatusCode` (UNSET/OK/ERROR) — our enums, mapped to OTel internally
  (mirror the C++ enum names/semantics; Python uses UPPER per PEP8 enums).
- `robotops.Span` — thin handle wrapping the OTel span: `set_attribute(key, value)`,
  `add_event(name, attributes=None)`, `set_status(code, message="")`,
  `context() -> SpanContext`, `valid`. No-op-safe when invalid.
- `robotops.SpanContext` — dataclass: `trace_id` (16 bytes/int), `span_id`,
  `trace_flags`, `remote`; `trace_id_hex()`, `span_id_hex()`, `valid`, `sampled`.

## Internals (`src/robotops/`)
- `_provider.py` — build/hold the OTel `TracerProvider` with a `Resource`
  (service.name + resource_attributes), a `BatchSpanProcessor` wrapping the
  `OTLPSpanExporter` (proto-http, endpoint `<endpoint>/v1/traces`). Hold global state;
  `init`/`shutdown`/`force_flush` idempotent. When `enabled` is False or not
  initialized, return a no-op tracer so spans are cheap no-ops.
- `_api.py` — `trace` decorator (sync + async via `inspect.iscoroutinefunction`),
  `span` context manager (sync + async — implement an object with both
  `__enter__/__exit__` and `__aenter__/__aexit__`), `current_span`, `capture_context`
  (`opentelemetry.context.get_current`), `attach` (ctxmanager around
  `context.attach`/`detach`), the W3C helpers via `TraceContextTextMapPropagator`.
- `_types.py` — `Config`, `SpanKind`, `StatusCode`, `Span`, `SpanContext`, mapping
  helpers to/from OTel.
- `__init__.py` — re-export the public surface + `__version__` (single-source the
  version from package metadata or a `_version.py`; keep it matching pyproject 0.1.0).
- Keep `py.typed`.

## Dependencies (update pyproject.toml)
- Runtime: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`,
  `opentelemetry-api`. (Drop the generic `opentelemetry-exporter-otlp` meta-pkg in
  favour of the proto-http one to avoid pulling gRPC.)
- Dev extra already has pytest/ruff/mypy.

## Tests (`tests/`, pytest) — mirror the C++ suite; use OTel `InMemorySpanExporter`
Inject `InMemorySpanExporter` via `Config.exporter` (with a `SimpleSpanProcessor` or a
test-mode provider) and assert on captured spans after `force_flush`.
1. `__version__ == "0.1.0"`.
2. Root span via `@trace` is valid+sampled, no parent.
3. Nested decorators → child inherits trace_id, parent = outer span_id.
4. Sibling spans share trace_id, differ span_id.
5. `with robotops.span(...)` records attributes/status/events into the exported span.
6. **async**: `@trace` on async defs nests correctly across `await`.
7. **thread-pool carry**: `capture_context()` + `attach()` in a worker thread nests the
   worker's span under the captured parent (concurrent.futures.ThreadPoolExecutor).
8. W3C `inject`/`extract` round-trip (remote=True on extract).
9. Disabled (`ROBOTOPS_TRACE_ENABLED=0` / no init) → decorator + context manager are
   no-ops, nothing exported, no raise.
10. `init`/`shutdown` idempotent; `force_flush` drains.

## Verification — IN DOCKER (founder guidance: reproducibility, not host tooling)
The host has Python 3.14 + no opentelemetry; CI targets 3.10–3.12. Verify in a pinned
container, e.g.:
```sh
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -lc \
  "pip install -e '.[dev]' && ruff check . && mypy src && pytest -q"
```
All tests must pass; ruff + mypy clean. Report the REAL container output.

## Out of scope (later)
- rclpy monkey-patch (ROB-423, integrations repo).
- Python auto-import auto-init (ROB-421).
- Semantic-convention key constants (ROB-430, integrations repo).

## Deliverable
Implement on `feature/rob-420-sdk-core-python`. Verify in Docker (above). Update
CHANGELOG.md (0.1.0) + README.md (real examples mirroring the C++ README). Commit
(disclose AI authorship per org policy), push, open a PR into `development`. The repo's
CI (3.10/3.11/3.12 build+ruff+mypy+pytest) is the real gate — report `gh pr checks`
honestly.
