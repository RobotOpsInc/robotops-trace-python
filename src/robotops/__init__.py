"""RobotOps Trace — Python tracing SDK core.

This is the *SDK* half of the RobotOps "SDK + carrier" distributed-tracing model:
the SDK produces spans in-process; a local carrier (the robot_agent / OTLP
collector) receives them over OTLP and forwards them off-robot.

The public surface mirrors the build-out spec (ROB-433, §2.3):

    import robotops

    robotops.init()                      # explicit init (env-default auto-init is ROB-421)

    @robotops.trace                      # decorate a function → one span per call
    def plan_path(...): ...

    with robotops.span("execute"):       # context-managed span
        ...

.. note::
   This module is a **scaffold** (ROB-419). The bodies are import-safe no-ops so
   that ``import robotops; robotops.init()`` works today and downstream packages
   can build against the API. The real tracing implementation (contextvars
   propagation, async capture/restore, OTLP exporter wiring) lands in ROB-420.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

__all__ = ["__version__", "init", "trace", "span"]

__version__ = "0.1.0"

F = TypeVar("F", bound=Callable[..., Any])

# Tracks whether init() has run. The real implementation will hold the OTel
# TracerProvider / exporter handle here instead of a bare bool.
_initialized = False


def init(
    *,
    endpoint: str | None = None,
    service_name: str | None = None,
    **kwargs: Any,
) -> None:
    """Initialize the tracing SDK.

    In the real implementation this builds an OpenTelemetry ``TracerProvider``
    with an OTLP exporter pointed at the local carrier (default
    ``ROBOTOPS_OTLP_ENDPOINT`` / ``127.0.0.1:4317``) and installs context
    propagation. As a scaffold it is an idempotent no-op so importers and tests
    can call it safely.

    Args:
        endpoint: OTLP endpoint override (e.g. ``"127.0.0.1:4317"``).
        service_name: Logical service / node name attached to spans.
        **kwargs: Reserved for forward-compatible options.
    """
    global _initialized
    # Idempotent: a second init() (e.g. an explicit call after the ROB-421
    # env-default auto-init already ran at startup) is a no-op, so the explicit
    # path stays a safe override and never double-initializes.
    if _initialized:
        return
    # TODO(ROB-420): build the OTel TracerProvider + OTLP exporter, read
    # ROBOTOPS_OTLP_ENDPOINT / ROBOTOPS_TRACE_AUTOINIT, install propagators.
    _initialized = True


def trace(func: F) -> F:
    """Decorator that wraps ``func`` in a span named after the function.

    Scaffold behaviour: transparently calls through to ``func`` (a no-op span),
    preserving signature and metadata.
    """

    @functools.wraps(func)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        # TODO(ROB-420): open a real span keyed on func.__qualname__, record
        # exceptions, set status, and close it around the call.
        with span(func.__qualname__):
            return func(*args, **kwargs)

    return _wrapper  # type: ignore[return-value]


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Context manager that opens a span for the enclosed block.

    Scaffold behaviour: a no-op that yields ``None``. The real implementation
    yields a span handle supporting ``set_attribute`` / events and propagates
    context to child spans (including across async boundaries).

    Args:
        name: Span name.
        **attributes: Span attributes (semantic-convention keys, ROB-430).
    """
    # TODO(ROB-420): start_as_current_span(name, attributes=attributes), yield
    # the live span, record exceptions, and end it.
    yield None
