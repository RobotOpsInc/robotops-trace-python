"""Unix-domain-socket transport tests for robotops-trace (ROB-441).

Proves the shared transport contract end-to-end against the *real* OTLP/HTTP
exporter (no mocks):

1. **UDS round-trip.** A throwaway HTTP server bound to a Unix-domain socket
   accepts ``POST /v1/traces`` -> 200; the SDK is pointed at ``unix:///tmp/...``;
   spans emitted via ``@trace``/``span()`` + ``force_flush`` arrive over the
   socket as a decodable OTLP protobuf ``ExportTraceServiceRequest`` carrying the
   span we emitted.
2. **TCP fallback.** The same capture server over TCP (``http://127.0.0.1:port``)
   still receives the OTLP protobuf — the legacy transport is unchanged.
3. **Zero-robot-impact when the socket is absent.** Pointed at a non-existent
   socket, N traced calls run at ~no-op speed, nothing propagates to the caller,
   and ``force_flush()``/``shutdown()`` stay bounded.
4. **Endpoint parsing / default.** ``Config().endpoint`` defaults to the UDS
   socket; the ``unix://``/``http://`` helpers map to the right transport URL.
"""

from __future__ import annotations

import http.server
import os
import socketserver
import tempfile
import threading
import time
from collections.abc import Iterator

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

import robotops
from robotops._uds import socket_path_from_endpoint, uds_traces_endpoint

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
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    robotops.shutdown()
    yield
    robotops.shutdown()


# --- capture HTTP server (shared by the UDS + TCP transports) ----------------


class _Captured:
    """Thread-safe sink for received OTLP requests."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, bytes]] = []
        self.event = threading.Event()


def _make_handler(captured: _Captured) -> type[http.server.BaseHTTPRequestHandler]:
    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            captured.requests.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()
            captured.event.set()

        def log_message(self, *args: object) -> None:  # silence + avoid UDS addr fmt
            pass

    return _Handler


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def _decode_span_names(body: bytes) -> set[str]:
    req = ExportTraceServiceRequest()
    req.ParseFromString(body)
    names: set[str] = set()
    for rs in req.resource_spans:
        for ss in rs.scope_spans:
            for sp in ss.spans:
                names.add(sp.name)
    return names


def _emit_spans() -> None:
    @robotops.trace
    def grasp() -> int:
        with robotops.span("inner") as s:
            s.set_attribute("phase", "pre-grasp")
        return 1

    grasp()


# --- 1: real UDS round-trip --------------------------------------------------


def test_uds_round_trip_delivers_otlp_protobuf() -> None:
    captured = _Captured()
    tmpdir = tempfile.mkdtemp(prefix="rob441-")
    sock_path = os.path.join(tmpdir, "trace.sock")

    server = _UnixHTTPServer(sock_path, _make_handler(captured))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        robotops.init(
            robotops.Config(
                service_name="uds-node",
                endpoint=f"unix://{sock_path}",
                schedule_delay_ms=10,
                export_timeout_ms=2000,
            )
        )
        _emit_spans()
        assert robotops.force_flush(timeout=5.0)

        assert captured.event.wait(timeout=5.0), "no request arrived over the UDS"
        path, body = captured.requests[0]
        assert path == "/v1/traces"
        assert len(body) > 0
        names = _decode_span_names(body)
        assert any(n.endswith("grasp") for n in names), names
        assert "inner" in names, names
        print(
            f"\n[uds] delivered {len(body)} protobuf bytes over {sock_path} "
            f"to POST {path}; span names={sorted(names)}"
        )
    finally:
        robotops.shutdown()
        server.shutdown()
        server.server_close()


# --- 2: TCP fallback still works --------------------------------------------


def test_tcp_fallback_delivers_otlp_protobuf() -> None:
    captured = _Captured()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(captured))
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        robotops.init(
            robotops.Config(
                service_name="tcp-node",
                endpoint=f"http://127.0.0.1:{port}",
                schedule_delay_ms=10,
                export_timeout_ms=2000,
            )
        )
        _emit_spans()
        assert robotops.force_flush(timeout=5.0)

        assert captured.event.wait(timeout=5.0), "no request arrived over TCP"
        path, body = captured.requests[0]
        assert path == "/v1/traces"
        names = _decode_span_names(body)
        assert any(n.endswith("grasp") for n in names), names
        assert "inner" in names, names
        print(
            f"\n[tcp] delivered {len(body)} protobuf bytes over 127.0.0.1:{port} "
            f"to POST {path}; span names={sorted(names)}"
        )
    finally:
        robotops.shutdown()
        server.shutdown()
        server.server_close()


# --- 3: zero-robot-impact when the socket is absent --------------------------


def _drive(n: int) -> float:
    @robotops.trace
    def work(x: int) -> int:
        return x * 2

    start = time.perf_counter()
    for i in range(n):
        assert work(i) == i * 2
        with robotops.span("loop") as s:
            s.set_attribute("i", i)
    return time.perf_counter() - start


def test_uds_socket_absent_is_zero_impact() -> None:
    """Socket missing (agent down) ⇒ export best-effort on the bg thread, app
    stays fast, nothing propagates, flush/shutdown bounded."""
    tmpdir = tempfile.mkdtemp(prefix="rob441-")
    sock_path = os.path.join(tmpdir, "does-not-exist.sock")
    assert not os.path.exists(sock_path)

    export_timeout_s = 1.5
    robotops.init(
        robotops.Config(
            service_name="absent-node",
            endpoint=f"unix://{sock_path}",
            schedule_delay_ms=10,
            export_timeout_ms=int(export_timeout_s * 1000),
        )
    )

    n = 2000
    drive_err: BaseException | None = None
    try:
        elapsed = _drive(n)
    except BaseException as exc:  # noqa: BLE001 - asserting nothing escapes
        drive_err = exc
        elapsed = float("inf")

    assert drive_err is None, f"tracing leaked an exception: {drive_err!r}"
    assert elapsed < export_timeout_s, (
        f"{n} calls took {elapsed:.3f}s, >= one export timeout — calls are blocking"
    )
    assert elapsed < 2.0

    t0 = time.perf_counter()
    flushed = robotops.force_flush(timeout=0.3)
    flush_elapsed = time.perf_counter() - t0
    assert flush_elapsed < 2.0, f"force_flush hung: {flush_elapsed:.3f}s"

    t0 = time.perf_counter()
    robotops.shutdown(timeout=1.0)
    shutdown_elapsed = time.perf_counter() - t0
    assert shutdown_elapsed < 2.5, f"shutdown hung: {shutdown_elapsed:.3f}s"

    print(
        f"\n[uds-absent] {n} traced calls={elapsed * 1000:.1f}ms "
        f"force_flush(0.3s)={flush_elapsed * 1000:.1f}ms (drained={flushed}) "
        f"shutdown={shutdown_elapsed * 1000:.1f}ms"
    )


# --- 4: endpoint parsing / default -------------------------------------------


def test_default_endpoint_is_uds() -> None:
    assert robotops.Config().endpoint == "unix:///run/robotops/trace.sock"


def test_socket_path_extraction() -> None:
    assert (
        socket_path_from_endpoint("unix:///run/robotops/trace.sock")
        == "/run/robotops/trace.sock"
    )


def test_uds_traces_endpoint_encoding() -> None:
    url = uds_traces_endpoint("/run/robotops/trace.sock")
    assert url == "http+unix://%2Frun%2Frobotops%2Ftrace.sock/v1/traces"


def test_env_endpoint_overrides_both_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    # unix:// via env wins over a Config default.
    monkeypatch.setenv("ROBOTOPS_OTLP_ENDPOINT", "unix:///tmp/from-env.sock")
    from robotops._provider import _resolve_config

    cfg = _resolve_config(robotops.Config(endpoint="http://127.0.0.1:4318"))
    assert cfg.endpoint == "unix:///tmp/from-env.sock"

    monkeypatch.setenv("ROBOTOPS_OTLP_ENDPOINT", "http://10.0.0.5:4318")
    cfg2 = _resolve_config(robotops.Config())
    assert cfg2.endpoint == "http://10.0.0.5:4318"
