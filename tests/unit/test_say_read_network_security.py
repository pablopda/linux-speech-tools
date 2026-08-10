"""Hermetic regressions for say-read's DNS-pinned HTTP/render transport."""

import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
from urllib.parse import quote

import pytest

from src.tts import say_read


class FakeRawResponse:
    def __init__(self, body=b"public response", headers=None, status=200, reason="OK"):
        self.body = body
        self.headers = headers or [("Content-Type", "text/plain; charset=utf-8")]
        self.status = status
        self.reason = reason
        self.closed = False

    def getheaders(self):
        return self.headers

    def read(self, size=-1):
        if not self.body:
            return b""
        if size < 0:
            chunk, self.body = self.body, b""
        else:
            chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, response=None):
        self.response = response or FakeRawResponse()
        self.requests = []
        self.closed = False
        self.sock = FakeSocket()

    def request(self, method, target, body=None, headers=None):
        self.requests.append((method, target, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class FakeSocket:
    def __init__(self):
        self.timeouts = []
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "address",
    [
        "100.64.0.1",
        "100.127.255.254",
        "192.88.99.1",
        "192.88.99.2",
        "::ffff:100.64.0.1",
        "::ffff:100.127.255.254",
    ],
)
def test_non_public_ipv4_special_addresses_are_rejected(address):
    assert not say_read._is_public_ip(say_read.ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["224.0.0.1", "ff02::1"])
def test_multicast_addresses_are_explicitly_rejected(address):
    assert not say_read._is_public_ip(say_read.ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "::",
        "::1",
        "100::1",
        "2001:2::1",
        "2001:db8::1",
        "3f00::1",
        "3ffe::1",
        "3fff::1",
        "fc00::1",
        "fe80::1",
        "fec0::1",
        "feff:ffff::1",
        "::127.0.0.1",
        "::ffff:0:127.0.0.1",
        "64:ff9b::10.0.0.1",
        "64:ff9b:1::7f00:1",
        "2002:7f00:1::",
        "2001:0:7f00:1:0:0:f7f7:f7f7",
        "2001:0:5db8:d822:0:0:80ff:fffe",
        "2001:4860::5efe:10.0.0.1",
    ],
)
def test_ipv6_special_or_private_translation_addresses_are_rejected(address):
    assert not say_read._is_public_ip(say_read.ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "93.184.216.34",
        "192.0.0.9",
        "192.0.0.10",
        "2606:2800:220:1:248:1893:25c8:1946",
        "::93.184.216.34",
        "::ffff:93.184.216.34",
        "::ffff:0:93.184.216.34",
        "64:ff9b::93.184.216.34",
        "2002:5db8:d822::",
        "2001:0:5db8:d822:0:0:f7f7:f7f7",
        "2001:1::3",
        "2001:3::1",
        "2001:4:112::1",
        "2001:20::1",
        "2001:30::1",
        "2620:4f:8000::1",
    ],
)
def test_public_native_and_unambiguous_embedded_addresses_remain_allowed(address):
    assert say_read._is_public_ip(say_read.ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "fec0::1",
        "192.88.99.2",
        "3fff::1",
        "::127.0.0.1",
        "::ffff:0:127.0.0.1",
        "64:ff9b::10.0.0.1",
    ],
)
def test_non_public_special_literals_are_rejected_before_resolution(monkeypatch, address):
    monkeypatch.setattr(
        say_read,
        "_bounded_getaddrinfo",
        lambda *args, **kwargs: pytest.fail("rejected literal reached DNS"),
    )

    url_host = "[{}]".format(address) if ":" in address else address
    with pytest.raises(ValueError, match="non-public address"):
        say_read._resolve_public_url("https://{}/article".format(url_host))


@pytest.mark.parametrize(
    "address",
    [
        "fec0::1",
        "192.88.99.2",
        "3fff::1",
        "::127.0.0.1",
        "::ffff:0:127.0.0.1",
        "64:ff9b::10.0.0.1",
    ],
)
def test_non_public_special_resolver_results_are_rejected(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sockaddr = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    monkeypatch.setattr(
        say_read,
        "_bounded_getaddrinfo",
        lambda *args, **kwargs: [
            (
                family,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                sockaddr,
            )
        ],
    )

    with pytest.raises(ValueError, match="resolves to non-public address"):
        say_read._resolve_public_url("https://reader.example/article")


@pytest.mark.parametrize(
    ("family", "address"),
    [
        (socket.AF_INET, "93.184.216.34"),
        (socket.AF_INET6, "2606:2800:220:1:248:1893:25c8:1946"),
        (socket.AF_INET6, "64:ff9b::93.184.216.34"),
    ],
)
def test_public_native_and_translated_resolver_results_remain_allowed(
    monkeypatch, family, address
):
    sockaddr = (address, 443) if family == socket.AF_INET else (address, 443, 0, 0)
    monkeypatch.setattr(
        say_read,
        "_bounded_getaddrinfo",
        lambda *args, **kwargs: [
            (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)
        ],
    )

    _parts, host, addresses = say_read._resolve_public_url(
        "https://reader.example/article"
    )

    assert host == "reader.example"
    assert addresses == (
        (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, sockaddr),
    )


def test_direct_request_uses_single_validated_resolution(monkeypatch):
    """A second, rebound DNS answer must never be consulted by the transport."""
    resolutions = [
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 80))],
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 80))],
    ]
    resolution_calls = []

    def rebinding_resolver(*args, **kwargs):
        answer = resolutions[len(resolution_calls)]
        resolution_calls.append((args, kwargs))
        return answer

    captured = {}
    connection = FakeConnection()

    def open_validated(parts, ascii_host, addresses, timeout, **kwargs):
        captured["host"] = ascii_host
        captured["addresses"] = addresses
        return connection

    monkeypatch.setattr(say_read.socket, "getaddrinfo", rebinding_resolver)
    monkeypatch.setattr(say_read, "_open_pinned_connection", open_validated)

    response = say_read._safe_request_once("http://reader.example/article")
    response.close()

    assert len(resolution_calls) == 1
    assert captured["host"] == "reader.example"
    assert captured["addresses"] == (
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("93.184.216.34", 80)),
    )
    assert connection.requests[0][1] == "/article"
    assert connection.requests[0][3]["Host"] == "reader.example"


def test_socket_connect_uses_exact_validated_sockaddr_without_dns(monkeypatch):
    connected = []

    class FakeSocket:
        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, sockaddr):
            connected.append(sockaddr)

        def close(self):
            pass

    monkeypatch.setattr(say_read.socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr(
        say_read.socket,
        "getaddrinfo",
        lambda *args, **kwargs: pytest.fail("connection attempted a second DNS resolution"),
    )
    address = (
        socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        ("93.184.216.34", 443),
    )

    sock = say_read._connect_validated_address(address, 7)

    assert sock.timeout == 7
    assert connected == [("93.184.216.34", 443)]


def test_https_pinned_connection_keeps_original_sni_and_ca_validation(monkeypatch):
    parts = say_read.urlsplit("https://reader.example/article")
    address = (
        socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        ("93.184.216.34", 443),
    )
    raw_socket = FakeSocket()
    tls_socket = FakeSocket()
    observed = {}

    class FakeContext:
        def wrap_socket(self, sock, server_hostname):
            observed["raw_socket"] = sock
            observed["server_hostname"] = server_hostname
            return tls_socket

    class FakeHTTPConnection:
        def __init__(self, host, port, timeout):
            observed["connection"] = (host, port, timeout)
            self.sock = None

    monkeypatch.setattr(say_read, "_connect_validated_address", lambda *args: raw_socket)

    def make_context():
        observed["verified_context"] = True
        return FakeContext()

    monkeypatch.setattr(say_read, "_default_ssl_context", make_context)
    monkeypatch.setattr(say_read.http.client, "HTTPConnection", FakeHTTPConnection)

    connection = say_read._open_pinned_connection(
        parts, "reader.example", (address,), timeout=11
    )

    assert observed["server_hostname"] == "reader.example"
    assert observed["raw_socket"] is raw_socket
    assert observed["connection"][:2] == ("reader.example", 443)
    assert 0 < observed["connection"][2] <= 11
    assert observed["verified_context"]
    assert connection.sock is tls_socket


def test_safe_get_follows_relative_redirect_through_pinned_requests(monkeypatch):
    requested = []
    first_connection = FakeConnection(
        FakeRawResponse(
            b"",
            [("Location", "/final")],
            status=302,
            reason="Found",
        )
    )
    final_connection = FakeConnection(FakeRawResponse(b"final"))
    responses = [
        say_read._PinnedResponse(first_connection, first_connection.response, "http://reader.example/start"),
        say_read._PinnedResponse(final_connection, final_connection.response, "http://reader.example/final"),
    ]

    def request_once(url, **kwargs):
        requested.append(url)
        return responses.pop(0)

    monkeypatch.setattr(say_read, "_safe_request_once", request_once)

    response = say_read._safe_get("http://reader.example/start", debug=False)
    assert requested == [
        "http://reader.example/start",
        "http://reader.example/final",
    ]
    assert first_connection.closed
    assert b"".join(response.iter_content(64)) == b"final"
    response.close()


def test_safe_get_closes_response_when_status_check_fails(monkeypatch):
    connection = FakeConnection(
        FakeRawResponse(b"unavailable", status=503, reason="Unavailable")
    )
    response = say_read._PinnedResponse(
        connection,
        connection.response,
        "https://reader.example/unavailable",
    )
    monkeypatch.setattr(say_read, "_safe_request_once", lambda *args, **kwargs: response)

    with pytest.raises(Exception, match="503"):
        say_read._safe_get("https://reader.example/unavailable", debug=False)

    assert connection.closed
    assert connection.response.closed


def test_safe_get_shares_one_deadline_across_redirects(monkeypatch):
    observed_deadlines = []
    first = FakeConnection(
        FakeRawResponse(
            b"",
            [("Location", "/final")],
            status=302,
            reason="Found",
        )
    )
    final = FakeConnection(FakeRawResponse(b"done"))
    responses = [
        say_read._PinnedResponse(first, first.response, "https://reader.example/start"),
        say_read._PinnedResponse(final, final.response, "https://reader.example/final"),
    ]

    def request_once(url, **kwargs):
        observed_deadlines.append(kwargs["deadline"])
        return responses.pop(0)

    monkeypatch.setattr(say_read, "_safe_request_once", request_once)

    response = say_read._safe_get("https://reader.example/start", debug=False)
    response.close()

    assert len(observed_deadlines) == 2
    assert observed_deadlines[0] == observed_deadlines[1]


def test_resolver_is_bounded_by_request_deadline(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked_resolver(*args, **kwargs):
        entered.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(say_read.socket, "getaddrinfo", blocked_resolver)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="resolution deadline"):
            say_read._resolve_public_url(
                "https://reader.example/",
                deadline=time.monotonic() + 0.03,
            )
    finally:
        release.set()

    assert entered.is_set()
    assert time.monotonic() - started < 0.5


def test_pinned_connection_caps_address_attempts(monkeypatch):
    attempts = []

    def fail_connect(address, timeout):
        attempts.append((address, timeout))
        raise OSError("unreachable")

    monkeypatch.setattr(say_read, "_connect_validated_address", fail_connect)
    addresses = tuple(
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            ("93.184.216.{}".format(index + 1), 80),
        )
        for index in range(say_read.MAX_URL_ADDRESS_ATTEMPTS + 3)
    )

    with pytest.raises(OSError, match="unreachable"):
        say_read._open_pinned_connection(
            say_read.urlsplit("http://reader.example/"),
            "reader.example",
            addresses,
            timeout=1,
        )

    assert len(attempts) == say_read.MAX_URL_ADDRESS_ATTEMPTS


def test_address_cap_retains_ipv4_and_ipv6_failover(monkeypatch):
    attempts = []
    ipv6_addresses = tuple(
        (
            socket.AF_INET6,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            ("2606:2800:220:1:248:1893:25c8:{:x}".format(index + 1), 80, 0, 0),
        )
        for index in range(say_read.MAX_URL_ADDRESS_ATTEMPTS + 1)
    )
    ipv4_address = (
        socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        ("93.184.216.34", 80),
    )
    successful_socket = FakeSocket()

    def connect(address, timeout):
        attempts.append(address)
        if address[0] == socket.AF_INET:
            return successful_socket
        raise OSError("IPv6 route unavailable")

    monkeypatch.setattr(say_read, "_connect_validated_address", connect)

    connection = say_read._open_pinned_connection(
        say_read.urlsplit("http://reader.example/"),
        "reader.example",
        ipv6_addresses + (ipv4_address,),
        timeout=1,
    )

    assert ipv4_address in attempts
    assert len(attempts) <= say_read.MAX_URL_ADDRESS_ATTEMPTS
    assert connection.sock is successful_socket


def test_response_read_checks_the_request_deadline_before_reading():
    connection = FakeConnection(FakeRawResponse(b"late"))
    response = say_read._PinnedResponse(
        connection,
        connection.response,
        "https://reader.example/slow",
        deadline=time.monotonic() - 1,
    )

    with pytest.raises(TimeoutError, match="deadline exceeded"):
        next(response.iter_content(64))

    assert connection.response.body == b"late"
    response.close()


def test_response_byte_limit_is_strict(monkeypatch):
    class OversizedResponse:
        def iter_content(self, chunk_size):
            yield b"123456789"

    monkeypatch.setattr(say_read, "MAX_URL_BYTES", 5)

    assert say_read._read_capped_bytes(OversizedResponse(), debug=False) == b"12345"


class FakeRoute:
    def __init__(self):
        self.aborted = None
        self.fulfilled = None
        self.continued = False

    def abort(self, reason):
        self.aborted = reason

    def fulfill(self, **kwargs):
        self.fulfilled = kwargs

    def continue_(self):
        self.continued = True


class FakeRequest:
    def __init__(self, url, method="GET", headers=None, body=None):
        self.url = url
        self.method = method
        self.headers = headers or {}
        self.post_data_buffer = body
        self.post_data = None


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_render_state_changing_methods_abort_without_transport(monkeypatch, method):
    transport_calls = []

    def forbidden_transport(*args, **kwargs):
        transport_calls.append((args, kwargs))
        pytest.fail("state-changing render request reached the transport")

    monkeypatch.setattr(say_read, "_safe_request_once", forbidden_transport)
    route = FakeRoute()

    say_read._proxy_render_request(
        route,
        FakeRequest("https://reader.example/action", method=method, body=b"payload"),
        debug=False,
    )

    assert route.aborted == "blockedbyclient"
    assert route.fulfilled is None
    assert not route.continued
    assert transport_calls == []


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_additional_read_only_render_methods_use_pinned_transport(
    monkeypatch, method
):
    raw = FakeRawResponse(b"")
    connection = FakeConnection(raw)
    response = say_read._PinnedResponse(
        connection,
        raw,
        "https://reader.example/resource",
    )
    calls = []

    def request_once(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(say_read, "_safe_request_once", request_once)
    route = FakeRoute()

    say_read._proxy_render_request(
        route,
        FakeRequest("https://reader.example/resource", method=method),
        debug=False,
    )

    assert calls[0][0] == "https://reader.example/resource"
    assert calls[0][1]["method"] == method
    assert route.aborted is None
    assert route.fulfilled["status"] == 200
    assert not route.continued
    assert connection.closed


def test_render_private_subresource_aborts_before_transport(monkeypatch):
    transport_calls = []

    def forbidden_transport(*args, **kwargs):
        transport_calls.append((args, kwargs))
        pytest.fail("private render request reached the connection layer")

    monkeypatch.setattr(say_read, "_open_pinned_connection", forbidden_transport)
    route = FakeRoute()

    say_read._proxy_render_request(
        route,
        FakeRequest("http://169.254.169.254/latest/meta-data/"),
        debug=False,
    )

    assert route.aborted == "blockedbyclient"
    assert route.fulfilled is None
    assert not route.continued
    assert transport_calls == []


def test_render_rebound_subresource_aborts_before_connection(monkeypatch):
    monkeypatch.setattr(
        say_read.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.9", 80)),
        ],
    )
    monkeypatch.setattr(
        say_read,
        "_open_pinned_connection",
        lambda *args, **kwargs: pytest.fail("rebound address reached the connection layer"),
    )
    route = FakeRoute()

    say_read._proxy_render_request(
        route,
        FakeRequest("http://assets.example/private.js"),
        debug=False,
    )

    assert route.aborted == "blockedbyclient"
    assert route.fulfilled is None
    assert not route.continued


def test_public_render_subresource_is_fulfilled_by_pinned_transport(monkeypatch):
    raw = FakeRawResponse(
        b"console.log('safe')",
        [("Content-Type", "text/javascript"), ("Content-Length", "19")],
    )
    connection = FakeConnection(raw)
    response = say_read._PinnedResponse(connection, raw, "https://cdn.example/app.js")
    calls = []

    def request_once(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(say_read, "_safe_request_once", request_once)
    route = FakeRoute()

    say_read._proxy_render_request(
        route,
        FakeRequest("https://cdn.example/app.js", headers={"Accept": "*/*"}),
        debug=False,
    )

    assert calls[0][0] == "https://cdn.example/app.js"
    assert route.aborted is None
    assert not route.continued
    assert route.fulfilled["status"] == 200
    assert route.fulfilled["body"] == b"console.log('safe')"
    assert "Content-Length" not in route.fulfilled["headers"]
    assert connection.closed


def test_render_fulfillment_preserves_duplicate_set_cookie_headers(monkeypatch):
    raw = FakeRawResponse(
        b"ok",
        [
            ("Set-Cookie", "first=1; Path=/; HttpOnly"),
            ("Set-Cookie", "second=2; Path=/; Secure"),
            ("Content-Type", "text/plain"),
        ],
    )
    connection = FakeConnection(raw)
    response = say_read._PinnedResponse(
        connection, raw, "https://reader.example/session"
    )
    monkeypatch.setattr(
        say_read,
        "_safe_request_once",
        lambda *args, **kwargs: response,
    )
    route = FakeRoute()

    say_read._proxy_render_request(
        route,
        FakeRequest("https://reader.example/session"),
        debug=False,
    )

    assert route.fulfilled["headers"]["Set-Cookie"] == (
        "first=1; Path=/; HttpOnly\nsecond=2; Path=/; Secure"
    )
    assert connection.closed


def test_render_fulfillment_strips_browser_network_hint_headers(monkeypatch):
    raw = FakeRawResponse(
        b"<html><body>safe</body></html>",
        [
            ("Content-Type", "text/html"),
            ("Link", "<http://127.0.0.1:9>; rel=preconnect"),
            ("Alt-Svc", 'h2="127.0.0.1:9"'),
            ("NEL", '{"report_to":"private"}'),
            ("Report-To", '{"url":"http://127.0.0.1:9/report"}'),
            ("Reporting-Endpoints", 'private="http://127.0.0.1:9/report"'),
        ],
    )
    connection = FakeConnection(raw)
    response = say_read._PinnedResponse(
        connection, raw, "https://reader.example/article"
    )
    monkeypatch.setattr(
        say_read,
        "_safe_request_once",
        lambda *args, **kwargs: response,
    )
    route = FakeRoute()

    say_read._proxy_render_request(
        route,
        FakeRequest("https://reader.example/article"),
        debug=False,
    )

    fulfilled_headers = {
        name.lower(): value for name, value in route.fulfilled["headers"].items()
    }
    assert fulfilled_headers == {"content-type": "text/html"}
    assert connection.closed


def test_chromium_render_args_force_owned_proxy_and_disable_direct_fallback():
    proxy_url = "http://127.0.0.1:43210"

    args = say_read._chromium_render_args(proxy_url)

    assert "--proxy-server={}".format(proxy_url) in args
    assert "--proxy-bypass-list=<-loopback>" in args
    assert "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1" in args
    assert "--disable-quic" in args
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args


def test_browser_egress_sink_owns_and_releases_its_loopback_listener():
    with say_read._BrowserEgressSink() as sink:
        proxy = say_read.urlsplit(sink.proxy_url)
        connection = socket.create_connection((proxy.hostname, proxy.port), timeout=1)
        connection.close()
        deadline = time.monotonic() + 1.0
        while sink.connection_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sink.connection_count == 1

    with pytest.raises(OSError):
        socket.create_connection((proxy.hostname, proxy.port), timeout=0.1)


def test_render_redirect_to_private_target_is_not_exposed_to_browser(monkeypatch):
    raw = FakeRawResponse(
        b"",
        [("Location", "http://127.0.0.1/admin")],
        status=302,
        reason="Found",
    )
    connection = FakeConnection(raw)
    response = say_read._PinnedResponse(connection, raw, "https://reader.example/start")
    monkeypatch.setattr(say_read, "_safe_request_once", lambda *args, **kwargs: response)
    route = FakeRoute()

    say_read._proxy_render_request(
        route,
        FakeRequest("https://reader.example/start"),
        debug=False,
    )

    assert route.aborted == "blockedbyclient"
    assert route.fulfilled is None
    assert not route.continued
    assert connection.closed


@pytest.mark.parametrize(
    ("content_type", "url", "extractor_name", "payload"),
    [
        ("application/pdf", "https://reader.example/book", "extract_pdf", b"%PDF-safe"),
        ("application/epub+zip", "https://reader.example/book", "extract_epub", b"EPUB-safe"),
    ],
)
def test_remote_binary_document_handoff_is_preserved(
    monkeypatch, content_type, url, extractor_name, payload
):
    class BinaryResponse:
        headers = {"content-type": content_type}

        def iter_content(self, chunk_size):
            yield payload

        def close(self):
            pass

    observed = []

    def extractor(path, debug):
        with open(path, "rb") as handle:
            observed.append(handle.read())
        return "extracted document"

    monkeypatch.setattr(say_read, "_safe_get", lambda requested, debug: BinaryResponse())
    monkeypatch.setattr(say_read, extractor_name, extractor)

    assert say_read.fetch_url(url, render=False, debug=False) == "extracted document"
    assert observed == [payload]


@pytest.mark.parametrize(
    ("final_url", "extractor_name", "payload"),
    [
        ("https://cdn.example/files/book.pdf", "extract_pdf", b"%PDF-safe"),
        ("https://cdn.example/files/book.epub", "extract_epub", b"EPUB-safe"),
    ],
)
def test_redirected_binary_document_uses_final_response_url(
    monkeypatch, final_url, extractor_name, payload
):
    class BinaryResponse:
        url = final_url
        headers = {"content-type": "application/octet-stream"}

        def iter_content(self, chunk_size):
            yield payload

        def close(self):
            pass

    observed = []

    def extractor(path, debug):
        with open(path, "rb") as handle:
            observed.append(handle.read())
        return "redirected document"

    monkeypatch.setattr(say_read, "_safe_get", lambda requested, debug: BinaryResponse())
    monkeypatch.setattr(say_read, extractor_name, extractor)

    assert (
        say_read.fetch_url(
            "https://reader.example/download",
            render=False,
            debug=False,
        )
        == "redirected document"
    )
    assert observed == [payload]


def test_playwright_route_is_installed_before_navigation(monkeypatch):
    # This test replaces _safe_get, whose production implementation normally
    # initializes the lazy HTML dependencies before fetch_url parses content.
    say_read.ensure_web_deps()
    events = []
    launch_calls = []
    private_route = FakeRoute()

    class InitialResponse(FakeRawResponse):
        def __init__(self):
            super().__init__(b"<html><body>short</body></html>", [("Content-Type", "text/html")])
            self.headers = {"content-type": "text/html"}
            self.encoding = "utf-8"
            self._content = b""

        @property
        def apparent_encoding(self):
            return "utf-8"

        def iter_content(self, chunk_size):
            yield self.body
            self.body = b""

    class FakePage:
        def __init__(self, context):
            self.context = context

        def goto(self, url, **kwargs):
            events.append("goto")
            assert self.context.handler is not None
            self.context.handler(
                private_route,
                FakeRequest("http://127.0.0.1/private-script.js"),
            )

        def wait_for_timeout(self, milliseconds):
            pass

        def content(self):
            return "<html><body>rendered public shell</body></html>"

    class FakeContext:
        handler = None

        def route(self, pattern, handler):
            events.append("route")
            self.handler = handler

        def add_init_script(self, script):
            events.append("init")

        def new_page(self):
            return FakePage(self)

        def close(self):
            pass

    class FakeBrowser:
        def launch(self, **kwargs):
            launch_calls.append(kwargs)
            return self

        def new_context(self, **kwargs):
            assert kwargs["service_workers"] == "block"
            return FakeContext()

        def close(self):
            pass

    class FakePlaywright:
        chromium = FakeBrowser()

    class FakeManager:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, exc_type, exc, traceback):
            pass

    playwright_package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakeManager()
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(say_read, "_safe_get", lambda url, debug: InitialResponse())
    monkeypatch.setattr(say_read, "_assert_public_url", lambda url: url)

    text = say_read.fetch_url("https://reader.example/app", render=True, debug=False)

    assert events.index("route") < events.index("goto")
    assert private_route.aborted == "blockedbyclient"
    assert not private_route.continued
    assert launch_calls[0]["headless"] is True
    assert any(
        arg.startswith("--proxy-server=http://127.0.0.1:")
        for arg in launch_calls[0]["args"]
    )
    assert "--proxy-bypass-list=<-loopback>" in launch_calls[0]["args"]
    assert launch_calls[0]["proxy"]["server"].startswith(
        "http://127.0.0.1:"
    )
    assert launch_calls[0]["proxy"]["bypass"] == "<-loopback>"
    assert text == "rendered public shell"


def test_installed_chrome_preconnect_is_confined_to_owned_proxy():
    chrome = next(
        (
            path
            for path in (
                shutil.which("google-chrome"),
                shutil.which("chromium"),
                shutil.which("chromium-browser"),
            )
            if path
        ),
        None,
    )
    if chrome is None:
        pytest.skip("installed Chrome/Chromium not available")

    targets = []
    ipv4_target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ipv4_target.bind(("127.0.0.1", 0))
    ipv4_target.listen(4)
    targets.append(ipv4_target)
    target_urls = [
        "http://127.0.0.1:{}".format(ipv4_target.getsockname()[1]),
        "http://localhost:{}".format(ipv4_target.getsockname()[1]),
    ]
    ipv6_target = None
    try:
        ipv6_target = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        ipv6_target.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ipv6_target.bind(("::1", 0))
        ipv6_target.listen(2)
    except OSError:
        if ipv6_target is not None:
            ipv6_target.close()
    else:
        targets.append(ipv6_target)
        target_urls.append(
            "http://[::1]:{}".format(ipv6_target.getsockname()[1])
        )

    try:
        html = "<html><head>{}</head><body>safe</body></html>".format(
            "".join(
                '<link rel="preconnect" href="{}">'.format(url)
                for url in target_urls
            )
        )
        data_url = "data:text/html," + quote(html)
        with say_read._BrowserEgressSink() as sink:
            with tempfile.TemporaryDirectory() as profile:
                command = [
                    chrome,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-breakpad",
                    "--disable-crash-reporter",
                    "--user-data-dir={}".format(profile),
                ]
                command.extend(say_read._chromium_render_args(sink.proxy_url))
                command.extend(["--dump-dom", data_url])
                subprocess.run(
                    command,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
            deadline = time.monotonic() + 1.0
            while sink.connection_count == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert sink.connection_count > 0

        for target in targets:
            target.setblocking(False)
            with pytest.raises(BlockingIOError):
                target.accept()
    finally:
        for target in targets:
            target.close()
