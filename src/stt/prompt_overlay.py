#!/usr/bin/env python3
"""Small Tk overlay process for live prompt dictation."""

from __future__ import annotations

import json
import queue
import sys
import threading
from typing import Optional


def stdin_reader(messages: "queue.Queue[Optional[str]]") -> None:
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            messages.put(str(payload.get("text", "")))
        except json.JSONDecodeError:
            continue
    messages.put(None)


def main() -> int:
    try:
        import tkinter as tk
    except Exception:
        return 1

    messages: "queue.Queue[Optional[str]]" = queue.Queue()
    reader = threading.Thread(target=stdin_reader, args=(messages,), daemon=True)
    reader.start()

    try:
        root = tk.Tk()
    except Exception:
        return 1

    root.title("Linux Speech Tools Dictation")
    root.geometry("760x220+80+80")
    root.attributes("-topmost", True)

    text_var = tk.StringVar(value="Listening...")
    label = tk.Label(
        root,
        textvariable=text_var,
        anchor="nw",
        justify="left",
        wraplength=720,
        font=("Sans", 15),
        padx=18,
        pady=18,
    )
    label.pack(fill="both", expand=True)

    def poll() -> None:
        try:
            while True:
                value = messages.get_nowait()
                if value is None:
                    root.destroy()
                    return
                text_var.set(value or "Listening...")
        except queue.Empty:
            pass
        root.after(100, poll)

    root.after(100, poll)
    try:
        root.mainloop()
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
