"""SDK-core tests for robotops-trace (ROB-420).

Mirrors the C++ core's 10-case suite. Spans are captured with OTel's
``InMemorySpanExporter`` injected via ``Config.exporter`` (wired through a
``SimpleSpanProcessor`` so spans land synchronously on span end), and we assert
on the exported ``ReadableSpan`` records after ``force_flush``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode as OTelStatusCode

import robotops

# Env vars that influence init; cleared before every test for isolation.
_ENV_VARS = (
    "ROBOTOPS_SERVICE_NAME",
    "ROBOTOPS_OTLP_ENDPOINT",
    "ROBOTOPS_TRACE_ENABLED",
    "ROBOTOPS_TRACE_MAX_QUEUE",
    "ROBOTOPS_TRACE_MAX_BATCH",
    "ROBOTOPS_TRACE_SCHEDULE_DELAY_MS",
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
    """An initialized SDK exporting into memory."""
    exporter = InMemorySpanExporter()
    robotops.init(robotops.Config(service_name="test-node", exporter=exporter))
    return exporter


def _by_name(exporter: InMemorySpanExporter, suffix: str) -> object:
    for sp in exporter.get_finished_spans():
        if sp.name.endswith(suffix):
            return sp
    raise AssertionError(f"no exported span ending in {suffix!r}")


# 1 ---------------------------------------------------------------------------
def test_version() -> None:
    assert robotops.__version__ == "0.1.0"


# 2 ---------------------------------------------------------------------------
def test_root_span_valid_sampled_no_parent(memory_exporter: InMemorySpanExporter) -> None:
    @robotops.trace
    def root() -> None:
        pass

    root()
    assert robotops.force_flush()

    span = _by_name(memory_exporter, "root")
    assert span.parent is None  # type: ignore[attr-defined]
    assert span.context.trace_id != 0  # type: ignore[attr-defined]
    assert span.context.span_id != 0  # type: ignore[attr-defined]
    assert span.context.trace_flags.sampled  # type: ignore[attr-defined]


# 3 ---------------------------------------------------------------------------
def test_nested_decorators_inherit(memory_exporter: InMemorySpanExporter) -> None:
    @robotops.trace
    def inner() -> None:
        pass

    @robotops.trace
    def outer() -> None:
        inner()

    outer()
    assert robotops.force_flush()

    outer_span = _by_name(memory_exporter, "outer")
    inner_span = _by_name(memory_exporter, "inner")
    assert inner_span.context.trace_id == outer_span.context.trace_id  # type: ignore[attr-defined]
    assert inner_span.parent is not None  # type: ignore[attr-defined]
    assert inner_span.parent.span_id == outer_span.context.span_id  # type: ignore[attr-defined]


# 4 ---------------------------------------------------------------------------
def test_siblings_share_trace_differ_span(memory_exporter: InMemorySpanExporter) -> None:
    @robotops.trace
    def left() -> None:
        pass

    @robotops.trace
    def right() -> None:
        pass

    @robotops.trace
    def parent() -> None:
        left()
        right()

    parent()
    assert robotops.force_flush()

    left_span = _by_name(memory_exporter, "left")
    right_span = _by_name(memory_exporter, "right")
    parent_span = _by_name(memory_exporter, "parent")
    assert left_span.context.trace_id == right_span.context.trace_id  # type: ignore[attr-defined]
    assert left_span.context.span_id != right_span.context.span_id  # type: ignore[attr-defined]
    assert left_span.parent.span_id == parent_span.context.span_id  # type: ignore[attr-defined]
    assert right_span.parent.span_id == parent_span.context.span_id  # type: ignore[attr-defined]


# 5 ---------------------------------------------------------------------------
def test_span_records_attributes_status_events(
    memory_exporter: InMemorySpanExporter,
) -> None:
    with robotops.span("control") as s:
        s.set_attribute("count", 7)
        s.add_event("grasp aborted")
        s.set_status(robotops.StatusCode.ERROR, "no plan")

    assert robotops.force_flush()

    span = _by_name(memory_exporter, "control")
    assert span.attributes["count"] == 7  # type: ignore[attr-defined]
    assert any(e.name == "grasp aborted" for e in span.events)  # type: ignore[attr-defined]
    assert span.status.status_code is OTelStatusCode.ERROR  # type: ignore[attr-defined]
    assert span.status.description == "no plan"  # type: ignore[attr-defined]


# 6 ---------------------------------------------------------------------------
def test_async_trace_nests_across_await(memory_exporter: InMemorySpanExporter) -> None:
    @robotops.trace
    async def child() -> None:
        await asyncio.sleep(0)

    @robotops.trace
    async def parent() -> None:
        await child()

    asyncio.run(parent())
    assert robotops.force_flush()

    parent_span = _by_name(memory_exporter, "parent")
    child_span = _by_name(memory_exporter, "child")
    assert child_span.context.trace_id == parent_span.context.trace_id  # type: ignore[attr-defined]
    assert child_span.parent.span_id == parent_span.context.span_id  # type: ignore[attr-defined]


# 7 ---------------------------------------------------------------------------
def test_thread_pool_context_carry(memory_exporter: InMemorySpanExporter) -> None:
    def worker(ctx: object) -> None:
        with robotops.attach(ctx):  # type: ignore[arg-type]
            with robotops.span("worker"):
                pass

    with robotops.span("producer"):
        ctx = robotops.capture_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(worker, ctx).result()

    assert robotops.force_flush()

    producer_span = _by_name(memory_exporter, "producer")
    worker_span = _by_name(memory_exporter, "worker")
    assert worker_span.context.trace_id == producer_span.context.trace_id  # type: ignore[attr-defined]
    assert worker_span.parent.span_id == producer_span.context.span_id  # type: ignore[attr-defined]


# 8 ---------------------------------------------------------------------------
def test_w3c_inject_extract_roundtrip(memory_exporter: InMemorySpanExporter) -> None:
    with robotops.span("emit") as s:
        traceparent = robotops.inject_traceparent()
        live = s.context()

    extracted = robotops.extract_traceparent(traceparent)
    assert extracted.valid
    assert extracted.remote is True
    assert extracted.trace_id == live.trace_id
    assert extracted.span_id == live.span_id
    assert extracted.sampled  # sampled flag (bit 0) survives the round-trip
    flags = format(extracted.trace_flags, "02x")
    expected = f"00-{extracted.trace_id_hex()}-{extracted.span_id_hex()}-{flags}"
    assert traceparent == expected


# 9 ---------------------------------------------------------------------------
def test_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOTOPS_TRACE_ENABLED", "0")
    exporter = InMemorySpanExporter()
    # env override wins even though Config.enabled defaults True.
    robotops.init(robotops.Config(exporter=exporter))

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


def test_uninitialized_is_noop() -> None:
    # No init() at all → decorator + context manager are safe no-ops.
    @robotops.trace
    def work() -> int:
        with robotops.span("inner"):
            return 7
        return 0

    assert work() == 7
    assert not robotops.current_span().valid


# 10 --------------------------------------------------------------------------
def test_init_shutdown_idempotent_and_flush() -> None:
    exporter = InMemorySpanExporter()
    robotops.init(robotops.Config(exporter=exporter))
    # Second init is a no-op; must not swap the provider.
    robotops.init(robotops.Config(service_name="ignored"))

    with robotops.span("drain"):
        pass

    assert robotops.force_flush()
    assert len(exporter.get_finished_spans()) == 1

    robotops.shutdown()
    robotops.shutdown()  # idempotent
    # After shutdown, spans are no-ops again.
    assert not robotops.current_span().valid
