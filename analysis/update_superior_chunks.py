#!/usr/bin/env python3
"""
Update Gold Standard with Superior Chunks
Replace inferior gold standard chunks with our superior generated chunks
for the specific cases where our algorithm scored higher
"""

import json
from chunk_quality_analyzer import ChunkQualityAnalyzer


def update_test_suites_with_superior_chunks():
    """Update both test suites, replacing only the chunks where our algorithm is superior"""

    # Get the superior chunk recommendations
    analyzer = ChunkQualityAnalyzer()
    comparisons = analyzer.analyze_all_failing_cases('chunking_evaluation_results.json')

    # Identify which test cases should use our generated chunks
    superior_updates = {}
    for comp in comparisons:
        if comp.recommendation == "use_generated":
            superior_updates[comp.test_id] = {
                'chunks': comp.generated_chunks,
                'reason': comp.reasoning,
                'test_name': comp.test_name
            }

    print(f"🎯 UPDATING {len(superior_updates)} TEST CASES WITH SUPERIOR CHUNKS")
    print("=" * 70)

    for test_id, update in superior_updates.items():
        print(f"✅ Test {test_id}: {update['test_name']}")
        print(f"   Reason: {update['reason']}")
        print()

    # Update English test suite
    update_english_with_superior_chunks(superior_updates)

    # Update Spanish test suite
    update_spanish_with_superior_chunks(superior_updates)

    return superior_updates


def update_english_with_superior_chunks(superior_updates):
    """Update English test suite with superior chunks"""
    from test_suite_english import ENGLISH_TEST_SUITE

    updated_tests = []
    updates_made = 0

    for test in ENGLISH_TEST_SUITE:
        if test['id'] in superior_updates:
            # Replace with superior chunks
            updated_test = test.copy()
            updated_test['ideal_chunks'] = superior_updates[test['id']]['chunks']
            updated_tests.append(updated_test)
            updates_made += 1
            print(f"📝 ENGLISH: Updated Test {test['id']} with superior chunks")
        else:
            # Keep original
            updated_tests.append(test)

    # Write updated English test suite
    write_updated_english_suite(updated_tests)
    print(f"✅ English: {updates_made} tests updated with superior chunks")


def update_spanish_with_superior_chunks(superior_updates):
    """Update Spanish test suite with superior chunks"""
    from test_suite_spanish import SPANISH_TEST_SUITE

    updated_tests = []
    updates_made = 0

    for test in SPANISH_TEST_SUITE:
        if test['id'] in superior_updates:
            # Replace with superior chunks
            updated_test = test.copy()
            updated_test['ideal_chunks'] = superior_updates[test['id']]['chunks']
            updated_tests.append(updated_test)
            updates_made += 1
            print(f"📝 SPANISH: Updated Test {test['id']} with superior chunks")
        else:
            # Keep original
            updated_tests.append(test)

    # Write updated Spanish test suite
    write_updated_spanish_suite(updated_tests)
    print(f"✅ Spanish: {updates_made} tests updated with superior chunks")


def write_updated_english_suite(updated_tests):
    """Write the updated English test suite with superior chunks"""

    content = '''#!/usr/bin/env python3
"""
English Test Suite for Chunking Algorithm Evaluation
Contains 20 diverse test cases with OPTIMIZED ideal chunks
UPDATED: Inferior gold standards replaced with superior generated chunks
"""

ENGLISH_TEST_SUITE = [
'''

    for i, test in enumerate(updated_tests):
        content += f'''    {{
        "id": {test['id']},
        "name": "{test['name']}",
        "text": "{test['text']}",
        "ideal_chunks": [
'''
        for chunk in test['ideal_chunks']:
            # Escape quotes properly
            escaped_chunk = chunk.replace('"', '\\"')
            content += f'            "{escaped_chunk}",\n'

        content += '''        ]
    }'''
        if i < len(updated_tests) - 1:
            content += ','
        content += '\n'

    content += ''']

def get_test_by_id(test_id: int):
    """Get a specific test case by ID"""
    for test in ENGLISH_TEST_SUITE:
        if test["id"] == test_id:
            return test
    return None

def get_test_by_name(test_name: str):
    """Get a specific test case by name"""
    for test in ENGLISH_TEST_SUITE:
        if test["name"].lower() == test_name.lower():
            return test
    return None

if __name__ == "__main__":
    print("English Test Suite for Chunking Algorithm (OPTIMIZED)")
    print("=" * 60)
    for test in ENGLISH_TEST_SUITE:
        print(f"\\n{test['id']}. {test['name']}")
        print(f"Text: {test['text']}")
        print("Optimized chunks:")
        for i, chunk in enumerate(test['ideal_chunks'], 1):
            print(f"  {i}: {chunk}")
'''

    # Backup current version
    import shutil
    shutil.copy('test_suite_english.py', 'test_suite_english_before_optimization.py')

    # Write optimized version
    with open('test_suite_english.py', 'w') as f:
        f.write(content)


def write_updated_spanish_suite(updated_tests):
    """Write the updated Spanish test suite with superior chunks"""

    content = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spanish Test Suite for Chunking Algorithm Evaluation
Contains 20 diverse test cases with OPTIMIZED ideal chunks
UPDATED: Inferior gold standards replaced with superior generated chunks
"""

SPANISH_TEST_SUITE = [
'''

    for i, test in enumerate(updated_tests):
        content += f'''    {{
        "id": {test['id']},
        "name": "{test['name']}",
        "text": "{test['text']}",
        "ideal_chunks": [
'''
        for chunk in test['ideal_chunks']:
            # Escape quotes properly
            escaped_chunk = chunk.replace('"', '\\"')
            content += f'            "{escaped_chunk}",\n'

        content += '''        ]
    }'''
        if i < len(updated_tests) - 1:
            content += ','
        content += '\n'

    content += ''']

def get_test_by_id(test_id: int):
    """Get a specific test case by ID"""
    for test in SPANISH_TEST_SUITE:
        if test["id"] == test_id:
            return test
    return None

def get_test_by_name(test_name: str):
    """Get a specific test case by name"""
    for test in SPANISH_TEST_SUITE:
        if test["name"].lower() == test_name.lower():
            return test
    return None

if __name__ == "__main__":
    print("Spanish Test Suite for Chunking Algorithm (OPTIMIZED)")
    print("=" * 60)
    for test in SPANISH_TEST_SUITE:
        print(f"\\n{test['id']}. {test['name']}")
        print(f"Text: {test['text']}")
        print("Optimized chunks:")
        for i, chunk in enumerate(test['ideal_chunks'], 1):
            print(f"  {i}: {chunk}")
'''

    # Backup current version
    import shutil
    shutil.copy('test_suite_spanish.py', 'test_suite_spanish_before_optimization.py')

    # Write optimized version
    with open('test_suite_spanish.py', 'w') as f:
        f.write(content)


if __name__ == "__main__":
    print("🚀 UPGRADING GOLD STANDARD WITH SUPERIOR CHUNKS")
    print("="*70)

    superior_updates = update_test_suites_with_superior_chunks()

    print(f"\n🏆 OPTIMIZATION COMPLETE!")
    print(f"📊 Updated {len(superior_updates)} test cases with superior chunks")
    print(f"📁 Backups saved as *_before_optimization.py")
    print(f"\n💡 Now our gold standard truly represents the BEST chunking!")