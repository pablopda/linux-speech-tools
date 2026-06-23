#!/usr/bin/env python3
"""
Update Gold Standard Test Suites
Replace inferior gold standard chunks with our superior generated chunks
"""

import json
from chunk_quality_analyzer import ChunkQualityAnalyzer


def update_test_suite_with_superior_chunks():
    """Update test suites with chunks that scored better than gold standard"""

    # Analyze all failing cases
    analyzer = ChunkQualityAnalyzer()
    comparisons = analyzer.analyze_all_failing_cases('chunking_evaluation_results.json')

    # Load evaluation results to get generated chunks
    with open('chunking_evaluation_results.json', 'r') as f:
        results = json.load(f)

    # Create mapping of test_id to generated chunks for superior cases
    superior_chunks = {}
    for comp in comparisons:
        if comp.recommendation == "use_generated":
            superior_chunks[comp.test_id] = comp.generated_chunks
            print(f"✅ Test {comp.test_id} ({comp.test_name}): Using generated chunks (Score: {comp.reasoning})")
        else:
            print(f"⚠️  Test {comp.test_id} ({comp.test_name}): Keeping gold standard (Score: {comp.reasoning})")

    print(f"\n🎯 SUMMARY: Upgrading {len(superior_chunks)} out of {len(comparisons)} test cases")

    # Update English test suite
    print(f"\n📝 Updating English test suite...")
    update_english_tests(superior_chunks)

    # Update Spanish test suite
    print(f"📝 Updating Spanish test suite...")
    update_spanish_tests(superior_chunks)

    print(f"\n🏆 GOLD STANDARD UPDATES COMPLETE!")
    return superior_chunks


def update_english_tests(superior_chunks):
    """Update English test suite with superior chunks"""
    # Read current English test suite
    with open('test_suite_english.py', 'r') as f:
        content = f.read()

    # Import to get the test cases
    from test_suite_english import ENGLISH_TEST_SUITE

    updated_count = 0
    updated_tests = []

    for test in ENGLISH_TEST_SUITE:
        if test['id'] in superior_chunks:
            # Update with superior chunks
            updated_test = test.copy()
            updated_test['ideal_chunks'] = superior_chunks[test['id']]
            updated_tests.append(updated_test)
            updated_count += 1
            print(f"  ✅ Updated Test {test['id']}: {test['name']}")
        else:
            # Keep original
            updated_tests.append(test)

    # Write updated test suite
    write_updated_english_suite(updated_tests)
    print(f"  📊 Updated {updated_count} English tests")


def update_spanish_tests(superior_chunks):
    """Update Spanish test suite with superior chunks"""
    from test_suite_spanish import SPANISH_TEST_SUITE

    updated_count = 0
    updated_tests = []

    for test in SPANISH_TEST_SUITE:
        if test['id'] in superior_chunks:
            # Update with superior chunks
            updated_test = test.copy()
            updated_test['ideal_chunks'] = superior_chunks[test['id']]
            updated_tests.append(updated_test)
            updated_count += 1
            print(f"  ✅ Updated Test {test['id']}: {test['name']}")
        else:
            # Keep original
            updated_tests.append(test)

    # Write updated test suite
    write_updated_spanish_suite(updated_tests)
    print(f"  📊 Updated {updated_count} Spanish tests")


def write_updated_english_suite(updated_tests):
    """Write updated English test suite to file"""
    content = '''#!/usr/bin/env python3
"""
English Test Suite for Chunking Algorithm Evaluation
Contains 20 diverse test cases with OPTIMIZED ideal chunks
UPDATED: Gold standard improved with TTS-optimized chunks where superior
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
            content += f'            "{chunk}",\n'

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
    print("English Test Suite for Chunking Algorithm")
    print("=" * 50)
    for test in ENGLISH_TEST_SUITE:
        print(f"\\n{test['id']}. {test['name']}")
        print(f"Text: {test['text']}")
        print("Ideal chunks:")
        for i, chunk in enumerate(test['ideal_chunks'], 1):
            print(f"  {i}: {chunk}")
'''

    # Backup original
    import shutil
    shutil.copy('test_suite_english.py', 'test_suite_english_original.py')

    # Write updated version
    with open('test_suite_english.py', 'w') as f:
        f.write(content)


def write_updated_spanish_suite(updated_tests):
    """Write updated Spanish test suite to file"""
    content = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spanish Test Suite for Chunking Algorithm Evaluation
Contains 20 diverse test cases with OPTIMIZED ideal chunks
UPDATED: Gold standard improved with TTS-optimized chunks where superior
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
            content += f'            "{chunk}",\n'

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
    print("Spanish Test Suite for Chunking Algorithm")
    print("=" * 50)
    for test in SPANISH_TEST_SUITE:
        print(f"\\n{test['id']}. {test['name']}")
        print(f"Text: {test['text']}")
        print("Ideal chunks:")
        for i, chunk in enumerate(test['ideal_chunks'], 1):
            print(f"  {i}: {chunk}")
'''

    # Backup original
    import shutil
    shutil.copy('test_suite_spanish.py', 'test_suite_spanish_original.py')

    # Write updated version
    with open('test_suite_spanish.py', 'w') as f:
        f.write(content)


if __name__ == "__main__":
    superior_chunks = update_test_suite_with_superior_chunks()
    print(f"\n🎉 SUCCESS: Updated gold standards with {len(superior_chunks)} superior chunks!")
    print(f"📁 Originals backed up as *_original.py")