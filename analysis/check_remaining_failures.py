#!/usr/bin/env python3
"""
Check Remaining Failures for Gold Standard Algorithm
Identify what specific cases are still failing and why
"""

from gold_standard_chunker import GoldStandardChunker
from test_suite_english import ENGLISH_TEST_SUITE
from test_suite_spanish import SPANISH_TEST_SUITE

def check_remaining_failures():
    """Check what tests are still failing"""

    print("🔍 CHECKING REMAINING GOLD STANDARD FAILURES")
    print("=" * 60)

    gold_chunker = GoldStandardChunker()
    failing_tests = []

    # Check English tests
    print("\n📋 English Test Suite:")
    for i, test_case in enumerate(ENGLISH_TEST_SUITE, 1):
        text = test_case['text']
        expected = test_case['ideal_chunks']
        generated = gold_chunker.gold_standard_chunk_text(text)

        passes = generated == expected

        if not passes:
            failing_tests.append({
                'id': i,
                'name': test_case['name'],
                'language': 'english',
                'text': text,
                'expected': expected,
                'generated': generated
            })
            print(f"   ❌ Test {i}: {test_case['name']}")
        else:
            print(f"   ✅ Test {i}: {test_case['name']}")

    # Check Spanish tests
    print("\n📋 Spanish Test Suite:")
    for i, test_case in enumerate(SPANISH_TEST_SUITE, 1):
        text = test_case['text']
        expected = test_case['ideal_chunks']
        generated = gold_chunker.gold_standard_chunk_text(text)

        passes = generated == expected

        test_id = i + 20  # Spanish tests start at 21

        if not passes:
            failing_tests.append({
                'id': test_id,
                'name': test_case['name'],
                'language': 'spanish',
                'text': text,
                'expected': expected,
                'generated': generated
            })
            print(f"   ❌ Test {test_id}: {test_case['name']}")
        else:
            print(f"   ✅ Test {test_id}: {test_case['name']}")

    print(f"\n📊 SUMMARY")
    print("=" * 60)
    total_tests = len(ENGLISH_TEST_SUITE) + len(SPANISH_TEST_SUITE)
    passing_tests = total_tests - len(failing_tests)
    pass_rate = (passing_tests / total_tests) * 100

    print(f"Total tests: {total_tests}")
    print(f"Passing tests: {passing_tests}")
    print(f"Failing tests: {len(failing_tests)}")
    print(f"Pass rate: {pass_rate:.1f}%")

    if failing_tests:
        print(f"\n🔍 DETAILED FAILING CASES:")
        for test in failing_tests:
            print(f"\n📍 Test #{test['id']}: {test['name']} ({test['language']})")
            print(f"   Text: {test['text']}")
            print(f"   Expected chunks ({len(test['expected'])}):")
            for j, chunk in enumerate(test['expected'], 1):
                print(f"     {j}. '{chunk}'")
            print(f"   Generated chunks ({len(test['generated'])}):")
            for j, chunk in enumerate(test['generated'], 1):
                print(f"     {j}. '{chunk}'")
    else:
        print("\n🎉 ALL TESTS PASSING! 100% SUCCESS RATE!")

    return failing_tests

if __name__ == "__main__":
    remaining_failures = check_remaining_failures()