# Product Requirements Document (PRD) — Voice Memos Skill

> **Status:** Implemented (Core CLI & Local Verification Complete; Remote CI & PR Merge Pending)
> **Repository:** `grapeot/voice-memos-skill` (Public, MIT License)

## 1. Problem Statement

macOS Apple Voice Memos stores recordings inside a protected group container (`~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings`), backed by a Core Data SQLite database (`CloudRecordings.db`).

Users and AI agents trying to list or export Voice Memos face three recurring problems:
1. **Leftover audio files after deletion:** Deleting a recording in the Voice Memos app sets `ZEVICTIONDATE` in `CloudRecordings.db` (moving the item to "Recently Deleted"), while the `.m4a` or `.qta` file frequently remains on disk. Naive scripts that scan the folder for `.m4a` files accidentally ingest deleted recordings.
2. **Unsafe SQLite reads:** Opening `CloudRecordings.db` without read-only URI semantics can create an empty database file when the path is wrong, while using `immutable=1` or copying only `CloudRecordings.db` misses uncheckpointed rows in `CloudRecordings.db-wal`.
3. **Cloud-evicted or missing local media:** A recording can be active in the database while its local audio file is absent on disk. Tools need a clear distinction between recording lifecycle state (`active` vs. `deleted`) and local file presence (`local` vs. `not_local`).

`voice-memos-skill` provides a small, zero-runtime-dependency Python CLI (`voice-memos`) and an agent skill (`skills/voice-memos/SKILL.md`) that solve this cleanly and deterministically.

## 2. Goals

1. **Authoritative metadata inspection:** Read `CloudRecordings.db` (`ZCLOUDRECORDING`) in read-only mode with full SQLite WAL visibility and determine `state` (`active` vs. `deleted`) strictly from `ZEVICTIONDATE`.
2. **Independent media availability reporting:** Report `audio.availability` (`local` vs. `not_local`) independently from `state`, so callers can see whether a file is present on disk without confusing disk residue for an active recording.
3. **Safe native audio export:** Copy locally available `.m4a` / `.qta` files for `active` recordings to a user-specified `--output-dir` with size and SHA-256 verification, refusing `deleted` or `not_local` recordings and refusing to overwrite differing existing files.
4. **Machine-readable CLI contract:** Emit a uniform JSON envelope to `stdout` on both success and failure (except `--help` and `--version`), write diagnostics only to `stderr`, and return deterministic exit codes (`0`, `2`, `3`, `4`, `5`).
5. **Single-skill agent ergonomics:** Provide one concise root skill (`skills/voice-memos/SKILL.md`) that AI coding agents (Codex, Claude Code, Cursor, OpenCode) can install and invoke without multi-step ceremony.

## 3. Non-Goals (Out of Scope)

To keep the tool small, reliable, and audit-friendly, the following are explicitly out of scope:
- **No deleted-recording export opt-in:** `export` never exports `deleted` recordings; there is no `--include-deleted` or `--force` flag on `export`.
- **No source mutation or sync:** The CLI never modifies `CloudRecordings.db`, never deletes or moves source audio files, and never triggers iCloud sync or cloud downloads.
- **No background daemon or file watcher:** Every invocation is a single synchronous command.
- **No audio transcoding or probing:** Native `.m4a` and `.qta` extensions are preserved as-is; there is no `ffmpeg` dependency, no waveform/codec decoding, and `duration_seconds` comes directly from `ZDURATION` in `CloudRecordings.db`.
- **No speech-to-text (ASR) or downstream pipeline orchestration:** Transcription, batch ingestion, and downstream note routing belong to separate tools.

## 4. Target Personas & Core Workflows

### 4.1 AI Coding Agent / Automation Script
- Runs `voice-memos doctor` to verify TCC read access and schema support before querying.
- Runs `voice-memos list --date YYYY-MM-DD --timezone <IANA_TZ>` to find active recordings from a specific day.
- Runs `voice-memos export <recording_id> --output-dir <dir>` to copy a verified `.m4a` / `.qta` file into a working directory for downstream processing.

### 4.2 Developer / Power User
- Runs `voice-memos list --state deleted` or `voice-memos get <recording_id>` to audit which recordings are marked deleted in Voice Memos even when their audio files still occupy local disk space.
- Uses `voice-memos export <recording_id> --output-dir <dir> --dry-run` to preview the sanitized destination path before copying.

## 5. Functional Requirements Summary

| Subcommand | Purpose | Source Access | Mutates Destination? |
| --- | --- | --- | --- |
| `doctor` | Validate source directory, read-only SQLite open, required `ZCLOUDRECORDING` columns, and total/active/deleted counts | Read-only | No |
| `list` | List recordings filtered by `--state`, `--date` (in `--timezone`), and `--limit`, ordered newest-first with ID tiebreaker | Read-only | No |
| `get` | Fetch normalized metadata and local file availability for a single `recording_id` (`active` or `deleted`) | Read-only | No |
| `export` | Copy native local audio for an `active` recording to `--output-dir` (or preview with `--dry-run`) | Read-only | Yes (unless `--dry-run`) |

## 6. Acceptance Criteria

1. Root flags (`--source`, `--db`, `--version`) are accepted before the subcommand on both `voice-memos` and `python -m voice_memos_skill`.
2. Deleted recordings (`ZEVICTIONDATE IS NOT NULL`) with local audio files still on disk are excluded from default `voice-memos list`, inspectable via `voice-memos list --state deleted|all` and `voice-memos get <id>`, and rejected by `voice-memos export <id>` with exit code `4`.
3. Active recordings whose audio file is absent on disk report `state: "active"` and `audio.availability: "not_local"`, and are rejected by `voice-memos export <id>` with exit code `4`.
4. Exporting an active local recording twice to the same `--output-dir` returns `status: "exported"` on the first run and `status: "already_exists"` on the second run (both exit `0`), while a differing pre-existing destination file fails with exit code `5`.
5. Source application data (`CloudRecordings.db` and media files) is never mutated across any command.
