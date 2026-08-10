#!/usr/bin/env -S uv run --extra kokoro --extra read python
# High-quality neural TTS using Kokoro-ONNX
# Install once with: uv sync --extra kokoro
# Then run with: uv run src/tts/say_read.py
"""
say_read.py — Offline reader using kokoro-onnx

Key features:
- URL / PDF / EPUB / HTML / TXT extraction (OCR fallback for image-only PDFs)
- Cleans UI junk, chunks safely, force-splits when needed (never stalls)
- Plays once at end OR streams piece-by-piece right away
- Progress logs with per-piece timings
- Optional --max-chars cap and JS render (--render) for SPA pages

Examples:
  uv run src/tts/say_read.py --max-chars 6000 --stream https://www.bbc.com/news/technology
  uv run src/tts/say_read.py -l es -v ef_dora https://elpais.com/tecnologia/
  uv run src/tts/say_read.py -o /tmp/article.mp3 https://www.bbc.com/news/technology
  lynx -dump -nolist URL | head -c 5000 | uv run src/tts/say_read.py -  # stdin
"""

from __future__ import annotations

import argparse
import base64
import http.client
import ipaddress
import math
import os
import queue
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

# Version information
__version__ = "1.1.0"

np = None
sf = None
Kokoro = None
requests = None
BeautifulSoup = None


def ensure_audio_deps():
    global np, sf, Kokoro
    if np is not None and sf is not None and Kokoro is not None:
        return
    import numpy as _np
    import soundfile as _sf
    from kokoro_onnx import Kokoro as _Kokoro
    np = _np
    sf = _sf
    Kokoro = _Kokoro


def ensure_web_deps():
    """Lazily import the URL/HTML reader deps (the ``read`` extra).

    Kept out of module import so the pure-text helpers (e.g. ``canonical_chunks``)
    stay importable without ``requests``/``beautifulsoup4`` installed — the release
    QA gate runs the test suite in a minimal, dependency-free environment.
    """
    global requests, BeautifulSoup
    if requests is not None and BeautifulSoup is not None:
        return
    import requests as _requests
    from bs4 import BeautifulSoup as _BeautifulSoup
    requests = _requests
    BeautifulSoup = _BeautifulSoup


try:
    from readability import Document as ReadabilityDoc
except Exception:
    ReadabilityDoc = None

# PDF / EPUB
try:
    from pdfminer.high_level import extract_text as pdf_extract_text
except Exception:
    pdf_extract_text = None

try:
    import ebooklib
    from ebooklib import epub
except Exception:
    ebooklib = None
    epub = None


# ======================== utils ========================

MAX_URL_BYTES = int(os.environ.get('SAYREAD_MAX_URL_BYTES', str(5 * 1024 * 1024)))
MAX_PDF_OCR_PAGES = int(os.environ.get('SAYREAD_MAX_OCR_PAGES', '25'))
OCR_TIMEOUT_SECONDS = int(os.environ.get('SAYREAD_OCR_TIMEOUT', '30'))
MAX_URL_REDIRECTS = int(os.environ.get('SAYREAD_MAX_REDIRECTS', '5'))
URL_REQUEST_TIMEOUT_SECONDS = 20.0
MAX_URL_ADDRESS_ATTEMPTS = 4
_RESOLVER_SLOTS = threading.BoundedSemaphore(4)

# Keep this policy independent of ``ipaddress.is_global``/``is_private``.
# Those properties embed the special-purpose registry shipped with a particular
# Python release: for example, Python 3.8 treats 3fff::/20 as global while all
# currently supported runtimes reject IANA's public 2001:1::3 anycast control.
# This snapshot follows IANA's IPv4/IPv6 special-purpose registries as updated
# 2025-10-09. Ordinary IPv6 is limited to today's 2000::/3 global-unicast
# envelope; transition formats are handled explicitly below.
_IPV4_PUBLIC_SPECIAL_ADDRESSES = {
    ipaddress.ip_address('192.0.0.9'),
    ipaddress.ip_address('192.0.0.10'),
}
_IPV4_NON_PUBLIC_NETWORKS = tuple(
    ipaddress.ip_network(prefix)
    for prefix in (
        '0.0.0.0/8',
        '10.0.0.0/8',
        '100.64.0.0/10',
        '127.0.0.0/8',
        '169.254.0.0/16',
        '172.16.0.0/12',
        '192.0.0.0/24',
        '192.0.2.0/24',
        '192.88.99.0/24',
        '192.168.0.0/16',
        '198.18.0.0/15',
        '198.51.100.0/24',
        '203.0.113.0/24',
        '224.0.0.0/4',
        '240.0.0.0/4',
    )
)
_IPV6_GLOBAL_UNICAST_NETWORK = ipaddress.ip_network('2000::/3')
_IPV6_PUBLIC_SPECIAL_NETWORKS = tuple(
    ipaddress.ip_network(prefix)
    for prefix in (
        '2001:1::1/128',
        '2001:1::2/128',
        '2001:1::3/128',
        '2001:3::/32',
        '2001:4:112::/48',
        '2001:20::/28',
        '2001:30::/28',
        '2620:4f:8000::/48',
    )
)
_IPV6_NON_PUBLIC_SPECIAL_NETWORKS = tuple(
    ipaddress.ip_network(prefix)
    for prefix in (
        '2001::/23',
        '2001:db8::/32',
        '3f00::/8',
    )
)
_IPV4_COMPATIBLE_NETWORK = ipaddress.ip_network('::/96')
_IPV4_TRANSLATED_NETWORK = ipaddress.ip_network('::ffff:0:0:0/96')
_NAT64_WELL_KNOWN_NETWORK = ipaddress.ip_network('64:ff9b::/96')
_NAT64_LOCAL_USE_NETWORK = ipaddress.ip_network('64:ff9b:1::/48')
_ISATAP_INTERFACE_MARKERS = {0x00005EFE, 0x02005EFE}


def _request_deadline(timeout: float = URL_REQUEST_TIMEOUT_SECONDS) -> float:
    """Return a finite monotonic deadline for one complete URL request."""
    if isinstance(timeout, bool):
        raise ValueError("URL request timeout must be a positive finite number")
    value = float(timeout)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("URL request timeout must be a positive finite number")
    return time.monotonic() + value


def _remaining_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError("URL request deadline exceeded")
    return remaining


def _bounded_getaddrinfo(host: str, port: int, deadline: float):
    """Resolve without allowing the libc resolver to exceed our deadline.

    ``socket.getaddrinfo`` has no timeout API. A small bounded set of daemon
    workers keeps the caller deadline enforceable without allowing repeated
    stuck resolutions to create an unbounded number of threads.
    """
    if not _RESOLVER_SLOTS.acquire(timeout=_remaining_time(deadline)):
        raise TimeoutError("URL resolver capacity deadline exceeded")
    outcome = queue.Queue(maxsize=1)

    def resolve():
        try:
            value = socket.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
            outcome.put((True, value))
        except BaseException as exc:
            outcome.put((False, exc))
        finally:
            _RESOLVER_SLOTS.release()

    worker = threading.Thread(
        target=resolve,
        name="say-read-resolver",
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        _RESOLVER_SLOTS.release()
        raise
    try:
        ok, value = outcome.get(timeout=_remaining_time(deadline))
    except queue.Empty:
        raise TimeoutError("URL resolution deadline exceeded")
    if ok:
        return value
    raise value


def _is_public_ipv4(ip: "ipaddress.IPv4Address") -> bool:
    """Use an explicit, version-stable public IPv4 policy."""
    if ip in _IPV4_PUBLIC_SPECIAL_ADDRESSES:
        return True
    return not any(ip in network for network in _IPV4_NON_PUBLIC_NETWORKS)


def _ipv4_tail(ip: "ipaddress.IPv6Address") -> "ipaddress.IPv4Address":
    return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)


def _is_public_ip(ip: "ipaddress._BaseAddress") -> bool:
    """Reject any address that is not a routable, public unicast IP.

    Covers loopback (127/8, ::1), link-local (169.254/16 incl. the cloud
    metadata endpoint 169.254.169.254, fe80::/10), private ranges
    (10/8, 172.16/12, 192.168/16), ULA (fc00::/7), and unspecified /
    multicast / reserved space.
    """
    if isinstance(ip, ipaddress.IPv4Address):
        return _is_public_ipv4(ip)

    mapped = ip.ipv4_mapped
    if mapped is not None:
        return _is_public_ipv4(mapped)

    # These formats carry one unambiguous IPv4 destination in their low 32
    # bits.  Accept them only when that destination passes the same strict IPv4
    # policy as an ordinary A record.
    if (
        ip in _IPV4_COMPATIBLE_NETWORK
        or ip in _IPV4_TRANSLATED_NETWORK
        or ip in _NAT64_WELL_KNOWN_NETWORK
    ):
        return _is_public_ipv4(_ipv4_tail(ip))

    # RFC 8215's local-use NAT64 prefix can use variable RFC 6052 layouts.  The
    # active prefix length is not encoded in the address, so extracting the
    # effective IPv4 destination from a bare address is ambiguous.  Fail closed.
    if ip in _NAT64_LOCAL_USE_NETWORK:
        return False

    six_to_four = ip.sixtofour
    if six_to_four is not None:
        return _is_public_ipv4(six_to_four)

    teredo = ip.teredo
    if teredo is not None:
        server, client = teredo
        return _is_public_ipv4(server) and _is_public_ipv4(client)

    # ISATAP embeds an IPv4 next hop in the interface identifier, but its
    # routing context is site-specific and cannot be authorized from the
    # address alone.
    interface_marker = (int(ip) >> 32) & 0xFFFFFFFF
    if interface_marker in _ISATAP_INTERFACE_MARKERS:
        return False

    if any(ip in network for network in _IPV6_PUBLIC_SPECIAL_NETWORKS):
        return True
    if any(ip in network for network in _IPV6_NON_PUBLIC_SPECIAL_NETWORKS):
        return False
    return ip in _IPV6_GLOBAL_UNICAST_NETWORK


def _resolve_public_url(url: str, *, deadline=None):
    """Resolve an HTTP(S) URL to an immutable set of public socket addresses.

    The returned addresses are the only addresses the transport may connect
    to. Keeping resolution and connection in one data flow closes the usual
    DNS-rebinding gap where a hostname is validated and then resolved again by
    an HTTP client.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ('http', 'https'):
        raise ValueError(f"refusing non-http(s) URL scheme: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise ValueError("URL has no host")
    try:
        port = parts.port or (443 if parts.scheme.lower() == 'https' else 80)
    except ValueError as e:
        raise ValueError(f"invalid URL port: {e}")
    try:
        ascii_host = host.encode('idna').decode('ascii')
    except UnicodeError as e:
        raise ValueError(f"invalid URL host: {e}")
    # A bare IP literal in the URL still has to be public.
    try:
        literal = ipaddress.ip_address(ascii_host)
    except ValueError:
        literal = None
    if literal is not None and not _is_public_ip(literal):
        raise ValueError(f"refusing non-public address: {host}")
    request_deadline = deadline if deadline is not None else _request_deadline()
    try:
        infos = _bounded_getaddrinfo(ascii_host, port, request_deadline)
    except socket.gaierror as e:
        raise ValueError(f"could not resolve host {host!r}: {e}")
    if not infos:
        raise ValueError(f"could not resolve host {host!r}")
    public_addresses = []
    seen = set()
    for info in infos:
        _remaining_time(request_deadline)
        sockaddr = info[4]
        try:
            resolved = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise ValueError(f"unparseable resolved address for {host!r}: {sockaddr[0]!r}")
        if not _is_public_ip(resolved):
            raise ValueError(f"host {host!r} resolves to non-public address {resolved}")
        key = (info[0], info[1], info[2], sockaddr)
        if key not in seen:
            seen.add(key)
            public_addresses.append((info[0], info[1], info[2], sockaddr))
    return parts, ascii_host, tuple(public_addresses)


def _assert_public_url(url: str, *, deadline=None) -> str:
    """Validate an HTTP(S) URL and all of its currently resolved addresses."""
    _resolve_public_url(url, deadline=deadline)
    return url


class _PinnedResponse:
    """Small requests-like wrapper around a response on a pinned socket."""

    def __init__(self, connection, raw_response, url: str, *, deadline=None):
        self._connection = connection
        self._raw_response = raw_response
        self.url = url
        self.status_code = raw_response.status
        self.reason = raw_response.reason
        self.header_items = list(raw_response.getheaders())
        self.headers = {}
        for name, value in self.header_items:
            self.headers[name.lower()] = value
        self.encoding = _declared_charset(self.headers.get('content-type', ''))
        self._content = b''
        self._deadline = deadline

    @property
    def is_redirect(self) -> bool:
        return (
            self.status_code in (301, 302, 303, 307, 308)
            and bool(self.headers.get('location'))
        )

    @property
    def apparent_encoding(self):
        if not self._content:
            return None
        detector = requests.models.Response()
        detector._content = self._content
        return detector.apparent_encoding

    def raise_for_status(self):
        if 400 <= self.status_code:
            raise requests.HTTPError(
                f"{self.status_code} {self.reason} for url: {self.url}",
                response=self,
            )

    def iter_content(self, chunk_size: int):
        while True:
            if self._deadline is not None:
                timeout = _remaining_time(self._deadline)
                sock = getattr(self._connection, 'sock', None)
                if sock is None:
                    fp = getattr(self._raw_response, 'fp', None)
                    raw = getattr(fp, 'raw', None)
                    sock = getattr(raw, '_sock', None)
                if sock is not None:
                    sock.settimeout(timeout)
            chunk = self._raw_response.read(chunk_size)
            if not chunk:
                return
            yield chunk

    def close(self):
        try:
            self._raw_response.close()
        finally:
            self._connection.close()


def _declared_charset(content_type: str):
    match = re.search(r'(?:^|;)\s*charset\s*=\s*["\']?([^;"\'\s]+)', content_type, re.I)
    return match.group(1).strip() if match else None


def _host_header(parts, ascii_host: str) -> str:
    host = f"[{ascii_host}]" if ':' in ascii_host else ascii_host
    default_port = 443 if parts.scheme.lower() == 'https' else 80
    try:
        port = parts.port
    except ValueError as e:
        raise ValueError(f"invalid URL port: {e}")
    return f"{host}:{port}" if port and port != default_port else host


def _connect_validated_address(address, timeout: float):
    family, socktype, proto, sockaddr = address
    peer = ipaddress.ip_address(sockaddr[0])
    if not _is_public_ip(peer):
        raise ValueError(f"refusing non-public connection address: {peer}")
    sock = socket.socket(family, socktype, proto)
    try:
        sock.settimeout(timeout)
        # sockaddr came directly from the validated getaddrinfo result. Passing
        # it to a family-specific socket connects numerically without DNS.
        sock.connect(sockaddr)
        return sock
    except Exception:
        sock.close()
        raise


def _connection_candidates(addresses):
    """Cap attempts while retaining both IPv4 and IPv6 opportunities."""
    addresses = list(addresses)
    by_family = {
        socket.AF_INET: [],
        socket.AF_INET6: [],
    }
    other = []
    for address in addresses:
        if address[0] in by_family:
            by_family[address[0]].append(address)
        else:
            other.append(address)
    family_order = []
    for address in addresses:
        family = address[0]
        if family in by_family and family not in family_order:
            family_order.append(family)
    selected = []
    while len(selected) < MAX_URL_ADDRESS_ATTEMPTS:
        added = False
        for family in family_order:
            if by_family[family]:
                selected.append(by_family[family].pop(0))
                added = True
                if len(selected) == MAX_URL_ADDRESS_ATTEMPTS:
                    break
        if not added:
            break
    if len(selected) < MAX_URL_ADDRESS_ATTEMPTS:
        selected.extend(other[:MAX_URL_ADDRESS_ATTEMPTS - len(selected)])
    return selected


def _default_ssl_context():
    ca_bundle = (
        os.environ.get('REQUESTS_CA_BUNDLE')
        or os.environ.get('CURL_CA_BUNDLE')
        or requests.certs.where()
    )
    if os.path.isdir(ca_bundle):
        return ssl.create_default_context(capath=ca_bundle)
    return ssl.create_default_context(cafile=ca_bundle)


def _open_pinned_connection(
    parts,
    ascii_host: str,
    addresses,
    timeout: float,
    *,
    deadline=None,
):
    """Open HTTP(S) directly to one of the already validated addresses."""
    last_error = None
    port = parts.port or (443 if parts.scheme.lower() == 'https' else 80)
    request_deadline = (
        deadline if deadline is not None else _request_deadline(timeout)
    )
    for address in _connection_candidates(addresses):
        raw_sock = None
        try:
            raw_sock = _connect_validated_address(
                address,
                min(timeout, _remaining_time(request_deadline)),
            )
            if parts.scheme.lower() == 'https':
                context = _default_ssl_context()
                raw_sock.settimeout(_remaining_time(request_deadline))
                raw_sock = context.wrap_socket(raw_sock, server_hostname=ascii_host)
            remaining = _remaining_time(request_deadline)
            raw_sock.settimeout(remaining)
            connection = http.client.HTTPConnection(
                ascii_host,
                port,
                timeout=remaining,
            )
            connection.sock = raw_sock
            return connection
        except Exception as e:
            last_error = e
            if raw_sock is not None:
                try:
                    raw_sock.close()
                except OSError:
                    pass
    if last_error is not None:
        raise last_error
    raise ValueError("URL resolved to no usable public address")


def _request_target(parts) -> str:
    target = parts.path or '/'
    if parts.query:
        target = f"{target}?{parts.query}"
    # Match Requests' useful handling of spaces/non-ASCII path characters.
    return requests.utils.requote_uri(target)


def _safe_request_once(
    url: str,
    method: str = 'GET',
    headers=None,
    body=None,
    timeout: float = URL_REQUEST_TIMEOUT_SECONDS,
    *,
    deadline=None,
):
    """Issue one non-redirecting request through a DNS-pinned connection."""
    ensure_web_deps()
    request_deadline = (
        deadline if deadline is not None else _request_deadline(timeout)
    )
    parts, ascii_host, addresses = _resolve_public_url(
        url,
        deadline=request_deadline,
    )
    connection = _open_pinned_connection(
        parts,
        ascii_host,
        addresses,
        timeout,
        deadline=request_deadline,
    )
    outbound_headers = {}
    for name, value in (headers or {}).items():
        if name.lower() not in (
            'host', 'connection', 'proxy-connection', 'keep-alive',
            'transfer-encoding', 'upgrade', 'content-length', 'accept-encoding',
        ):
            outbound_headers[name] = value
    outbound_headers['Host'] = _host_header(parts, ascii_host)
    outbound_headers['Accept-Encoding'] = 'identity'
    if parts.username is not None and not any(
        name.lower() == 'authorization' for name in outbound_headers
    ):
        username = unquote(parts.username)
        password = unquote(parts.password or '')
        token = base64.b64encode(f"{username}:{password}".encode()).decode('ascii')
        outbound_headers['Authorization'] = f"Basic {token}"
    try:
        remaining = _remaining_time(request_deadline)
        connection.timeout = remaining
        connection.sock.settimeout(remaining)
        connection.request(
            method.upper(),
            _request_target(parts),
            body=body,
            headers=outbound_headers,
        )
        remaining = _remaining_time(request_deadline)
        connection.timeout = remaining
        connection.sock.settimeout(remaining)
        return _PinnedResponse(
            connection,
            connection.getresponse(),
            url,
            deadline=request_deadline,
        )
    except Exception:
        connection.close()
        raise


def dbg(msg: str, enabled: bool):
    if enabled:
        print(msg, file=sys.stderr, flush=True)


def progress(current: int, total: int):
    print(f"[say-read] [{current}/{total}]", file=sys.stderr, flush=True)

def clean_text(s: str) -> str:
    s = unicodedata.normalize('NFC', s)
    s = re.sub(r'[ \t\r\f\v]+', ' ', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    s = re.sub(r'\b(?:BUTTON|Share|Comments)\b', ' ', s, flags=re.I)
    def keep(ch):
        cat = unicodedata.category(ch)
        return not cat.startswith('C') or ch in '\n\t'
    s = ''.join(ch if keep(ch) else ' ' for ch in s)
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r' *\n *', '\n', s)
    return re.sub(r'\n{3,}', '\n\n', s).strip()

def split_sentences(text: str, maxlen: int) -> list[str]:
    if maxlen <= 0:
        raise ValueError("maxlen must be greater than zero")
    out, i, n = [], 0, len(text)
    while i < n:
        j = min(i + maxlen, n)
        cut = max(text.rfind(x, i, j) for x in ('. ', '! ', '? ', '; ', ': ', ', ', ' '))
        cut = j if cut <= i + maxlen // 3 else cut + 1
        chunk = text[i:cut].strip()
        if chunk:
            out.append(chunk)
        i = cut
    return out

def _force_split(s: str) -> list[str]:
    n = len(s)
    if n <= 2:
        return [s]
    mid = n // 2
    left_ws = s.rfind(' ', 0, mid + 1)
    right_ws = s.find(' ', mid)
    if left_ws == -1 and right_ws == -1:
        return [s[:mid], s[mid:]]
    split_at = left_ws if (right_ws == -1 or (mid - left_ws) <= (right_ws - mid)) else right_ws
    a, b = s[:split_at].strip(), s[split_at:].strip()
    if len(a) < 10 or len(b) < 10:
        return [s[:mid], s[mid:]]
    return [a, b]


def _split_to_limit(text: str, max_size: int) -> list[str]:
    """Split text so no returned piece exceeds max_size."""
    if len(text) <= max_size:
        return [text.strip()] if text.strip() else []

    pieces = []
    remaining = text.strip()
    min_good = max(20, max_size // 3)
    while len(remaining) > max_size:
        window = remaining[:max_size]
        candidates = [
            window.rfind(mark)
            for mark in ('. ', '! ', '? ', '; ', ': ', ', ', ' ')
        ]
        cut = max(candidates)
        if cut < min_good:
            cut = max_size
        else:
            cut += 1
        piece = remaining[:cut].strip()
        if piece:
            pieces.append(piece)
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def enforce_chunk_limit(pieces: list[str], max_size: int) -> list[str]:
    bounded = []
    for piece in pieces:
        bounded.extend(_split_to_limit(piece, max_size))
    return bounded


def canonical_chunks(text: str, target_size: int, lang: str, debug: bool = False) -> list[str]:
    """Chunk text with the best available chunker, falling back to local split."""
    max_size = max(target_size * 2, target_size + 80)
    try:
        chunking_dir = Path(__file__).resolve().parents[1] / "chunking"
        if str(chunking_dir) not in sys.path:
            sys.path.insert(0, str(chunking_dir))
        from gold_standard_chunker import GoldStandardChunker
        chunker = GoldStandardChunker(
            target_size=target_size,
            max_size=max_size,
            min_chunk_size=max(20, min(80, target_size // 4)),
        )
        normalized_lang = (lang or "").lower()
        if normalized_lang.startswith("es"):
            chunker.detect_language = lambda _text: "spanish"
        elif normalized_lang.startswith("en"):
            chunker.detect_language = lambda _text: "english"
        pieces = [p.strip() for p in chunker.gold_standard_chunk_text(text) if p.strip()]
        if pieces:
            return enforce_chunk_limit(pieces, max_size)
    except Exception as exc:
        dbg(f"[say-read] gold chunker unavailable; using fallback splitter: {exc}", debug)
    return enforce_chunk_limit(split_sentences(text, target_size), max_size)


def trim_to_boundary(text: str, max_chars: int, lang: str, debug: bool = False) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    candidate = text[:max_chars].rstrip()
    for pattern in (r'(?s)^(.+[.!?])(?:\s|$)', r'(?s)^(.+[,;:])(?:\s|$)', r'(?s)^(.+)\s+\S*$'):
        match = re.match(pattern, candidate)
        if match and len(match.group(1).strip()) >= max(40, max_chars // 3):
            return match.group(1).strip()
    return candidate


# ======================== extraction ========================

def _safe_get(
    url: str,
    debug: bool,
    timeout: float = URL_REQUEST_TIMEOUT_SECONDS,
):
    """GET through DNS-pinned sockets and validate every redirect hop.

    Returns the final streaming Response (caller must close it) or None.
    """
    current = url
    deadline = _request_deadline(timeout)
    for hop in range(MAX_URL_REDIRECTS + 1):
        r = _safe_request_once(
            current,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout,
            deadline=deadline,
        )
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get('location')
            r.close()
            if not location:
                raise ValueError("redirect without Location header")
            # Resolve relative redirects against the current URL.
            current = requests.compat.urljoin(current, location)
            dbg(f"[say-read] following redirect to {current}", debug)
            continue
        try:
            r.raise_for_status()
        except Exception:
            try:
                r.close()
            except Exception:
                pass
            raise
        return r
    raise ValueError(f"too many redirects (>{MAX_URL_REDIRECTS})")


def _read_capped_bytes(r, debug: bool) -> bytes:
    buf = bytearray()
    for chunk in r.iter_content(65536):
        if not chunk:
            continue
        remaining = max(0, MAX_URL_BYTES - len(buf))
        if remaining == 0:
            break
        if len(chunk) > remaining:
            buf.extend(chunk[:remaining])
            dbg(f"[say-read] URL response exceeded {MAX_URL_BYTES} bytes; truncating", debug)
            break
        buf.extend(chunk)
        if len(buf) == MAX_URL_BYTES:
            break
    return bytes(buf)


_HOP_BY_HOP_RESPONSE_HEADERS = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailer', 'transfer-encoding', 'upgrade', 'content-length',
}
_BROWSER_NETWORK_HINT_RESPONSE_HEADERS = {
    'alt-svc',
    'link',
    'nel',
    'network-error-logging',
    'report-to',
    'reporting-endpoints',
}
_RENDER_METHODS = {'GET', 'HEAD', 'OPTIONS'}


class _BrowserEgressSink:
    """Owned rejecting HTTP proxy used to deny Chromium direct network egress.

    Playwright's request routes do not cover browser connection hints such as
    ``<link rel=preconnect>``. Chromium is therefore pointed at this loopback
    listener, which accepts and immediately closes every connection. Validated
    content still reaches Chromium exclusively through ``route.fulfill``.
    """

    def __init__(self):
        self._listener = None
        self._thread = None
        self._stopping = threading.Event()
        self.connection_count = 0
        self.proxy_url = ''

    def __enter__(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(('127.0.0.1', 0))
            listener.listen(16)
            listener.settimeout(0.1)
        except Exception:
            listener.close()
            raise
        self._listener = listener
        self.proxy_url = 'http://127.0.0.1:{}'.format(listener.getsockname()[1])
        self._thread = threading.Thread(
            target=self._reject_connections,
            name='say-read-browser-egress-sink',
            daemon=True,
        )
        try:
            self._thread.start()
        except Exception:
            listener.close()
            self._listener = None
            raise
        return self

    def _reject_connections(self):
        while not self._stopping.is_set():
            try:
                connection, _address = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connection_count += 1
            try:
                connection.close()
            except OSError:
                pass

    def close(self):
        listener = self._listener
        if listener is None:
            return
        self._stopping.set()
        listener.close()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._listener = None
        self._thread = None

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def _chromium_render_args(proxy_url: str):
    """Return Chromium flags that make the owned rejecting proxy mandatory."""
    return [
        '--disable-background-networking',
        '--disable-component-update',
        '--disable-default-apps',
        '--disable-features=PreconnectToSearch,Prerender2,SpeculationRulesPrefetch,SpeculationRulesPrefetchProxy',
        '--disable-preconnect',
        '--disable-quic',
        '--disable-sync',
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
        '--no-first-run',
        '--proxy-bypass-list=<-loopback>',
        '--proxy-server={}'.format(proxy_url),
    ]


def _render_request_headers(request):
    all_headers = getattr(request, 'all_headers', None)
    headers = all_headers() if callable(all_headers) else request.headers
    return dict(headers or {})


def _render_request_body(request):
    body = getattr(request, 'post_data_buffer', None)
    if callable(body):
        body = body()
    if body is None:
        text_body = getattr(request, 'post_data', None)
        if callable(text_body):
            text_body = text_body()
        if text_body is not None:
            body = text_body.encode('utf-8')
    return body


def _proxy_render_request(route, request, debug: bool):
    """Fulfil one browser request through the same DNS-pinned transport.

    The browser is never allowed to continue the request itself: private
    destinations, DNS rebinding, unsupported methods, and transport failures
    all abort before Chromium can open a socket.
    """
    response = None
    try:
        deadline = _request_deadline()
        method = request.method.upper()
        if method not in _RENDER_METHODS:
            raise ValueError(f"refusing render request method: {method}")
        request_body = _render_request_body(request)
        if request_body is not None and len(request_body) > MAX_URL_BYTES:
            raise ValueError("render request body exceeds configured byte limit")
        response = _safe_request_once(
            request.url,
            method=method,
            headers=_render_request_headers(request),
            body=request_body,
            deadline=deadline,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get('location')
            if not location:
                raise ValueError("render redirect without Location header")
            _assert_public_url(
                requests.compat.urljoin(request.url, location),
                deadline=deadline,
            )
        response_body = b'' if method == 'HEAD' else _read_capped_bytes(response, debug)
        response_headers = {}
        response_header_names = {}
        for name, value in response.header_items:
            normalized = name.lower()
            if (
                normalized in _HOP_BY_HOP_RESPONSE_HEADERS
                or normalized in _BROWSER_NETWORK_HINT_RESPONSE_HEADERS
            ):
                continue
            if normalized not in response_header_names:
                response_header_names[normalized] = name
                response_headers[name] = value
                continue
            stored_name = response_header_names[normalized]
            separator = '\n' if normalized == 'set-cookie' else ', '
            response_headers[stored_name] += separator + value
        route.fulfill(
            status=response.status_code,
            headers=response_headers,
            body=response_body,
        )
    except Exception as e:
        dbg(f"[say-read] blocked render request: {e}", debug)
        route.abort('blockedbyclient')
    finally:
        if response is not None:
            response.close()


_BLOCK_BROWSER_SIDE_CHANNELS = r"""
(() => {
  const blocked = function() { throw new Error('network channel blocked by say-read'); };
  for (const name of [
    'WebSocket', 'EventSource', 'WebTransport', 'RTCPeerConnection',
    'webkitRTCPeerConnection', 'Worker', 'SharedWorker',
  ]) {
    try {
      Object.defineProperty(globalThis, name, {
        configurable: false,
        enumerable: false,
        get: () => blocked,
        set: () => undefined,
      });
    } catch (_) {}
  }
  try {
    Object.defineProperty(Navigator.prototype, 'sendBeacon', {
      configurable: false,
      value: () => false,
    });
  } catch (_) {}
})();
"""


def fetch_url(url: str, render: bool, debug: bool) -> str:
    html = ''
    content_type = ''
    try:
        r = _safe_get(url, debug)
        try:
            content_type = r.headers.get('content-type', '').lower()
            final_url = getattr(r, 'url', url) or url
            final_path = urlsplit(final_url).path.lower()
            # Binary documents served over HTTP: hand off to the file extractors.
            if 'application/pdf' in content_type or final_path.endswith('.pdf'):
                data = _read_capped_bytes(r, debug)
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
                    tmp = f.name
                    f.write(data)
                try:
                    return extract_pdf(tmp, debug)
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            if 'application/epub' in content_type or final_path.endswith('.epub'):
                data = _read_capped_bytes(r, debug)
                with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as f:
                    tmp = f.name
                    f.write(data)
                try:
                    return extract_epub(tmp, debug)
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            if content_type and not any(t in content_type for t in ('text/', 'html', 'xml', 'json')):
                dbg(f"[say-read] unsupported content-type: {content_type}", debug)
                return ''
            data = _read_capped_bytes(r, debug)
            # Decode using the declared/apparent encoding rather than latin-1.
            # The streaming body is already drained, so seed _content from the
            # bytes we read; otherwise r.apparent_encoding -> r.content raises
            # RuntimeError("content already consumed") for responses with no
            # charset header (e.g. RSS/XHTML), dropping otherwise-valid pages.
            r._content = data
            r.encoding = r.encoding or r.apparent_encoding
            enc = r.encoding or 'utf-8'
            html = data.decode(enc, errors='replace')
        finally:
            r.close()
    except ValueError as e:
        # SSRF guard / malformed redirect: surface clearly, do not fetch.
        dbg(f"[say-read] refusing URL: {e}", debug)
        return ''
    except Exception as e:
        dbg(f"[say-read] requests failed: {e}", debug)

    main_text = ''
    if html:
        try:
            if ReadabilityDoc:
                doc = ReadabilityDoc(html)
                html2 = doc.summary(html_partial=True)
                soup = BeautifulSoup(html2, 'lxml')
                main_text = soup.get_text(separator=' ', strip=True)
        except Exception:
            pass
        if len(main_text) < 400:  # fallback: full-page
            soup = BeautifulSoup(html, 'lxml')
            main_text = soup.get_text(separator=' ', strip=True)

    if render and len(main_text) < 400:
        try:
            # Validate before launching Chromium. Every navigation, redirect,
            # fetch/XHR, script, stylesheet, image, and frame is then fulfilled
            # by _proxy_render_request; Chromium never connects to it directly.
            _assert_public_url(url)
            from playwright.sync_api import sync_playwright
            with _BrowserEgressSink() as egress_sink:
                with sync_playwright() as p:
                    b = p.chromium.launch(
                        headless=True,
                        args=_chromium_render_args(egress_sink.proxy_url),
                        proxy={
                            'server': egress_sink.proxy_url,
                            'bypass': '<-loopback>',
                        },
                    )
                    context = None
                    try:
                        context = b.new_context(service_workers='block')
                        context.route(
                            '**/*',
                            lambda route, request: _proxy_render_request(route, request, debug),
                        )
                        route_web_socket = getattr(context, 'route_web_socket', None)
                        if callable(route_web_socket):
                            route_web_socket('**/*', lambda websocket: websocket.close())
                        context.add_init_script(_BLOCK_BROWSER_SIDE_CHANNELS)
                        page = context.new_page()
                        page.goto(url, wait_until='networkidle', timeout=30000)
                        page.wait_for_timeout(1000)
                        html = page.content()
                    finally:
                        try:
                            if context is not None:
                                context.close()
                        finally:
                            b.close()
            soup = BeautifulSoup(html, 'lxml')
            main_text = soup.get_text(separator=' ', strip=True)
            dbg("[say-read] used Playwright render", debug)
        except ValueError as e:
            dbg(f"[say-read] refusing render URL: {e}", debug)
        except Exception as e:
            dbg(f"[say-read] render failed: {e}", debug)
    return main_text

def extract_pdf(path: str, debug: bool) -> str:
    if pdf_extract_text is not None:
        try:
            txt = pdf_extract_text(path) or ''
            if len(txt.strip()) > 40:
                return txt
        except Exception as e:
            dbg(f"[say-read] pdfminer failed: {e}", debug)
    if shutil.which('tesseract') and shutil.which('pdftoppm'):
        tmpdir = tempfile.mkdtemp()
        try:
            try:
                subprocess.run(
                    ['pdftoppm','-r','200','-f','1','-l',str(MAX_PDF_OCR_PAGES),path, f'{tmpdir}/page','-png'],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=max(OCR_TIMEOUT_SECONDS, 30) * MAX_PDF_OCR_PAGES
                )
            except subprocess.TimeoutExpired:
                dbg("[say-read] pdftoppm timed out; OCR aborted", debug)
            parts=[]
            for page_index, img in enumerate(sorted(Path(tmpdir).glob('page-*.png')), 1):
                if page_index > MAX_PDF_OCR_PAGES:
                    dbg(f"[say-read] OCR page limit reached ({MAX_PDF_OCR_PAGES})", debug)
                    break
                try:
                    out = subprocess.run(
                        ['tesseract', str(img), 'stdout', '-l', 'eng+spa', '--psm', '6'],
                        check=False, capture_output=True, text=True, timeout=OCR_TIMEOUT_SECONDS
                    )
                    parts.append(out.stdout)
                except Exception:
                    pass
            result = '\n'.join(parts)
            if not result.strip():
                dbg("[say-read] OCR produced no text", debug)
            return result
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return ''

def extract_epub(path: str, debug: bool) -> str:
    ensure_web_deps()
    if epub is None:
        dbg("[say-read] ebooklib not installed; cannot read EPUB", debug)
        return ''
    try:
        book = epub.read_epub(path)
        parts=[]
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):  # type: ignore
            soup = BeautifulSoup(item.get_content(), 'lxml')
            parts.append(soup.get_text(separator=' ', strip=True))
        return '\n'.join(parts)
    except Exception as e:
        dbg(f"[say-read] epub read failed: {e}", debug)
        return ''

def extract_input(src: str, render: bool, debug: bool) -> str:
    if src == '-':
        return sys.stdin.read()
    if re.match(r'^https?://', src, re.I):
        return fetch_url(src, render, debug)
    low = src.lower()
    if low.endswith('.pdf'):
        return extract_pdf(src, debug)
    if low.endswith('.epub'):
        return extract_epub(src, debug)
    if low.endswith(('.html','.htm')):
        ensure_web_deps()
        try:
            html = Path(src).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            with open(src, 'r', errors='ignore') as f:
                html = f.read()
        if ReadabilityDoc:
            try:
                doc = ReadabilityDoc(html)
                soup = BeautifulSoup(doc.summary(html_partial=True),'lxml')
                t = soup.get_text(separator=' ', strip=True)
                if t:
                    return t
            except Exception:
                pass
        return BeautifulSoup(html,'lxml').get_text(separator=' ', strip=True)
    try:
        return Path(src).read_text(encoding='utf-8', errors='ignore')
    except Exception:
        with open(src, 'r', errors='ignore') as f:
            return f.read()


# ======================== TTS with Kokoro ========================

def synth_retry(k: Any, text: str, voice: str | None, lang: str, debug: bool, depth: int = 0):
    ensure_audio_deps()
    t0 = time.perf_counter()
    try:
        a, sr = k.create(text, voice=voice, speed=1.0, lang=lang)
        return a, sr, False, time.perf_counter() - t0  # no split
    except Exception as e:
        if debug:
            dbg(f"[say-read] synth fail (len={len(text)}, depth={depth}) → {e}", True)

        # Decide how to split smaller
        if len(text) <= 40 or depth >= 8:
            parts = _force_split(text) if len(text) > 20 else []
            if not parts:  # last resort: short silence (rare)
                return np.zeros(2400, dtype=np.float32), 24000, True, time.perf_counter() - t0
        else:
            parts = split_sentences(text, max(60, len(text) // 2))
            if len(parts) < 2:
                parts = _force_split(text)

        audio, sr = [], None
        total_time = 0.0
        for sub in parts:
            x, sr, _, dt = synth_retry(k, sub, voice, lang, debug, depth + 1)
            total_time += dt
            audio.append(x)
        return np.concatenate(audio), sr, True, total_time

def write_audio(arr: Any, sr: int, out: str):
    ensure_audio_deps()
    out_path = Path(out)
    if out_path.suffix.lower() == '.wav':
        sf.write(out, arr, sr)
    else:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            tmp = f.name
        sf.write(tmp, arr, sr)
        try:
            subprocess.check_call(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',tmp,out])
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

def play_buf(arr: Any, sr: int, player: str | None):
    ensure_audio_deps()
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        tmp = f.name
    sf.write(tmp, arr, sr)
    try:
        if player == 'ffplay':
            subprocess.run(['ffplay','-hide_banner','-loglevel','error','-nodisp','-autoexit', tmp], check=True)
        elif player == 'mpv':
            subprocess.run(['mpv','--no-video','--really-quiet', tmp], check=True)
        elif player == 'paplay':
            subprocess.run(['paplay', tmp], check=True)
        elif player == 'aplay':
            subprocess.run(['aplay', tmp], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            print("[say-read] no audio player found. Install ffplay/mpv/paplay/aplay or use --out.", file=sys.stderr)
            raise RuntimeError("no audio player found")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

def play_buf_filtered(wav_path: str, player: str | None, trim: bool):
    # Use ffplay/mpv with optional silence trimming
    if player == 'ffplay':
        cmd = ['ffplay','-hide_banner','-loglevel','error','-nodisp','-autoexit', wav_path]
        if trim:
            cmd = ['ffplay','-hide_banner','-loglevel','error','-nodisp','-autoexit',
                   '-af','silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:stop_periods=1:stop_duration=0.05:stop_threshold=-40dB',
                   wav_path]
        subprocess.run(cmd, check=True)
    elif player == 'mpv':
        cmd = ['mpv','--no-video','--really-quiet', wav_path]
        if trim:
            cmd = ['mpv','--no-video','--really-quiet','--af=lavfi=[silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:stop_periods=1:stop_duration=0.05:stop_threshold=-40dB]', wav_path]
        subprocess.run(cmd, check=True)
    elif player == 'paplay':
        subprocess.run(['paplay', wav_path], check=True)
    elif player == 'aplay':
        subprocess.run(['aplay', wav_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print("[say-read] no audio player found. Install ffplay/mpv/paplay/aplay or use --out.", file=sys.stderr)
        raise RuntimeError("no audio player found")

def stream_fast(k, pieces, voice, lang, debug):
    # Requires ffplay
    if not shutil.which('ffplay'):
        dbg("[say-read] --stream-fast needs ffplay; falling back to --stream", True)
        return None

    # The raw-PCM pipe is fixed at this sample rate; if the synth disagrees we
    # bail to the normal stream path (which writes a correctly-tagged WAV).
    EXPECTED_SR = 24000
    try:
        proc = subprocess.Popen(
            ['ffplay','-hide_banner','-loglevel','error','-nodisp','-autoexit',
             '-f','s16le','-ar',str(EXPECTED_SR),'-i','-'],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except Exception as e:
        dbg(f"[say-read] failed to start ffplay: {e}", True)
        return None

    total_t = 0.0
    broken_pipe = False
    sr_mismatch = False
    try:
        for i, p in enumerate(pieces, 1):
            a, sr, did_split, dt = synth_retry(k, p, voice, lang, debug)
            total_t += dt
            if sr != EXPECTED_SR:
                dbg(f"[say-read] synth sample rate {sr} != {EXPECTED_SR}; "
                    f"falling back to --stream", True)
                sr_mismatch = True
                break
            pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype('<i2').tobytes()
            if proc.stdin is None:
                break
            try:
                proc.stdin.write(pcm)
                proc.stdin.flush()
            except BrokenPipeError:
                dbg("[say-read] ffplay closed early", debug)
                broken_pipe = True
                break
            progress(i, len(pieces))
            if debug:
                dbg(f"[say-read] [fast {i}/{len(pieces)}] len={len(p)} split={did_split} synth={dt:.2f}s total={total_t:.2f}s", True)
    except BaseException as e:
        # Includes KeyboardInterrupt: don't leak/hang the ffplay process.
        dbg(f"[say-read] stream-fast aborted: {e!r}", debug)
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
    try:
        return_code = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        dbg("[say-read] ffplay did not exit; terminating", debug)
        proc.terminate()
        try:
            return_code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            return_code = -1
    if sr_mismatch:
        return None
    if broken_pipe or return_code != 0:
        dbg(f"[say-read] ffplay stream failed with exit code {return_code}", True)
        return None
    return True


# ======================== main ========================

def main():
    ap = argparse.ArgumentParser(description="Read a URL/FILE/TXT with kokoro-onnx (offline).")
    ap.add_argument('source', help="URL | /path/file | - (stdin)")
    ap.add_argument('-l','--lang', default=os.environ.get('KOKORO_LANG','en-us'), help='language code (e.g., en-us, es, fr)')
    ap.add_argument('-v','--voice', default=os.environ.get('KOKORO_VOICE',''), help='voice id (e.g., af_heart, ef_dora)')
    ap.add_argument('-c','--chunk', type=int, default=320, help='target characters per piece (lower is safer)')
    ap.add_argument('-o','--out', help='write to WAV/MP3 instead of playing')
    ap.add_argument('--model',  default=os.environ.get('KOKORO_MODEL',  str(Path.home()/ 'models/kokoro/kokoro-v1.0.onnx')), help='kokoro model path')
    ap.add_argument('--voices', default=os.environ.get('KOKORO_VOICES', str(Path.home()/ 'models/kokoro/voices-v1.0.bin')), help='voices pack path')
    ap.add_argument('--render', action='store_true', help='use Playwright to render JS pages')
    ap.add_argument('--player', default=os.environ.get('SAYREAD_PLAYER',''), help='ffplay|mpv|paplay|aplay')
    ap.add_argument('--max-chars', type=int, default=int(os.environ.get('SAYREAD_MAXCHARS','0')), help='truncate text to this many chars before reading')
    ap.add_argument('--stream', action='store_true', help='play each piece as soon as it is synthesized')
    ap.add_argument('--stream-fast', action='store_true', help='low-latency streaming via one ffplay process')
    ap.add_argument('--trim-silence', action='store_true', help='remove leading/trailing silence in playback/output')
    ap.add_argument('-d','--debug', action='store_true')
    args = ap.parse_args()

    if args.chunk <= 0:
        print("[say-read] --chunk must be greater than zero", file=sys.stderr)
        return 2
    if args.player and args.player not in ('ffplay', 'mpv', 'paplay', 'aplay'):
        print("[say-read] --player must be one of: ffplay, mpv, paplay, aplay", file=sys.stderr)
        return 2
    if args.player and not shutil.which(args.player):
        print(f"[say-read] requested player not found: {args.player}", file=sys.stderr)
        return 1

    raw = extract_input(args.source, args.render, args.debug)
    text = clean_text(raw)

    if args.max_chars and len(text) > args.max_chars:
        text = trim_to_boundary(text, args.max_chars, args.lang, args.debug)
        if args.debug:
            dbg(f"[say-read] clipped to {len(text)} chars (max-chars)", True)

    if args.debug:
        dbg(f"[say-read] extracted {len(raw)} chars; after cleanup {len(text)} chars", True)
        dbg(f"[say-read] sample: {text[:400]}", True)

    if not text:
        print("[say-read] no text extracted", file=sys.stderr)
        return 1

    player = args.player or next((p for p in ('ffplay','mpv','paplay','aplay') if shutil.which(p)), None)
    if not args.out and not player and not (args.stream_fast and shutil.which('ffplay')):
        print("[say-read] no audio player found. Install ffplay/mpv/paplay/aplay or use --out.", file=sys.stderr)
        return 1

    ensure_audio_deps()

    # init Kokoro
    k = Kokoro(args.model, args.voices)
    voice = args.voice or ('ef_dora' if args.lang.lower().startswith('es') else 'af_heart')

    pieces = canonical_chunks(text, args.chunk, args.lang, args.debug)
    if args.debug:
        dbg(f"[say-read] pieces: {len(pieces)}", True)
        dbg(f"[say-read] longest piece: {max((len(p) for p in pieces), default=0)}", True)

    # Fast stream path: one ffplay process, raw PCM
    if args.stream_fast and not args.out:
        ok = stream_fast(k, pieces, voice, args.lang, args.debug)
        if ok:
            return 0
        # if not ok, fall through to normal stream

    if args.stream and not args.out:
        # Stream piece-by-piece (hear immediately)
        total_t = 0.0
        for i, p in enumerate(pieces, 1):
            a, sr, did_split, dt = synth_retry(k, p, voice, args.lang, args.debug)
            total_t += dt
            progress(i, len(pieces))
            if args.debug:
                dbg(f"[say-read] [{i}/{len(pieces)}] len={len(p)} split={did_split} synth={dt:.2f}s total={total_t:.2f}s", True)
            # A transient player failure on one piece must not kill the read.
            try:
                play_buf(a, sr, player)
            except (subprocess.CalledProcessError, OSError) as e:
                dbg(f"[say-read] player failed on piece {i}; continuing: {e}", args.debug)
        return 0

    # Non-stream: synth all, then play once or write file
    audio_list = []
    sr = None
    total_t = 0.0
    for i, p in enumerate(pieces, 1):
        a, sr, did_split, dt = synth_retry(k, p, voice, args.lang, args.debug)
        audio_list.append(a)
        total_t += dt
        progress(i, len(pieces))
        if args.debug:
            dbg(f"[say-read] [{i}/{len(pieces)}] len={len(p)} split={did_split} synth={dt:.2f}s total={total_t:.2f}s", True)

    wav = np.concatenate(audio_list)
    if args.out:
        # optional trim when saving via ffmpeg filter
        if args.trim_silence and Path(args.out).suffix.lower() != '.wav':
            # For mp3 with trim: write temp wav, then ffmpeg with filter
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                tmp = f.name
            sf.write(tmp, wav, sr)
            try:
                subprocess.check_call(['ffmpeg','-hide_banner','-loglevel','error','-y',
                                       '-i', tmp,
                                       '-af','silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:stop_periods=1:stop_duration=0.05:stop_threshold=-40dB',
                                       args.out])
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        elif args.trim_silence and Path(args.out).suffix.lower() == '.wav':
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                tmp = f.name
            sf.write(tmp, wav, sr)
            try:
                subprocess.check_call(['ffmpeg','-hide_banner','-loglevel','error','-y',
                                       '-i', tmp,
                                       '-af','silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:stop_periods=1:stop_duration=0.05:stop_threshold=-40dB',
                                       args.out])
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        else:
            write_audio(wav, sr, args.out)
        print(f"Wrote {args.out}")
    else:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            tmp = f.name
        sf.write(tmp, wav, sr)
        play_buf_filtered(tmp, player, args.trim_silence)
        try:
            os.remove(tmp)
        except OSError:
            pass
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
