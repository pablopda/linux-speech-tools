#!/usr/bin/env bash
# Demo script showing the difference between chunked and continuous audio streaming

set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}"
    echo "╭─────────────────────────────────────────────────────────────╮"
    echo "│                  Audio Streaming Demo                       │"
    echo "│              Chunked vs Continuous Playback                │"
    echo "╰─────────────────────────────────────────────────────────────╯"
    echo -e "${NC}"
}

print_section() {
    echo -e "${YELLOW}[$1]${NC} $2"
}

# Create test content
create_test_content() {
    local test_file="/tmp/audio_streaming_test.txt"
    cat > "$test_file" << 'EOF'
This is a demonstration of audio streaming improvements.
The original streaming mode played each text chunk separately, creating noticeable gaps between segments.
This resulted in choppy, unprofessional audio that interrupted the listening flow.
Our new continuous streaming technology eliminates these gaps completely.
The audio now flows smoothly from one segment to the next, creating a natural, professional listening experience.
This improvement makes long-form content much more enjoyable to consume through speech synthesis.
EOF
    echo "$test_file"
}

# Check if required tools are available
check_requirements() {
    local missing=()

    if [ ! -f "say_read.py" ]; then
        missing+=("say_read.py")
    fi

    if [ ! -f "say_read_continuous.py" ]; then
        missing+=("say_read_continuous.py")
    fi

    if ! command -v python3 >/dev/null; then
        missing+=("python3")
    fi

    if ! command -v ffplay >/dev/null && ! command -v mpv >/dev/null; then
        missing+=("ffplay or mpv")
    fi

    if [ ${#missing[@]} -ne 0 ]; then
        print_section "ERROR" "Missing requirements:"
        for item in "${missing[@]}"; do
            echo "  - $item"
        done
        echo ""
        echo "Please ensure you're in the speech-tools directory with all dependencies installed."
        return 1
    fi

    return 0
}

# Test original streaming mode
test_original_streaming() {
    print_section "TEST 1" "Original Streaming (with gaps)"
    echo "This demonstrates the chunked audio with gaps between pieces..."
    echo ""

    # Use original say_read.py with streaming mode
    python3 say_read.py --stream --debug "$1" 2>/dev/null || {
        echo "❌ Original streaming test failed"
        return 1
    }

    echo ""
    echo "🎧 Did you notice the gaps between audio segments?"
    echo ""
}

# Test continuous streaming
test_continuous_streaming() {
    print_section "TEST 2" "Continuous Streaming (smooth)"
    echo "This demonstrates the new gap-free continuous audio..."
    echo ""

    # Use our new continuous streaming
    python3 say_read_continuous.py --debug "$1" 2>/dev/null || {
        echo "❌ Continuous streaming test failed"
        return 1
    }

    echo ""
    echo "🎵 Notice how smooth and professional the audio flows!"
    echo ""
}

# Test buffered streaming
test_buffered_streaming() {
    print_section "TEST 3" "Buffered Streaming (extra smooth)"
    echo "This demonstrates buffered streaming for maximum smoothness..."
    echo ""

    # Use buffered continuous streaming
    python3 say_read_continuous.py --continuous-buffered --debug "$1" 2>/dev/null || {
        echo "❌ Buffered streaming test failed"
        return 1
    }

    echo ""
    echo "✨ The buffered approach provides the smoothest possible playback!"
    echo ""
}

# Performance comparison
performance_comparison() {
    print_section "PERFORMANCE" "Speed Comparison"
    echo "Measuring streaming performance..."
    echo ""

    local test_content="$1"

    # Time original streaming
    echo "Original streaming:"
    time python3 say_read.py --stream --debug "$test_content" >/dev/null 2>&1 || echo "Failed"

    echo ""
    echo "Continuous streaming:"
    time python3 say_read_continuous.py --debug "$test_content" >/dev/null 2>&1 || echo "Failed"

    echo ""
    echo "Buffered streaming:"
    time python3 say_read_continuous.py --continuous-buffered --debug "$test_content" >/dev/null 2>&1 || echo "Failed"
}

# Main demo function
main() {
    print_header

    print_section "SETUP" "Checking requirements and creating test content"

    if ! check_requirements; then
        exit 1
    fi

    local test_file
    test_file=$(create_test_content)

    echo "✅ Created test content: $test_file"
    echo ""

    print_section "DEMO" "You'll hear 3 different streaming approaches"
    echo ""
    echo "🔊 Make sure your audio is on and at a comfortable volume"
    echo ""

    read -p "Press Enter to start the audio streaming demo..."
    echo ""

    # Run tests
    test_original_streaming "$test_file"

    read -p "Press Enter to hear the improved continuous streaming..."
    echo ""

    test_continuous_streaming "$test_file"

    read -p "Press Enter to hear the buffered streaming version..."
    echo ""

    test_buffered_streaming "$test_file"

    # Performance comparison
    echo ""
    read -p "Press Enter to run a performance comparison (no audio)..."
    echo ""

    performance_comparison "$test_file"

    # Cleanup
    rm -f "$test_file"

    echo ""
    print_section "SUMMARY" "Audio Streaming Improvements"
    echo ""
    echo -e "${GREEN}✅ Eliminated gaps between audio chunks${NC}"
    echo -e "${GREEN}✅ Professional, smooth audio playback${NC}"
    echo -e "${GREEN}✅ Buffered streaming for extra reliability${NC}"
    echo -e "${GREEN}✅ Maintains all original functionality${NC}"
    echo -e "${GREEN}✅ Better error handling and fallbacks${NC}"
    echo ""
    echo "🎵 Your Linux speech tools now provide professional-quality"
    echo "   continuous audio streaming for URLs and documents!"
    echo ""
}

# Run the demo
main "$@"