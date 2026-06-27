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

"""Unix-domain-socket transport for the OTLP/HTTP (protobuf) exporter.

The OTel ``OTLPSpanExporter`` POSTs spans with a ``requests.Session``. To make
it speak OTLP/HTTP over a **Unix-domain socket** (the default carrier transport,
ROB-441) instead of TCP, we mount a tiny ``requests`` transport adapter that
dials an ``AF_UNIX`` socket. No extra third-party dependency is added: the
adapter is ~40 lines built on the ``requests`` + ``urllib3`` that the OTLP HTTP
exporter already pulls in.

Wire contract (must match the C++ exporter + the agent receiver):

* ``ROBOTOPS_OTLP_ENDPOINT=unix:///abs/path`` selects the UDS transport; the
  POST still targets ``/v1/traces`` with a protobuf body, exactly like the TCP
  (``http://host:port``) path.

The socket path is carried to the adapter through an internal
``http+unix://<url-encoded-path>/v1/traces`` endpoint URL: the adapter is mounted
on the ``http+unix://`` prefix, url-decodes the netloc back into the socket path,
and connects there. The HTTP request line still uses the real ``/v1/traces``
path, so the receiver sees a stock OTLP/HTTP POST.
"""

from __future__ import annotations

import socket
from typing import Any
from urllib.parse import quote, unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection
from urllib3.connectionpool import HTTPConnectionPool

# Internal URL scheme the exporter's Session is pointed at. The real OTLP path
# (/v1/traces) rides in the URL path; the url-encoded socket path rides in the
# netloc so the adapter can recover it.
UDS_SCHEME = "http+unix"
_UDS_PREFIX = f"{UDS_SCHEME}://"


class _UnixHTTPConnection(HTTPConnection):  # type: ignore[misc]
    """An ``HTTPConnection`` whose ``connect()`` dials an ``AF_UNIX`` socket."""

    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # urllib3 sets ``self.timeout`` (a float, from the per-request Timeout)
        # before calling connect(); honour it so a hung/slow agent can't wedge
        # the background export thread past the bounded export timeout.
        timeout = self.timeout
        if isinstance(timeout, (int, float)):
            sock.settimeout(timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class _UnixHTTPConnectionPool(HTTPConnectionPool):  # type: ignore[misc]
    """A connection pool that hands out ``AF_UNIX`` connections."""

    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost")
        self._socket_path = socket_path

    def _new_conn(self) -> _UnixHTTPConnection:
        return _UnixHTTPConnection(self._socket_path)


class UnixHTTPAdapter(HTTPAdapter):  # type: ignore[misc]
    """A ``requests`` transport adapter that routes a request to a UDS.

    The target socket path is the url-decoded netloc of the request URL (an
    ``http+unix://`` URL). Supports both the requests >= 2.32 connection hook
    (``get_connection_with_tls_context``) and the older ``get_connection``.
    """

    def _pool_for(self, url: str) -> _UnixHTTPConnectionPool:
        socket_path = unquote(urlparse(url).netloc)
        return _UnixHTTPConnectionPool(socket_path)

    def get_connection_with_tls_context(
        self, request: Any, verify: Any, proxies: Any = None, cert: Any = None
    ) -> _UnixHTTPConnectionPool:
        return self._pool_for(request.url)

    def get_connection(
        self, url: str, proxies: Any = None
    ) -> _UnixHTTPConnectionPool:  # pragma: no cover - requests < 2.32 fallback
        return self._pool_for(url)


def socket_path_from_endpoint(endpoint: str) -> str:
    """Extract the filesystem socket path from a ``unix://`` endpoint.

    ``unix:///run/robotops/trace.sock`` -> ``/run/robotops/trace.sock``.
    """
    return endpoint[len("unix://") :]


def uds_traces_endpoint(socket_path: str) -> str:
    """Build the internal ``http+unix://`` endpoint URL the exporter POSTs to.

    The socket path is url-encoded into the netloc; the OTLP ``/v1/traces`` path
    is preserved so the receiver sees a stock OTLP/HTTP request.
    """
    return f"{_UDS_PREFIX}{quote(socket_path, safe='')}/v1/traces"


def build_uds_session() -> requests.Session:
    """A ``requests.Session`` with the UDS adapter mounted on ``http+unix://``."""
    session = requests.Session()
    session.mount(_UDS_PREFIX, UnixHTTPAdapter())
    return session
