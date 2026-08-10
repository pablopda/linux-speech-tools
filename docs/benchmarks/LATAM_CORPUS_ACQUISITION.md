# Private LATAM ASR corpus acquisition

`lst-asr-corpus` prepares the exact 36-clip corpus required by the LATAM ASR
gate without placing audio, references, speaker identifiers or capture details
in the repository. It does not download a model, run an ASR engine, or create
quality evidence. No recording was performed while this workflow was added.

## Safety and acceptance contract

- The workspace must be an absolute, current-user-owned, `0700` directory
  outside the source checkout. Symlinks, FIFOs, other special files, hard-linked
  private files and group/world-readable workspace state are rejected.
- `init` creates `manifest.json` and a private `.corpus.lock` as `0600`, plus an
  `audio/` directory as `0700`. Init, status, recording, review, and validation
  take that owner-only advisory lock with a bounded wait, so concurrent commands
  cannot overwrite one another's manifest updates or hang indefinitely behind
  a live holder. The fixed plan is AR=24, MX=4, CO=4 and CL=4, with all eight
  required categories and one planned code-switch clip.
- If initial manifest creation fails, only the known-empty managed audio
  directory is rolled back. The private lock is reusable, and the next `init`
  safely resumes instead of rejecting its own partial state.
- Speaker names are not accepted. Each recording requires a pseudonymous token
  in the fixed form `speaker-ar-01` (accent plus a two- or three-digit number).
  A token may be reused for clips from the same accent,
  but never across accents. MX, CO and CL clips must be supplied by authentic
  speakers of those accents; do not imitate them with the AR speaker.
- Each capture requires explicit consent, human reference review and authentic
  accent confirmation. Post-capture privacy review is a separate explicit step
  so it cannot be attested before the resulting audio exists.
- ffmpeg receives an argument vector, not a shell command. Capture uses the
  selected PulseAudio source, has a hard deadline, and must produce a bounded
  16 kHz mono PCM WAV. The workflow hashes and decodes the file before an atomic
  manifest update. Re-recording is explicit and preserves the prior accepted
  file if the new capture fails.
- Every accepted audio file must still be a current-user-owned private regular
  file with exactly one hard link. Validation checks those properties on the
  exact pinned descriptor used for checksum and decode, then verifies the same
  inode and metadata fingerprint remained stable. Readable-by-group/world,
  hard-linked, or transiently changed audio is rejected even when its checksum
  is correct.
- `status` emits only fixed labels, pseudonymous clip IDs and progress states.
  It never prints references, paths, speaker IDs or capture-device values.
- Invalid command-line input receives a fixed diagnostic. Unexpected arguments,
  invalid typed values and private paths are never echoed by the parser.

If the manifest replacement completes but its parent-directory fsync cannot be
confirmed, the command reports a fixed, path-free durability error and keeps
the new manifest and audio together. Run `status` before retrying. It never
rolls the audio back behind already-committed manifest metadata. Failures before
the manifest commit point restore the prior audio/manifest pair.

The confirmations record that a human performed the reviews; software cannot
prove consent, a speaker's authentic accent or reference accuracy by itself.
The authentic-accent flag stores a human-reviewed evidence attestation; it is
not identity or accent authentication by the program.

## Guided workflow

Choose a private location outside the checkout. The parent must already exist:

```bash
export LST_LATAM_CORPUS="${XDG_DATA_HOME:-$HOME/.local/share}/lst-private/latam-asr"
install -d -m 700 "$(dirname "$LST_LATAM_CORPUS")"
./bin/lst-asr-corpus init --directory "$LST_LATAM_CORPUS"
./bin/lst-asr-corpus status --directory "$LST_LATAM_CORPUS"
```

Inspect available PulseAudio/PipeWire-Pulse source names without recording:

```bash
pactl list short sources
```

Prepare a private UTF-8 reference. Use an empty file for a planned `silence`
clip. Do not put a transcript or a speaker name in the filename:

```bash
umask 077
printf '%s\n' 'reviewed private reference' \
  > "$LST_LATAM_CORPUS/reference-current.txt"
```

After the speaker has consented, the reference was reviewed by a fluent human,
and the speaker's listed accent is authentic, record one clip. Pick a duration
long enough for this clip but no longer than necessary:

```bash
./bin/lst-asr-corpus record \
  --directory "$LST_LATAM_CORPUS" \
  --id ar-001 \
  --source default \
  --speaker-id speaker-ar-01 \
  --reference-file "$LST_LATAM_CORPUS/reference-current.txt" \
  --max-seconds 8 \
  --consent-confirmed \
  --reference-reviewed \
  --authentic-accent-confirmed
```

Listen to the private file locally. If it contains no unapproved names,
background conversation or other identifying content, record the separate
privacy decision:

```bash
ffplay -nodisp -autoexit "$LST_LATAM_CORPUS/audio/ar-001.wav"
./bin/lst-asr-corpus review \
  --directory "$LST_LATAM_CORPUS" \
  --id ar-001 \
  --privacy-reviewed
```

Resume at any time with `status`. A status or validation command waits only for
the bounded lock interval while a record/review transaction owns the workspace;
if the holder remains live, it returns a path-free busy error and can be retried.
To replace an existing recording, repeat the `record` command with `--replace`;
consent, reference and authentic-accent confirmations are required again and
privacy returns to pending until the new file is reviewed. A pre-commit failure
preserves the prior audio and manifest.

Use distinct accent-scoped pseudonyms, for example `speaker-ar-01`,
`speaker-mx-01`, `speaker-co-01` and `speaker-cl-01`. A single person or token
must not span the four benchmark accents.

## Mechanical handoff

When all 36 entries say `ready`, hand the same private manifest to the strict
benchmark `--validate-only` contract:

```bash
./bin/lst-asr-corpus validate --directory "$LST_LATAM_CORPUS"

# Equivalent direct command:
uv run --locked python -m src.utils.asr_benchmark \
  --manifest "$LST_LATAM_CORPUS/manifest.json" \
  --validate-only
```

The output is an aggregate-only preflight and manifest checksum. Passing it is
not WER, latency, GPU, consent or quality evidence. Keep the full workspace out
of git and proceed to the two-engine run only after reviewing that private
evidence.

`lst-asr-corpus` itself loads the installed environment helper and runs through
`uv --project ... run --locked`. Native-package installations therefore reuse
their persisted user-data `UV_PROJECT_ENVIRONMENT` and never create a project
environment below read-only `/usr/share`.
