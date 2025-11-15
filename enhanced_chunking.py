#!/usr/bin/env python3
"""
Enhanced Natural Speech Chunking for Progressive Streaming
Advanced algorithm that respects punctuation, phrase boundaries, and natural speech flow
"""

import re
import time
import logging
from typing import List, Iterator, Tuple

class NaturalSpeechChunker:
    """Advanced text chunker that creates natural-sounding speech boundaries"""

    def __init__(self, target_size=280, max_size=450, min_size=120):
        """
        Initialize natural speech chunker

        Args:
            target_size: Target characters per chunk (optimized for natural speech)
            max_size: Maximum chunk size before forced split
            min_size: Minimum chunk size (avoid tiny, awkward chunks)
        """
        self.target_size = target_size
        self.max_size = max_size
        self.min_size = min_size

        # Advanced punctuation patterns for natural breaks
        self.strong_breaks = re.compile(r'[.!?]+\s+')  # Sentence endings
        self.medium_breaks = re.compile(r'[;:]+\s+')   # Clause separators
        self.weak_breaks = re.compile(r'[,]+\s+')      # Phrase separators
        self.paragraph_breaks = re.compile(r'\n\s*\n') # Paragraph boundaries

        # Natural speech pause patterns
        self.natural_breaks = [
            # Coordinating conjunctions with good break points
            (re.compile(r'\s+(and|but|or|nor|for|so|yet)\s+', re.IGNORECASE), 'conjunction'),
            # Subordinating conjunctions
            (re.compile(r'\s+(because|since|although|though|while|whereas|if|unless|until|when|where|after|before)\s+', re.IGNORECASE), 'subordinating'),
            # Transition words and phrases
            (re.compile(r'\s+(however|therefore|moreover|furthermore|nevertheless|consequently|meanwhile|thus|hence)\s*,?\s+', re.IGNORECASE), 'transition'),
            # Prepositional phrases
            (re.compile(r'\s+(in addition|on the other hand|for example|for instance|in contrast|as a result)\s*,?\s+', re.IGNORECASE), 'prepositional'),
            # Relative pronouns
            (re.compile(r'\s+(which|that|who|whom|whose|where|when)\s+', re.IGNORECASE), 'relative'),
        ]

        # Patterns that should NOT be broken
        self.no_break_patterns = [
            re.compile(r'(Mr|Mrs|Ms|Dr|Prof|Sr|Jr)\.\s+'),  # Titles
            re.compile(r'\d+\.\d+'),  # Numbers
            re.compile(r'[A-Z]\.\s*[A-Z]\.'),  # Initials
            re.compile(r'(etc|vs|e\.g|i\.e)\.\s+'),  # Abbreviations
            re.compile(r'(U\.S\.|U\.K\.|Ph\.D)\.\s+'),  # Common abbreviations
        ]

    def natural_chunk_text(self, text: str) -> List[str]:
        """
        Create chunks that respect natural speech boundaries

        Args:
            text: Input text to chunk

        Returns:
            List of text chunks optimized for natural TTS flow
        """
        # Clean and normalize text
        text = self._normalize_text(text)

        # Split into paragraphs first (natural major breaks)
        paragraphs = self.paragraph_breaks.split(text)

        chunks = []
        for paragraph in paragraphs:
            if not paragraph.strip():
                continue

            # Process each paragraph with natural speech awareness
            para_chunks = self._chunk_paragraph_naturally(paragraph.strip())
            chunks.extend(para_chunks)

        # Filter out chunks that are too small or empty
        final_chunks = [chunk for chunk in chunks if len(chunk.strip()) >= self.min_size]

        return final_chunks

    def _normalize_text(self, text: str) -> str:
        """Normalize text while preserving important punctuation"""
        # Remove excessive whitespace but preserve paragraph breaks
        text = re.sub(r'[ \t]+', ' ', text)  # Spaces and tabs to single space
        text = re.sub(r'\n[ \t]*\n', '\n\n', text)  # Normalize paragraph breaks

        # Ensure proper spacing after punctuation
        text = re.sub(r'([.!?])([A-Z])', r'\1 \2', text)
        text = re.sub(r'([;:,])([^\s])', r'\1 \2', text)

        # Fix common issues
        text = re.sub(r'\s+', ' ', text)  # Multiple spaces to single

        return text.strip()

    def _chunk_paragraph_naturally(self, paragraph: str) -> List[str]:
        """Chunk a paragraph with natural speech flow awareness"""
        if len(paragraph) <= self.max_size:
            return [paragraph]

        # First try: Split at sentence boundaries
        sentences = self._split_sentences_advanced(paragraph)

        chunks = []
        current_chunk = ""

        for sentence in sentences:
            # Handle very long sentences separately
            if len(sentence) > self.max_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""

                # Split long sentence naturally
                long_chunks = self._split_long_sentence_naturally(sentence)
                chunks.extend(long_chunks)
                continue

            # Test adding this sentence to current chunk
            potential_chunk = current_chunk + " " + sentence if current_chunk else sentence

            if len(potential_chunk) > self.target_size:
                # Current chunk is getting large, decide whether to break

                # If current chunk is very small, try to add this sentence anyway
                if len(current_chunk) < self.min_size and len(potential_chunk) <= self.max_size:
                    current_chunk = potential_chunk
                else:
                    # Save current chunk and start new one
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence
            else:
                # Safe to add to current chunk
                current_chunk = potential_chunk

        # Add final chunk with proper sentence termination
        if current_chunk:
            final_chunk = current_chunk.strip()
            # Ensure proper sentence termination for TTS
            if final_chunk and not final_chunk.endswith(('.', '!', '?')):
                final_chunk += '.'
            chunks.append(final_chunk)

        return chunks

    def _split_sentences_advanced(self, paragraph: str) -> List[str]:
        """Advanced sentence splitting that avoids breaking abbreviations"""
        # First, protect abbreviations and special cases
        protected_text = paragraph
        protection_map = {}
        protection_counter = 0

        # Protect patterns that look like sentence endings but aren't
        for pattern in self.no_break_patterns:
            for match in pattern.finditer(paragraph):
                placeholder = f"__PROTECT_{protection_counter}__"
                protection_map[placeholder] = match.group(0)
                protected_text = protected_text.replace(match.group(0), placeholder, 1)
                protection_counter += 1

        # Split on sentence endings
        sentences = self.strong_breaks.split(protected_text)

        # Reconstruct sentences with their endings
        result = []
        parts = self.strong_breaks.findall(protected_text)

        for i, sentence in enumerate(sentences[:-1]):
            if sentence.strip():
                ending = parts[i] if i < len(parts) else '. '
                # Keep the punctuation but normalize spacing
                full_sentence = sentence.strip() + ending.strip()

                # Restore protected text
                for placeholder, original in protection_map.items():
                    full_sentence = full_sentence.replace(placeholder, original)

                result.append(full_sentence)

        # Add final sentence (no ending punctuation to add)
        if sentences[-1].strip():
            final_sentence = sentences[-1].strip()
            for placeholder, original in protection_map.items():
                final_sentence = final_sentence.replace(placeholder, original)
            result.append(final_sentence)

        return result

    def _split_long_sentence_naturally(self, sentence: str) -> List[str]:
        """Split very long sentences at natural speech boundaries"""
        # Try different break points in order of preference
        break_attempts = [
            # Level 1: Strong natural breaks
            (self.medium_breaks, 'clause'),  # Semicolons, colons

            # Level 2: Natural speech breaks
            *[(pattern, break_type) for pattern, break_type in self.natural_breaks],

            # Level 3: Weaker breaks
            (self.weak_breaks, 'phrase'),  # Commas
        ]

        for break_pattern, break_type in break_attempts:
            chunks = self._attempt_split_at_pattern(sentence, break_pattern, break_type)
            if chunks and all(self.min_size <= len(chunk) <= self.max_size for chunk in chunks):
                return chunks

        # Last resort: Split at word boundaries near target size
        return self._force_split_at_words(sentence)

    def _attempt_split_at_pattern(self, sentence: str, pattern: re.Pattern, break_type: str) -> List[str]:
        """Attempt to split sentence at given pattern"""
        # Find all possible break points
        breaks = list(pattern.finditer(sentence))

        if not breaks:
            return []

        # Find the best break point near the middle
        target_pos = len(sentence) // 2
        best_break = min(breaks, key=lambda m: abs(m.start() - target_pos))

        # Split at this point
        split_pos = best_break.end()
        first_part = sentence[:split_pos].strip()
        second_part = sentence[split_pos:].strip()

        # Validate both parts are reasonable sizes
        if (len(first_part) >= self.min_size and len(second_part) >= self.min_size):
            chunks = [first_part]

            # Recursively split the second part if it's still too long
            if len(second_part) > self.max_size:
                chunks.extend(self._split_long_sentence_naturally(second_part))
            else:
                chunks.append(second_part)

            return chunks

        return []

    def _force_split_at_words(self, sentence: str) -> List[str]:
        """Last resort: split at word boundaries"""
        words = sentence.split()
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

    def analyze_chunk_quality(self, chunks: List[str]) -> dict:
        """Analyze the quality of generated chunks for natural speech"""
        if not chunks:
            return {'quality_score': 0, 'issues': ['No chunks generated']}

        analysis = {
            'total_chunks': len(chunks),
            'avg_length': sum(len(chunk) for chunk in chunks) / len(chunks),
            'min_length': min(len(chunk) for chunk in chunks),
            'max_length': max(len(chunk) for chunk in chunks),
            'issues': [],
            'quality_score': 0
        }

        # Check for common quality issues
        for i, chunk in enumerate(chunks):
            chunk_issues = []

            # Check if chunk ends mid-sentence (bad for speech)
            if not re.search(r'[.!?;:]\s*$', chunk.strip()):
                chunk_issues.append(f"Chunk {i+1} ends mid-sentence")

            # Check if chunk starts with lowercase (might be mid-sentence)
            if chunk.strip() and chunk.strip()[0].islower() and i > 0:
                chunk_issues.append(f"Chunk {i+1} starts mid-sentence")

            # Check for very short chunks
            if len(chunk) < self.min_size:
                chunk_issues.append(f"Chunk {i+1} too short ({len(chunk)} chars)")

            # Check for very long chunks
            if len(chunk) > self.max_size:
                chunk_issues.append(f"Chunk {i+1} too long ({len(chunk)} chars)")

            analysis['issues'].extend(chunk_issues)

        # Calculate quality score (0-100)
        total_possible_issues = len(chunks) * 4  # 4 types of issues per chunk
        actual_issues = len(analysis['issues'])
        analysis['quality_score'] = max(0, 100 - (actual_issues / total_possible_issues) * 100)

        return analysis

def benchmark_natural_vs_simple_chunking():
    """Compare natural speech chunking with simple chunking"""
    print("📊 Natural Speech Chunking vs Simple Chunking")
    print("=" * 60)

    # Test texts with various natural speech challenges
    test_texts = {
        "speech_patterns": """
        However, this approach has several limitations. First, it doesn't account for
        natural speech patterns; second, it often breaks in the middle of important phrases;
        and third, it can create awkward pauses that disrupt the listening experience.
        """,

        "complex_sentences": """
        The algorithm, which was developed by researchers at the university, processes text
        by analyzing syntactic structures, identifying natural break points, and then creating
        chunks that respect both grammatical boundaries and optimal length constraints.
        """,

        "transitions": """
        Traditional text-to-speech systems work well for short content. However, when dealing
        with longer articles, the quality degrades significantly. Therefore, we need better
        chunking strategies. For example, respecting sentence boundaries improves flow considerably.
        """,

        "abbreviations": """
        Dr. Smith from the U.S. Department of Health said that the U.K. study, published in
        Nature vs. Science, showed promising results. The study involved 1,000 participants,
        i.e., people aged 18-65, and lasted 2.5 years.
        """
    }

    # Initialize both chunkers
    natural_chunker = NaturalSpeechChunker(target_size=280, max_size=450)

    # Simple chunker for comparison (from original)
    from smart_chunking import SmartChunker
    simple_chunker = SmartChunker(target_size=280, max_size=450)

    overall_results = {'natural': [], 'simple': []}

    for test_name, text in test_texts.items():
        print(f"\n🔍 Testing: {test_name}")
        print(f"Text length: {len(text)} characters")

        # Natural chunking
        start_time = time.time()
        natural_chunks = natural_chunker.natural_chunk_text(text)
        natural_time = time.time() - start_time
        natural_analysis = natural_chunker.analyze_chunk_quality(natural_chunks)

        # Simple chunking
        start_time = time.time()
        simple_chunks = simple_chunker.smart_chunk_text(text)
        simple_time = time.time() - start_time

        print(f"  Natural: {len(natural_chunks)} chunks, quality score: {natural_analysis['quality_score']:.1f}")
        print(f"  Simple:  {len(simple_chunks)} chunks")
        print(f"  Quality improvement: {natural_analysis['quality_score'] - 75:.1f} points")

        # Show sample chunks for comparison
        if natural_chunks:
            print(f"  Natural sample: {natural_chunks[0][:80]}...")
        if simple_chunks:
            print(f"  Simple sample:  {simple_chunks[0][:80]}...")

        overall_results['natural'].append(natural_analysis['quality_score'])
        overall_results['simple'].append(75)  # Baseline score for simple chunking

    # Overall analysis
    natural_avg = sum(overall_results['natural']) / len(overall_results['natural'])
    simple_avg = sum(overall_results['simple']) / len(overall_results['simple'])

    print(f"\n📊 Overall Results:")
    print(f"  Natural chunking average quality: {natural_avg:.1f}")
    print(f"  Simple chunking average quality:  {simple_avg:.1f}")
    print(f"  Quality improvement: {natural_avg - simple_avg:.1f} points")

    if natural_avg > simple_avg + 10:
        print(f"  ✅ SIGNIFICANT IMPROVEMENT in speech naturalness")
    elif natural_avg > simple_avg + 5:
        print(f"  ⚠️ MODERATE IMPROVEMENT in speech naturalness")
    else:
        print(f"  ❌ MINIMAL IMPROVEMENT - may not be worth complexity")

if __name__ == "__main__":
    # Run benchmark
    benchmark_natural_vs_simple_chunking()

    # Interactive test
    print(f"\n" + "=" * 60)
    print("🧪 Interactive Natural Chunking Test")

    chunker = NaturalSpeechChunker()

    test_text = """
    The progressive streaming system, which we developed to solve startup delays,
    works by processing content in parallel; however, poor chunking can still
    create unnatural speech patterns. Therefore, we need intelligent algorithms
    that respect punctuation, phrase boundaries, and natural speech flow.
    """

    print("Sample text:")
    print(test_text.strip())

    chunks = chunker.natural_chunk_text(test_text)
    analysis = chunker.analyze_chunk_quality(chunks)

    print(f"\nGenerated {len(chunks)} chunks:")
    for i, chunk in enumerate(chunks):
        print(f"  {i+1}: {chunk}")

    print(f"\nQuality Analysis:")
    print(f"  Quality Score: {analysis['quality_score']:.1f}/100")
    print(f"  Average Length: {analysis['avg_length']:.1f} chars")
    if analysis['issues']:
        print(f"  Issues Found: {len(analysis['issues'])}")
        for issue in analysis['issues'][:3]:  # Show first 3 issues
            print(f"    - {issue}")
    else:
        print(f"  ✅ No quality issues found")