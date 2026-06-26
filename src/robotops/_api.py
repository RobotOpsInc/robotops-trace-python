# Copyright 2026 Robot Ops Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The ergonomic public surface: ``@trace``, ``span()``, context carry, W3C.

Intra-process parent/child propagation rides OTel's contextvars-backed current
context — deterministic within a task/thread and automatic across ``await``.
``capture_context`` / ``attach`` carry that context across a thread/executor
boundary (capture on submit, restore on run). The W3C helpers are the
cross-process carrier.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar, overload

from opentelemetry import context as _otel_context
from opentelemetry import trace as _otel_trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from . import _provider
from ._types import Span, SpanContext, SpanKind, _span_context_from_otel

F = TypeVar("F", bound=Callable[..., Any])

_PROPAGATOR = TraceContextTextMapPropagator()


class _SpanContextManager:
    """A span scope usable as both a sync (``with``) and async (``async with``)
    context manager. Wraps OTel's ``start_as_current_span`` so the span is set
    as current for the block and ended (with exception recording) on exit."""

    __slots__ = ("_name", "_kind", "_attributes", "_cm", "_handle")

    def __init__(
        self,
        name: str,
        kind: SpanKind,
        attributes: dict[str, Any] | None,
    ) -> None:
        self._name = name
        self._kind = kind
        self._attributes = attributes
        self._cm: Any = None
        self._handle = Span(None)

    def _open(self) -> Span:
        from ._types import to_otel_kind

        tracer = _provider.get_tracer()
        cm = tracer.start_as_current_span(
            self._name,
            kind=to_otel_kind(self._kind),
            attributes=self._attributes,
            record_exception=True,
            set_status_on_exception=True,
            end_on_exit=True,
        )
        otel_span = cm.__enter__()
        self._cm = cm
        self._handle = Span(otel_span)
        return self._handle

    def _close(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._cm is None:
            return False
        return bool(self._cm.__exit__(exc_type, exc, tb))

    def __enter__(self) -> Span:
        return self._open()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return self._close(exc_type, exc, tb)

    async def __aenter__(self) -> Span:
        return self._open()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return self._close(exc_type, exc, tb)


def span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> _SpanContextManager:
    """Open a span for the enclosing block.

    Works as a sync context manager (``with robotops.span(...) as s``) and an
    async one (``async with robotops.span(...)``). No-op-safe when the SDK is
    disabled/uninitialized.
    """
    return _SpanContextManager(name, kind, attributes)


@overload
def trace(func: F) -> F: ...


@overload
def trace(
    *,
    name: str | None = ...,
    kind: SpanKind = ...,
    attributes: dict[str, Any] | None = ...,
) -> Callable[[F], F]: ...


def trace(
    func: F | None = None,
    *,
    name: str | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> F | Callable[[F], F]:
    """Decorate a function so each call opens a span.

    Usable bare (``@trace``) or parameterized (``@trace(name=..., kind=...,
    attributes=...)``). Supports both ``def`` and ``async def`` — async spans
    nest correctly across ``await``. The default span name is the function's
    qualified name.
    """

    def decorator(fn: F) -> F:
        span_name = name or fn.__qualname__

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async with _SpanContextManager(span_name, kind, attributes):
                    return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with _SpanContextManager(span_name, kind, attributes):
                return fn(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]

    if func is not None:
        return decorator(func)
    return decorator


def current_span() -> Span:
    """Handle to the span active on the calling task/thread (no-op-safe)."""
    return Span(_otel_trace.get_current_span())


def capture_context() -> _otel_context.Context:
    """Snapshot the current context for an executor hop (capture-on-submit)."""
    return _otel_context.get_current()


@contextmanager
def attach(context: _otel_context.Context) -> Iterator[None]:
    """Install a captured context as current for the block (restore-on-run).

    Spans opened while it is active nest under the captured context. Use it on
    the worker side of a thread-pool / executor hop.
    """
    token = _otel_context.attach(context)
    try:
        yield
    finally:
        _otel_context.detach(token)


def inject_traceparent() -> str:
    """Serialize the current context to a W3C ``traceparent`` header.

    Returns ``"00-<32 hex trace id>-<16 hex span id>-<2 hex flags>"``, or an
    empty string when there is no valid current span.
    """
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get("traceparent", "")


def extract_traceparent(traceparent: str) -> SpanContext:
    """Parse a W3C ``traceparent`` into a (remote) ``SpanContext``.

    On a malformed header the returned context is invalid.
    """
    ctx = _PROPAGATOR.extract(carrier={"traceparent": traceparent})
    otel_ctx = _otel_trace.get_current_span(ctx).get_span_context()
    sc = _span_context_from_otel(otel_ctx)
    # Extracted-from-the-wire contexts are remote by definition.
    return SpanContext(
        trace_id=sc.trace_id,
        span_id=sc.span_id,
        trace_flags=sc.trace_flags,
        remote=True,
    )
