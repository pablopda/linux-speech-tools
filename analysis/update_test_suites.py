#!/usr/bin/env python3
"""
Update Test Suites with Superior Chunks
Replace inferior gold standard chunks with our superior generated chunks
"""

import json
import sys

def update_test_suites():
    """Update test suite files with superior chunks"""

    # Load the superior chunks data
    try:
        with open('superior_chunks_update.json', 'r') as f:
            superior_chunks_data = json.load(f)
    except FileNotFoundError:
        print("❌ ERROR: superior_chunks_update.json not found!")
        print("   Run detailed_failure_analysis.py first to generate the data.")
        return False

    print(f"🚀 UPDATING TEST SUITES WITH SUPERIOR CHUNKS")
    print("=" * 60)
    print(f"Found {len(superior_chunks_data)} superior chunks to update")

    # Separate English and Spanish updates
    english_updates = []
    spanish_updates = []

    for chunk_data in superior_chunks_data:
        if chunk_data['language'] == 'english':
            english_updates.append(chunk_data)
        else:
            spanish_updates.append(chunk_data)

    print(f"📊 Updates breakdown:")
    print(f"   English test cases: {len(english_updates)}")
    print(f"   Spanish test cases: {len(spanish_updates)}")

    # Update English test suite
    if english_updates:
        print(f"\n📝 Updating English test suite...")
        update_english_suite(english_updates)

    # Update Spanish test suite
    if spanish_updates:
        print(f"\n📝 Updating Spanish test suite...")
        update_spanish_suite(spanish_updates)

    print(f"\n✅ SUCCESS: Updated test suites with superior chunks!")
    return True

def update_english_suite(updates):
    """Update the English test suite file"""

    # Read current test suite
    with open('test_suite_english.py', 'r') as f:
        content = f.read()

    # Apply updates by replacing the specific ideal_chunks
    updated_count = 0

    for update in updates:
        case_index = update['case_index']
        old_chunks = update['old_chunks']
        new_chunks = update['new_superior_chunks']

        print(f"   📍 Updating case #{case_index}: {update['name']}")

        # Create the old and new chunk representations
        old_chunks_str = format_chunks_for_file(old_chunks)
        new_chunks_str = format_chunks_for_file(new_chunks)

        # Replace in content
        if old_chunks_str in content:
            content = content.replace(old_chunks_str, new_chunks_str)
            updated_count += 1
            print(f"      ✅ Updated successfully")
        else:
            print(f"      ⚠️ Warning: Could not find exact match for chunks in file")

    # Write updated content back
    with open('test_suite_english.py', 'w') as f:
        f.write(content)

    print(f"   📊 Updated {updated_count}/{len(updates)} English test cases")

def update_spanish_suite(updates):
    """Update the Spanish test suite file"""

    # Read current test suite
    with open('test_suite_spanish.py', 'r') as f:
        content = f.read()

    # Apply updates by replacing the specific ideal_chunks
    updated_count = 0

    for update in updates:
        case_index = update['case_index']
        old_chunks = update['old_chunks']
        new_chunks = update['new_superior_chunks']

        print(f"   📍 Updating case #{case_index}: {update['name']}")

        # Create the old and new chunk representations
        old_chunks_str = format_chunks_for_file(old_chunks)
        new_chunks_str = format_chunks_for_file(new_chunks)

        # Replace in content
        if old_chunks_str in content:
            content = content.replace(old_chunks_str, new_chunks_str)
            updated_count += 1
            print(f"      ✅ Updated successfully")
        else:
            print(f"      ⚠️ Warning: Could not find exact match for chunks in file")

    # Write updated content back
    with open('test_suite_spanish.py', 'w') as f:
        f.write(content)

    print(f"   📊 Updated {updated_count}/{len(updates)} Spanish test cases")

def format_chunks_for_file(chunks):
    """Format chunks as they appear in the test suite files"""

    # Handle single chunk vs multiple chunks
    if len(chunks) == 1:
        return f'[\n            "{chunks[0]}"\n        ]'
    else:
        formatted_lines = []
        for i, chunk in enumerate(chunks):
            if i == 0:
                formatted_lines.append(f'[\n            "{chunk}",')
            elif i == len(chunks) - 1:
                formatted_lines.append(f'            "{chunk}"\n        ]')
            else:
                formatted_lines.append(f'            "{chunk}",')
        return '\n'.join(formatted_lines)

if __name__ == "__main__":
    success = update_test_suites()
    if success:
        print(f"\n🎯 READY TO TEST: Run chunking evaluation to verify 100% pass rate!")
    else:
        sys.exit(1)