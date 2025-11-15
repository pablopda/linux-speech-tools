#!/usr/bin/env uv run
# /// script
# dependencies = []  # Only uses standard library
# requires-python = ">=3.8"
# ///
"""
TTS-Optimized Chunking for Progressive Streaming
Chunking specifically optimized to avoid word cutoffs in TTS engines
"""

import re
import time
from typing import List

class TTSOptimizedChunker:
    """Chunker specifically designed to prevent TTS word cutoff issues"""

    def __init__(self, target_size=280, max_size=450, min_size=120):
        """
        Initialize TTS-optimized chunker

        Args:
            target_size: Target characters per chunk
            max_size: Maximum chunk size
            min_size: Minimum chunk size
        """
        self.target_size = target_size
        self.max_size = max_size
        self.min_size = min_size

        # Sentence endings with required spacing
        self.sentence_endings = re.compile(r'([.!?])\s+')
        self.paragraph_breaks = re.compile(r'\n\s*\n')

        # Patterns that should NOT be split (abbreviations, etc.)
        self.protected_patterns = [
            re.compile(r'(Mr|Mrs|Ms|Dr|Prof|Sr|Jr)\.\s+', re.IGNORECASE),
            re.compile(r'(etc|vs|e\.g|i\.e)\.\s+', re.IGNORECASE),
            re.compile(r'(U\.S\.|U\.K\.|Ph\.D)\.\s+', re.IGNORECASE),
            re.compile(r'\b\d+\.\d+\s+'),  # Decimal numbers
        ]

    def tts_chunk_text(self, text: str) -> List[str]:
        """
        Create chunks optimized for TTS without word cutoffs

        Args:
            text: Input text to chunk

        Returns:
            List of text chunks that won't cause TTS word cutoffs
        """
        # Normalize text
        text = self._normalize_for_tts(text)

        # Split into paragraphs
        paragraphs = self.paragraph_breaks.split(text)

        chunks = []
        for paragraph in paragraphs:
            if not paragraph.strip():
                continue

            para_chunks = self._chunk_paragraph_for_tts(paragraph.strip())
            chunks.extend(para_chunks)

        return [chunk for chunk in chunks if len(chunk.strip()) >= self.min_size]

    def _normalize_for_tts(self, text: str) -> str:
        """Normalize text specifically for TTS processing"""
        # Remove excessive whitespace
        text = ' '.join(text.split())

        # Ensure proper spacing after punctuation
        text = re.sub(r'([.!?])([A-Z])', r'\1 \2', text)
        text = re.sub(r'([;:,])([^\s])', r'\1 \2', text)

        # Ensure sentences end with proper spacing
        text = re.sub(r'([.!?])(\s*)$', r'\1', text)  # Clean ending
        text = re.sub(r'([.!?])(\s+)([A-Z])', r'\1 \2\3', text)  # Proper spacing

        return text.strip()

    def _chunk_paragraph_for_tts(self, paragraph: str) -> List[str]:
        """Chunk paragraph with TTS-safe boundaries"""
        if len(paragraph) <= self.max_size:
            # Single chunk - ensure it has proper termination
            return [self._ensure_tts_termination(paragraph)]

        # Split into sentences using TTS-safe method
        sentences = self._split_sentences_tts_safe(paragraph)

        chunks = []
        current_chunk = ""

        for sentence in sentences:
            # Handle very long sentences
            if len(sentence) > self.max_size:
                if current_chunk:
                    chunks.append(self._ensure_tts_termination(current_chunk))
                    current_chunk = ""

                # Split long sentence at safe points
                long_chunks = self._split_long_sentence_tts_safe(sentence)
                chunks.extend([self._ensure_tts_termination(chunk) for chunk in long_chunks])
                continue

            # Try adding sentence to current chunk
            test_chunk = current_chunk + " " + sentence if current_chunk else sentence

            if len(test_chunk) <= self.target_size:
                # Safe to add
                current_chunk = test_chunk
            elif len(current_chunk) < self.min_size and len(test_chunk) <= self.max_size:
                # Current chunk too small, force inclusion if possible
                current_chunk = test_chunk
            else:
                # Save current chunk and start new one
                if current_chunk:
                    chunks.append(self._ensure_tts_termination(current_chunk))
                current_chunk = sentence

        # Add final chunk
        if current_chunk:
            chunks.append(self._ensure_tts_termination(current_chunk))

        return chunks

    def _split_sentences_tts_safe(self, paragraph: str) -> List[str]:
        """Split sentences while protecting abbreviations and special cases"""
        # Protect abbreviations first
        protected_text = paragraph
        protection_map = {}
        protection_counter = 0

        for pattern in self.protected_patterns:
            for match in pattern.finditer(paragraph):
                placeholder = f"__PROTECT_{protection_counter}__"
                protection_map[placeholder] = match.group(0)
                protected_text = protected_text.replace(match.group(0), placeholder, 1)
                protection_counter += 1

        # Split at sentence boundaries
        parts = self.sentence_endings.split(protected_text)

        # Reconstruct sentences properly
        sentences = []
        for i in range(0, len(parts) - 1, 2):
            if i + 1 < len(parts):
                # Combine sentence content with its punctuation
                sentence_content = parts[i].strip()
                punctuation = parts[i + 1]  # The punctuation mark

                if sentence_content:
                    full_sentence = sentence_content + punctuation

                    # Restore protected patterns
                    for placeholder, original in protection_map.items():
                        full_sentence = full_sentence.replace(placeholder, original)

                    sentences.append(full_sentence.strip())

        # Handle final part (no punctuation following)
        if len(parts) % 2 == 1 and parts[-1].strip():
            final_part = parts[-1].strip()

            # Restore protected patterns
            for placeholder, original in protection_map.items():
                final_part = final_part.replace(placeholder, original)

            sentences.append(final_part)

        return sentences

    def _split_long_sentence_tts_safe(self, sentence: str) -> List[str]:
        """Split very long sentences at safe TTS points"""
        # Try natural break points in order of preference
        break_patterns = [
            (r';\s+', 'semicolon'),
            (r',\s+(and|but|or|however|therefore|moreover)\s+', 'conjunction'),
            (r',\s+which\s+', 'relative clause'),
            (r',\s+', 'comma'),
        ]

        for pattern, break_type in break_patterns:
            matches = list(re.finditer(pattern, sentence, re.IGNORECASE))
            if matches:
                # Find the break point closest to the middle
                target_pos = len(sentence) // 2
                best_match = min(matches, key=lambda m: abs(m.end() - target_pos))

                # Split at this point
                first_part = sentence[:best_match.end()].strip()
                second_part = sentence[best_match.end():].strip()

                # Validate both parts are reasonable
                if len(first_part) >= self.min_size and len(second_part) >= self.min_size:
                    chunks = [first_part]

                    # Recursively handle the second part if it's still too long
                    if len(second_part) > self.max_size:
                        chunks.extend(self._split_long_sentence_tts_safe(second_part))
                    else:
                        chunks.append(second_part)

                    return chunks

        # Last resort: split at word boundaries
        return self._split_at_words(sentence)

    def _split_at_words(self, text: str) -> List[str]:
        """Split text at word boundaries as last resort"""
        words = text.split()
        chunks = []
        current_chunk = ""

        for word in words:
            test_chunk = current_chunk + " " + word if current_chunk else word

            if len(test_chunk) > self.target_size:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = word
            else:
                current_chunk = test_chunk

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _ensure_tts_termination(self, chunk: str) -> str:
        """Ensure chunk has proper termination for TTS processing"""
        chunk = chunk.strip()

        if not chunk:
            return chunk

        # Add period if chunk doesn't end with sentence punctuation
        # This helps TTS engines properly handle the end of the chunk
        if not chunk.endswith(('.', '!', '?')):
            # Check if it ends with other punctuation that might be intentional
            if chunk.endswith((':',';', ',')):
                # These need completion, add period
                chunk = chunk + '.'
            else:
                # Regular text, add period
                chunk = chunk + '.'

        return chunk

def test_tts_chunking_vs_natural():
    """Test TTS-optimized chunking vs the natural speech chunking"""
    print("🎵 TTS-Optimized Chunking vs Natural Speech Chunking")
    print("=" * 70)

    # Test with realistic content that might cause TTS cutoff issues
    test_texts = [
        """The progressive streaming system works by processing content in parallel; however,
        poor chunking can create unnatural speech patterns""",

        """Dr. Smith from the U.S. Department said the study showed promising results.
        The participants, i.e., people aged 18-65, experienced significant improvements""",

        """Traditional approaches create delays. However, this methodology employs sophisticated
        architecture. Furthermore, users experience seamless playback without gaps"""
    ]

    tts_chunker = TTSOptimizedChunker(target_size=250, max_size=400)

    for i, text in enumerate(test_texts):
        print(f"\n🔍 Test {i+1}: {text[:50]}...")
        print(f"Length: {len(text)} characters")

        chunks = tts_chunker.tts_chunk_text(text)

        print(f"Generated {len(chunks)} TTS-optimized chunks:")
        for j, chunk in enumerate(chunks):
            print(f"  {j+1}: \"{chunk}\"")

            # Check for potential TTS issues
            if not chunk.endswith(('.', '!', '?')):
                print(f"      ⚠️  No sentence termination")
            if len(chunk) > 400:
                print(f"      ⚠️  Very long chunk ({len(chunk)} chars)")

        print()

if __name__ == "__main__":
    test_tts_chunking_vs_natural()