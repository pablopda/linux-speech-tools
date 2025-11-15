#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chunking Test Framework
Comprehensive testing and evaluation system for text chunking algorithms
"""

import sys
import json
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
from difflib import SequenceMatcher
import time

# Import test suites
from test_suite_english import ENGLISH_TEST_SUITE
from test_suite_spanish import SPANISH_TEST_SUITE

# Import chunking algorithms
from enhanced_chunking import NaturalSpeechChunker
from tts_optimized_chunking import TTSOptimizedChunker


@dataclass
class ChunkingResult:
    """Result of a chunking algorithm on a specific test"""
    test_id: int
    test_name: str
    algorithm_name: str
    original_text: str
    generated_chunks: List[str]
    ideal_chunks: List[str]
    execution_time: float


@dataclass
class TestMetrics:
    """Metrics for evaluating chunking quality"""
    exact_match_score: float  # Percentage of tests with exact chunk matches
    chunk_count_accuracy: float  # How close is the number of chunks
    boundary_accuracy: float  # How well are sentence boundaries preserved
    similarity_score: float  # Average text similarity
    avg_chunk_length: float  # Average chunk length
    failed_tests: List[int]  # IDs of failed tests


class ChunkingEvaluator:
    """Evaluates chunking algorithms against gold standards"""

    def __init__(self):
        self.results: List[ChunkingResult] = []

    def normalize_text(self, text: str) -> str:
        """Normalize text for comparison"""
        return " ".join(text.split()).strip()

    def calculate_similarity(self, chunks1: List[str], chunks2: List[str]) -> float:
        """Calculate similarity between two chunk lists"""
        text1 = " ".join(chunks1)
        text2 = " ".join(chunks2)

        normalized1 = self.normalize_text(text1)
        normalized2 = self.normalize_text(text2)

        return SequenceMatcher(None, normalized1, normalized2).ratio()

    def check_boundary_preservation(self, generated: List[str], ideal: List[str], original: str) -> float:
        """Check how well sentence boundaries are preserved"""
        # Count sentence-ending punctuation in original
        sentence_endings = ['.', '!', '?', ':', ';']
        original_boundaries = sum(1 for char in original if char in sentence_endings)

        if original_boundaries == 0:
            return 1.0

        # Count preserved boundaries in generated chunks
        preserved_boundaries = 0
        for chunk in generated:
            chunk = chunk.strip()
            if chunk and chunk[-1] in sentence_endings:
                preserved_boundaries += 1

        return min(preserved_boundaries / original_boundaries, 1.0)

    def evaluate_chunks(self, result: ChunkingResult) -> TestMetrics:
        """Evaluate a single chunking result"""
        generated = result.generated_chunks
        ideal = result.ideal_chunks

        # Exact match check
        exact_match = (generated == ideal)
        exact_match_score = 1.0 if exact_match else 0.0

        # Chunk count accuracy
        ideal_count = len(ideal)
        generated_count = len(generated)
        if ideal_count == 0:
            chunk_count_accuracy = 1.0 if generated_count == 0 else 0.0
        else:
            chunk_count_accuracy = 1.0 - abs(ideal_count - generated_count) / ideal_count

        # Boundary accuracy
        boundary_accuracy = self.check_boundary_preservation(generated, ideal, result.original_text)

        # Similarity score
        similarity_score = self.calculate_similarity(generated, ideal)

        # Average chunk length
        avg_chunk_length = sum(len(chunk) for chunk in generated) / len(generated) if generated else 0

        # Failed test indication
        failed_tests = [] if exact_match else [result.test_id]

        return TestMetrics(
            exact_match_score=exact_match_score,
            chunk_count_accuracy=chunk_count_accuracy,
            boundary_accuracy=boundary_accuracy,
            similarity_score=similarity_score,
            avg_chunk_length=avg_chunk_length,
            failed_tests=failed_tests
        )

    def test_algorithm(self, algorithm, algorithm_name: str, test_suite: List[Dict], language: str) -> List[ChunkingResult]:
        """Test an algorithm against a test suite"""
        results = []

        print(f"\nTesting {algorithm_name} on {language} test suite...")
        print("=" * 60)

        for test_case in test_suite:
            print(f"Running test {test_case['id']}: {test_case['name']}")

            start_time = time.time()
            try:
                # Use the correct method name based on algorithm type
                if hasattr(algorithm, 'natural_chunk_text'):
                    generated_chunks = algorithm.natural_chunk_text(test_case['text'])
                elif hasattr(algorithm, 'tts_chunk_text'):
                    generated_chunks = algorithm.tts_chunk_text(test_case['text'])
                elif hasattr(algorithm, 'gold_standard_chunk_text'):
                    generated_chunks = algorithm.gold_standard_chunk_text(test_case['text'])
                else:
                    generated_chunks = algorithm.create_chunks(test_case['text'])
                execution_time = time.time() - start_time

                result = ChunkingResult(
                    test_id=test_case['id'],
                    test_name=test_case['name'],
                    algorithm_name=algorithm_name,
                    original_text=test_case['text'],
                    generated_chunks=generated_chunks,
                    ideal_chunks=test_case['ideal_chunks'],
                    execution_time=execution_time
                )

                results.append(result)
                self.results.append(result)

                # Quick feedback
                if generated_chunks == test_case['ideal_chunks']:
                    print("  ✅ PASS")
                else:
                    print("  ❌ FAIL")

            except Exception as e:
                print(f"  💥 ERROR: {str(e)}")
                execution_time = time.time() - start_time

                result = ChunkingResult(
                    test_id=test_case['id'],
                    test_name=test_case['name'],
                    algorithm_name=algorithm_name,
                    original_text=test_case['text'],
                    generated_chunks=[],
                    ideal_chunks=test_case['ideal_chunks'],
                    execution_time=execution_time
                )

                results.append(result)
                self.results.append(result)

        return results

    def aggregate_metrics(self, results: List[ChunkingResult]) -> TestMetrics:
        """Aggregate metrics across multiple test results"""
        if not results:
            return TestMetrics(0, 0, 0, 0, 0, [])

        metrics = [self.evaluate_chunks(result) for result in results]

        total_tests = len(results)
        exact_matches = sum(m.exact_match_score for m in metrics)

        return TestMetrics(
            exact_match_score=exact_matches / total_tests,
            chunk_count_accuracy=sum(m.chunk_count_accuracy for m in metrics) / total_tests,
            boundary_accuracy=sum(m.boundary_accuracy for m in metrics) / total_tests,
            similarity_score=sum(m.similarity_score for m in metrics) / total_tests,
            avg_chunk_length=sum(m.avg_chunk_length for m in metrics) / total_tests,
            failed_tests=[test_id for m in metrics for test_id in m.failed_tests]
        )

    def detailed_failure_analysis(self, result: ChunkingResult):
        """Provide detailed analysis of a failed test"""
        print(f"\n🔍 DETAILED FAILURE ANALYSIS - Test {result.test_id}")
        print("=" * 70)
        print(f"Test Name: {result.test_name}")
        print(f"Algorithm: {result.algorithm_name}")
        print(f"\nOriginal Text ({len(result.original_text)} chars):")
        print(f"  {result.original_text}")

        print(f"\nIdeal Chunks ({len(result.ideal_chunks)} chunks):")
        for i, chunk in enumerate(result.ideal_chunks, 1):
            print(f"  {i}: {chunk}")

        print(f"\nGenerated Chunks ({len(result.generated_chunks)} chunks):")
        for i, chunk in enumerate(result.generated_chunks, 1):
            print(f"  {i}: {chunk}")

        # Character-level comparison
        ideal_text = " ".join(result.ideal_chunks)
        generated_text = " ".join(result.generated_chunks)

        print(f"\nText Reconstruction Comparison:")
        print(f"  Ideal:     '{ideal_text}'")
        print(f"  Generated: '{generated_text}'")

        similarity = self.calculate_similarity(result.generated_chunks, result.ideal_chunks)
        print(f"  Similarity: {similarity:.2%}")

        # Identify specific issues
        issues = []
        if len(result.generated_chunks) != len(result.ideal_chunks):
            issues.append(f"Chunk count mismatch: {len(result.generated_chunks)} vs {len(result.ideal_chunks)}")

        if ideal_text != generated_text:
            issues.append("Text reconstruction differs from original")

        # Check for split words/abbreviations
        for i, chunk in enumerate(result.generated_chunks):
            if chunk.strip().endswith(('.', '!', '?')):
                continue
            # Check if this might be a problematic split
            if i < len(result.generated_chunks) - 1:
                next_chunk = result.generated_chunks[i + 1].strip()
                if (chunk.strip().endswith(('U', 'Dr', 'Ph')) and
                    next_chunk.startswith(('.', 'S.', 'D.'))):
                    issues.append(f"Possible abbreviation split: '{chunk.strip()}' + '{next_chunk}'")

        if issues:
            print(f"\n⚠️  Identified Issues:")
            for issue in issues:
                print(f"    - {issue}")

    def generate_report(self):
        """Generate comprehensive test report"""
        if not self.results:
            print("No test results to report")
            return

        print("\n" + "="*80)
        print("CHUNKING ALGORITHM EVALUATION REPORT")
        print("="*80)

        # Group results by algorithm and language
        algorithms = {}
        for result in self.results:
            key = result.algorithm_name
            if key not in algorithms:
                algorithms[key] = {'english': [], 'spanish': []}

            # Determine language based on test_id patterns or content
            if any(test['id'] == result.test_id for test in ENGLISH_TEST_SUITE):
                algorithms[key]['english'].append(result)
            else:
                algorithms[key]['spanish'].append(result)

        # Generate metrics for each algorithm/language combo
        for algorithm_name, lang_results in algorithms.items():
            print(f"\n📊 ALGORITHM: {algorithm_name}")
            print("-" * 60)

            for language, results in lang_results.items():
                if not results:
                    continue

                print(f"\n🌍 Language: {language.upper()} ({len(results)} tests)")
                metrics = self.aggregate_metrics(results)

                print(f"  Exact Match Score:     {metrics.exact_match_score:.2%}")
                print(f"  Chunk Count Accuracy:  {metrics.chunk_count_accuracy:.2%}")
                print(f"  Boundary Accuracy:     {metrics.boundary_accuracy:.2%}")
                print(f"  Similarity Score:      {metrics.similarity_score:.2%}")
                print(f"  Avg Chunk Length:      {metrics.avg_chunk_length:.1f} chars")
                print(f"  Failed Tests:          {len(metrics.failed_tests)}/{len(results)} ({len(metrics.failed_tests)/len(results):.1%})")

                if metrics.failed_tests:
                    print(f"  Failed Test IDs:       {metrics.failed_tests}")

        # Show detailed failures for debugging
        failed_results = [r for r in self.results if r.generated_chunks != r.ideal_chunks]

        if failed_results:
            print(f"\n🚨 DETAILED FAILURE ANALYSIS ({len(failed_results)} failures)")
            print("="*80)

            # Show first 3 failures in detail
            for result in failed_results[:3]:
                self.detailed_failure_analysis(result)

            if len(failed_results) > 3:
                print(f"\n... and {len(failed_results) - 3} more failures")

    def run_full_evaluation(self):
        """Run complete evaluation of all algorithms"""
        print("🧪 STARTING COMPREHENSIVE CHUNKING EVALUATION")
        print("="*80)

        # Initialize algorithms
        from gold_standard_chunker import GoldStandardChunker

        algorithms = [
            (NaturalSpeechChunker(), "Enhanced Natural Speech"),
            (TTSOptimizedChunker(), "TTS Optimized"),
            (GoldStandardChunker(), "Gold Standard")
        ]

        # Test each algorithm on both language test suites
        for algorithm, name in algorithms:
            self.test_algorithm(algorithm, name, ENGLISH_TEST_SUITE, "English")
            self.test_algorithm(algorithm, name, SPANISH_TEST_SUITE, "Spanish")

        # Generate comprehensive report
        self.generate_report()


def main():
    """Main function to run the evaluation"""
    evaluator = ChunkingEvaluator()
    evaluator.run_full_evaluation()

    # Save detailed results to JSON for further analysis
    results_data = []
    for result in evaluator.results:
        results_data.append({
            'test_id': result.test_id,
            'test_name': result.test_name,
            'algorithm_name': result.algorithm_name,
            'original_text': result.original_text,
            'generated_chunks': result.generated_chunks,
            'ideal_chunks': result.ideal_chunks,
            'execution_time': result.execution_time,
            'exact_match': result.generated_chunks == result.ideal_chunks
        })

    with open('chunking_evaluation_results.json', 'w', encoding='utf-8') as f:
        json.dump(results_data, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Detailed results saved to chunking_evaluation_results.json")


if __name__ == "__main__":
    main()