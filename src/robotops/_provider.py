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

"""Tracer lifecycle + global provider state.

Holds the singleton OTel ``TracerProvider`` (Resource + processor + exporter)
behind ``init`` / ``shutdown`` / ``force_flush``, all idempotent and never
raising into user code. When the SDK is disabled or uninitialized, the tracer
is a no-op so spans cost almost nothing and export nothing.
"""

from __future__ import annotations

import logging
import os
import threading

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.trace import NoOpTracer, Tracer

from ._types import Config
from ._version import __version__

_logger = logging.getLogger("robotops.trace")

_NOOP_TRACER: Tracer = NoOpTracer()

# Global state, guarded by ``_lock``. ``_provider`` is the live SDK provider (or
# None when disabled/uninitialized); ``_tracer`` is what callers actually use.
_lock = threading.Lock()
_provider: TracerProvider | None = None
_tracer: Tracer = _NOOP_TRACER
_initialized = False


def _env_bool(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "off", "no", ""}


def _resolve_config(config: Config | None) -> Config:
    """Build the effective Config: explicit values, then env overrides (env wins)."""
    cfg = config if config is not None else Config()

    service_name = os.environ.get("ROBOTOPS_SERVICE_NAME")
    if service_name is not None:
        cfg.service_name = service_name

    endpoint = os.environ.get("ROBOTOPS_OTLP_ENDPOINT")
    if endpoint is not None:
        cfg.endpoint = endpoint

    enabled = os.environ.get("ROBOTOPS_TRACE_ENABLED")
    if enabled is not None:
        cfg.enabled = _env_bool(enabled)

    for env_name, attr in (
        ("ROBOTOPS_TRACE_MAX_QUEUE", "max_queue"),
        ("ROBOTOPS_TRACE_MAX_BATCH", "max_batch"),
        ("ROBOTOPS_TRACE_SCHEDULE_DELAY_MS", "schedule_delay_ms"),
        ("ROBOTOPS_TRACE_EXPORT_TIMEOUT_MS", "export_timeout_ms"),
    ):
        raw = os.environ.get(env_name)
        if raw is not None:
            try:
                setattr(cfg, attr, int(raw))
            except ValueError:
                _logger.warning("ignoring non-integer %s=%r", env_name, raw)

    return cfg


def _build_processor(cfg: Config) -> SpanProcessor:
    """Default OTLP/HTTP-protobuf export, or a SimpleSpanProcessor around an
    injected exporter (deterministic for tests)."""
    if cfg.exporter is not None:
        return SimpleSpanProcessor(cfg.exporter)

    # Imported lazily so a missing OTLP exporter never breaks ``import robotops``
    # or the test path that injects its own exporter.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    # A bounded network timeout is part of the zero-robot-impact invariant: it
    # caps how long the background export thread can sit on a slow/unreachable
    # carrier, so a stuck agent can't wedge force_flush()/shutdown(). It applies
    # equally to the UDS and TCP transports.
    timeout_s = max(cfg.export_timeout_ms, 1) / 1000

    # Transport selection (shared contract with the C++ exporter + agent):
    #   unix:///abs/path  => OTLP/HTTP-protobuf over a Unix-domain socket (default)
    #   http://host:port  => OTLP/HTTP-protobuf over TCP (loopback fallback)
    # Both POST a protobuf body to /v1/traces.
    if cfg.endpoint.startswith("unix://"):
        from ._uds import build_uds_session, socket_path_from_endpoint, uds_traces_endpoint

        socket_path = socket_path_from_endpoint(cfg.endpoint)
        exporter = OTLPSpanExporter(
            endpoint=uds_traces_endpoint(socket_path),
            timeout=timeout_s,
            session=build_uds_session(),
        )
    else:
        traces_endpoint = cfg.endpoint.rstrip("/") + "/v1/traces"
        exporter = OTLPSpanExporter(
            endpoint=traces_endpoint,
            timeout=timeout_s,
        )
    return BatchSpanProcessor(
        exporter,
        max_queue_size=cfg.max_queue,
        max_export_batch_size=cfg.max_batch,
        schedule_delay_millis=cfg.schedule_delay_ms,
    )


def init(config: Config | None = None) -> None:
    """Initialize the tracer. Idempotent (a second call is a no-op + warn) and
    never raises into user code."""
    global _provider, _tracer, _initialized
    with _lock:
        if _initialized:
            _logger.warning("robotops.init() called again; ignoring")
            return
        try:
            cfg = _resolve_config(config)
            if not cfg.enabled:
                # Kill switch: stay a no-op, export nothing.
                _provider = None
                _tracer = _NOOP_TRACER
                _initialized = True
                return

            resource = Resource.create(
                {"service.name": cfg.service_name, **cfg.resource_attributes}
            )
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(_build_processor(cfg))

            _provider = provider
            _tracer = provider.get_tracer("robotops-trace", __version__)
            _initialized = True
        except Exception:  # pragma: no cover - defensive; init must never throw
            _logger.exception("robotops.init() failed; tracing disabled")
            _provider = None
            _tracer = _NOOP_TRACER
            _initialized = True


def shutdown(timeout: float = 10.0) -> None:
    """Flush + release the provider, bounded by ``timeout`` (seconds).

    Idempotent and never raises. The flush/join runs on a daemon thread so a
    slow/unreachable carrier can never wedge the caller past ``timeout`` — part
    of the zero-robot-impact invariant. (The SDK's own export worker is a daemon
    thread too, so any abandoned in-flight export dies with the process and never
    blocks shutdown.)
    """
    global _provider, _tracer, _initialized
    with _lock:
        provider = _provider
        _provider = None
        _tracer = _NOOP_TRACER
        _initialized = False
    if provider is None:
        return

    def _do_shutdown() -> None:
        try:
            provider.shutdown()
        except Exception:  # pragma: no cover - defensive
            _logger.exception("robotops.shutdown() failed")

    done = threading.Thread(target=_do_shutdown, name="robotops-shutdown", daemon=True)
    done.start()
    done.join(timeout)
    if done.is_alive():
        _logger.warning(
            "robotops.shutdown() exceeded %.3fs; abandoning the in-flight export "
            "to a slow/unreachable carrier (best-effort, never blocks the host)",
            timeout,
        )


def force_flush(timeout: float = 5.0) -> bool:
    """Block until the queue is drained or ``timeout`` (seconds) elapses.

    Returns True if fully drained (or nothing to flush), False otherwise. Never
    raises and never blocks the caller past ``timeout``: the drain runs on a
    daemon thread and is abandoned (best-effort) once the bound elapses, so a
    wedged carrier can't gate the caller. This enforces the zero-robot-impact
    invariant even though the underlying SDK's ``force_flush`` may itself ignore
    the timeout and block on a full-queue drain.
    """
    with _lock:
        provider = _provider
    if provider is None:
        return True

    result: list[bool] = []

    def _do_flush() -> None:
        try:
            result.append(bool(provider.force_flush(timeout_millis=int(timeout * 1000))))
        except Exception:  # pragma: no cover - defensive
            _logger.exception("robotops.force_flush() failed")
            result.append(False)

    worker = threading.Thread(target=_do_flush, name="robotops-force-flush", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        _logger.warning(
            "robotops.force_flush() exceeded %.3fs; returning best-effort (the "
            "background drain to a slow/unreachable carrier continues off-thread)",
            timeout,
        )
        return False
    return result[0] if result else False


def get_tracer() -> Tracer:
    """The active tracer (a no-op tracer when disabled/uninitialized)."""
    with _lock:
        return _tracer


def is_initialized() -> bool:
    with _lock:
        return _initialized
