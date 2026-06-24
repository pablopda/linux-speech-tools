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

import argparse, os, re, sys, shutil, tempfile, subprocess, unicodedata, time
import ipaddress, socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Version information
__version__ = "1.0.2"

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


def _is_public_ip(ip: "ipaddress._BaseAddress") -> bool:
    """Reject any address that is not a routable, public unicast IP.

    Covers loopback (127/8, ::1), link-local (169.254/16 incl. the cloud
    metadata endpoint 169.254.169.254, fe80::/10), private ranges
    (10/8, 172.16/12, 192.168/16), ULA (fc00::/7), and unspecified /
    multicast / reserved space.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _assert_public_url(url: str) -> str:
    """Validate scheme + resolve host and ensure every resolved IP is public.

    Returns the (unchanged) URL on success; raises ValueError on any
    disallowed scheme, unresolvable host, or non-public address. Used to
    guard every outbound request hop against SSRF.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ('http', 'https'):
        raise ValueError(f"refusing non-http(s) URL scheme: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise ValueError("URL has no host")
    # A bare IP literal in the URL still has to be public.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not _is_public_ip(literal):
        raise ValueError(f"refusing non-public address: {host}")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme.lower() == 'https' else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"could not resolve host {host!r}: {e}")
    if not infos:
        raise ValueError(f"could not resolve host {host!r}")
    for info in infos:
        sockaddr = info[4]
        try:
            resolved = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise ValueError(f"unparseable resolved address for {host!r}: {sockaddr[0]!r}")
        if not _is_public_ip(resolved):
            raise ValueError(f"host {host!r} resolves to non-public address {resolved}")
    return url


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

def _safe_get(url: str, debug: bool):
    """GET with SSRF guards: validate every hop's resolved IP and follow
    redirects manually (allow_redirects=False) up to MAX_URL_REDIRECTS.

    Returns the final streaming Response (caller must close it) or None.
    """
    ensure_web_deps()
    current = url
    for hop in range(MAX_URL_REDIRECTS + 1):
        _assert_public_url(current)  # re-validate each hop (raises on non-public)
        r = requests.get(current, timeout=20, stream=True, allow_redirects=False,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get('location')
            r.close()
            if not location:
                raise ValueError("redirect without Location header")
            # Resolve relative redirects against the current URL.
            current = requests.compat.urljoin(current, location)
            dbg(f"[say-read] following redirect to {current}", debug)
            continue
        r.raise_for_status()
        return r
    raise ValueError(f"too many redirects (>{MAX_URL_REDIRECTS})")


def _read_capped_bytes(r, debug: bool) -> bytes:
    buf = bytearray()
    for chunk in r.iter_content(65536):
        if not chunk:
            continue
        buf.extend(chunk)
        if len(buf) > MAX_URL_BYTES:
            dbg(f"[say-read] URL response exceeded {MAX_URL_BYTES} bytes; truncating", debug)
            break
    return bytes(buf)


def fetch_url(url: str, render: bool, debug: bool) -> str:
    html = ''
    content_type = ''
    try:
        r = _safe_get(url, debug)
        try:
            content_type = r.headers.get('content-type', '').lower()
            # Binary documents served over HTTP: hand off to the file extractors.
            if 'application/pdf' in content_type or url.lower().split('?', 1)[0].endswith('.pdf'):
                data = _read_capped_bytes(r, debug)
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
                    tmp = f.name
                    f.write(data)
                try:
                    return extract_pdf(tmp, debug)
                finally:
                    try: os.remove(tmp)
                    except OSError: pass
            if 'application/epub' in content_type or url.lower().split('?', 1)[0].endswith('.epub'):
                data = _read_capped_bytes(r, debug)
                with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as f:
                    tmp = f.name
                    f.write(data)
                try:
                    return extract_epub(tmp, debug)
                finally:
                    try: os.remove(tmp)
                    except OSError: pass
            if content_type and not any(t in content_type for t in ('text/', 'html', 'xml', 'json')):
                dbg(f"[say-read] unsupported content-type: {content_type}", debug)
                return ''
            data = _read_capped_bytes(r, debug)
            # Decode using the declared/apparent encoding rather than latin-1.
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
            _assert_public_url(url)  # guard the Playwright navigation too
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                page = b.new_page()
                page.goto(url, wait_until='networkidle', timeout=30000)
                # Redirects are followed inside the browser, so re-validate the
                # final URL: a redirect/DNS-rebind to an internal address must not
                # have its rendered content read back. (Sub-resource requests are
                # not covered — this guards the document navigation, the SSRF concern.)
                _assert_public_url(page.url)
                page.wait_for_timeout(1000)
                html = page.content()
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
                if t: return t
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
            try: os.remove(tmp)
            except OSError: pass

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
        try: os.remove(tmp)
        except: pass

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
        if args.debug: dbg(f"[say-read] clipped to {len(text)} chars (max-chars)", True)

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
                try: os.remove(tmp)
                except OSError: pass
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
                try: os.remove(tmp)
                except OSError: pass
        else:
            write_audio(wav, sr, args.out)
        print(f"Wrote {args.out}")
    else:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            tmp = f.name
        sf.write(tmp, wav, sr)
        play_buf_filtered(tmp, player, args.trim_silence)
        try: os.remove(tmp)
        except: pass
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
