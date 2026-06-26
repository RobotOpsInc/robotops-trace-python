# robotops-trace

[![CI](https://github.com/RobotOpsInc/robotops-trace-python/actions/workflows/ci.yml/badge.svg)](https://github.com/RobotOpsInc/robotops-trace-python/actions/workflows/ci.yml)

**The Python tracing SDK core for RobotOps.** This is the *SDK* half of the
RobotOps **"SDK + carrier"** distributed-tracing model: the SDK produces spans
in-process; a local **carrier** (the `robot_agent` / OTLP collector) receives
them over OTLP and forwards them off-robot. It is OpenTelemetry-compatible and
has **no ROS dependency**.

> **Status: scaffold (ROB-419).** The public API is in place and import-safe,
> but the bodies are no-ops. Real tracing — contextvars propagation, async
> capture/restore, OTLP exporter wiring — lands in **ROB-420**. Do not expect
> spans to be emitted yet.

## Install

```sh
pip install robotops-trace
```

- **Package name:** `robotops-trace` (PyPI) - RobotOps' first PyPI package.
- **Import name:** `robotops`.

## Usage

```python
import robotops

# Explicit initialization (override path).
robotops.init(endpoint="127.0.0.1:4317", service_name="path_planner")

# Decorate a function → one span per call.
@robotops.trace
def plan_path(start, goal):
    ...

# Or scope a span around a block.
with robotops.span("execute_trajectory", controller="joint_traj"):
    ...
```

### Auto-init (env-default)

Following the Datadog `-javaagent` model, you set tracing on **once** in the
launch environment and every process auto-instruments with zero per-process
code — no explicit `init()` call needed:

```sh
export ROBOTOPS_TRACE_AUTOINIT=1            # auto-import hook turns tracing on
export ROBOTOPS_OTLP_ENDPOINT=127.0.0.1:4317  # point the exporter at the local carrier
```

Processes launched **outside** that environment still work by calling
`robotops.init()` explicitly. (Auto-init mechanics are ROB-421.)

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
