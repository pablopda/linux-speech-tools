#!/usr/bin/env python3
"""
Real-World Chunking Test: Substack Article
Test our 100% pass rate Gold Standard chunker on actual content
"""

import re
import sys
from pathlib import Path

# Import path for the chunker is provided centrally by tests/conftest.py (J4).
# Fall back to a local insert so this file still runs as a standalone script
# (``python real_world_chunking_test.py``) without pytest/conftest loaded.
if __name__ == "__main__" or "gold_standard_chunker" not in sys.modules:
    _CHUNKING = Path(__file__).resolve().parents[2] / "src" / "chunking"
    if str(_CHUNKING) not in sys.path:
        sys.path.insert(0, str(_CHUNKING))

from gold_standard_chunker import GoldStandardChunker

def run_real_world_content():
    """Test our chunker on real Substack article content"""

    print("🌍 REAL-WORLD CHUNKING TEST: SUBSTACK ARTICLE")
    print("=" * 70)

    # The article content
    article_text = """The attention economy is inverting
By Sam Schillace

Once upon a time, in the way back before generative AI, people would have to work hard to produce a digital object of any real quality - an image, a document, a report, a PowerPoint. This was helpful in one way - the obvious effort that went into its creation was a good signal or proxy for quality. We've discussed this part of it before, and this effect was present across all kinds of media - both personal and broadcast. If you could get on TV, you likely had something to say, because (for a while at least) it was hard to do that. Filters and signals are great heuristics for deciding what to pay attention to, or they used to be.

But there's a different kind of economy at play here, too. At some fundamental level, giving you an artifact like this is a "transaction" in what we might call an attention economy. That is, I "spent" a bunch of my time and attention to create something, and "gave" that attention to you. You can consume it with (often) less attention. The value is flowing from me to you in the "attention economy" - I am doing more attentional work than you are. (this is a little more complicated because I might do one thing and share it to many people, so I can be amortizing my own attention work, but that's ok - we can still contemplate each individual interaction for the purposes here).

But what happens in the generative world? In that world, the signal is broken - a complex, well-crafted artifact is much easier to build, so it's appearance is no longer a proxy for quality. This is a familiar effect. But the other thing that's happening is that the attentional transaction is now "inverted" - I may have spend a small amount of my time and attention to take up a large amount of yours. I'm no longer "paying" you with attention, or transferring value to you, I'm taking it away.

This problem has recently been labeled "work slop", and that's a good example. Work often has this signaling problem, or even a 'busy work' problem: make someone produce a report not because it's valuable per se but because the act of producing it as an assurance that the work was done or is a proxy for some other activity."""

    # Initialize our Gold Standard chunker
    gold_chunker = GoldStandardChunker()

    print(f"📰 Article: 'The attention economy is inverting'")
    print(f"📊 Original length: {len(article_text)} characters")
    print(f"📊 Word count: {len(article_text.split())} words")

    print(f"\n🔧 Processing with Gold Standard chunker...")

    # Process the content
    chunks = gold_chunker.gold_standard_chunk_text(article_text)

    print(f"\n📊 CHUNKING RESULTS")
    print("=" * 50)
    print(f"Number of chunks: {len(chunks)}")
    print(f"Average chunk length: {sum(len(chunk) for chunk in chunks) / len(chunks):.1f} chars")

    # Analyze for TTS quality issues
    print(f"\n🔍 TTS QUALITY ANALYSIS")
    print("=" * 50)

    word_cutoff_issues = 0
    spacing_issues = 0
    abbreviation_issues = 0

    for i, chunk in enumerate(chunks):
        # Check for word cutoffs: a chunk ending mid-word means the CURRENT chunk
        # ends with a letter AND the NEXT chunk starts with a letter (the word was
        # split across the boundary). Compare neighbours, not the chunk to itself.
        if chunk.endswith((' ', '\t', '\n')):
            pass  # Good - ends with whitespace
        elif (
            i < len(chunks) - 1
            and chunk[-1:].isalpha()
            and chunks[i + 1][0:1].isalpha()
        ):
            word_cutoff_issues += 1
            print(f"   ⚠️ Potential word cutoff between chunks {i + 1} and {i + 2}")

        # Check for spacing issues
        if '  ' in chunk:
            spacing_issues += 1

        # Check for abbreviation handling (U.S., Dr., etc.)
        abbrev_patterns = [r'\bU\.S\.\s+[a-z]', r'\bDr\.\s+[a-z]', r'\bMr\.\s+[a-z]']
        for pattern in abbrev_patterns:
            if re.search(pattern, chunk, re.IGNORECASE):
                abbreviation_issues += 1

    # Display detailed chunks
    print(f"\n📝 DETAILED CHUNKS FOR TTS")
    print("=" * 50)

    for i, chunk in enumerate(chunks, 1):
        chunk_preview = chunk.strip()[:100] + "..." if len(chunk) > 100 else chunk.strip()
        print(f"Chunk {i:2d} ({len(chunk):3d} chars): {chunk_preview}")

    # Quality summary
    print(f"\n✅ TTS QUALITY SUMMARY")
    print("=" * 50)
    print(f"Word cutoff issues: {word_cutoff_issues}")
    print(f"Spacing issues: {spacing_issues}")
    print(f"Abbreviation issues: {abbreviation_issues}")

    if word_cutoff_issues == 0 and spacing_issues == 0:
        print("🎉 PERFECT TTS QUALITY: No word cutoffs or spacing issues detected!")
        print("🎵 Ready for smooth audio playback!")
    else:
        print("⚠️ Some TTS quality issues detected - may affect audio flow")

    # Verify text reconstruction
    # The chunker intentionally trims chunk edges, so reconstruct with a single
    # separator and compare normalized content rather than byte-for-byte spacing.
    reconstructed = ' '.join(chunk.strip() for chunk in chunks)
    normalize = lambda value: re.sub(r'\s+', ' ', value).strip()
    reconstruction_ok = normalize(reconstructed) == normalize(article_text)
    if reconstruction_ok:
        print("✅ TEXT RECONSTRUCTION: Perfect content preservation!")
    else:
        print("❌ TEXT RECONSTRUCTION: Content altered during chunking")
        print(f"   Original length: {len(article_text.strip())}")
        print(f"   Reconstructed length: {len(reconstructed.strip())}")

    return chunks, reconstruction_ok, word_cutoff_issues, spacing_issues

def test_real_world_content():
    chunks, reconstruction_ok, word_cutoff_issues, spacing_issues = run_real_world_content()
    assert chunks
    assert reconstruction_ok
    assert word_cutoff_issues == 0
    assert spacing_issues == 0

if __name__ == "__main__":
    chunks = run_real_world_content()

    # Optional: Show first 3 chunks in detail for inspection
    print(f"\n🔍 SAMPLE CHUNKS FOR INSPECTION")
    print("=" * 50)
    for i, chunk in enumerate(chunks[:3], 1):
        print(f"\nChunk {i}:")
        print(f"'{chunk}'")
        print(f"Length: {len(chunk)} characters")
        print("-" * 30)
