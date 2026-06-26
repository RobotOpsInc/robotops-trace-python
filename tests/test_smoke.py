"""Smoke tests for the robotops-trace scaffold (ROB-419).

These assert the public API is importable and behaves as a safe no-op. They do
NOT test real tracing — that arrives with the implementation in ROB-420.
"""

from __future__ import annotations

import robotops


def test_version() -> None:
    assert robotops.__version__ == "0.1.0"


def test_init_is_safe_and_idempotent() -> None:
    robotops.init()
    robotops.init(endpoint="127.0.0.1:4317", service_name="test-node")


def test_trace_decorator_is_passthrough() -> None:
    @robotops.trace
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5
    # Decorator preserves metadata.
    assert add.__name__ == "add"


def test_span_context_manager_is_noop() -> None:
    with robotops.span("unit-test", component="smoke"):
        result = 21 * 2
    assert result == 42


def test_public_api_surface() -> None:
    for name in ("init", "trace", "span", "__version__"):
        assert hasattr(robotops, name)
