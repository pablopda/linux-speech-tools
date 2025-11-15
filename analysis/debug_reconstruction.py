#!/usr/bin/env python3
"""
Debug Text Reconstruction Issue
Identify the 5-character difference in the chunked content
"""

from gold_standard_chunker import GoldStandardChunker

def debug_reconstruction():
    """Debug the text reconstruction discrepancy"""

    article_text = """The attention economy is inverting
By Sam Schillace

Once upon a time, in the way back before generative AI, people would have to work hard to produce a digital object of any real quality - an image, a document, a report, a PowerPoint. This was helpful in one way - the obvious effort that went into its creation was a good signal or proxy for quality. We've discussed this part of it before, and this effect was present across all kinds of media - both personal and broadcast. If you could get on TV, you likely had something to say, because (for a while at least) it was hard to do that. Filters and signals are great heuristics for deciding what to pay attention to, or they used to be.

But there's a different kind of economy at play here, too. At some fundamental level, giving you an artifact like this is a "transaction" in what we might call an attention economy. That is, I "spent" a bunch of my time and attention to create something, and "gave" that attention to you. You can consume it with (often) less attention. The value is flowing from me to you in the "attention economy" - I am doing more attentional work than you are. (this is a little more complicated because I might do one thing and share it to many people, so I can be amortizing my own attention work, but that's ok - we can still contemplate each individual interaction for the purposes here).

But what happens in the generative world? In that world, the signal is broken - a complex, well-crafted artifact is much easier to build, so it's appearance is no longer a proxy for quality. This is a familiar effect. But the other thing that's happening is that the attentional transaction is now "inverted" - I may have spend a small amount of my time and attention to take up a large amount of yours. I'm no longer "paying" you with attention, or transferring value to you, I'm taking it away.

This problem has recently been labeled "work slop", and that's a good example. Work often has this signaling problem, or even a 'busy work' problem: make someone produce a report not because it's valuable per se but because the act of producing it as an assurance that the work was done or is a proxy for some other activity."""

    gold_chunker = GoldStandardChunker()
    chunks = gold_chunker.gold_standard_chunk_text(article_text)

    reconstructed = ''.join(chunks)

    print("🔍 DEBUGGING TEXT RECONSTRUCTION DISCREPANCY")
    print("=" * 60)
    print(f"Original length: {len(article_text)}")
    print(f"Reconstructed length: {len(reconstructed)}")
    print(f"Difference: {len(article_text) - len(reconstructed)} characters")

    # Find the exact differences
    print(f"\n🔎 CHARACTER-BY-CHARACTER COMPARISON")
    print("-" * 40)

    min_len = min(len(article_text), len(reconstructed))
    differences_found = 0

    for i in range(min_len):
        if article_text[i] != reconstructed[i]:
            print(f"Difference at position {i}:")
            print(f"  Original: '{article_text[i]}' (ord: {ord(article_text[i])})")
            print(f"  Reconstructed: '{reconstructed[i]}' (ord: {ord(reconstructed[i])})")

            # Show context
            start = max(0, i-20)
            end = min(len(article_text), i+21)
            print(f"  Context orig: '{article_text[start:end]}'")
            print(f"  Context reco: '{reconstructed[start:end]}'")
            print()
            differences_found += 1

            if differences_found >= 3:  # Limit output
                break

    # Check if one is longer than the other
    if len(article_text) != len(reconstructed):
        longer = article_text if len(article_text) > len(reconstructed) else reconstructed
        shorter = reconstructed if len(article_text) > len(reconstructed) else article_text
        print(f"Extra characters in {'original' if longer == article_text else 'reconstructed'}:")
        print(f"'{longer[len(shorter):]}'")

    return differences_found == 0

if __name__ == "__main__":
    is_perfect = debug_reconstruction()
    if is_perfect:
        print("✅ Perfect reconstruction!")
    else:
        print("⚠️ Minor reconstruction differences found")