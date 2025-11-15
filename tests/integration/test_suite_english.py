#!/usr/bin/env python3
"""
English Test Suite for Chunking Algorithm Evaluation
Contains 20 diverse test cases with gold standard ideal chunks
"""

ENGLISH_TEST_SUITE = [
    {
        "id": 1,
        "name": "Simple Sentences",
        "text": "The cat sat on the mat. It was a beautiful day. The sun was shining brightly.",
        "ideal_chunks": [
            "The cat sat on the mat. It was a beautiful day. The sun was shining brightly."
        ]
    },
    {
        "id": 2,
        "name": "Complex Sentence with Subordinate Clauses",
        "text": "Although the weather was terrible, we decided to go hiking because we had planned this trip for months, and we didn't want to disappoint the children.",
        "ideal_chunks": [
            "Although the weather was terrible, we decided to go hiking because we had planned this trip for months,",
            "and we didn't want to disappoint the children."
        ]
    },
    {
        "id": 3,
        "name": "Abbreviations and Titles",
        "text": "Dr. Smith, Ph.D., works for the U.S. Department of Defense. He earned his degree from MIT in 2010.",
        "ideal_chunks": [
            "Dr. Smith, Ph.D., works for the U.S. Department of Defense.",
            "He earned his degree from MIT in 2010."
        ]
    },
    {
        "id": 4,
        "name": "Lists and Enumerations",
        "text": "We need to buy apples, oranges, bananas, and grapes. Additionally, we should pick up bread, milk, and cheese from the dairy section.",
        "ideal_chunks": [
            "We need to buy apples, oranges, bananas, and grapes.",
            "Additionally, we should pick up bread, milk, and cheese from the dairy section."
        ]
    },
    {
        "id": 5,
        "name": "Numbers and Dates",
        "text": "The meeting is scheduled for January 15th, 2024, at 3:30 P.M. We expect approximately 125 attendees.",
        "ideal_chunks": [
            "The meeting is scheduled for January 15th, 2024, at 3:30 P.M.",
            "We expect approximately 125 attendees."
        ]
    },
    {
        "id": 6,
        "name": "Quotations and Dialogue",
        "text": "She said, \"I think we should leave early.\" Her friend replied, \"That's a good idea, but let's finish our coffee first.\"",
        "ideal_chunks": [
            "She said, \"I think we should leave early.\" Her friend replied, \"That's a good idea, but let's finish our coffee first.\""
        ]
    },
    {
        "id": 7,
        "name": "Technical Content",
        "text": "The HTTP protocol uses port 80 for standard connections. HTTPS, however, uses port 443 and provides encrypted communication through SSL/TLS certificates.",
        "ideal_chunks": [
            "The HTTP protocol uses port 80 for standard connections. HTTPS, however, uses port 443 and provides encrypted communication through SSL/TLS certificates."
        ]
    },
    {
        "id": 8,
        "name": "Long Sentence Requiring Break",
        "text": "The comprehensive report, which was submitted by the research team after months of intensive data collection and analysis, clearly demonstrates that the new methodology produces significantly better results than traditional approaches, especially when applied to large-scale datasets with complex interdependencies.",
        "ideal_chunks": [
            "The comprehensive report, which was submitted by the research team after months of intensive data collection and analysis, clearly demonstrates that the new methodology produces significantly better results than traditional approaches,",
            "especially when applied to large-scale datasets with complex interdependencies."
        ]
    },
    {
        "id": 9,
        "name": "Questions and Exclamations",
        "text": "Are you ready for the presentation? I hope so! We've worked really hard on this project.",
        "ideal_chunks": [
            "Are you ready for the presentation? I hope so!",
            "We've worked really hard on this project."
        ]
    },
    {
        "id": 10,
        "name": "Transitional Phrases",
        "text": "First, we need to gather all the requirements. Next, we'll create a detailed design document. Finally, we can begin the implementation phase.",
        "ideal_chunks": [
            "First, we need to gather all the requirements.",
            "Next, we'll create a detailed design document.",
            "Finally, we can begin the implementation phase."
        ]
    },
    {
        "id": 11,
        "name": "Multiple Abbreviations",
        "text": "The CEO, CFO, and CTO met with representatives from NASA, FBI, and the U.S.A. to discuss the classified project.",
        "ideal_chunks": [
            "The CEO, CFO, and CTO met with representatives from NASA, FBI, and the U.S.A. to discuss the classified project."
        ]
    },
    {
        "id": 12,
        "name": "Nested Quotations",
        "text": "John said, \"Mary told me, 'The deadline has been moved to Friday,' but I'm not sure if she's correct.\"",
        "ideal_chunks": [
            "John said, \"Mary told me, 'The deadline has been moved to Friday,' but I'm not sure if she's correct.\""
        ]
    },
    {
        "id": 13,
        "name": "URLs and Email",
        "text": "Please visit our website at www.example.com or send an email to support@company.org for more information.",
        "ideal_chunks": [
            "Please visit our website at www.example.com or send an email to support@company.org for more information."
        ]
    },
    {
        "id": 14,
        "name": "Mixed Punctuation",
        "text": "The results were amazing! (We achieved a 95% success rate.) However, we still need to address some minor issues; specifically, the loading time could be improved.",
        "ideal_chunks": [
            "The results were amazing! (We achieved a 95% success rate.)",
            "However, we still need to address some minor issues; specifically, the loading time could be improved."
        ]
    },
    {
        "id": 15,
        "name": "Scientific Content",
        "text": "The study examined the effects of temperature on enzyme activity. At 25°C, the enzyme showed optimal performance, but at 45°C, activity decreased by 30%.",
        "ideal_chunks": [
            "The study examined the effects of temperature on enzyme activity.",
            "At 25°C, the enzyme showed optimal performance, but at 45°C, activity decreased by 30%."
        ]
    },
    {
        "id": 16,
        "name": "Narrative with Time References",
        "text": "Yesterday morning, Sarah woke up at 6:00 A.M. and immediately began preparing for her important job interview. She had been looking forward to this opportunity for weeks.",
        "ideal_chunks": [
            "Yesterday morning, Sarah woke up at 6:00 A.M. and immediately began preparing for her important job interview.",
            "She had been looking forward to this opportunity for weeks."
        ]
    },
    {
        "id": 17,
        "name": "Conditional Statements",
        "text": "If the weather improves tomorrow, we'll go to the beach; otherwise, we'll stay home and watch movies. Either way, we'll have a good time.",
        "ideal_chunks": [
            "If the weather improves tomorrow, we'll go to the beach; otherwise, we'll stay home and watch movies.",
            "Either way, we'll have a good time."
        ]
    },
    {
        "id": 18,
        "name": "Comparative Analysis",
        "text": "While Method A provides faster results, Method B offers greater accuracy. Therefore, the choice depends on whether speed or precision is more important for your specific use case.",
        "ideal_chunks": [
            "While Method A provides faster results, Method B offers greater accuracy.",
            "Therefore, the choice depends on whether speed or precision is more important for your specific use case."
        ]
    },
    {
        "id": 19,
        "name": "Very Short Sentences",
        "text": "Stop. Listen carefully. This is important. We must act now.",
        "ideal_chunks": [
            "Stop. Listen carefully. This is important. We must act now."
        ]
    },
    {
        "id": 20,
        "name": "Complex Technical Discussion",
        "text": "The machine learning model utilizes a convolutional neural network (CNN) architecture with batch normalization and dropout layers. During training, we observed that the validation accuracy plateaued at approximately 87.5% after 50 epochs. To improve performance, we implemented data augmentation techniques including rotation, scaling, and horizontal flipping, which resulted in a final accuracy of 92.3%.",
        "ideal_chunks": [
            "The machine learning model utilizes a convolutional neural network (CNN)",
            "architecture with batch normalization and dropout layers. During training, we observed that the validation accuracy plateaued at approximately 87.5% after 50 epochs. To improve performance, we implemented data augmentation techniques including rotation, scaling,",
            "and horizontal flipping, which resulted in a final accuracy of 92.3%."
        ]
    }
]

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
        print(f"\n{test['id']}. {test['name']}")
        print(f"Text: {test['text']}")
        print("Ideal chunks:")
        for i, chunk in enumerate(test['ideal_chunks'], 1):
            print(f"  {i}: {chunk}")