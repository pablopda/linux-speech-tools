#!/usr/bin/env python3
"""Install or verify model assets used by Linux Speech Tools.

Python dependencies are installed by uv extras. This module handles the model
files that uv cannot provide: Kokoro ONNX assets and optional faster-whisper
model prefetching.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path


KOKORO_MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx"
)
KOKORO_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin"
)
KOKORO_MODEL_SHA256 = "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"
KOKORO_VOICES_SHA256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
MIN_KOKORO_MODEL_BYTES = 10 * 1024 * 1024
MIN_KOKORO_VOICES_BYTES = 1024 * 1024
ALLOWED_WHISPER_MODELS = {
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "large",
    "large-v1",
    "large-v2",
    "large-v3",
}


def default_kokoro_dir() -> Path:
    return Path(os.environ.get("KOKORO_MODEL_DIR", Path.home() / "models" / "kokoro"))


def default_kokoro_model() -> Path:
    return Path(
        os.environ.get(
            "KOKORO_MODEL",
            str(default_kokoro_dir() / "kokoro-v1.0.onnx"),
        )
    )


def default_kokoro_voices() -> Path:
    return Path(
        os.environ.get(
            "KOKORO_VOICES",
            str(default_kokoro_dir() / "voices-v1.0.bin"),
        )
    )


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(
    url: str,
    target: Path,
    dry_run: bool,
    min_bytes: int = 1,
    expected_sha256: str | None = None,
) -> None:
    target = target.expanduser()
    if target.exists() and target.stat().st_size >= min_bytes:
        if expected_sha256 and sha256_file(target) != expected_sha256:
            print(f"warning: {target} checksum mismatch; replacing")
        else:
            if expected_sha256:
                print(f"ok: {target} checksum verified")
            print(f"ok: {target} ({human_size(target.stat().st_size)})")
            return
    elif target.exists():
        print(
            f"warning: {target} is too small "
            f"({human_size(target.stat().st_size)} < {human_size(min_bytes)}); replacing"
        )
    if target.exists() and target.stat().st_size >= min_bytes and not expected_sha256:
        print(f"ok: {target} ({human_size(target.stat().st_size)})")
        return

    if dry_run:
        print(f"dry-run: would download {url}")
        print(f"dry-run: would write {target}")
        if expected_sha256:
            print(f"dry-run: would verify sha256 {expected_sha256}")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        print(f"downloading: {url}")
        with urllib.request.urlopen(url) as response, tmp_path.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        if tmp_path.stat().st_size < min_bytes:
            raise RuntimeError(
                f"downloaded file is too small: {human_size(tmp_path.stat().st_size)} "
                f"< {human_size(min_bytes)}"
            )
        if expected_sha256:
            actual_sha256 = sha256_file(tmp_path)
            if actual_sha256 != expected_sha256:
                raise RuntimeError(
                    f"sha256 mismatch for {url}: {actual_sha256} != {expected_sha256}"
                )
        tmp_path.replace(target)
        print(f"wrote: {target} ({human_size(target.stat().st_size)})")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def check_file(label: str, path: Path, min_bytes: int = 1) -> bool:
    path = path.expanduser()
    if path.exists() and path.stat().st_size >= min_bytes:
        print(f"ok: {label}: {path} ({human_size(path.stat().st_size)})")
        return True
    if path.exists():
        print(
            f"invalid: {label}: {path} "
            f"({human_size(path.stat().st_size)} < {human_size(min_bytes)})"
        )
        return False
    print(f"missing: {label}: {path}")
    return False


def setup_kokoro(args: argparse.Namespace) -> bool:
    model_path = Path(args.kokoro_model).expanduser()
    voices_path = Path(args.kokoro_voices).expanduser()

    if args.check:
        model_ok = check_file("Kokoro model", model_path, MIN_KOKORO_MODEL_BYTES)
        voices_ok = check_file("Kokoro voices", voices_path, MIN_KOKORO_VOICES_BYTES)
        return model_ok and voices_ok

    download_file(
        args.kokoro_model_url,
        model_path,
        args.dry_run,
        MIN_KOKORO_MODEL_BYTES,
        args.kokoro_model_sha256,
    )
    download_file(
        args.kokoro_voices_url,
        voices_path,
        args.dry_run,
        MIN_KOKORO_VOICES_BYTES,
        args.kokoro_voices_sha256,
    )
    return True


def clear_state(dry_run: bool) -> bool:
    candidates = []
    runtime_root = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_root:
        candidates.append(Path(runtime_root) / "linux-speech-tools")
    candidates.append(
        Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        / "linux-speech-tools"
    )

    for path in candidates:
        if not path.exists():
            continue
        if dry_run:
            print(f"dry-run: would remove {path}")
        else:
            shutil.rmtree(path)
            print(f"removed: {path}")
    return True


def whisper_repo_id(model_name: str) -> str:
    if model_name not in ALLOWED_WHISPER_MODELS:
        raise ValueError(
            f"unsupported faster-whisper model '{model_name}'. "
            f"Use one of: {', '.join(sorted(ALLOWED_WHISPER_MODELS))}"
        )
    if "/" in model_name:
        return model_name
    return f"Systran/faster-whisper-{model_name}"


def whisper_cache_status(model_name: str) -> str:
    repo_id = whisper_repo_id(model_name)
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        return "unknown: huggingface_hub cache scanner is unavailable"

    try:
        cache_info = scan_cache_dir()
    except Exception as exc:
        return f"unknown: could not scan Hugging Face cache ({exc})"

    for repo in cache_info.repos:
        if repo.repo_id == repo_id:
            return f"cached: {repo_id} ({human_size(repo.size_on_disk)})"
    return f"not cached: {repo_id} will download on first use"


def setup_whisper(args: argparse.Namespace) -> bool:
    if args.whisper_model not in ALLOWED_WHISPER_MODELS:
        print(
            f"error: unsupported faster-whisper model '{args.whisper_model}'. "
            f"Use one of: {', '.join(sorted(ALLOWED_WHISPER_MODELS))}",
            file=sys.stderr,
        )
        return False

    if args.check:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            print(
                "missing: faster-whisper is not installed. "
                "Run `uv sync --extra stt` first.",
                file=sys.stderr,
            )
            return False

        print("ok: faster-whisper import works")
        print(f"info: selected faster-whisper model: {args.whisper_model}")
        print(f"info: {whisper_cache_status(args.whisper_model)}")
        print(
            "info: faster-whisper stores allowlisted Systran models in the "
            "Hugging Face cache and downloads on first use."
        )
        return True

    if args.dry_run:
        print(f"dry-run: would prefetch faster-whisper model {args.whisper_model}")
        return True

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(
            "error: faster-whisper is not installed. "
            "Run `uv sync --extra stt` first.",
            file=sys.stderr,
        )
        return False

    print(f"prefetching faster-whisper model: {args.whisper_model}")
    WhisperModel(
        args.whisper_model,
        device=args.whisper_device,
        compute_type=args.whisper_compute_type,
    )
    print("ok: faster-whisper model is available in the local cache")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or verify model assets for Linux Speech Tools."
    )
    parser.add_argument("--kokoro", action="store_true", help="install/check Kokoro TTS model files")
    parser.add_argument("--stt", action="store_true", help="prefetch/check faster-whisper model cache")
    parser.add_argument("--all", action="store_true", help="process Kokoro and STT models")
    parser.add_argument("--check", action="store_true", help="verify model status without downloading")
    parser.add_argument("--dry-run", action="store_true", help="show actions without downloading")
    parser.add_argument("--clear-state", action="store_true", help="remove private logs, WAVs, and fallback transcripts")
    parser.add_argument(
        "--kokoro-model",
        default=str(default_kokoro_model()),
        help="target path for kokoro-v1.0.onnx",
    )
    parser.add_argument(
        "--kokoro-voices",
        default=str(default_kokoro_voices()),
        help="target path for voices-v1.0.bin",
    )
    parser.add_argument("--kokoro-model-url", default=KOKORO_MODEL_URL)
    parser.add_argument("--kokoro-voices-url", default=KOKORO_VOICES_URL)
    parser.add_argument("--kokoro-model-sha256", default=os.environ.get("KOKORO_MODEL_SHA256", KOKORO_MODEL_SHA256))
    parser.add_argument("--kokoro-voices-sha256", default=os.environ.get("KOKORO_VOICES_SHA256", KOKORO_VOICES_SHA256))
    parser.add_argument(
        "--whisper-model",
        default=os.environ.get("WHISPER_MODEL", "tiny"),
        help="faster-whisper model to prefetch",
    )
    parser.add_argument(
        "--whisper-device",
        default=os.environ.get("WHISPER_DEVICE", "cpu"),
        help="device for faster-whisper prefetch",
    )
    parser.add_argument(
        "--whisper-compute-type",
        default=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        help="compute type for faster-whisper prefetch",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.all:
        args.kokoro = True
        args.stt = True

    if args.clear_state:
        return 0 if clear_state(args.dry_run) else 1

    if not args.kokoro and not args.stt:
        if args.check:
            args.kokoro = True
            args.stt = True
        else:
            parser.print_help()
            return 2

    ok = True
    if args.kokoro:
        ok = setup_kokoro(args) and ok
    if args.stt:
        ok = setup_whisper(args) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
