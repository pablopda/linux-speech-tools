"""Behavioral quality tests for the Gold Standard chunker (J2 / J7).

The English and Spanish *test suites* under ``tests/integration`` are pure data
modules (``ENGLISH_TEST_SUITE`` / ``SPANISH_TEST_SUITE``). On their own they were
being collected by pytest as empty test files. Here we actually *drive* that data
through ``GoldStandardChunker`` and assert real invariants:

* every case produces at least one chunk (no empty output);
* no chunk exceeds the chunker's hard ``max_size``;
* the chunks round-trip back to the source text under whitespace normalization
  (content preservation -- consistent with the reconstruction assertion used by
  ``real_world_chunking_test.py``, see J9);
* no chunk is split mid-word across a boundary (the J3 invariant).

These tests are hermetic: the chunker uses only the standard library, so there
are no models, audio, or network dependencies.
"""

import re

import pytest

# Path setup is handled by tests/conftest.py (src/ and src/chunking on sys.path).
from gold_standard_chunker import GoldStandardChunker
from test_suite_english import ENGLISH_TEST_SUITE
from test_suite_spanish import SPANISH_TEST_SUITE


def _normalize(text: str) -> str:
    """Collapse runs of whitespace so trimmed-edge chunks compare equal."""
    return re.sub(r"\s+", " ", text).strip()


def _cases(suite, language):
    return [
        pytest.param(case, id=f"{language}-{case['id']:02d}-{case['name']}")
        for case in suite
    ]


ALL_CASES = _cases(ENGLISH_TEST_SUITE, "en") + _cases(SPANISH_TEST_SUITE, "es")


@pytest.fixture(scope="module")
def chunker():
    return GoldStandardChunker()


@pytest.mark.parametrize("case", ALL_CASES)
def test_case_produces_non_empty_chunks(chunker, case):
    chunks = chunker.gold_standard_chunk_text(case["text"])
    assert chunks, f"chunker returned no chunks for: {case['text']!r}"
    assert all(chunk.strip() for chunk in chunks), "produced a blank/whitespace chunk"


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_chunk_exceeds_max_size(chunker, case):
    chunks = chunker.gold_standard_chunk_text(case["text"])
    oversized = [len(chunk) for chunk in chunks if len(chunk) > chunker.max_size]
    assert not oversized, (
        f"chunks exceeded max_size={chunker.max_size}: sizes={oversized} "
        f"for case {case['name']!r}"
    )


@pytest.mark.parametrize("case", ALL_CASES)
def test_content_round_trips(chunker, case):
    chunks = chunker.gold_standard_chunk_text(case["text"])
    reconstructed = _normalize(" ".join(chunks))
    assert reconstructed == _normalize(case["text"]), (
        "content not preserved after chunking for case "
        f"{case['name']!r}"
    )


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_mid_word_split_across_boundaries(chunker, case):
    """A chunk must not end mid-word with the next chunk continuing that word.

    This is the same neighbour-comparison invariant fixed in
    ``real_world_chunking_test.py`` (J3): compare the END of each chunk to the
    START of the *next* chunk, guarded so the final chunk is never indexed past.
    """
    chunks = chunker.gold_standard_chunk_text(case["text"])
    cut_words = []
    for i, chunk in enumerate(chunks):
        if chunk.endswith((" ", "\t", "\n")):
            continue
        if (
            i < len(chunks) - 1
            and chunk[-1:].isalpha()
            and chunks[i + 1][0:1].isalpha()
        ):
            cut_words.append((i, chunk[-15:], chunks[i + 1][:15]))
    assert not cut_words, f"mid-word split detected for {case['name']!r}: {cut_words}"


def test_helper_off_by_one_detection_is_sound():
    """Guard against regressing the J3 fix in the shared detector logic.

    A genuine mid-word split (``comput`` | ``er``) must be detected, while a
    clean whitespace/sentence boundary must not be flagged.
    """

    def count_cut_words(chunks):
        n = 0
        for i, chunk in enumerate(chunks):
            if chunk.endswith((" ", "\t", "\n")):
                continue
            if (
                i < len(chunks) - 1
                and chunk[-1:].isalpha()
                and chunks[i + 1][0:1].isalpha()
            ):
                n += 1
        return n

    assert count_cut_words(["the comput", "er was fast."]) == 1
    assert count_cut_words(["First sentence.", "Second sentence."]) == 0
    assert count_cut_words(["ends with space ", "next start"]) == 0
    # The final chunk ending in a letter must NOT be flagged (no chunk after it).
    assert count_cut_words(["one.", "two"]) == 0
