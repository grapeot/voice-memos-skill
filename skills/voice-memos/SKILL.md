---
name: voice-memos
description: List, inspect, and export local macOS Apple Voice Memos (including deleted-state inspection and native .m4a/.qta export) via the read-safe voice-memos CLI.
---

# Apple Voice Memos Skill (`voice-memos`)

## Goal & Success Criteria

Inspect and export local macOS Apple Voice Memos deterministically without mutating the source container or accidentally ingesting deleted recordings whose audio files still remain on disk.

An operation succeeds when:
1. Metadata and lifecycle state (`active` vs. `deleted`) are read from `CloudRecordings.db` (`ZCLOUDRECORDING.ZEVICTIONDATE`) via `voice-memos`, never by globbing `.m4a` files in the container directory.
2. Exported audio files come only from `active` recordings with `audio.availability == "local"`, copied into an explicit `--output-dir` in their native `.m4a` / `.qta` format with verified byte size and SHA-256 digest.
3. The Voice Memos source application data (`CloudRecordings.db` and media files in `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings`) remains untouched.

## Critical Pitfall: On-Disk Residue vs. Deleted State

When a user deletes a recording in Apple Voice Memos, macOS sets `ZEVICTIONDATE` in `CloudRecordings.db` (`RCQueryManager.deletedRecordingsPredicate`), **but the `.m4a` / `.qta` file often stays on disk** until macOS purges it later.
- **Never** scan `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings` directly with `ls`/`find` to find active recordings.
- **Never** infer deletion state from `ZFLAGS`.
- Always use `voice-memos`, which reports `state` (`active` | `deleted`) and `audio.availability` (`local` | `not_local`) independently, and refuses to export `deleted` or `not_local` recordings (exit code `4`).

## Installation & Agent Registration

When a human hands this repository URL (`https://github.com/grapeot/voice-memos-skill`) to an AI coding agent (Codex, Claude Code, Cursor, or OpenCode):

1. Check the target workspace's `AGENTS.md` or `CLAUDE.md` and skill routing index first.
2. Install the CLI inside the repository's virtual environment using `uv`:
   ```bash
   uv venv
   source .venv/bin/activate
   uv pip install -e '.[dev]'
   python -m pytest
   ```
3. Register **only** this single root skill (`skills/voice-memos/SKILL.md`) according to the target workspace's skill conventions.

## CLI Reference

Entry points: `voice-memos` or `python -m voice_memos_skill`.
**Important:** Root flags (`--source`, `--db`, `--version`) must be placed **before** the subcommand.

```bash
# 1. Verify database readability, schema support, and active/deleted counts
voice-memos doctor

# 2. List active recordings (newest first; default --state active, --limit 50)
voice-memos list
voice-memos list --date 2026-10-17 --timezone America/Los_Angeles --limit 20

# 3. Inspect deleted recordings (including those with leftover local audio files)
voice-memos list --state deleted
voice-memos get <recording_id>

# 4. Preview or export an active local recording to an output directory
voice-memos export <recording_id> --output-dir ./exports --dry-run
voice-memos export <recording_id> --output-dir ./exports

# 5. Override source directory or database path (root flags go BEFORE subcommand)
voice-memos --source /path/to/Recordings list --state all
voice-memos --source /path/to/Recordings --db /path/to/CloudRecordings.db doctor
```

### Subcommand & Option Summary

- **Root flags:**
  - `--source DIR` (default: `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings`)
  - `--db PATH` (default: `<source>/CloudRecordings.db`)
  - `--version`
- **`doctor`**: Checks read-only SQLite access (`mode=ro`), required `ZCLOUDRECORDING` columns, and `total` / `active` / `deleted` counts.
- **`list`**: `--state {active,deleted,all}` (default `active`), `--date YYYY-MM-DD`, `--timezone IANA_NAME` (default `UTC`), `--limit N` (default `50`). Duration (`duration_seconds`) comes from `ZDURATION` in SQLite.
- **`get <recording_id>`**: Returns normalized metadata and `audio` (`availability`, `path`, `size_bytes`) for any `active` or `deleted` recording.
- **`export <recording_id> --output-dir DIR [--dry-run]`**: Copies native `.m4a` / `.qta` audio for an `active` + `local` recording to `<output-dir>/<sanitized_id><ext>`. Returns `status` (`planned` | `exported` | `already_exists`), `source_size_bytes`, `output_size_bytes`, `sha256`, and `verified`. Does not transcode or decode audio.

## Output Envelope & Exit Codes

All commands (except `--help` and `--version`) print a single JSON envelope to `stdout` (`{"command": "...", "data": {...}, "error": null}` on success, or `{"command": "...", "data": null, "error": {"code": "...", "message": "..."}}` on failure), with diagnostics on `stderr`.

- `0`: Success (including empty `list`, `export --dry-run`, or byte-identical `already_exists`)
- `2`: Invalid CLI arguments / flag values
- `3`: Source directory, SQLite database, TCC permission, or schema error (if accessing the default macOS container fails with exit code `3`, the user's terminal app may need Full Disk Access in **System Settings → Privacy & Security → Full Disk Access**)
- `4`: Recording not found, recording is `deleted` (on `export`), or audio is `not_local` (on `export`)
- `5`: Destination error, copy verification mismatch, or existing destination file with differing bytes (never overwritten)
