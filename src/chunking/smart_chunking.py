#!/usr/bin/env python3
"""
Smart Content Chunking for Progressive Streaming MVP
Simple optimization to reduce processing time and improve user experience
"""

import re
import time
import logging
from typing import List, Iterator

class SmartChunker:
    """Improved text chunking for faster TTS processing"""

    def __init__(self, target_size=250, max_size=400, min_size=100):
        """
        Initialize smart chunker with optimized parameters

        Args:
            target_size: Target characters per chunk (reduced from 320)
            max_size: Maximum chunk size before forced split
            min_size: Minimum chunk size (avoid tiny chunks)
        """
        self.target_size = target_size
        self.max_size = max_size
        self.min_size = min_size

        # Sentence ending patterns
        self.sentence_endings = re.compile(r'[.!?]+\s+')

        # Paragraph break patterns
        self.paragraph_breaks = re.compile(r'\n\s*\n')

    def smart_chunk_text(self, text: str) -> List[str]:
        """
        Split text into smart chunks respecting sentence boundaries

        Args:
            text: Input text to chunk

        Returns:
            List of text chunks optimized for TTS
        """
        # Clean and normalize text
        text = self._normalize_text(text)

        # Split into paragraphs first
        paragraphs = self.paragraph_breaks.split(text)

        chunks = []
        for paragraph in paragraphs:
            if not paragraph.strip():
                continue

            # Process each paragraph
            para_chunks = self._chunk_paragraph(paragraph.strip())
            chunks.extend(para_chunks)

        return [chunk for chunk in chunks if len(chunk.strip()) >= self.min_size]

    def _normalize_text(self, text: str) -> str:
        """Normalize text for better processing"""
        # Remove extra whitespace
        text = ' '.join(text.split())

        # Ensure space after punctuation
        text = re.sub(r'([.!?])([A-Z])', r'\1 \2', text)

        return text

    def _chunk_paragraph(self, paragraph: str) -> List[str]:
        """Chunk a single paragraph into optimal sizes"""
        if len(paragraph) <= self.max_size:
            return [paragraph]

        # Split into sentences
        sentences = self._split_sentences(paragraph)

        chunks = []
        current_chunk = ""

        for sentence in sentences:
            # If single sentence is too long, force split
            if len(sentence) > self.max_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""

                # Split long sentence
                long_chunks = self._force_split_sentence(sentence)
                chunks.extend(long_chunks)
                continue

            # Check if adding sentence exceeds target
            potential_chunk = current_chunk + " " + sentence if current_chunk else sentence

            if len(potential_chunk) > self.target_size:
                # Current chunk is ready
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                current_chunk = potential_chunk

        # Add remaining chunk
        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks

    def _split_sentences(self, paragraph: str) -> List[str]:
        """Split paragraph into sentences"""
        # Use regex to split on sentence endings
        sentences = self.sentence_endings.split(paragraph)

        # Reconstruct sentences with their endings
        result = []
        parts = self.sentence_endings.findall(paragraph)

        for i, sentence in enumerate(sentences[:-1]):
            if sentence.strip():
                ending = parts[i] if i < len(parts) else '. '
                result.append(sentence.strip() + ending.strip())

        # Add last sentence
        if sentences[-1].strip():
            result.append(sentences[-1].strip())

        return result

    def _force_split_sentence(self, sentence: str) -> List[str]:
        """Force split a long sentence at natural breaks"""
        # Try to split at commas, semicolons, or conjunctions
        break_points = [', ', '; ', ' and ', ' but ', ' or ', ' because ', ' although ']

        for break_point in break_points:
            if break_point in sentence:
                parts = sentence.split(break_point, 1)
                if len(parts[0]) > self.min_size and len(parts[1]) > self.min_size:
                    result = [parts[0] + break_point.rstrip()]
                    result.extend(self._force_split_sentence(parts[1]))
                    return result

        # Last resort: split at target size
        chunks = []
        while len(sentence) > self.max_size:
            # Find last space before max_size
            split_pos = sentence.rfind(' ', 0, self.max_size)
            if split_pos == -1:
                split_pos = self.max_size

            chunks.append(sentence[:split_pos])
            sentence = sentence[split_pos:].lstrip()

        if sentence:
            chunks.append(sentence)

        return chunks

class ProgressiveContentFetcher:
    """Fetch and chunk content progressively for early audio start"""

    def __init__(self, chunker: SmartChunker = None):
        self.chunker = chunker or SmartChunker()

    def fetch_and_chunk_progressive(self, source: str) -> Iterator[str]:
        """
        Fetch content and yield chunks as soon as they're ready

        Args:
            source: URL, file path, or stdin marker

        Yields:
            Text chunks ready for TTS processing
        """
        if source.startswith(('http://', 'https://')):
            yield from self._fetch_url_progressive(source)
        elif source == '-':
            yield from self._fetch_stdin_progressive()
        else:
            yield from self._fetch_file_progressive(source)

    def _fetch_url_progressive(self, url: str) -> Iterator[str]:
        """Fetch URL content and chunk progressively"""
        try:
            import requests
            from bs4 import BeautifulSoup

            print(f"⏳ Fetching: {url}")

            # Stream the response
            with requests.get(url, stream=True, timeout=10) as response:
                response.raise_for_status()

                content_buffer = ""
                for chunk in response.iter_content(chunk_size=8192, decode_unicode=True):
                    content_buffer += chunk or ""

                    # Process when we have enough content for parsing
                    if len(content_buffer) > 4096:
                        text_chunks = self._extract_and_chunk_text(content_buffer)
                        for text_chunk in text_chunks:
                            yield text_chunk
                        content_buffer = ""

                # Process remaining content
                if content_buffer:
                    text_chunks = self._extract_and_chunk_text(content_buffer)
                    for text_chunk in text_chunks:
                        yield text_chunk

        except Exception as e:
            logging.error(f"URL fetch failed: {e}")
            yield f"Error fetching content: {e}"

    def _extract_and_chunk_text(self, html_content: str) -> List[str]:
        """Extract text from HTML and chunk it"""
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_content, 'html.parser')

            # Remove unwanted elements
            for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
                element.decompose()

            # Extract text
            text = soup.get_text()

            # Chunk the extracted text
            if hasattr(self.chunker, 'natural_chunk_text'):
                return self.chunker.natural_chunk_text(text)
            else:
                return self.chunker.smart_chunk_text(text)

        except Exception as e:
            logging.error(f"HTML parsing failed: {e}")
            return []

    def _fetch_stdin_progressive(self) -> Iterator[str]:
        """Fetch from stdin and chunk progressively"""
        import sys

        content = sys.stdin.read()
        if hasattr(self.chunker, 'natural_chunk_text'):
            chunks = self.chunker.natural_chunk_text(content)
        else:
            chunks = self.chunker.smart_chunk_text(content)

        for chunk in chunks:
            yield chunk

    def _fetch_file_progressive(self, file_path: str) -> Iterator[str]:
        """Fetch from file and chunk progressively"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if hasattr(self.chunker, 'natural_chunk_text'):
                chunks = self.chunker.natural_chunk_text(content)
            else:
                chunks = self.chunker.smart_chunk_text(content)

            for chunk in chunks:
                yield chunk

        except Exception as e:
            logging.error(f"File read failed: {e}")
            yield f"Error reading file: {e}"

def benchmark_chunking_improvement():
    """Benchmark smart chunking vs simple chunking"""
    print("📊 Benchmarking Smart Chunking Performance")
    print("=" * 50)

    # Test text samples
    test_texts = {
        "short": "This is a short test text. It has a few sentences. We want to see how it chunks.",
        "medium": """
        This is a medium-length test text that contains multiple paragraphs and sentences.
        We want to test how the smart chunker handles different types of content.

        The chunker should respect sentence boundaries and create logical breaks.
        It should also handle paragraphs appropriately and create chunks that are
        neither too long nor too short for optimal TTS processing.
        """,
        "long": """
        This is a longer test document that simulates a real article or blog post.
        The content contains multiple paragraphs, various sentence lengths, and different
        types of punctuation that should be handled appropriately.

        The smart chunker needs to balance several factors: chunk size, sentence integrity,
        paragraph boundaries, and overall readability. It should create chunks that flow
        naturally when converted to speech.

        Additionally, the chunker should handle edge cases like very long sentences,
        short paragraphs, and various punctuation patterns. The goal is to create
        chunks that sound natural and don't have awkward breaks when spoken aloud.
        """
    }

    # Initialize chunkers
    smart_chunker = SmartChunker(target_size=250, max_size=400)
    simple_chunker = SmartChunker(target_size=320, max_size=320)  # Simulate current system

    for name, text in test_texts.items():
        print(f"\n🔍 Testing {name} text ({len(text)} characters)")

        # Smart chunking
        start_time = time.time()
        smart_chunks = smart_chunker.smart_chunk_text(text)
        smart_time = time.time() - start_time

        # Simple chunking (current approach)
        start_time = time.time()
        simple_chunks = simple_chunker.smart_chunk_text(text)
        simple_time = time.time() - start_time

        print(f"  Smart: {len(smart_chunks)} chunks, {smart_time:.4f}s")
        print(f"  Simple: {len(simple_chunks)} chunks, {simple_time:.4f}s")
        print(f"  Chunk size improvement: {len(simple_chunks) - len(smart_chunks)} fewer chunks")

        # Show sample chunks
        print(f"  Sample smart chunk: {smart_chunks[0][:80]}..." if smart_chunks else "")

if __name__ == "__main__":
    # Run benchmarking
    benchmark_chunking_improvement()

    # Test progressive fetching
    print("\n" + "=" * 50)
    print("🧪 Testing Progressive Content Fetching")

    fetcher = ProgressiveContentFetcher()

    # Test with a simple text
    test_text = "This is a test. It has multiple sentences. We want to see progressive chunking work."

    print("Test chunks:")
    chunks = list(fetcher.chunker.smart_chunk_text(test_text))
    for i, chunk in enumerate(chunks):
        print(f"  {i+1}: {chunk}")