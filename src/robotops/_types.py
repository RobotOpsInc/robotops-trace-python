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

"""Core RobotOps-owned value/handle types and their mapping to OpenTelemetry.

These are deliberately thin: users program against ``robotops`` types and never
have to touch raw OTel objects, but every type maps 1:1 onto an OTel primitive
so the underlying SDK stays in charge of the actual span/context machinery. The
enum names/semantics mirror the C++ core (ROB-419); Python uses ``UPPER`` per
PEP 8 enum convention.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from opentelemetry import trace as _otel_trace
from opentelemetry.trace.status import Status as _OTelStatus
from opentelemetry.trace.status import StatusCode as _OTelStatusCode

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExporter
    from opentelemetry.trace import Span as _OTelSpan

# Attribute values accepted on spans/events. Mirrors OTel's allowed scalar set
# (plus homogeneous sequences thereof); passed straight through to OTel.
AttributeValue = Any


class SpanKind(enum.Enum):
    """OTLP span kind. Values match the OTLP wire enum (mirrors C++ ``SpanKind``)."""

    INTERNAL = 1
    SERVER = 2
    CLIENT = 3
    PRODUCER = 4
    CONSUMER = 5


class StatusCode(enum.Enum):
    """OTLP status code. Values match the OTLP wire enum (mirrors C++ ``StatusCode``)."""

    UNSET = 0
    OK = 1
    ERROR = 2


_KIND_TO_OTEL: dict[SpanKind, _otel_trace.SpanKind] = {
    SpanKind.INTERNAL: _otel_trace.SpanKind.INTERNAL,
    SpanKind.SERVER: _otel_trace.SpanKind.SERVER,
    SpanKind.CLIENT: _otel_trace.SpanKind.CLIENT,
    SpanKind.PRODUCER: _otel_trace.SpanKind.PRODUCER,
    SpanKind.CONSUMER: _otel_trace.SpanKind.CONSUMER,
}

_STATUS_TO_OTEL: dict[StatusCode, _OTelStatusCode] = {
    StatusCode.UNSET: _OTelStatusCode.UNSET,
    StatusCode.OK: _OTelStatusCode.OK,
    StatusCode.ERROR: _OTelStatusCode.ERROR,
}


def to_otel_kind(kind: SpanKind) -> _otel_trace.SpanKind:
    """Map a RobotOps ``SpanKind`` to the OTel one."""
    return _KIND_TO_OTEL[kind]


def to_otel_status(code: StatusCode, message: str = "") -> _OTelStatus:
    """Map a RobotOps ``StatusCode`` (+ message) to an OTel ``Status``."""
    return _OTelStatus(_STATUS_TO_OTEL[code], description=message or None)


@dataclass(frozen=True)
class SpanContext:
    """W3C-style span identity: 128-bit trace id + 64-bit span id + flags.

    Mirrors the C++ ``SpanContext`` value type. Ids are stored as ints (OTel's
    native representation); ``trace_id_hex`` / ``span_id_hex`` give the W3C hex.
    """

    trace_id: int = 0
    span_id: int = 0
    trace_flags: int = 0
    remote: bool = False

    @property
    def valid(self) -> bool:
        """True when both ids are non-zero."""
        return self.trace_id != 0 and self.span_id != 0

    @property
    def sampled(self) -> bool:
        """True when the sampled flag (bit 0) is set."""
        return bool(self.trace_flags & 0x01)

    def trace_id_hex(self) -> str:
        """32 lowercase hex chars."""
        return format(self.trace_id, "032x")

    def span_id_hex(self) -> str:
        """16 lowercase hex chars."""
        return format(self.span_id, "016x")


def _span_context_from_otel(ctx: _otel_trace.SpanContext) -> SpanContext:
    return SpanContext(
        trace_id=ctx.trace_id,
        span_id=ctx.span_id,
        trace_flags=int(ctx.trace_flags),
        remote=bool(ctx.is_remote),
    )


class Span:
    """Non-owning handle onto an OTel span.

    All mutators are no-op-safe: when the handle wraps no span, or an invalid /
    non-recording span (SDK disabled, no active span), the methods do nothing
    and never raise. Mirrors the C++ ``Span`` handle.
    """

    __slots__ = ("_span",)

    def __init__(self, otel_span: _OTelSpan | None = None) -> None:
        self._span = otel_span

    @property
    def valid(self) -> bool:
        """True when wrapping a span with a valid span context."""
        if self._span is None:
            return False
        return bool(self._span.get_span_context().is_valid)

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        if self._span is None:
            return
        try:
            self._span.set_attribute(key, value)
        except Exception:  # pragma: no cover - defensive; never raise into user code
            pass

    def add_event(
        self, name: str, attributes: dict[str, AttributeValue] | None = None
    ) -> None:
        if self._span is None:
            return
        try:
            self._span.add_event(name, attributes=attributes)
        except Exception:  # pragma: no cover - defensive
            pass

    def set_status(self, code: StatusCode, message: str = "") -> None:
        if self._span is None:
            return
        try:
            self._span.set_status(to_otel_status(code, message))
        except Exception:  # pragma: no cover - defensive
            pass

    def context(self) -> SpanContext:
        """Snapshot of this span's identity (invalid when wrapping no span)."""
        if self._span is None:
            return SpanContext()
        return _span_context_from_otel(self._span.get_span_context())


@dataclass
class Config:
    """Tracer configuration. Every field has an env override; env always wins so
    a fleet can retune or kill-switch without a redeploy (mirrors C++ ``Config``).
    """

    # env ROBOTOPS_SERVICE_NAME overrides.
    service_name: str = "unknown_service"
    # env ROBOTOPS_OTLP_ENDPOINT overrides; "/v1/traces" is appended.
    # Default is a Unix-domain socket (ROB-441): "unix:///abs/path" selects the
    # UDS transport, "http://host:port" selects the TCP loopback fallback. Both
    # POST a protobuf body to /v1/traces.
    endpoint: str = "unix:///run/robotops/trace.sock"
    # env ROBOTOPS_TRACE_ENABLED=0 hard-disables (the runtime kill switch).
    enabled: bool = True
    # Extra resource attributes merged with service.name.
    resource_attributes: dict[str, str] = field(default_factory=dict)
    # env ROBOTOPS_TRACE_MAX_QUEUE — bounded queue capacity (drop when full).
    max_queue: int = 2048
    # env ROBOTOPS_TRACE_MAX_BATCH — max spans per export call.
    max_batch: int = 512
    # env ROBOTOPS_TRACE_SCHEDULE_DELAY_MS — periodic flush interval.
    schedule_delay_ms: int = 5000
    # env ROBOTOPS_TRACE_EXPORT_TIMEOUT_MS — bounded per-export network timeout.
    # Caps how long a single OTLP/HTTP export call may block the background
    # export thread when the carrier is slow/unreachable, so a stuck agent can
    # never wedge force_flush()/shutdown() (the zero-robot-impact invariant).
    export_timeout_ms: int = 10000
    # None => default OTLP/HTTP-protobuf exporter built from `endpoint`.
    # Inject an OTel SpanExporter (e.g. InMemorySpanExporter) for tests.
    exporter: SpanExporter | None = None
