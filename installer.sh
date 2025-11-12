#!/usr/bin/env bash
# Linux Speech Tools Installer
# VERSION=1.0.0

cat > /home/arkat/bin/talk2claude <<'TOOL'
#!/usr/bin/env bash
set -euo pipefail

# Defaults
STT_VENV="${STT_VENV:-$HOME/.venvs/stt}"
WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"
ASR_LANG="${ASR_LANG:-en}"        # "auto" to auto-detect
MODE="paste"                      # or "type"
SEND_ENTER=0
DUR="${DUR:-8}"
PASTE_DELAY="${PASTE_DELAY:-0.35}"
CLIPBOARD_ONLY=0                  # auto-enabled in terminals (see below)

# Absolute paths
YDO="${YDO:-$(command -v ydotool || echo /usr/bin/ydotool)}"
FFM="${FFM:-$(command -v ffmpeg  || echo /usr/bin/ffmpeg)}"
WLCOPY="$(command -v wl-copy || true)"
XCLIP="$(command -v xclip || true)"
NOTIFY="$(command -v notify-send || true)"   # sudo apt install libnotify-bin

# Stable runtime dir
RDIR="$HOME/.cache/t2c"
PID="$RDIR/ffmpeg.pid"
WAV="$RDIR/clip.wav"
LOG="$RDIR/rec.log"
mkdir -p "$RDIR"

usage(){ cat >&2 <<EOF
talk2claude [no args] => record ${DUR}s, transcribe, paste
Subcommands:
  toggle        start if idle; else stop + transcribe + (copy+paste)
  start         start background recording
  stop          stop recording; transcribe; (copy+paste)
  status        show recorder status
Options:
  -l, --lang CODE|auto
  -m, --model NAME
      --type               type instead of clipboard paste
      --enter              press Enter after inserting
      --time SECONDS       one-shot duration
      --clipboard-only     copy text to clipboard, don’t paste
      --paste-delay SEC    delay before Ctrl+V
Env:
  T2C_ALLOW_PASTE_IN_TERMINAL=1  # allow auto-paste when run inside a terminal
EOF
exit 2; }

# --- helpers ---
alive(){ kill -0 "$1" 2>/dev/null; }
beep(){ canberra-gtk-play --id="$1" >/dev/null 2>&1 || true; }
note(){ [ -n "$NOTIFY" ] && "$NOTIFY" "$@"; }

ensure_socket(){
  # choose socket path without compound tests
  local sock=""
  if [ -n "${YDOTOOL_SOCKET:-}" ] && [ -S "$YDOTOOL_SOCKET" ]; then
    sock="$YDOTOOL_SOCKET"
  elif [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -S "$XDG_RUNTIME_DIR/.ydotool_socket" ]; then
    sock="$XDG_RUNTIME_DIR/.ydotool_socket"
  elif [ -S /tmp/.ydotool_socket ]; then
    sock="/tmp/.ydotool_socket"
  fi
  if [ -n "$sock" ]; then
    export YDOTOOL_SOCKET="$sock"
    return 0
  fi
  return 1
}

copy_to_clipboard(){
  # prefer wl-clipboard; fallback xclip
  if [ -n "$WLCOPY" ]; then
    printf '%s' "$1" | "$WLCOPY"
    return 0
  elif [ -n "$XCLIP" ]; then
    printf '%s' "$1" | "$XCLIP" -selection clipboard
    return 0
  fi
  return 1
}

do_insert(){
  local text="$1"
  copy_to_clipboard "$text" || true

  if [ "$CLIPBOARD_ONLY" -eq 1 ]; then
    note "Dictation" "Copied to clipboard. Press Ctrl+V to paste."
    return 0
  fi
  if ! ensure_socket; then
    note "Dictation" "Copied to clipboard (no auto-paste: ydotool socket missing)."
    return 0
  fi

  if [ "$MODE" = "paste" ] && [ -n "$WLCOPY" -o -n "$XCLIP" ]; then
    sleep "$PASTE_DELAY"
    # Ctrl+V (silenced)
    "$YDO" key 29:1 47:1 47:0 29:0 >/dev/null 2>&1
  else
    "$YDO" type "$text" >/dev/null 2>&1
  fi
  if [ "$SEND_ENTER" -eq 1 ]; then
    "$YDO" key 28:1 28:0 >/dev/null 2>&1
  fi
}

start_rec(){
  # clean stale pid
  if [ -f "$PID" ]; then
    local p; p="$(cat "$PID" 2>/dev/null || true)"
    if [ -z "${p:-}" ] || ! alive "$p"; then rm -f "$PID"; fi
  fi
  if [ -f "$PID" ]; then
    note "Dictation" "Already recording."
    return 0
  fi
  rm -f "$WAV"
  beep audio-input-start
  note "Dictation" "Recording… Press the shortcut again to stop."
  "$FFM" -nostdin -f pulse -i default -ac 1 -ar 16000 -c:a pcm_s16le -y "$WAV" >"$LOG" 2>&1 &
  echo $! > "$PID"
}

stop_rec(){
  # return 1 if not recording (so toggle knows to start)
  if [ ! -f "$PID" ]; then
    return 1
  fi
  local p; p="$(cat "$PID" 2>/dev/null || true)"
  if [ -z "${p:-}" ] || ! alive "$p"; then rm -f "$PID"; return 1; fi
  note "Dictation" "Stopping… (finalizing audio)"
  kill -INT "$p" 2>/dev/null || true
  # wait up to ~3s for ffmpeg to exit
  local i=0
  while [ $i -lt 30 ]; do
    if ! alive "$p"; then break; fi
    sleep 0.1
    i=$((i+1))
  done
  if alive "$p"; then kill -TERM "$p" 2>/dev/null || true; sleep 0.2; fi
  if alive "$p"; then kill -KILL "$p" 2>/dev/null || true; fi
  rm -f "$PID"
  sleep 0.15
  beep audio-input-stop
}

transcribe_and_insert(){
  if [ ! -s "$WAV" ]; then
    note "Dictation" "No audio captured."
    return 1
  fi
  local py="$STT_VENV/bin/python"
  if [ ! -x "$py" ]; then
    note "Dictation" "Error: STT venv missing."
    return 1
  fi
  note "Dictation" "Transcribing…"
  # run Whisper
  local txt
  txt="$("$py" - <<'PY' "$WAV" "$WHISPER_MODEL" "$ASR_LANG"
import sys, re
from faster_whisper import WhisperModel
audio, model_name, lang_in = sys.argv[1], sys.argv[2], sys.argv[3]
code = (lang_in or "").strip().lower()
code = re.split(r'[._-]', code)[0] if code else ""
lang = None if code in ("", "auto") else code
m = WhisperModel(model_name, device="cpu", compute_type="int8")
segments, info = m.transcribe(audio, beam_size=5, language=lang)
print(" ".join(s.text.strip() for s in segments).strip())
PY
)"
  if [ -z "${txt// }" ]; then
    note "Dictation" "No speech recognized."
    return 1
  fi
  note "Dictation" "Ready. Pasting in ${PASTE_DELAY}s…"
  do_insert "$txt"
  note "Dictation" "Done."
}

one_shot(){
  "$FFM" -nostdin -f pulse -i default -t "$DUR" -ac 1 -ar 16000 -c:a pcm_s16le -y "$WAV" -loglevel error
  transcribe_and_insert
}

# --- parse args (support flags before/after subcommand) ---
CMD=""
while [ $# -gt 0 ]; do
  case "$1" in
    toggle|start|stop|status) CMD="$1"; shift ;;
    -l|--lang)  ASR_LANG="${2:?}"; shift 2 ;;
    -m|--model) WHISPER_MODEL="${2:?}"; shift 2 ;;
    --type)     MODE="type"; shift ;;
    --enter)    SEND_ENTER=1; shift ;;
    --time)     DUR="${2:?}"; shift 2 ;;
    --clipboard-only) CLIPBOARD_ONLY=1; shift ;;
    --paste-delay)    PASTE_DELAY="${2:?}"; shift 2 ;;
    -h|--help)  usage ;;
    --) shift; break ;;
    *)  # ignore unknown trailing stuff
        break ;;
  esac
done

# Auto-safe when invoked from a terminal (unless user opts in)
if [ -t 1 ] && [ -z "${T2C_ALLOW_PASTE_IN_TERMINAL:-}" ]; then
  CLIPBOARD_ONLY=1
fi

# --- main ---
case "${CMD:-}" in
  toggle)
    if stop_rec; then
      transcribe_and_insert
    else
      start_rec
    fi
    ;;
  start)  start_rec ;;
  stop)   stop_rec && transcribe_and_insert ;;
  status) if [ -f "$PID" ]; then echo "[t2c] recording (pid $(cat "$PID"))"; else echo "[t2c] idle"; fi ;;
  "")     one_shot ;;
  *)      usage ;;
esac
TOOL
chmod +x /home/arkat/bin/talk2claude
