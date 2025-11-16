# Scripts Directory Reorganization Plan

## Current Structure Analysis

### Total: 17 scripts across 4 subdirectories
```
scripts/
├── (7 scripts in root)
├── demo/ (2 scripts)
├── install/ (3 scripts)
├── release/ (1 script)
└── setup/ (3 scripts)
```

## Detailed Script Analysis

### 1. Testing & Debug Scripts (Root Level) - **REDUNDANT**

#### **test-speech.sh** (26 lines)
- **Purpose**: Basic speech test
- **Uses**: talk2claude
- **Status**: ❌ **REDUNDANT** - Overlaps with debug-speech.sh
- **Action**: Merge into debug-speech.sh

#### **debug-speech.sh** (57 lines)
- **Purpose**: Detailed debug version with step-by-step tests
- **Uses**: ffmpeg, whisper model testing
- **Status**: ✅ **KEEP** - Useful for troubleshooting
- **Action**: Merge test-speech.sh into this, add --quick flag

#### **simple-speech.sh** (72 lines)
- **Purpose**: Simplified speech-to-clipboard bypassing notifications
- **Uses**: Custom implementation
- **Status**: ❌ **REDUNDANT** - Duplicates talk2claude functionality
- **Action**: DELETE (functionality exists in talk2claude)

#### **toggle-speech.sh** (200 lines)
- **Purpose**: Toggle speech recording on/off
- **Uses**: Complex state management
- **Status**: 🟡 **QUESTIONABLE** - Overlaps with talk2claude toggle
- **Action**: Move to examples/ or integrate into talk2claude

#### **test-gnome-integration.sh** (115 lines)
- **Purpose**: Test GNOME features
- **Uses**: gnome-dictation, talk2claude
- **Status**: 🟡 **MOVE** - Developer tool
- **Action**: Move to tests/ directory

### 2. GNOME Support Scripts (Root Level)

#### **gnome-notification-handler.sh** (184 lines)
- **Purpose**: D-Bus handler for GNOME notifications
- **Uses**: Called by say-read-gnome
- **Status**: ❌ **WILL BE OBSOLETE** after say-read consolidation
- **Action**: Integrate into unified say-read or move to src/gnome/

### 3. Release Scripts

#### **pre-release-check.sh** (294 lines)
- **Purpose**: Comprehensive pre-release validation
- **Status**: ✅ **KEEP** - Essential for releases
- **Action**: Keep in scripts/release/

#### **quick-release-check.sh** (85 lines)
- **Purpose**: Quick validation subset
- **Status**: ❌ **REDUNDANT** - Subset of pre-release-check
- **Action**: Merge into pre-release-check.sh as --quick flag

#### **release/release.sh** (470 lines)
- **Purpose**: Main release automation
- **Status**: ✅ **KEEP** - Core functionality
- **Action**: Keep as-is

### 4. Demo Scripts (/demo)

#### **demo-audio-streaming.sh** (206 lines)
- **Purpose**: Demonstrates chunked vs continuous audio
- **Status**: 🟡 **MOVE** - Not production code
- **Action**: Move to examples/ or docs/demos/

#### **demo-gnome-media-integration.sh** (279 lines)
- **Purpose**: GNOME media controls demo
- **Status**: 🟡 **MOVE** - Not production code
- **Action**: Move to examples/ or docs/demos/

### 5. Installation Scripts (/install)

#### **install-with-uv.sh** (330 lines)
- **Purpose**: Modern uv-based installer
- **Status**: ✅ **KEEP** - Primary installer
- **Action**: Keep as-is

#### **install-continuous-streaming-deps.sh** (242 lines)
- **Purpose**: Install streaming dependencies
- **Status**: ❌ **OBSOLETE** - MVP handles this differently
- **Action**: DELETE or move to legacy/

#### **install-gnome-integration.sh** (250 lines)
- **Purpose**: Install GNOME features
- **Status**: 🟡 **MERGE** - Should be part of main installer
- **Action**: Merge into install-with-uv.sh as optional feature

### 6. Setup Scripts (/setup)

#### **setup-hotkey.sh** (39 lines)
- **Purpose**: Configure keyboard shortcuts
- **Status**: ✅ **KEEP** - User utility
- **Action**: Keep, maybe rename to setup-keyboard-shortcuts.sh

#### **choose-recording-mode.sh** (60 lines)
- **Purpose**: Select recording method
- **Status**: 🟡 **QUESTIONABLE** - Might be obsolete
- **Action**: Review if still needed with current architecture

#### **restart-gnome-wayland.sh** (53 lines)
- **Purpose**: GNOME/Wayland restart utility
- **Status**: 🟡 **MOVE** - System utility
- **Action**: Move to utils/ or document in troubleshooting

## Proposed New Structure

```
scripts/
├── install/
│   └── install-with-uv.sh          # Main installer (merge GNOME features)
├── release/
│   ├── release.sh                  # Main release script
│   └── pre-release-check.sh        # Validation (with --quick flag)
├── setup/
│   └── setup-keyboard-shortcuts.sh # Hotkey configuration
└── debug/
    └── debug-speech.sh              # Troubleshooting (with --quick flag)

tests/                               # Move test scripts here
└── test-gnome-integration.sh

examples/                            # Create new directory
├── demos/
│   ├── demo-audio-streaming.sh
│   └── demo-gnome-media-integration.sh
└── toggle-speech.sh                # Example implementation

src/gnome/                          # Move GNOME internals
└── gnome-notification-handler.sh  # If still needed after consolidation
```

## Action Summary

### DELETE (5 scripts - 30% reduction)
1. `scripts/test-speech.sh` - Merge into debug-speech.sh
2. `scripts/simple-speech.sh` - Redundant with talk2claude
3. `scripts/quick-release-check.sh` - Merge into pre-release-check.sh
4. `scripts/install/install-continuous-streaming-deps.sh` - Obsolete
5. `scripts/choose-recording-mode.sh` - Likely obsolete

### MOVE (6 scripts)
1. `scripts/toggle-speech.sh` → `examples/`
2. `scripts/test-gnome-integration.sh` → `tests/`
3. `scripts/demo/*` → `examples/demos/`
4. `scripts/gnome-notification-handler.sh` → `src/gnome/` (or delete)
5. `scripts/restart-gnome-wayland.sh` → `docs/troubleshooting/`

### MERGE (2 operations)
1. Merge `install-gnome-integration.sh` into `install-with-uv.sh`
2. Add `--quick` flag to `pre-release-check.sh` (replacing quick-release-check.sh)

### KEEP AS-IS (4 scripts)
1. `scripts/install/install-with-uv.sh`
2. `scripts/release/release.sh`
3. `scripts/release/pre-release-check.sh`
4. `scripts/setup/setup-keyboard-shortcuts.sh`

## Benefits

1. **53% reduction** in scripts/ directory (17 → 8 scripts)
2. **Clear separation** between production and examples
3. **No redundancy** - each script has unique purpose
4. **Better organization** - scripts grouped by function
5. **Cleaner root** - only essential subdirectories

## Implementation Priority

### Phase 1: Remove Redundant Scripts
- Delete test-speech.sh, simple-speech.sh, quick-release-check.sh
- Delete obsolete install-continuous-streaming-deps.sh

### Phase 2: Create New Structure
- Create examples/ and examples/demos/ directories
- Create tests/ directory if not exists
- Move demo scripts to examples/demos/

### Phase 3: Consolidate Features
- Merge GNOME installation into main installer
- Add --quick flag to pre-release-check.sh
- Merge test-speech.sh functionality into debug-speech.sh

### Phase 4: Final Cleanup
- Update any references in documentation
- Remove empty directories
- Update CI/CD if needed

## Result

From a cluttered scripts/ with 17 scripts of overlapping functionality, we'll have a clean structure with 8 essential scripts, each with a clear, unique purpose. The demos and examples will be properly separated, making it clear what's production vs educational content.