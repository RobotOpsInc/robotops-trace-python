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

"""RobotOps Trace — the Python tracing SDK core.

The *SDK* half of the RobotOps "SDK + carrier" distributed-tracing model: the
SDK produces spans in-process; a local **carrier** (the ``robot_agent`` / OTLP
collector) receives them over OTLP/HTTP-protobuf and forwards them off-robot.

The public API is a thin, ergonomic layer over the **opentelemetry-sdk** and
mirrors the proven C++ core (ROB-419) so the two SDKs feel like one product::

    import robotops

    robotops.init()                       # reads env; idempotent

    @robotops.trace                       # span named after the function
    def plan(): ...

    @robotops.trace                       # works on async defs too
    async def execute(): ...

    with robotops.span("control") as s:    # sync context manager
        s.set_attribute("count", 7)
        s.add_event("grasp aborted")
        s.set_status(robotops.StatusCode.ERROR, "no plan")

    async with robotops.span("io"):        # async context manager
        ...

    robotops.shutdown()
"""

from __future__ import annotations

from ._api import (
    attach,
    capture_context,
    current_span,
    extract_traceparent,
    inject_traceparent,
    span,
    trace,
)
from ._provider import force_flush, init, shutdown
from ._types import Config, Span, SpanContext, SpanKind, StatusCode
from ._version import __version__

__all__ = [
    "Config",
    "Span",
    "SpanContext",
    "SpanKind",
    "StatusCode",
    "__version__",
    "attach",
    "capture_context",
    "current_span",
    "extract_traceparent",
    "force_flush",
    "init",
    "inject_traceparent",
    "shutdown",
    "span",
    "trace",
]
