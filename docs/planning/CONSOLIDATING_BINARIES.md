# Binary Consolidation Plan for Linux Speech Tools

## Current State Analysis

The project currently has **9 binaries** in the `bin/` directory with significant overlap and redundancy.

### Existing Binary Inventory

| Binary | Purpose | Type | Status |
|--------|---------|------|--------|
| `say` | Cloud-based TTS using Edge TTS | Core TTS | ✅ Keep |
| `say-local` | Offline TTS using Kokoro model | Core TTS | ✅ Keep |
| `say-read` | Basic document/URL reader | Document Reader | ❌ Replace with MVP |
| `say-read-es` | Spanish variant of say-read | Document Reader | ❌ Remove |
| `say-read-continuous` | Enhanced with continuous streaming | Document Reader | ❌ Remove |
| `say-read-mvp` | Progressive streaming implementation | Document Reader | ✅ Becomes new say-read |
| `say-read-gnome` | GNOME integration with media controls | Document Reader | 🔄 Merge |
| `gnome-dictation` | GNOME speech-to-clipboard wrapper | Desktop Integration | ✅ Keep |
| `talk2claude` | Speech-to-text using Whisper | Speech Recognition | ✅ Keep |

## Identified Problems

### 1. Document Reader Fragmentation
- **5 different `say-read` variants** implementing the same core functionality
- Each variant adds a single feature that should be a flag/option
- Language-specific scripts (Spanish) instead of language parameters
- Performance improvements already solved in MVP implementation
- No way to use local Kokoro model with document reading

### 2. Maintenance Overhead
- Multiple scripts to update when fixing bugs
- Duplicated code across variants
- Confusing for users to choose which script to use
- Version inconsistencies between variants

### 3. Poor Discoverability
- Users don't know which variant has which features
- No clear upgrade path from basic to advanced features
- Documentation burden for multiple similar tools

## Proposed Consolidation

### Target State: 5 Binaries (45% Reduction)

```
bin/
├── say              # Cloud TTS (Edge TTS)
├── say-local        # Offline TTS (Kokoro)
├── say-read         # Unified document reader with all features
├── talk2claude      # Speech-to-text/dictation
└── gnome-dictation  # Desktop integration wrapper
```

### Unified `say-read` Design

The new `say-read` will be based on the MVP implementation (currently `say-read-mvp`) with additional options:

```bash
# Basic usage (using MVP's progressive streaming by default)
say-read document.txt
say-read https://example.com/article

# Language selection (replaces say-read-es)
say-read -l es document.txt
say-read --language spanish article.pdf

# Use local Kokoro model instead of Edge TTS
say-read --local document.txt           # Use local Kokoro model
say-read --local -l es document.txt     # Local model with Spanish

# Desktop integration (replaces say-read-gnome)
say-read --gnome document.txt           # Enable GNOME media controls
say-read --notifications document.txt   # Enable desktop notifications

# Combined options
say-read -l es --local spanish-article.pdf
say-read --local --gnome document.txt
```

### Feature Mapping

| Old Command | New Command |
|-------------|-------------|
| `say-read file.txt` | `say-read file.txt` (now with MVP performance) |
| `say-read-es file.txt` | `say-read -l es file.txt` |
| `say-read-continuous file.txt` | `say-read file.txt` (MVP already handles this) |
| `say-read-mvp file.txt` | `say-read file.txt` (becomes default) |
| `say-read-gnome file.txt` | `say-read --gnome file.txt` |

## Implementation Plan

### Phase 1: Create Unified Binary (Week 1)
1. **Use MVP as base**
   - Rename `say-read-mvp` to `say-read`
   - MVP already includes progressive streaming optimizations
   - No need to port continuous streaming (MVP handles this better)

2. **Add new features to MVP base**
   - Add `--local` flag to use Kokoro model instead of Edge TTS
   - Port GNOME integration from `say-read-gnome`
   - Add language parameter system (`-l` / `--language`)
   - Integrate Spanish language support directly

3. **Testing**
   - Test local model switching
   - Test language parameter functionality
   - Verify GNOME integration works
   - Performance benchmarks to confirm MVP improvements retained

### Phase 2: Transition Period (Weeks 2-4)
1. **Create compatibility layer**
   ```bash
   # Symlinks with deprecation warnings
   say-read-es → say-read --language es "$@"
   say-read-continuous → say-read "$@"  # No special flag needed
   say-read-mvp → say-read "$@"  # Direct replacement
   say-read-gnome → say-read --gnome "$@"
   ```

2. **Update documentation**
   - Update README with new usage
   - Add migration guide
   - Update man pages/help text

3. **User communication**
   - Add deprecation warnings to old scripts
   - Show helpful migration messages
   - Update installer to use new structure

### Phase 3: Cleanup (Week 5)
1. **Remove deprecated scripts**
   - Delete old variant scripts
   - Remove compatibility symlinks
   - Clean up unused dependencies

2. **Final optimization**
   - Profile unified script
   - Optimize import times
   - Reduce memory footprint

## Benefits

### For Users
- **Better performance by default**: MVP implementation with 50%+ faster startup
- **Local model support**: Can switch between cloud and local TTS
- **Simpler mental model**: One tool for document reading
- **Feature discovery**: All options visible in `--help`
- **Better documentation**: Single comprehensive man page

### For Developers
- **Reduced maintenance**: Fix bugs in one place
- **Cleaner codebase**: No duplicate implementations
- **MVP as foundation**: Already optimized codebase as the base
- **Easier testing**: Single test suite
- **Simpler CI/CD**: Fewer artifacts to manage

### For the Project
- **45% reduction** in binary count (9 → 5)
- **Cleaner architecture**: Clear separation of concerns
- **Better extensibility**: Easy to add new features as flags
- **Performance gains**: MVP improvements become default
- **Professional appearance**: Less cluttered bin directory

## Migration Examples

### Example 1: Spanish User
```bash
# Before
$ say-read-es ~/Documents/spanish-article.txt

# After
$ say-read -l es ~/Documents/spanish-article.txt
# or
$ say-read --language spanish ~/Documents/spanish-article.txt
```

### Example 2: Local Model User
```bash
# Before (had to use different TTS binary)
$ # No direct way to use local model with say-read

# After (simple flag)
$ say-read --local large-document.pdf     # Use Kokoro local model
$ say-read large-document.pdf             # Use Edge TTS (default)
```

### Example 3: GNOME Desktop User
```bash
# Before
$ say-read-gnome ~/book.epub  # If they found this variant

# After
$ say-read --gnome ~/book.epub
# or with full desktop integration
$ say-read --gnome --notifications ~/book.epub
```

## Backwards Compatibility

### Transition Strategy
1. **Version 1.1.0**: Introduce unified `say-read` with all features
2. **Version 1.2.0**: Add deprecation warnings to old scripts
3. **Version 1.3.0**: Convert old scripts to compatibility wrappers
4. **Version 2.0.0**: Remove old scripts entirely

### Compatibility Wrapper Example
```bash
#!/usr/bin/env bash
# say-read-es compatibility wrapper
echo "Warning: say-read-es is deprecated. Use 'say-read -l es' instead." >&2
exec "$(dirname "$0")/say-read" --language es "$@"
```

## Success Metrics

- **Code reduction**: Measure lines of duplicate code removed
- **User adoption**: Track usage of new unified options vs old scripts
- **Bug reports**: Reduction in variant-specific issues
- **Performance**: Ensure no regression in startup/execution time
- **Documentation**: Simplified from 5 man pages to 1

## Timeline

| Week | Phase | Deliverables |
|------|-------|--------------|
| 1 | Design & Implementation | Unified `say-read` with all features |
| 2-4 | Transition | Compatibility layer, documentation, warnings |
| 5 | Cleanup | Remove old scripts, final optimization |
| 6 | Release | Version 2.0.0 with consolidated binaries |

## Key Decisions

1. **MVP becomes the new say-read**: The MVP implementation has already solved performance issues, so it becomes the foundation
2. **No progressive/continuous flags needed**: MVP already handles this optimally
3. **Add --local flag**: Allow switching between Edge TTS (cloud) and Kokoro (local) models
4. **Language as parameter**: Replace language-specific scripts with `-l` parameter
5. **GNOME features as flags**: Integrate desktop features as optional flags

## Conclusion

This consolidation will transform Linux Speech Tools from a collection of related scripts into a cohesive, professional toolset. By reducing 9 binaries to 5 and using the MVP implementation as the foundation, we get better performance by default while maintaining all functionality. The addition of the `--local` flag provides flexibility between cloud and local TTS models, addressing a key missing feature.