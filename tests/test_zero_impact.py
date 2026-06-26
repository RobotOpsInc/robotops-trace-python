"""Zero-Robot-Impact Invariant tests for robotops-trace (ROB-440).

These tests empirically guarantee the ROB-418 invariant for the Python core: a
tracing failure must **never** crash, throw into, or block the host application.

Three properties are proven against the *real* OTLP/HTTP exporter (not a mock):

1. **Fault injection (non-blocking).** With the exporter pointed at a dead
   (connection-refused) endpoint *and* a slow one (a socket server that accepts
   then never replies), N decorated/contextmanager calls complete at ~no-op
   speed — wall-clock is gated on in-process span creation, never on the
   network. Export rides the ``BatchSpanProcessor`` background thread, so the
   caller is never blocked. No tracing exception escapes to the caller, and
   ``force_flush()`` / ``shutdown()`` return within their bound even though the
   carrier is wedged (this is why a bounded ``export_timeout_ms`` exists).
2. **Exception passthrough.** A user exception raised inside a ``@trace``
   function or a ``with robotops.span()`` block propagates **unchanged** to the
   caller, and the span is still closed with ERROR status and a recorded
   ``exception`` event (asserted via an injected ``InMemorySpanExporter``).
3. **No-op when disabled.** ``ROBOTOPS_TRACE_ENABLED=0`` (or uninitialized) runs
   the user code, produces no spans, and never raises — and a user exception
   still propagates.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode as OTelStatusCode

import robotops

_ENV_VARS = (
    "ROBOTOPS_SERVICE_NAME",
    "ROBOTOPS_OTLP_ENDPOINT",
    "ROBOTOPS_TRACE_ENABLED",
    "ROBOTOPS_TRACE_MAX_QUEUE",
    "ROBOTOPS_TRACE_MAX_BATCH",
    "ROBOTOPS_TRACE_SCHEDULE_DELAY_MS",
    "ROBOTOPS_TRACE_EXPORT_TIMEOUT_MS",
)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset global SDK state + env around every test."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    robotops.shutdown()
    yield
    robotops.shutdown()


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    """An initialized SDK exporting into memory (synchronous SimpleSpanProcessor)."""
    exporter = InMemorySpanExporter()
    robotops.init(robotops.Config(service_name="test-node", exporter=exporter))
    return exporter


def _by_name(exporter: InMemorySpanExporter, suffix: str) -> object:
    for sp in exporter.get_finished_spans():
        if sp.name.endswith(suffix):
            return sp
    raise AssertionError(f"no exported span ending in {suffix!r}")


# --- fault-injection harness -------------------------------------------------


class _SlowSink:
    """A TCP server that accepts connections and then never replies.

    Simulates a *slow*/hung carrier: the OTLP/HTTP export blocks on the socket
    read until the bounded export timeout fires. The connection is held open so
    the exporter's ``requests.post`` actually waits (vs. a fast RST).
    """

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self.port: int = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._held: list[socket.socket] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self._held.append(conn)  # hold open, never respond

    def close(self) -> None:
        self._stop.set()
        for conn in self._held:
            try:
                conn.close()
            except OSError:
                pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)


def _free_then_closed_port() -> int:
    """Bind+close an ephemeral port to obtain one that refuses connections."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


def _drive(n: int) -> float:
    """Run N traced calls (decorator + context manager) and return wall-clock."""

    @robotops.trace
    def work(x: int) -> int:
        return x * 2

    start = time.perf_counter()
    for i in range(n):
        assert work(i) == i * 2
        with robotops.span("loop") as s:
            s.set_attribute("i", i)
    return time.perf_counter() - start


_N = 2000


def test_disabled_baseline_speed() -> None:
    """Baseline: the no-op tracer should run N calls near-instantly."""
    robotops.init(robotops.Config(enabled=False))
    elapsed = _drive(_N)
    print(f"\n[zero-impact] disabled baseline: {_N} calls in {elapsed * 1000:.1f} ms")
    assert elapsed < 2.0


@pytest.mark.parametrize("mode", ["dead", "slow"])
def test_fault_injection_non_blocking(mode: str) -> None:
    """Dead/slow carrier ⇒ app stays fast, no exception escapes, flush/shutdown
    stay bounded. The whole point: a *single* synchronous export would cost up
    to ``export_timeout`` seconds, yet all N calls finish in a fraction of it."""
    export_timeout_ms = 1500
    export_timeout_s = export_timeout_ms / 1000.0

    sink: _SlowSink | None = None
    if mode == "slow":
        sink = _SlowSink()
        endpoint = f"http://127.0.0.1:{sink.port}"
    else:
        endpoint = f"http://127.0.0.1:{_free_then_closed_port()}"

    try:
        robotops.init(
            robotops.Config(
                service_name="fault-node",
                endpoint=endpoint,
                # Tiny flush interval so the bg thread actively tries (and gets
                # stuck on) the dead/slow carrier during the run.
                schedule_delay_ms=10,
                export_timeout_ms=export_timeout_ms,
            )
        )

        # (a) N calls complete at ~no-op speed — NOT gated on the network.
        drive_err: BaseException | None = None
        try:
            elapsed = _drive(_N)
        except BaseException as exc:  # noqa: BLE001 - asserting nothing escapes
            drive_err = exc
            elapsed = float("inf")

        # (b) no tracing exception propagated to the caller.
        assert drive_err is None, f"tracing leaked an exception: {drive_err!r}"

        # (a) wall-clock proof: all N calls in a fraction of ONE export timeout.
        assert elapsed < export_timeout_s, (
            f"{_N} calls took {elapsed:.3f}s, >= one export timeout "
            f"({export_timeout_s:.3f}s) — calls are blocking on the network"
        )
        assert elapsed < 2.0

        # (c) force_flush returns within ~its timeout even though export is wedged.
        t0 = time.perf_counter()
        flushed = robotops.force_flush(timeout=0.3)
        flush_elapsed = time.perf_counter() - t0
        assert flush_elapsed < 2.0, f"force_flush hung: {flush_elapsed:.3f}s"

        # (c) shutdown returns within ~its bound even though export is wedged.
        shutdown_budget = 1.0
        t0 = time.perf_counter()
        robotops.shutdown(timeout=shutdown_budget)
        shutdown_elapsed = time.perf_counter() - t0
        assert shutdown_elapsed < shutdown_budget + 1.5, (
            f"shutdown hung: {shutdown_elapsed:.3f}s"
        )

        print(
            f"\n[zero-impact] mode={mode} endpoint={endpoint} "
            f"export_timeout={export_timeout_s:.1f}s :: "
            f"{_N} traced calls={elapsed * 1000:.1f}ms "
            f"force_flush(0.3s)={flush_elapsed * 1000:.1f}ms (drained={flushed}) "
            f"shutdown={shutdown_elapsed * 1000:.1f}ms"
        )
    finally:
        robotops.shutdown()
        if sink is not None:
            sink.close()


# --- exception passthrough ---------------------------------------------------


class _UserBoom(RuntimeError):
    """A user-domain exception that must propagate unchanged."""


def _exception_event(span: object) -> object:
    events = span.events  # type: ignore[attr-defined]
    for ev in events:
        if ev.name == "exception":
            return ev
    raise AssertionError("span recorded no 'exception' event")


def test_trace_exception_propagates_and_records(
    memory_exporter: InMemorySpanExporter,
) -> None:
    @robotops.trace
    def boom() -> None:
        raise _UserBoom("kaboom")

    with pytest.raises(_UserBoom, match="kaboom"):
        boom()

    assert robotops.force_flush()
    span = _by_name(memory_exporter, "boom")
    assert span.status.status_code is OTelStatusCode.ERROR  # type: ignore[attr-defined]
    ev = _exception_event(span)
    assert ev.attributes["exception.type"].endswith("_UserBoom")  # type: ignore[attr-defined]
    assert ev.attributes["exception.message"] == "kaboom"  # type: ignore[attr-defined]


def test_span_cm_exception_propagates_and_records(
    memory_exporter: InMemorySpanExporter,
) -> None:
    with pytest.raises(_UserBoom, match="no plan"):
        with robotops.span("control") as s:
            s.set_attribute("phase", "pre-grasp")
            raise _UserBoom("no plan")

    assert robotops.force_flush()
    span = _by_name(memory_exporter, "control")
    # The span still closed and recorded the user attribute set before the raise.
    assert span.attributes["phase"] == "pre-grasp"  # type: ignore[attr-defined]
    assert span.status.status_code is OTelStatusCode.ERROR  # type: ignore[attr-defined]
    ev = _exception_event(span)
    assert ev.attributes["exception.type"].endswith("_UserBoom")  # type: ignore[attr-defined]


def test_async_trace_exception_propagates_and_records(
    memory_exporter: InMemorySpanExporter,
) -> None:
    import asyncio

    @robotops.trace
    async def boom_async() -> None:
        await asyncio.sleep(0)
        raise _UserBoom("async-kaboom")

    with pytest.raises(_UserBoom, match="async-kaboom"):
        asyncio.run(boom_async())

    assert robotops.force_flush()
    span = _by_name(memory_exporter, "boom_async")
    assert span.status.status_code is OTelStatusCode.ERROR  # type: ignore[attr-defined]
    _exception_event(span)


# --- no-op when disabled -----------------------------------------------------


def test_disabled_runs_user_code_no_spans_no_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOTOPS_TRACE_ENABLED", "0")
    exporter = InMemorySpanExporter()
    robotops.init(robotops.Config(exporter=exporter))  # env kill-switch wins

    @robotops.trace
    def work() -> int:
        with robotops.span("inner") as s:
            s.set_attribute("k", "v")
            assert not s.valid
        return 42

    assert work() == 42
    assert not robotops.current_span().valid
    assert robotops.force_flush()
    assert exporter.get_finished_spans() == ()


def test_disabled_still_propagates_user_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOTOPS_TRACE_ENABLED", "0")
    robotops.init(robotops.Config())

    @robotops.trace
    def boom() -> None:
        raise _UserBoom("still-raises")

    with pytest.raises(_UserBoom, match="still-raises"):
        boom()

    with pytest.raises(_UserBoom, match="cm-raises"):
        with robotops.span("inner"):
            raise _UserBoom("cm-raises")


def test_uninitialized_runs_user_code_no_raise() -> None:
    @robotops.trace
    def work() -> int:
        with robotops.span("inner"):
            return 7
        return 0

    assert work() == 7
    assert not robotops.current_span().valid
