# Voice Memos Skill (`voice-memos`)

A lightweight, read-safe Python CLI and AI agent skill for inspecting and exporting local macOS Apple Voice Memos (`CloudRecordings.db` and native `.m4a` / `.qta` media).

> **Project Status:** Core CLI implemented; local tests, cross-platform CI, and read-only macOS metadata smoke verified.

## Why This Tool Exists

macOS Voice Memos stores recording metadata in a Core Data SQLite database (`CloudRecordings.db`) alongside audio files in `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings`.

Scanning that directory with `find` or `ls` is unreliable: when a user deletes a recording in Voice Memos, the audio file often remains on disk until macOS purges it later. Only the SQLite `ZCLOUDRECORDING.ZEVICTIONDATE` column reliably distinguishes active recordings from deleted ones. `voice-memos` queries `CloudRecordings.db` in read-only mode (preserving SQLite WAL visibility), pairs each row with its local file state, and exports only active, locally available recordings in their native audio format.

## Features

- **Zero runtime dependencies:** Python 3.11+ standard library only at runtime (`pytest` for development tests).
- **Authoritative deletion filtering:** Uses `ZCLOUDRECORDING.ZEVICTIONDATE` (matching `VoiceMemos.framework`'s `RCQueryManager` predicates) rather than guessing from filenames or `ZFLAGS`.
- **Safe read-only database access:** Opens `CloudRecordings.db` via SQLite URI `mode=ro` so live WAL changes remain visible and missing databases are never created.
- **Native audio export:** Copies locally available `.m4a` / `.qta` audio to an explicit `--output-dir` with SHA-256 verification, refusing deleted or non-local recordings and never overwriting differing files.
- **Deterministic JSON output:** Every invocation (except `--help` and `--version`) prints a single JSON envelope to `stdout` (both on success and on error), keeping diagnostic messages strictly on `stderr`.

## Installation

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv venv
source .venv/bin/activate
uv pip install -e '.[dev]'
```

Once installed, the CLI can be invoked either as `voice-memos` or `python -m voice_memos_skill`.

### Installing as an Agent Skill

To install this tool into an AI coding assistant (Codex, Claude Code, Cursor, or OpenCode), share the repository URL (`https://github.com/grapeot/voice-memos-skill`) with your agent and ask it to install the skill. The agent should:

1. Read the target workspace's `AGENTS.md` or `CLAUDE.md` and skill index/routing conventions.
2. Clone or update this repository and install the package into a virtual environment using `uv`.
3. Register only the single root skill at [`skills/voice-memos/SKILL.md`](skills/voice-memos/SKILL.md).

## CLI Usage

Root options (`--source`, `--db`, `--version`) must be placed **before** the subcommand.

```text
voice-memos [--source DIR] [--db PATH] [--version] {doctor,list,get,export} ...
```

### Root Options

| Option | Default | Description |
| --- | --- | --- |
| `--source DIR` | `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings` | Directory containing Voice Memos recordings and database |
| `--db PATH` | `<source>/CloudRecordings.db` | Explicit path to the SQLite database file |
| `--version` | — | Print package version and exit |

### Subcommands

#### `doctor` — Check database readability and recording counts

```bash
voice-memos doctor
voice-memos --source /path/to/Recordings doctor
```

Checks that the source directory and `CloudRecordings.db` are readable, validates that `ZCLOUDRECORDING` exposes the required schema columns, and reports total, active, and deleted recording counts. Does not modify or create any files, and does not probe audio codecs or microphone state.

#### `list` — List recordings with state and date filtering

```bash
voice-memos list
voice-memos list --state all --limit 20
voice-memos list --date 2026-10-17 --timezone America/Los_Angeles
voice-memos --source /path/to/Recordings list --state deleted
```

| Option | Default | Description |
| --- | --- | --- |
| `--state {active,deleted,all}` | `active` | Filter by recording lifecycle state |
| `--date YYYY-MM-DD` | — | Filter recordings created on this calendar date in `--timezone` |
| `--timezone IANA_NAME` | `UTC` | IANA timezone used to evaluate `--date` against `created_at` |
| `--limit N` | `50` | Maximum number of recordings to return (positive integer) |

Results are ordered newest-first (`created_at` descending) with a deterministic tiebreaker on `id` (`ZUNIQUEID`).

#### `get` — Inspect a single recording (active or deleted)

```bash
voice-memos get <recording_id>
voice-memos --source /path/to/Recordings get <recording_id>
```

Returns normalized metadata and local file availability for `<recording_id>`, including recordings in the `deleted` state.

#### `export` — Copy an active local recording to a destination directory

```bash
voice-memos export <recording_id> --output-dir ./exports
voice-memos export <recording_id> --output-dir ./exports --dry-run
voice-memos --source /path/to/Recordings export <recording_id> --output-dir ./exports
```

| Argument / Option | Required | Description |
| --- | --- | --- |
| `recording_id` | Yes | Unique ID (`ZUNIQUEID`) of the recording to export |
| `--output-dir DIR` | Yes | Destination directory for the exported audio file |
| `--dry-run` | No | Validate and return planned paths without creating `--output-dir` or copying bytes |

Export rules:
- Only `active` recordings with `audio.availability == "local"` can be exported. Deleted recordings and `not_local` recordings are rejected with exit code `4`, even if a leftover audio file still exists on disk.
- The original native file extension (`.m4a` or `.qta`) is preserved without transcoding (`ffmpeg` is not required).
- Output filenames are sanitized from `<recording_id><ext>`.
- If the destination file already exists with identical size and SHA-256 digest, `export` succeeds (`exit 0`) with `status: "already_exists"`.
- If the destination file already exists with different content, `export` fails (`exit 5`) with a conflict error and never overwrites it.

## JSON Envelope & Exit Codes

All commands (except `--help` and `--version`) emit a single JSON object to `stdout`:

```json
{
  "command": "list",
  "data": {
    "recordings": [
      {
        "id": "9F2C6B8A-1234-4E5A-9B01-ABCDEF012345",
        "title": "Project note",
        "created_at": "2026-10-17T14:22:10Z",
        "deleted_at": null,
        "duration_seconds": 42.5,
        "state": "active",
        "audio": {
          "availability": "local",
          "path": "/Users/example/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/20261017-142210-9F2C6B8A.m4a",
          "size_bytes": 348120
        }
      }
    ],
    "count": 1
  },
  "error": null
}
```

On failure, `stdout` still receives a structured JSON envelope while human-oriented diagnostics are written only to `stderr`:

```json
{
  "command": "export",
  "data": null,
  "error": {
    "code": "RECORDING_DELETED",
    "message": "Recording '9F2C6B8A-1234-4E5A-9B01-ABCDEF012345' is in deleted state and cannot be exported."
  }
}
```

### Exit Codes

| Exit Code | Meaning |
| --- | --- |
| `0` | Success (including empty `list` results, `export --dry-run`, and `already_exists` exports) |
| `2` | Invalid CLI arguments or option values |
| `3` | Source directory, SQLite database, permission, or schema validation error |
| `4` | Recording not found, recording is `deleted` (on `export`), or audio is `not_local` (on `export`) |
| `5` | Destination directory error, file copy verification failure, or existing file conflict |

## macOS Permissions (TCC / Full Disk Access)

On macOS, `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings` is protected by Transparency, Consent, and Control (TCC). If `voice-memos doctor` fails with exit code `3` (`SOURCE_ACCESS_ERROR` or `DB_OPEN_ERROR`), grant Full Disk Access (or the appropriate container access permission) to your terminal application in **System Settings → Privacy & Security → Full Disk Access**, then restart the terminal.

## Development & Testing

```bash
uv venv
source .venv/bin/activate
uv pip install -e '.[dev]'
python -m pytest
```

Documentation structure:
- [`AGENTS.md`](AGENTS.md) — Contributor workflow, privacy rules, and repository conventions
- [`docs/prd.md`](docs/prd.md) — Product requirements and scope boundaries
- [`docs/rfc.md`](docs/rfc.md) — Technical specification, SQLite schema contract, and JSON schemas
- [`docs/test.md`](docs/test.md) — Offline synthetic test plan and optional local smoke verification
- [`docs/working.md`](docs/working.md) — Roadmap status, changelog, and lessons learned
- [`skills/voice-memos/SKILL.md`](skills/voice-memos/SKILL.md) — Root skill definition for AI coding agents

## License

MIT
