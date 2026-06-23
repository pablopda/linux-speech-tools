# experiments/ — archived prototypes (not part of the package)

This directory holds code that is **not on any runtime path** and is **excluded
from the installed package and the test suite**. It is kept for reference and
history, not for use. The June 2026 audit
(`docs/planning/CODE_AUDIT_2026-06.md`) confirmed none of it is reachable from
the live tools.

Do not import from here in `src/` or `bin/`. If you revive a module, move it
back into `src/` and add real tests.

## Contents

- `chunking/` — superseded text-chunking prototypes. The live chunker is
  `src/chunking/gold_standard_chunker.py`; these earlier variants
  (`smart_chunking`, `enhanced_chunking`, `tts_optimized_chunking`,
  `chunk_quality_analyzer`, `chunking_test_framework`) diverged from it and
  carried their own bugs (e.g. short inputs silently dropped to `[]`).
- `stt/` — standalone dictation prototypes (`faster_whisper_simple`,
  `faster_whisper_vad`, `manual_faster_check`). The live dictation core is
  `src/stt/session.py` + `src/stt/runtime.py`, driven by
  `src/stt/faster_whisper_auto.py`. `faster_whisper_vad` is a diverged copy of
  the session core.
- `tts/` — `early_start_player.py`, an MVP progressive-streaming player that
  was never wired up (its imports never resolved). The live read-aloud path is
  `src/tts/say_read.py`.
- `analysis/` — one-off developer scripts used to tune the chunkers against the
  gold-standard suites. They import the prototypes above by bare name and
  expect to be run manually with the appropriate `PYTHONPATH`.
