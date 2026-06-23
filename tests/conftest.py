"""Shared pytest configuration for the test suite.

Responsibilities:

* Make the in-repo source packages importable without per-file ``sys.path``
  hacks. The chunking modules under ``src/chunking`` are imported by their bare
  module name (e.g. ``gold_standard_chunker``), and ``src`` itself is a namespace
  root for ``src.stt`` / ``src.tts`` imports. Both are wired up here once so the
  integration and unit tests resolve imports consistently (J4).
* Keep ``real_world_validation_report.py`` out of collection. It is a manual
  diagnostic *report* (no ``test_`` functions, no asserts) rather than a test,
  so it must never be imported during a normal ``pytest tests/`` run (J1).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CHUNKING = SRC / "chunking"
INTEGRATION = ROOT / "tests" / "integration"

# ``src`` for ``import src.stt...`` / ``import src.tts...`` style imports;
# ``src/chunking`` so the chunker modules resolve by bare name; and
# ``tests/integration`` so the ENGLISH/SPANISH test-suite data modules
# (``test_suite_english`` / ``test_suite_spanish``) import by bare name from the
# unit tests too. Insert at the front so the repo copy always wins over anything
# installed site-wide.
for path in (str(ROOT), str(SRC), str(CHUNKING), str(INTEGRATION)):
    if path not in sys.path:
        sys.path.insert(0, path)


# Files under tests/ that look importable but are NOT pytest tests. They are
# excluded from collection so a broken/optional top-level import in a manual
# report can never break ``pytest tests/`` (J1).
collect_ignore = ["integration/real_world_validation_report.py"]
collect_ignore_glob = ["**/real_world_validation_report.py"]
