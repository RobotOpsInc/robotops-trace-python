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
launch environment and every Python process auto-instruments with zero
per-process code — no explicit `init()` call needed:

```sh
export ROBOTOPS_TRACE_AUTOINIT=1              # truthy: 1 / true / yes / on
export ROBOTOPS_OTLP_ENDPOINT=127.0.0.1:4317  # point the exporter at the local carrier
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
