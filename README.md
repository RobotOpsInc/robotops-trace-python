# robotops-trace

[![CI](https://github.com/RobotOpsInc/robotops-trace-python/actions/workflows/ci.yml/badge.svg)](https://github.com/RobotOpsInc/robotops-trace-python/actions/workflows/ci.yml)

**The Python tracing SDK core for RobotOps.** This is the *SDK* half of the
RobotOps **"SDK + carrier"** distributed-tracing model: the SDK produces spans
in-process; a local **carrier** (the `robot_agent` / OTLP collector) receives
them over OTLP and forwards them off-robot. It is OpenTelemetry-compatible and
has **no ROS dependency**.

> **Status: v0.1.0 (first core cut, ROB-420).** The SDK core is implemented: the
> `@trace` decorator (sync + async), the `span()` sync/async context manager,
> contextvars-backed parent/child nesting, async/thread-pool context carry, W3C
> `traceparent` inject/extract, and an **OTLP/HTTP (protobuf)** exporter. The
> public API is a thin ergonomic layer over the `opentelemetry-sdk` and mirrors
> the C++ core (ROB-419) so the two SDKs feel like one product.

## Install

```sh
pip install robotops-trace
```

- **Package name:** `robotops-trace` (PyPI) - RobotOps' first PyPI package.
- **Import name:** `robotops`.

Built on `opentelemetry-sdk`; spans are exported over OTLP/HTTP (protobuf) to a
local carrier (the `robot_agent` / an OTLP collector).

## Usage

### Lifecycle + the `@trace` decorator

```python
import robotops

robotops.init()                                  # reads env; idempotent

@robotops.trace                                  # span named after the function
def plan_path(start, goal):
    ...

@robotops.trace(name="grasp", kind=robotops.SpanKind.CLIENT,
                attributes={"robot.action.result": "SUCCEEDED"})
def grasp():
    ...

robotops.shutdown()                              # flush + release
```

`@trace` also wraps `async def` — async spans nest correctly across `await`:

```python
@robotops.trace
async def execute():
    await drive()                                # child spans nest under execute
```

### Scoped spans with attributes, status, and events

`span()` is both a sync **and** an async context manager and yields a `Span`
handle:

```python
with robotops.span("control") as s:
    s.set_attribute("count", 7)
    s.set_attribute("retry", True)
    s.add_event("grasp aborted", {"force_n": 12.0})
    s.set_status(robotops.StatusCode.ERROR, "no plan")

async with robotops.span("io"):                  # async scope
    ...
```

Nesting is automatic and deterministic (contextvars): a span opened while
another is live inherits its `trace_id` and parents under it.

### Async / thread-pool context carry (capture on submit, restore on run)

Context follows `await` automatically, but does **not** cross a thread/executor
boundary on its own. Capture it on the producer and re-attach it on the worker:

```python
from concurrent.futures import ThreadPoolExecutor

def worker(ctx):
    with robotops.attach(ctx):                   # restore on this thread
        with robotops.span("async_work"):        # nests under the captured span
            ...

with robotops.span("producer"):
    ctx = robotops.capture_context()             # snapshot on the calling thread
    with ThreadPoolExecutor() as pool:
        pool.submit(worker, ctx).result()
```

### Cross-process propagation (W3C `traceparent`)

```python
header = robotops.inject_traceparent()           # "00-<trace>-<span>-<flags>"
# ... send `header` over the wire ...
sc = robotops.extract_traceparent(received)      # -> SpanContext (remote=True)
```

### Current span + flush

```python
span = robotops.current_span()                   # no-op-safe handle
robotops.force_flush(timeout=5.0)                # drain the export queue
```

### Custom exporter (e.g. for tests)

```python
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

mem = InMemorySpanExporter()
robotops.init(robotops.Config(service_name="grasp_node", exporter=mem))
# ... open spans ...
robotops.force_flush()
spans = mem.get_finished_spans()                 # inspect exported spans
robotops.shutdown()
```

### Environment variables

Every `Config` field has an env override; **env always wins**, so a fleet can
retune or kill-switch without a redeploy.

| Variable | Effect |
| --- | --- |
| `ROBOTOPS_SERVICE_NAME` | `service.name` resource attribute |
| `ROBOTOPS_OTLP_ENDPOINT` | OTLP base URL; `/v1/traces` is appended (default `http://127.0.0.1:4318`) |
| `ROBOTOPS_TRACE_ENABLED` | `0`/`false`/`off` hard-disables tracing (the runtime kill switch) |
| `ROBOTOPS_TRACE_MAX_QUEUE` | bounded queue capacity (drop when full) |
| `ROBOTOPS_TRACE_MAX_BATCH` | max spans per export call |
| `ROBOTOPS_TRACE_SCHEDULE_DELAY_MS` | periodic flush interval |

```sh
export ROBOTOPS_OTLP_ENDPOINT=http://127.0.0.1:4318
```

### Auto-init (env-default)

Following the Datadog `-javaagent` model, you set tracing on **once** in the
launch environment and every Python process auto-instruments with zero
per-process code — no explicit `init()` call needed:

```sh
export ROBOTOPS_TRACE_AUTOINIT=1                     # truthy: 1 / true / yes / on
export ROBOTOPS_OTLP_ENDPOINT=http://127.0.0.1:4318  # point the exporter at the local carrier
```

**How it works:** the wheel installs a `robotops_autoinit.pth` file into
site-packages. Because the file's single line begins with `import`, Python's
`site` module executes it at interpreter startup; it imports the tiny
`robotops._autoinit` hook **only when `ROBOTOPS_TRACE_AUTOINIT` is set**, and the
hook calls `robotops.init()` when the value is truthy. Importing the hook never
raises, so a process can never fail to start because of auto-init.

Processes launched **outside** that environment (or without the env var) are
unaffected and keep calling `robotops.init()` explicitly — the override path. The
explicit call is idempotent with auto-init, so there is never a double-initialize.

> The `.pth` hook only fires for an **installed** distribution (it must live in a
> site-packages directory). It does not run for a bare source/`-e` checkout that
> doesn't place the `.pth` into a site directory; in that case call
> `robotops.init()` explicitly.

## Development

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest -q          # tests
ruff check .       # lint
mypy               # type check
python -m build    # build sdist + wheel
```

### Branching & releases

- Feature branches (`feature/* | fix/* | task/* | chore/* | <issue#>-*`) merge
  into `development`; `main` accepts only `development`.
- Version source of truth is `pyproject.toml`; the changelog is `CHANGELOG.md`
  (Keep a Changelog). `version-check.yml` enforces a matching changelog entry
  and a version greater than the max published on PyPI.
- **Release** (`release.yml`, from `main`) publishes to **PyPI**;
  **Release Development** (`release-dev.yml`, from `development`) publishes
  pre-releases (`X.Y.ZrcN` / `X.Y.Z.devN`) to **TestPyPI**. Both use
  **PyPI Trusted Publishing (OIDC)** — no API token in secrets.

## ⚠️ One-time setup required before the first release

**PyPI Trusted Publisher registration is a manual, one-time step** and must be
done in the PyPI (and TestPyPI) project settings before any release workflow can
publish successfully:

1. Create / use the `RobotOpsInc` PyPI org account and reserve the
   `robotops-trace` name.
2. Register a **Trusted Publisher** for this repo:
   - Owner: `RobotOpsInc`
   - Repository: `robotops-trace-python`
   - Workflow: `release.yml` (PyPI) and `release-dev.yml` (TestPyPI)
   - Environment: `pypi` (PyPI) / `testpypi` (TestPyPI)
3. Repeat on **TestPyPI** for the pre-release lane.

Until this is done the build succeeds but the publish step will fail. See
`OPEN_ITEMS.md`.

## License

Apache-2.0. See [LICENSE](LICENSE).
