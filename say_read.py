#!/usr/bin/env python3
"""
say_read.py — Offline reader using kokoro-onnx

Key features:
- URL / PDF / EPUB / HTML / TXT extraction (OCR fallback for image-only PDFs)
- Cleans UI junk, chunks safely, force-splits when needed (never stalls)
- Plays once at end OR streams piece-by-piece right away
- Progress logs with per-piece timings
- Optional --max-chars cap and JS render (--render) for SPA pages

Examples:
  ~/.venvs/tts/bin/python ~/bin/say_read.py --player ffplay --max-chars 6000 --stream https://www.bbc.com/news/technology
  ~/.venvs/tts/bin/python ~/bin/say_read.py -l es -v ef_dora --player ffplay https://elpais.com/tecnologia/
  ~/.venvs/tts/bin/python ~/bin/say_read.py -o /tmp/article.mp3 https://www.bbc.com/news/technology
  lynx -dump -nolist URL | head -c 5000 | ~/.venvs/tts/bin/python ~/bin/say_read.py --player ffplay -  # stdin
"""

import argparse, os, re, sys, shutil, tempfile, subprocess, unicodedata, time
from pathlib import Path

# Version information
__version__ = "1.0.0"

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

import requests
from bs4 import BeautifulSoup
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

def dbg(msg: str, enabled: bool):
    if enabled:
        print(msg, file=sys.stderr, flush=True)

def clean_text(s: str) -> str:
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\[(?:[^\]]+)\]', ' ', s)          # [link text]
    s = re.sub(r'(BUTTON|Share|Comments)', ' ', s, flags=re.I)
    def keep(ch):
        cat = unicodedata.category(ch)
        return not (cat.startswith('C') or cat.startswith('M') or cat.startswith('S'))
    s = ''.join(ch if keep(ch) else ' ' for ch in s)
    return re.sub(r'\s+', ' ', s).strip()

def split_sentences(text: str, maxlen: int) -> list[str]:
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


# ======================== extraction ========================

def fetch_url(url: str, render: bool, debug: bool) -> str:
    html = ''
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        html = r.text
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
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                page = b.new_page()
                page.goto(url, wait_until='networkidle', timeout=30000)
                page.wait_for_timeout(1000)
                html = page.content()
                b.close()
            soup = BeautifulSoup(html, 'lxml')
            main_text = soup.get_text(separator=' ', strip=True)
            dbg("[say-read] used Playwright render", debug)
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
            subprocess.run(
                ['pdftoppm','-r','200',path, f'{tmpdir}/page','-png'],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            parts=[]
            for img in sorted(Path(tmpdir).glob('page-*.png')):
                try:
                    out = subprocess.run(
                        ['tesseract', str(img), 'stdout', '-l', 'eng+spa', '--psm', '6'],
                        check=False, capture_output=True, text=True
                    )
                    parts.append(out.stdout)
                except Exception:
                    pass
            return '\n'.join(parts)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return ''

def extract_epub(path: str, debug: bool) -> str:
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

def synth_retry(k: Kokoro, text: str, voice: str | None, lang: str, debug: bool, depth: int = 0):
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

def write_audio(arr: np.ndarray, sr: int, out: str):
    out_path = Path(out)
    if out_path.suffix.lower() == '.wav':
        sf.write(out, arr, sr)
    else:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            tmp = f.name
        sf.write(tmp, arr, sr)
        subprocess.check_call(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',tmp,out])
        os.remove(tmp)

def play_buf(arr: np.ndarray, sr: int, player: str | None):
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        tmp = f.name
    sf.write(tmp, arr, sr)
    try:
        if player == 'ffplay':
            subprocess.call(['ffplay','-hide_banner','-loglevel','error','-nodisp','-autoexit', tmp])
        elif player == 'mpv':
            subprocess.call(['mpv','--no-video','--really-quiet', tmp])
        elif player == 'paplay':
            subprocess.call(['paplay', tmp])
        elif player == 'aplay':
            subprocess.call(['aplay', tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            print(f"[say-read] saved to {tmp} (no player found)", file=sys.stderr)
            return
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
        subprocess.call(cmd)
    elif player == 'mpv':
        cmd = ['mpv','--no-video','--really-quiet', wav_path]
        if trim:
            cmd = ['mpv','--no-video','--really-quiet','--af=lavfi="[silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:stop_periods=1:stop_duration=0.05:stop_threshold=-40dB]"', wav_path]
        subprocess.call(cmd)
    elif player == 'paplay':
        subprocess.call(['paplay', wav_path])
    elif player == 'aplay':
        subprocess.call(['aplay', wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(f"[say-read] saved to {wav_path} (no player found)", file=sys.stderr)

def stream_fast(k, pieces, voice, lang, debug):
    # Requires ffplay
    if not shutil.which('ffplay'):
        dbg("[say-read] --stream-fast needs ffplay; falling back to --stream", True)
        return None

    try:
        proc = subprocess.Popen(
            ['ffplay','-hide_banner','-loglevel','error','-nodisp','-autoexit',
             '-f','s16le','-ar','24000','-i','-'],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except Exception as e:
        dbg(f"[say-read] failed to start ffplay: {e}", True)
        return None

    total_t = 0.0
    try:
        for i, p in enumerate(pieces, 1):
            a, sr, did_split, dt = synth_retry(k, p, voice, lang, debug)
            total_t += dt
            pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype('<i2').tobytes()
            if proc.stdin is None:
                break
            try:
                proc.stdin.write(pcm)
                proc.stdin.flush()
            except BrokenPipeError:
                dbg("[say-read] ffplay closed early", debug)
                break
            if debug:
                dbg(f"[say-read] [fast {i}/{len(pieces)}] len={len(p)} split={did_split} synth={dt:.2f}s total={total_t:.2f}s", True)
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()
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

    raw = extract_input(args.source, args.render, args.debug)
    text = clean_text(raw)

    if args.max_chars and len(text) > args.max_chars:
        text = text[:args.max_chars]
        if args.debug: dbg(f"[say-read] clipped to {len(text)} chars (max-chars)", True)

    if args.debug:
        dbg(f"[say-read] extracted {len(raw)} chars; after cleanup {len(text)} chars", True)
        dbg(f"[say-read] sample: {text[:400]}", True)

    if not text:
        print("[say-read] no text extracted", file=sys.stderr)
        return 1

    # init Kokoro
    k = Kokoro(args.model, args.voices)
    voice = args.voice or ('ef_dora' if args.lang.lower().startswith('es') else 'af_heart')

    pieces = split_sentences(text, args.chunk)
    if args.debug:
        dbg(f"[say-read] pieces: {len(pieces)}", True)

    player = args.player or next((p for p in ('ffplay','mpv','paplay','aplay') if shutil.which(p)), None)

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
            if args.debug:
                dbg(f"[say-read] [{i}/{len(pieces)}] len={len(p)} split={did_split} synth={dt:.2f}s total={total_t:.2f}s", True)
            play_buf(a, sr, player)
        return 0

    # Non-stream: synth all, then play once or write file
    audio_list = []
    sr = None
    total_t = 0.0
    for i, p in enumerate(pieces, 1):
        a, sr, did_split, dt = synth_retry(k, p, voice, args.lang, args.debug)
        audio_list.append(a)
        total_t += dt
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
            subprocess.check_call(['ffmpeg','-hide_banner','-loglevel','error','-y',
                                   '-i', tmp,
                                   '-af','silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:stop_periods=1:stop_duration=0.05:stop_threshold=-40dB',
                                   args.out])
            os.remove(tmp)
        elif args.trim_silence and Path(args.out).suffix.lower() == '.wav':
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                tmp = f.name
            sf.write(tmp, wav, sr)
            subprocess.check_call(['ffmpeg','-hide_banner','-loglevel','error','-y',
                                   '-i', tmp,
                                   '-af','silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:stop_periods=1:stop_duration=0.05:stop_threshold=-40dB',
                                   args.out])
            os.remove(tmp)
        else:
            write_audio(wav, sr, args.out)
        print(f"Wrote {args.out}")
    else:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            tmp = f.name
        sf.write(tmp, wav, sr)
        play_buf_filtered(tmp, args.player or None, args.trim_silence)
        try: os.remove(tmp)
        except: pass
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
