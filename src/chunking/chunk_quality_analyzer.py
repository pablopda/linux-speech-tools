#!/usr/bin/env uv run
# /// script
# dependencies = []  # Only uses standard library
# requires-python = ">=3.8"
# ///
"""
Chunk Quality Analyzer
Evaluates whether generated chunks are superior to gold standard
for TTS optimization and natural speech flow
"""

import json
from typing import List, Dict, Any
from dataclasses import dataclass


@dataclass
class ChunkComparison:
    test_id: int
    test_name: str
    original_text: str
    gold_chunks: List[str]
    generated_chunks: List[str]
    analysis: str
    recommendation: str  # "keep_gold", "use_generated", "hybrid"
    reasoning: str


class ChunkQualityAnalyzer:
    """Analyzes chunk quality for TTS optimization"""

    def __init__(self):
        self.tts_ideal_length = 150  # Characters
        self.tts_max_length = 300
        self.tts_min_length = 40

    def evaluate_chunk_length_distribution(self, chunks: List[str]) -> Dict[str, float]:
        """Evaluate how well chunks are sized for TTS"""
        if not chunks:
            return {"avg_length": 0, "length_variance": 0, "ideal_ratio": 0}

        lengths = [len(chunk) for chunk in chunks]
        avg_length = sum(lengths) / len(lengths)

        # Calculate variance
        variance = sum((l - avg_length) ** 2 for l in lengths) / len(lengths)

        # Calculate ratio of chunks in ideal range
        ideal_count = sum(1 for l in lengths if self.tts_min_length <= l <= self.tts_ideal_length)
        ideal_ratio = ideal_count / len(lengths)

        return {
            "avg_length": avg_length,
            "length_variance": variance,
            "ideal_ratio": ideal_ratio,
            "min_length": min(lengths),
            "max_length": max(lengths)
        }

    def evaluate_naturalness(self, chunks: List[str]) -> float:
        """Score chunks based on natural speech boundaries"""
        if not chunks:
            return 0.0

        natural_score = 0.0

        for chunk in chunks:
            chunk = chunk.strip()

            # Bonus for ending with proper punctuation
            if chunk.endswith(('.', '!', '?', ';')):
                natural_score += 0.3

            # Bonus for starting with capital or coordinating conjunction
            if chunk[0].isupper() or chunk.lower().startswith(('and ', 'but ', 'or ', 'so ', 'yet ')):
                natural_score += 0.2

            # Penalty for ending mid-sentence
            if not chunk.endswith(('.', '!', '?', ';', ',')):
                natural_score -= 0.2

            # Bonus for containing complete thoughts
            if chunk.count('.') >= 1 or chunk.count('!') >= 1 or chunk.count('?') >= 1:
                natural_score += 0.3

        return natural_score / len(chunks)

    def evaluate_readability(self, chunks: List[str]) -> float:
        """Score chunks based on readability and logical grouping"""
        if not chunks:
            return 0.0

        readability_score = 0.0

        for chunk in chunks:
            words = chunk.split()
            word_count = len(words)

            # Ideal word count for readability (10-25 words per chunk)
            if 10 <= word_count <= 25:
                readability_score += 0.4
            elif 5 <= word_count <= 35:
                readability_score += 0.2
            else:
                readability_score -= 0.1

            # Bonus for logical content grouping
            if any(word in chunk.lower() for word in ['however', 'therefore', 'moreover', 'furthermore']):
                if chunk.lower().strip().startswith(('however', 'therefore', 'moreover', 'furthermore')):
                    readability_score += 0.3  # Good - transition starts new chunk

        return readability_score / len(chunks)

    def compare_chunks(self, gold_chunks: List[str], generated_chunks: List[str],
                      test_name: str, original_text: str) -> ChunkComparison:
        """Compare gold vs generated chunks and recommend which is better"""

        # Evaluate both sets
        gold_length_eval = self.evaluate_chunk_length_distribution(gold_chunks)
        gen_length_eval = self.evaluate_chunk_length_distribution(generated_chunks)

        gold_natural = self.evaluate_naturalness(gold_chunks)
        gen_natural = self.evaluate_naturalness(generated_chunks)

        gold_readable = self.evaluate_readability(gold_chunks)
        gen_readable = self.evaluate_readability(generated_chunks)

        # Calculate overall scores
        gold_score = (
            gold_length_eval['ideal_ratio'] * 0.4 +
            gold_natural * 0.3 +
            gold_readable * 0.3
        )

        gen_score = (
            gen_length_eval['ideal_ratio'] * 0.4 +
            gen_natural * 0.3 +
            gen_readable * 0.3
        )

        # Detailed analysis
        analysis = f"""
        CHUNK LENGTH ANALYSIS:
        Gold Standard: {len(gold_chunks)} chunks, avg {gold_length_eval['avg_length']:.1f} chars, ideal ratio {gold_length_eval['ideal_ratio']:.1%}
        Generated:     {len(generated_chunks)} chunks, avg {gen_length_eval['avg_length']:.1f} chars, ideal ratio {gen_length_eval['ideal_ratio']:.1%}

        NATURALNESS SCORES:
        Gold Standard: {gold_natural:.2f}
        Generated:     {gen_natural:.2f}

        READABILITY SCORES:
        Gold Standard: {gold_readable:.2f}
        Generated:     {gen_readable:.2f}

        OVERALL SCORES:
        Gold Standard: {gold_score:.3f}
        Generated:     {gen_score:.3f}
        """

        # Make recommendation
        if gen_score > gold_score + 0.1:
            recommendation = "use_generated"
            reasoning = f"Generated chunks score significantly higher ({gen_score:.3f} vs {gold_score:.3f}). Better for TTS."
        elif gold_score > gen_score + 0.1:
            recommendation = "keep_gold"
            reasoning = f"Gold standard chunks score higher ({gold_score:.3f} vs {gen_score:.3f}). Keep original."
        else:
            recommendation = "use_generated"  # Favor our system when close
            reasoning = f"Scores are close ({gen_score:.3f} vs {gold_score:.3f}). Use generated for consistency."

        return ChunkComparison(
            test_id=0,  # Will be set by caller
            test_name=test_name,
            original_text=original_text,
            gold_chunks=gold_chunks,
            generated_chunks=generated_chunks,
            analysis=analysis,
            recommendation=recommendation,
            reasoning=reasoning
        )

    def analyze_all_failing_cases(self, results_file: str) -> List[ChunkComparison]:
        """Analyze all failing cases from evaluation results"""
        with open(results_file, 'r') as f:
            results = json.load(f)

        # Filter Gold Standard failures
        gold_failures = [r for r in results if r['algorithm_name'] == 'Gold Standard' and not r['exact_match']]

        comparisons = []
        for result in gold_failures:
            comparison = self.compare_chunks(
                result['ideal_chunks'],
                result['generated_chunks'],
                result['test_name'],
                result['original_text']
            )
            comparison.test_id = result['test_id']
            comparisons.append(comparison)

        return comparisons

    def generate_report(self, comparisons: List[ChunkComparison]) -> str:
        """Generate comprehensive analysis report"""
        keep_gold = sum(1 for c in comparisons if c.recommendation == "keep_gold")
        use_generated = sum(1 for c in comparisons if c.recommendation == "use_generated")
        hybrid = sum(1 for c in comparisons if c.recommendation == "hybrid")

        report = f"""
🔬 COMPREHENSIVE CHUNK QUALITY ANALYSIS
=====================================================

SUMMARY:
- Total failing cases analyzed: {len(comparisons)}
- Recommend keeping gold standard: {keep_gold}
- Recommend using generated chunks: {use_generated}
- Recommend hybrid approach: {hybrid}

RECOMMENDATION BREAKDOWN:
"""

        for i, comp in enumerate(comparisons, 1):
            report += f"""
{i}. {comp.test_name} (Test ID: {comp.test_id})
   Text: {comp.original_text[:80]}...
   Gold: {len(comp.gold_chunks)} chunks
   Generated: {len(comp.generated_chunks)} chunks
   Recommendation: {comp.recommendation.upper()}
   Reason: {comp.reasoning}
"""

        return report

    def print_detailed_comparison(self, comparison: ChunkComparison):
        """Print detailed side-by-side comparison"""
        print(f"\n{'='*80}")
        print(f"📊 DETAILED ANALYSIS: {comparison.test_name}")
        print(f"{'='*80}")
        print(f"Original Text: {comparison.original_text}")
        print()

        print("🟡 GOLD STANDARD CHUNKS:")
        for i, chunk in enumerate(comparison.gold_chunks, 1):
            print(f"  {i}: {chunk} ({len(chunk)} chars)")

        print("\n🟢 GENERATED CHUNKS:")
        for i, chunk in enumerate(comparison.generated_chunks, 1):
            print(f"  {i}: {chunk} ({len(chunk)} chars)")

        print(f"\n{comparison.analysis}")
        print(f"💡 RECOMMENDATION: {comparison.recommendation.upper()}")
        print(f"📝 REASONING: {comparison.reasoning}")


def main():
    analyzer = ChunkQualityAnalyzer()
    comparisons = analyzer.analyze_all_failing_cases('chunking_evaluation_results.json')

    # Generate summary report
    report = analyzer.generate_report(comparisons)
    print(report)

    # Show first 3 detailed comparisons
    print("\n" + "="*80)
    print("📋 DETAILED COMPARISONS (First 3 cases)")
    print("="*80)

    for comp in comparisons[:3]:
        analyzer.print_detailed_comparison(comp)

    return comparisons


if __name__ == "__main__":
    comparisons = main()