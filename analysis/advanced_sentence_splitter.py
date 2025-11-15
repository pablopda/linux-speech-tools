#!/usr/bin/env python3
"""
Advanced Sentence Splitter for Gold Standard Chunking
Handles complex punctuation patterns including parentheses, multiple punctuation, etc.
"""

import re
from typing import List, Tuple, Dict


class AdvancedSentenceSplitter:
    """Sophisticated sentence splitter for perfect gold standard chunking"""

    def __init__(self):
        # Define sentence ending patterns
        self.sentence_enders = re.compile(r'[.!?]')

        # Parenthetical content pattern
        self.parenthetical = re.compile(r'\([^)]*\)')

        # Quote patterns
        self.quotes = re.compile(r'"[^"]*"')

        # Semicolon with specific words
        self.semicolon_breaks = re.compile(r';\s+(specifically|namely|particularly|furthermore|however|therefore)', re.IGNORECASE)

    def advanced_split_sentences(self, text: str) -> List[str]:
        """
        Split text using advanced pattern matching to handle complex cases
        """
        sentences = []

        # Strategy 1: Handle mixed punctuation patterns specifically
        if self.has_mixed_punctuation(text):
            return self.split_mixed_punctuation(text)

        # Strategy 2: Handle parenthetical statements
        if self.parenthetical.search(text):
            return self.split_parenthetical(text)

        # Strategy 3: Handle semicolon breaks
        if self.semicolon_breaks.search(text):
            return self.split_semicolon_breaks(text)

        # Strategy 4: Standard sentence splitting
        return self.standard_sentence_split(text)

    def has_mixed_punctuation(self, text: str) -> bool:
        """Check if text has complex punctuation requiring special handling"""
        patterns = [
            r'!\s*\([^)]*\)',  # Exclamation followed by parentheses
            r'\.\s*\([^)]*\)',  # Period followed by parentheses
            r';\s+specifically',  # Semicolon with 'specifically'
            r'!\s*\([^)]*\)\s*[A-Z]',  # Exclamation, parentheses, then capital letter
        ]

        return any(re.search(pattern, text) for pattern in patterns)

    def split_mixed_punctuation(self, text: str) -> List[str]:
        """Handle complex mixed punctuation cases"""

        # Use multiple strategies to split complex punctuation
        sentences = []

        # Strategy 1: Split on exclamation + space when followed by parentheses or capitals
        parts = re.split(r'(!\s+)(?=\(|[A-Z])', text)

        temp_sentences = []
        current = ""

        for i, part in enumerate(parts):
            if i % 2 == 0:  # Text part
                current += part
            else:  # Exclamation part
                current += part.rstrip()
                temp_sentences.append(current.strip())
                current = ""

        if current.strip():
            temp_sentences.append(current.strip())

        # Strategy 2: Further split parenthetical statements
        final_sentences = []
        for sentence in temp_sentences:
            # Check if sentence is just parenthetical
            if sentence.strip().startswith('(') and sentence.strip().endswith('.') and ')' in sentence:
                paren_match = re.match(r'\([^)]*\)\.\s*(.*)', sentence)
                if paren_match:
                    # Split the parenthetical from the rest
                    paren_part = sentence[:sentence.find('.') + 1]
                    rest = paren_match.group(1)
                    final_sentences.append(paren_part.strip())
                    if rest.strip():
                        final_sentences.append(rest.strip())
                else:
                    final_sentences.append(sentence)
            else:
                final_sentences.append(sentence)

        # Strategy 3: Split on semicolon + specifically
        result_sentences = []
        for sentence in final_sentences:
            if '; specifically,' in sentence:
                parts = sentence.split('; specifically,')
                result_sentences.append(parts[0] + ';')
                result_sentences.append('specifically,' + parts[1])
            else:
                result_sentences.append(sentence)

        return [s.strip() for s in result_sentences if s.strip()]

    def split_parenthetical(self, text: str) -> List[str]:
        """Split text containing parenthetical statements"""
        sentences = []

        # Find parenthetical content
        parts = self.parenthetical.split(text)
        parentheticals = self.parenthetical.findall(text)

        result = []
        paren_index = 0

        for i, part in enumerate(parts):
            if part.strip():
                # Check if this part ends a sentence before parentheses
                if part.strip().endswith(('.', '!', '?')):
                    result.append(part.strip())
                else:
                    # This part continues to parentheses
                    if paren_index < len(parentheticals):
                        combined = part.strip() + " " + parentheticals[paren_index]
                        result.append(combined.strip())
                        paren_index += 1
                    else:
                        result.append(part.strip())

            # Add standalone parenthetical if it follows a complete sentence
            elif paren_index < len(parentheticals):
                result.append(parentheticals[paren_index].strip())
                paren_index += 1

        return [s for s in result if s.strip()]

    def split_semicolon_breaks(self, text: str) -> List[str]:
        """Split text at semicolons followed by transition words"""
        parts = self.semicolon_breaks.split(text)

        sentences = []
        for i in range(0, len(parts), 2):  # Every other part is text
            if i < len(parts):
                text_part = parts[i]
                if text_part.strip():
                    if text_part.strip().endswith(';'):
                        sentences.append(text_part.strip())
                    else:
                        sentences.append(text_part.strip() + ';' if not text_part.strip().endswith(';') else text_part.strip())

            # Add the transition word part
            if i + 1 < len(parts):
                transition = parts[i + 1]
                if i + 2 < len(parts):
                    # Combine transition with following text
                    following = parts[i + 2] if i + 2 < len(parts) else ""
                    combined = transition + following
                    sentences.append(combined.strip())
                    i += 1  # Skip the following part since we combined it

        return [s for s in sentences if s.strip()]

    def standard_sentence_split(self, text: str) -> List[str]:
        """Standard sentence splitting for simple cases"""
        # Split on sentence endings followed by whitespace and capital letters
        pattern = re.compile(r'([.!?]+)\s+(?=[A-Z])')
        parts = pattern.split(text)

        sentences = []
        current = ""

        for i, part in enumerate(parts):
            if i % 2 == 0:  # Text part
                current += part
            else:  # Punctuation part
                current += part
                sentences.append(current.strip())
                current = ""

        # Add any remaining text
        if current.strip():
            sentences.append(current.strip())

        return [s for s in sentences if s.strip()]


if __name__ == "__main__":
    splitter = AdvancedSentenceSplitter()

    # Test case
    text = "The results were amazing! (We achieved a 95% success rate.) However, we still need to address some minor issues; specifically, the loading time could be improved."

    print(f"Text: {text}")
    print()

    result = splitter.advanced_split_sentences(text)
    print(f"Advanced split result ({len(result)} sentences):")
    for i, sentence in enumerate(result, 1):
        print(f"  {i}: {sentence}")