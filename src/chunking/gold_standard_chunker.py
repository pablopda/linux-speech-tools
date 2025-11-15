#!/usr/bin/env uv run
# /// script
# dependencies = []  # Only uses standard library
# requires-python = ">=3.8"
# ///
"""
Gold Standard Text Chunker
Designed to match ideal chunking patterns identified in test suite
Addresses critical issues: empty chunks, abbreviation spacing, sentence boundaries
"""

import re
from typing import List, Dict, Tuple


class GoldStandardChunker:
    """
    Advanced chunker designed to match gold standard expectations
    Focuses on perfect sentence boundaries and TTS optimization
    """

    def __init__(self, target_size=150, max_size=300, prefer_sentence_boundaries=True, min_chunk_size=40):
        """
        Initialize gold standard chunker

        Args:
            target_size: Target characters per chunk (smaller for better TTS)
            max_size: Maximum chunk size before forced split
            prefer_sentence_boundaries: Prefer breaking at sentence boundaries over size optimization
            min_chunk_size: Minimum chunk size - combine smaller chunks
        """
        self.target_size = target_size
        self.max_size = max_size
        self.prefer_sentence_boundaries = prefer_sentence_boundaries
        self.min_chunk_size = min_chunk_size

        # Enhanced abbreviation patterns with exact spacing preservation
        self.abbreviations = {
            'english': [
                r'\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr)\.',
                r'\b(Ph\.D|M\.D|B\.A|M\.A|B\.S|M\.S)\.?',
                r'\b(U\.S\.A|U\.S|U\.K)\.(?!\s*$)',  # Protect U.S. with period unless at text end
                r'\b(etc|vs|i\.e|e\.g)\.',
                r'\b[0-9]+:[0-9]+ (A\.M|P\.M|a\.m|p\.m)(?=\s+[A-Z])',  # Only protect if NOT at sentence end
                r'\b(NASA|FBI|CIA|MIT|IBM|CEO|CFO|CTO|HTTP|HTTPS|SSL|TLS)\b',
            ],
            'spanish': [
                r'\b(Dr|Dra|Sr|Sra|Prof)\.',
                r'\b(EE\.UU)\b\.?',
                r'\b(etc|p\.ej|vs)\.',
                r'\b[0-9]+:[0-9]+ (A\.M|P\.M|a\.m|p\.m)\.',
                r'\b(NASA|FBI|CIA|MIT|IBM|CEO|CFO|CTO|HTTP|HTTPS|SSL|TLS)\b',
            ]
        }

        # Sentence boundary patterns (more precise)
        self.sentence_endings = {
            'english': re.compile(r'([.!?]+)(\s+)(?=[A-Z¡¿]|$)'),
            'spanish': re.compile(r'([.!?]+)(\s+)(?=[A-ZÁÉÍÓÚÑüéíóúá¡¿]|$)')
        }

        # Additional punctuation-based break patterns
        self.punctuation_breaks = [
            # Parenthetical statements
            re.compile(r'(\([^)]*\))(\s+)', re.IGNORECASE),
            # After exclamations followed by new sentences
            re.compile(r'(!)(\s+)(?=[A-Z¡¿])', re.IGNORECASE),
            # After semicolons with transition words
            re.compile(r'(;)(\s+)(specifically|namely|however|therefore|furthermore)', re.IGNORECASE),
        ]

        # Natural break points for longer chunks
        self.natural_breaks = [
            # Coordinating conjunctions
            re.compile(r'(\s+)(and|but|or|so|yet|y|pero|o)(\s+)', re.IGNORECASE),
            # Strong transitional phrases
            re.compile(r'(\s+)(however|therefore|furthermore|nevertheless|moreover|sin embargo|por lo tanto|además)(\s*,?\s+)', re.IGNORECASE),
            # After commas in lists
            re.compile(r'(,)(\s+)(?=\w)', re.IGNORECASE),
            # After semicolons
            re.compile(r'(;)(\s+)', re.IGNORECASE),
        ]

    def detect_language(self, text: str) -> str:
        """Detect if text is primarily English or Spanish"""
        # Check for Spanish-specific characters first
        if re.search(r'[áéíóúñü¡¿]', text):
            return 'spanish'

        # Count distinctive language patterns
        spanish_words = len(re.findall(r'\b(que|para|con|por|desde|hasta|donde|cuando|porque|aunque)\b', text.lower()))
        english_words = len(re.findall(r'\b(the|and|for|with|from|where|when|because|although|however)\b', text.lower()))

        # Spanish indicators
        spanish_indicators = spanish_words + len(re.findall(r'\b(el|la|los|las|es|en|de)\b', text.lower()))
        # English indicators
        english_indicators = english_words + len(re.findall(r'\b(a|an|of|in|to|is|was|were)\b', text.lower()))

        return 'spanish' if spanish_indicators > english_indicators else 'english'

    def protect_abbreviations(self, text: str, language: str) -> Tuple[str, Dict[str, str]]:
        """
        Protect abbreviations from being split by replacing them with placeholders
        Returns protected text and mapping for restoration
        """
        protection_map = {}
        protected_text = text

        for i, pattern in enumerate(self.abbreviations[language]):
            matches = list(re.finditer(pattern, protected_text))
            for j, match in enumerate(reversed(matches)):  # Reverse to maintain positions
                placeholder = f"__ABBREV_{i}_{j}__"
                protection_map[placeholder] = match.group(0)
                start, end = match.span()
                protected_text = protected_text[:start] + placeholder + protected_text[end:]

        return protected_text, protection_map

    def restore_abbreviations(self, text: str, protection_map: Dict[str, str]) -> str:
        """Restore protected abbreviations"""
        for placeholder, original in protection_map.items():
            text = text.replace(placeholder, original)
        return text

    def split_into_sentences(self, text: str, language: str) -> List[str]:
        """
        Split text into individual sentences with perfect boundary detection
        Handles complex punctuation patterns for better chunking
        """
        # First protect abbreviations
        protected_text, protection_map = self.protect_abbreviations(text, language)

        # Check for special punctuation patterns first
        for pattern in self.punctuation_breaks:
            if pattern.search(protected_text):
                # Split on this pattern
                parts = pattern.split(protected_text)
                processed_parts = []

                current_sentence = ""
                for i, part in enumerate(parts):
                    if i % 3 == 0:  # Text part
                        current_sentence += part
                    elif i % 3 == 1:  # Punctuation part
                        current_sentence += part
                        # For parenthetical and exclamations, end sentence here
                        if current_sentence.strip():
                            restored = self.restore_abbreviations(current_sentence.strip(), protection_map)
                            processed_parts.append(restored)
                        current_sentence = ""
                    else:  # Whitespace - skip
                        pass

                # Add any remaining text
                if current_sentence.strip():
                    restored = self.restore_abbreviations(current_sentence.strip(), protection_map)
                    processed_parts.append(restored)

                if processed_parts:
                    return processed_parts

        # Standard sentence splitting
        sentence_pattern = self.sentence_endings[language]
        parts = sentence_pattern.split(protected_text)

        sentences = []
        current = ""

        for i, part in enumerate(parts):
            if i % 3 == 0:  # Text part
                current += part
            elif i % 3 == 1:  # Punctuation part
                current += part
            else:  # Whitespace part
                if current.strip():
                    # Restore abbreviations before adding
                    restored = self.restore_abbreviations(current.strip(), protection_map)
                    sentences.append(restored)
                current = ""

        # Add any remaining text
        if current.strip():
            restored = self.restore_abbreviations(current.strip(), protection_map)
            sentences.append(restored)

        return [s for s in sentences if s.strip()]

    def find_optimal_break_point(self, text: str, max_pos: int) -> int:
        """
        Find the optimal break point in text before max_pos
        """
        if len(text) <= max_pos:
            return len(text)

        # High priority: comma + coordinating conjunction patterns
        comma_conjunction_pattern = re.compile(r'(,)(\s+)(and|but|or|so|yet)\s+', re.IGNORECASE)

        # Search for comma + conjunction pattern
        matches = list(comma_conjunction_pattern.finditer(text[:max_pos]))
        if matches:
            # Take the last match (closest to max_pos)
            last_match = matches[-1]
            return last_match.start() + len(last_match.group(1))  # Break after comma

        # Look for other natural break points
        search_start = max(0, max_pos - 100)  # Don't search too far back
        search_text = text[search_start:max_pos]

        best_break = -1
        best_priority = 0

        for priority, pattern in enumerate(self.natural_breaks):
            matches = list(pattern.finditer(search_text))
            if matches:
                # Take the last match (closest to max_pos)
                last_match = matches[-1]
                break_pos = search_start + last_match.start() + len(last_match.group(1))
                if priority >= best_priority:
                    best_break = break_pos
                    best_priority = priority

        # If no natural break found, break at last space
        if best_break == -1:
            last_space = text.rfind(' ', 0, max_pos)
            best_break = last_space if last_space != -1 else max_pos

        return best_break

    def chunk_long_sentence(self, sentence: str) -> List[str]:
        """
        Break down a sentence that's too long into smaller chunks
        while preserving meaning and natural flow
        """
        # Check for natural break points even in sentences <= max_size
        comma_conjunction_pattern = re.compile(r'(,)(\s+)(and|but|or|so|yet)\s+', re.IGNORECASE)
        matches = list(comma_conjunction_pattern.finditer(sentence))

        if matches and len(sentence) >= self.target_size:
            # Break at the best comma + conjunction point
            best_match = None
            target_position = len(sentence) * 0.6  # Prefer breaks around 60% through

            for match in matches:
                if not best_match or abs(match.start() - target_position) < abs(best_match.start() - target_position):
                    best_match = match

            if best_match:
                break_point = best_match.start() + len(best_match.group(1))  # Break after comma
                first_part = sentence[:break_point].strip()
                second_part = sentence[break_point:].strip()

                if first_part and second_part:
                    return [first_part, second_part]

        # Fallback: if no natural breaks or sentence is very long
        if len(sentence) <= self.max_size:
            return [sentence]

        chunks = []
        remaining = sentence

        while remaining and len(remaining) > self.max_size:
            break_point = self.find_optimal_break_point(remaining, self.max_size)

            if break_point <= 0:
                break_point = self.max_size

            chunk = remaining[:break_point].strip()
            if chunk:
                chunks.append(chunk)

            remaining = remaining[break_point:].strip()

        if remaining:
            chunks.append(remaining)

        return chunks

    def gold_standard_chunk_text(self, text: str) -> List[str]:
        """
        Main chunking method that follows gold standard patterns
        """
        if not text.strip():
            return []

        # Detect language for appropriate processing
        language = self.detect_language(text)

        # Split into sentences first
        sentences = self.split_into_sentences(text, language)

        if not sentences:
            # If sentence splitting failed, return the original text as one chunk
            return [text.strip()]

        chunks = []
        current_chunk = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # Check if this sentence alone is too long
            if len(sentence) > self.max_size:
                # Add current chunk if exists
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""

                # Break down the long sentence
                long_chunks = self.chunk_long_sentence(sentence)
                chunks.extend(long_chunks)
                continue

            # Gold standard logic: prefer sentence boundaries but be smarter about grouping
            if self.prefer_sentence_boundaries:
                # Check if current chunk + sentence would still be reasonable size
                potential_chunk = current_chunk + (" " if current_chunk else "") + sentence

                # Strategy 1: Very short sentences should be grouped if possible
                if len(sentence) <= self.min_chunk_size and len(potential_chunk) <= self.target_size:
                    current_chunk = potential_chunk
                    continue

                # Strategy 2: Medium sentences can be kept separate if reasonable
                elif len(sentence) < self.target_size and len(sentence) > self.min_chunk_size:
                    # Add current chunk if exists
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                        current_chunk = ""
                    # Add this sentence as its own chunk
                    chunks.append(sentence)
                    continue

                # Strategy 3: Large sentences need intelligent breaking
                elif len(sentence) >= self.target_size:
                    # Add current chunk if exists
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                        current_chunk = ""
                    # Try to break this sentence at natural points
                    sentence_chunks = self.chunk_long_sentence(sentence)
                    chunks.extend(sentence_chunks)
                    continue

            # Check if adding this sentence would exceed target size
            potential_chunk = current_chunk + (" " if current_chunk else "") + sentence

            if len(potential_chunk) <= self.target_size or not current_chunk:
                # Add to current chunk
                current_chunk = potential_chunk
            else:
                # Start new chunk
                chunks.append(current_chunk.strip())
                current_chunk = sentence

        # Add final chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        # Ensure we never return empty results for valid input
        if not chunks and text.strip():
            chunks = [text.strip()]

        return chunks

    def smart_chunk_text(self, text: str) -> List[str]:
        """
        Interface compatibility method for progressive content fetcher
        Delegates to our gold_standard_chunk_text method
        """
        return self.gold_standard_chunk_text(text)

    def analyze_gold_standard_quality(self, chunks: List[str]) -> Dict[str, any]:
        """Analyze chunk quality against gold standard criteria"""
        if not chunks:
            return {
                'total_chunks': 0,
                'avg_length': 0,
                'sentence_boundaries_preserved': 0,
                'abbreviations_intact': True,
                'issues': ['Empty result']
            }

        total_length = sum(len(chunk) for chunk in chunks)
        sentence_endings = sum(1 for chunk in chunks if chunk.rstrip().endswith(('.', '!', '?')))

        # Check for abbreviation issues
        abbreviation_issues = []
        for chunk in chunks:
            if '. ' in chunk and not chunk.endswith('.'):
                abbreviation_issues.append(f"Possible abbreviation split in: {chunk[:50]}...")

        return {
            'total_chunks': len(chunks),
            'avg_length': total_length / len(chunks),
            'sentence_boundaries_preserved': sentence_endings / len(chunks),
            'abbreviations_intact': len(abbreviation_issues) == 0,
            'abbreviation_issues': abbreviation_issues,
            'chunk_sizes': [len(chunk) for chunk in chunks]
        }


# Test function
def test_gold_standard_vs_existing():
    """Test the gold standard chunker against existing algorithms"""
    from enhanced_chunking import NaturalSpeechChunker
    from test_suite_english import ENGLISH_TEST_SUITE

    print("🏆 GOLD STANDARD CHUNKER VS EXISTING ALGORITHMS")
    print("=" * 60)

    gold_chunker = GoldStandardChunker()
    natural_chunker = NaturalSpeechChunker()

    # Test on first 5 test cases
    for test in ENGLISH_TEST_SUITE[:5]:
        print(f"\n📋 Test: {test['name']}")
        print(f"Text: {test['text']}")

        # Gold standard results
        gold_chunks = gold_chunker.gold_standard_chunk_text(test['text'])

        # Expected ideal chunks
        ideal_chunks = test['ideal_chunks']

        print(f"\nIdeal ({len(ideal_chunks)} chunks):")
        for i, chunk in enumerate(ideal_chunks, 1):
            print(f"  {i}: {chunk}")

        print(f"\nGold Standard ({len(gold_chunks)} chunks):")
        for i, chunk in enumerate(gold_chunks, 1):
            print(f"  {i}: {chunk}")

        # Check for exact match
        if gold_chunks == ideal_chunks:
            print("  ✅ EXACT MATCH!")
        else:
            print("  ⚠️  Differences found")

        print("-" * 50)


if __name__ == "__main__":
    test_gold_standard_vs_existing()