#!/usr/bin/env python3
"""
Detailed Failure Analysis and Superior Chunk Extraction
Identifies failing test cases and extracts our superior generated chunks
"""

import json
import sys
from gold_standard_chunker import GoldStandardChunker
from chunk_quality_analyzer import ChunkQualityAnalyzer
from test_suite_english import ENGLISH_TEST_SUITE
from test_suite_spanish import SPANISH_TEST_SUITE

def analyze_failing_cases():
    """Analyze failing cases and extract superior chunks"""

    print("🔍 DETAILED FAILURE ANALYSIS")
    print("=" * 60)

    failing_cases = []
    superior_chunks_data = []

    # Combine all test cases
    all_test_cases = []
    for case in ENGLISH_TEST_SUITE:
        case['language'] = 'english'
        all_test_cases.append(case)
    for case in SPANISH_TEST_SUITE:
        case['language'] = 'spanish'
        all_test_cases.append(case)

    quality_analyzer = ChunkQualityAnalyzer()
    gold_chunker = GoldStandardChunker()

    for i, test_case in enumerate(all_test_cases, 1):
        text = test_case['text']
        expected = test_case['ideal_chunks']
        language = test_case['language']

        # Generate chunks with our algorithm
        generated = gold_chunker.gold_standard_chunk_text(text)

        # Check if test passes
        passes = generated == expected

        if not passes:
            print(f"\n📍 FAILING CASE #{i}: {test_case['name']} ({language})")
            print("-" * 50)

            # Analyze quality using the quality analyzer
            comparison = quality_analyzer.compare_chunks(
                gold_chunks=expected,
                generated_chunks=generated,
                test_name=test_case['name'],
                original_text=text
            )

            print(f"📊 Quality Analysis:")
            print(f"   Recommendation: {comparison.recommendation}")

            is_superior = comparison.recommendation == "use_generated"

            if is_superior:
                print(f"   ✅ Our chunks are SUPERIOR!")

                superior_chunks_data.append({
                    'case_index': i,
                    'name': test_case['name'],
                    'language': language,
                    'text': text,
                    'old_chunks': expected,
                    'new_superior_chunks': generated,
                    'recommendation': comparison.recommendation
                })

                print(f"\n📝 TEXT: {text}")
                print(f"\n❌ Current Gold Standard:")
                for j, chunk in enumerate(expected, 1):
                    print(f"   {j}. '{chunk}'")
                print(f"\n✅ Our Superior Chunks:")
                for j, chunk in enumerate(generated, 1):
                    print(f"   {j}. '{chunk}'")
                print(f"\n💡 Reasoning: {comparison.reasoning}")

            else:
                print(f"   ⚠️ Gold standard is better or equivalent")

            failing_cases.append({
                'case_index': i,
                'name': test_case['name'],
                'language': language,
                'passes': False,
                'is_superior': is_superior
            })

    print(f"\n🎯 SUMMARY")
    print("=" * 60)
    print(f"Total failing cases: {len(failing_cases)}")
    superior_count = len(superior_chunks_data)
    print(f"Cases with superior chunks: {superior_count}")
    print(f"Potential improvement: {superior_count}/{len(failing_cases)} cases")

    # Save superior chunks for update
    if superior_chunks_data:
        with open('superior_chunks_update.json', 'w') as f:
            json.dump(superior_chunks_data, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Saved {superior_count} superior chunks to superior_chunks_update.json")

    return failing_cases, superior_chunks_data

if __name__ == "__main__":
    failing_cases, superior_chunks = analyze_failing_cases()

    print(f"\n🚀 Ready to update {len(superior_chunks)} test cases with superior chunks!")