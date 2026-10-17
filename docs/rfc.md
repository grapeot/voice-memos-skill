# Technical Specification (RFC) — Voice Memos Skill

> **Status:** Phase 1 Implementation Contract (Pre-Implementation Specification)
> **Runtime:** Python 3.11+ (Standard Library Only)

## 1. Architecture Overview

`voice_memos_skill` is a single-package Python CLI with zero third-party runtime dependencies. It reads macOS Apple Voice Memos metadata from `CloudRecordings.db` using Python's built-in `sqlite3` module, resolves local audio file metadata within the configured `--source` directory, and optionally copies active local audio files to a destination directory during `export`.

No Objective-C bridge or runtime Apple private framework loading is used. Instead, the CLI queries the documented `ZCLOUDRECORDING` SQLite table directly and fails explicitly if required columns are missing.

## 2. Apple Voice Memos Storage & Schema Authority

### 2.1 Default Paths

- **Source directory (`--source`):**
  `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings`
- **Database path (`--db`):**
  `<source>/CloudRecordings.db` (alongside `CloudRecordings.db-wal` and `CloudRecordings.db-shm` when open in WAL mode)

If `--source` is overridden and `--db` is omitted, `--db` defaults to `<source>/CloudRecordings.db`.

### 2.2 Authoritative SQLite Table and Columns

The CLI queries table `ZCLOUDRECORDING` in `CloudRecordings.db`.

| Column | Requirement | Role & Normalization |
| --- | --- | --- |
| `ZUNIQUEID` | Required | Unique string identifier (`id`) for the recording. Non-empty string rows only. |
| `ZPATH` | Required | Relative filename/subpath or path inside `--source` pointing to the `.m4a` / `.qta` media file. |
| `ZDATE` | Required | Core Data creation timestamp (seconds since `2001-01-01T00:00:00Z`). Converted to Unix epoch by adding `978307200`. |
| `ZDURATION` | Required | Recording duration in seconds (`duration_seconds`), read directly from DB metadata (not probed from media). |
| `ZEVICTIONDATE` | Required | Core Data deletion/eviction timestamp. `NULL` means `state = "active"`; non-`NULL` means `state = "deleted"`. |
| `ZCUSTOMLABEL` | Optional | User-visible custom label string. |
| `ZENCRYPTEDTITLE` | Optional | Fallback title column if stored as a plain SQLite text string; ignored if `NULL`, bytes/blob, or missing. |

**Title resolution:** If `ZCUSTOMLABEL` is present in the schema and holds a non-empty `str`, use it as `title`. Otherwise, if `ZENCRYPTEDTITLE` is present and holds a non-empty `str`, use it. Otherwise `title` is `null`. The CLI does not perform encryption key lookup or ciphertext decoding.

### 2.3 Deletion State Authority (`ZEVICTIONDATE`, Not `ZFLAGS`)

In macOS `VoiceMemos.framework`, `RCQueryManager.deletedRecordingsPredicate` filters recordings by `evictionDate != nil`, and `playableRecordingsExcludingDeletedPredicate` excludes recordings where `evictionDate != nil`.

Accordingly:
- `ZEVICTIONDATE IS NULL` $\rightarrow$ `state = "active"`
- `ZEVICTIONDATE IS NOT NULL` $\rightarrow$ `state = "deleted"`, with `deleted_at` computed as `ZEVICTIONDATE + 978307200` formatted as UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ` (or fractional seconds when present, normalized to UTC `Z`).
- `ZFLAGS` is **never** used to infer deletion state.

### 2.4 Independence of `state` and `audio.availability`

When a recording is deleted in Voice Memos, macOS frequently leaves the `.m4a` or `.qta` file on disk inside `Recordings/` until eviction cleanup runs. Conversely, an active recording synced via iCloud may have a database row before its audio file is downloaded locally.

Therefore, `state` and `audio.availability` are evaluated independently:
- `state`: `"active"` | `"deleted"` (from `ZEVICTIONDATE`)
- `audio.availability`: `"local"` | `"not_local"` (from whether the resolved `ZPATH` exists as a regular file inside `--source`)

When a row is `active` and its file is missing on disk, `audio.availability` is `"not_local"`. The CLI does not label it `"cloud_only"` because the local database alone cannot prove whether the file resides in iCloud or was missing for another reason.

## 3. SQLite Connection & Schema Validation Discipline

1. **Pre-open checks:** Verify `--source` exists and is a directory, and verify the target `--db` file already exists as a regular file and is readable. Never allow SQLite to create a new empty database file when `--db` is missing.
2. **Read-only URI with WAL visibility:** Open SQLite using a proper `file:` URI with `?mode=ro` (using `urllib.parse.quote` / `Path.as_uri()` plus `?mode=ro`, `uri=True`).
   - Do **not** pass `immutable=1`, because `immutable=1` instructs SQLite to ignore `-wal` and `-shm` files, hiding recent uncheckpointed recordings or deletions and risking malformed reads if the live database is mid-write.
   - Do **not** copy `CloudRecordings.db` alone to a temporary file without its `-wal` state.
3. **Schema validation (no filesystem fallback):** Inspect `PRAGMA table_info(ZCLOUDRECORDING)`. If `ZCLOUDRECORDING` does not exist or any required column (`ZUNIQUEID`, `ZPATH`, `ZDATE`, `ZDURATION`, `ZEVICTIONDATE`) is absent, fail immediately with exit code `3` (`SCHEMA_UNSUPPORTED`). Never fall back to scanning `.m4a` files on disk without the database.

## 4. Path Resolution & Containment

`ZPATH` in `CloudRecordings.db` is typically a relative filename (e.g., `20261017-142210-9F2C6B8A.m4a`, though arbitrary filenames are supported and UUID filenames are never assumed) or an absolute path inside the recordings container.

To resolve `ZPATH` safely:
1. Resolve `source_root = Path(source_dir).expanduser().resolve()`.
2. If `ZPATH` is null or empty, treat `audio.availability` as `"not_local"`, `audio.path` as `null`, and `audio.size_bytes` as `null`.
3. If `Path(zpath)` is relative, join `candidate = (source_root / zpath).resolve()`. If `Path(zpath)` is absolute, resolve `candidate = Path(zpath).resolve()`.
4. Verify containment using `candidate.is_relative_to(source_root)` (and ensure `candidate != source_root`).
   - If `candidate` resolves outside `source_root` (for example `../outside.m4a` or `/etc/passwd`), reject path resolution: treat `audio.availability` as `"not_local"` (`path: null`, `size_bytes: null`) during `list`/`get`, and fail `export` with exit code `4` (`AUDIO_NOT_LOCAL` / `INVALID_SOURCE_PATH`) so files outside `--source` are never read or copied.
5. If `candidate` is inside `source_root` and is an existing regular file, set:
   - `audio.availability = "local"`
   - `audio.path = str(candidate)`
   - `audio.size_bytes = candidate.stat().st_size`
   Otherwise set:
   - `audio.availability = "not_local"`
   - `audio.path = str(candidate)`
   - `audio.size_bytes = null`

## 5. Timestamp Conversion, Filtering & Ordering

- **Core Data epoch offset:** `CORE_DATA_EPOCH_OFFSET = 978307200` (seconds between `1970-01-01T00:00:00Z` and `2001-01-01T00:00:00Z`).
- **`created_at` formatting:** Convert `unix_ts = float(ZDATE) + 978307200` to a `datetime` in `timezone.utc` and format as ISO-8601 with trailing `Z` (e.g., `"2026-10-17T14:22:10Z"`).
- **`--date YYYY-MM-DD` and `--timezone IANA_NAME`:**
  - Validate `--timezone` using `zoneinfo.ZoneInfo(tz_name)` (defaults to `"UTC"`). Invalid timezone names fail with exit code `2`.
  - Validate `--date` using `datetime.date.fromisoformat(date_str)`. Invalid date strings fail with exit code `2`.
  - A recording matches `--date` if and only if its UTC creation timestamp converted via `.astimezone(ZoneInfo(tz_name)).date()` equals the target date. This handles local midnight boundaries and Daylight Saving Time (DST) transitions accurately.
- **Ordering:** `list` sorts matching records by `(-float(ZDATE), str(ZUNIQUEID))` (newest creation timestamp first; ascending `id` tiebreaker when two recordings share the exact same timestamp), then applies `--limit N`.

## 6. CLI Contract

### 6.1 Invocation & Root Flags

Entry points:
- `voice-memos`
- `python -m voice_memos_skill`

```text
voice-memos [--source DIR] [--db PATH] [--version] <subcommand> [subcommand-options]
```

Root flags:
- `--source DIR` (default: `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings`)
- `--db PATH` (default: `<source>/CloudRecordings.db`)
- `--version` (prints version and exits `0`)

### 6.2 Normalized Record Object

Used in `list` (`data.recordings[]`) and `get` (`data.recording`):

```json
{
  "id": "9F2C6B8A-1234-4E5A-9B01-ABCDEF012345",
  "title": "Morning Idea",
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
```

### 6.3 Subcommand Specifications

#### `doctor`

Options: none.

Success `data` payload:

```json
{
  "source_path": "/Users/example/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings",
  "db_path": "/Users/example/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/CloudRecordings.db",
  "db_readable": true,
  "schema_supported": true,
  "counts": {
    "total": 12,
    "active": 10,
    "deleted": 2
  }
}
```

#### `list`

Options:
- `--state {active,deleted,all}` (default: `active`)
- `--date YYYY-MM-DD` (optional)
- `--timezone IANA_NAME` (default: `UTC`)
- `--limit N` (default: `50`, must be integer `>= 1`)

Success `data` payload:

```json
{
  "recordings": [ ... ],
  "count": 1,
  "filters": {
    "state": "active",
    "date": "2026-10-17",
    "timezone": "UTC",
    "limit": 50
  }
}
```

#### `get`

Positional arguments:
- `recording_id`

Returns the normalized record regardless of whether `state` is `"active"` or `"deleted"`. If `recording_id` does not exist in `ZCLOUDRECORDING`, exits with code `4` (`RECORDING_NOT_FOUND`).

Success `data` payload:

```json
{
  "recording": { ... }
}
```

#### `export`

Arguments & options:
- `recording_id` (positional, required)
- `--output-dir DIR` (required)
- `--dry-run` (optional boolean flag)

Behavior:
1. Look up `recording_id` in `ZCLOUDRECORDING`.
   - If missing $\rightarrow$ exit code `4` (`RECORDING_NOT_FOUND`).
   - If `state == "deleted"` $\rightarrow$ exit code `4` (`RECORDING_DELETED`), even if the audio file still exists on disk.
   - If `audio.availability != "local"` $\rightarrow$ exit code `4` (`AUDIO_NOT_LOCAL`).
2. Compute deterministic destination filename:
   - Sanitize `recording_id` by replacing any character outside `[A-Za-z0-9._-]` with `_` (and stripping leading dots/empty names).
   - Preserve the native extension from the source file (`Path(source_audio_path).suffix`, e.g., `.m4a` or `.qta`).
   - `destination_path = Path(output_dir).expanduser().resolve() / f"{sanitized_id}{ext}"`.
3. Compute `source_size_bytes` and `source_sha256` of the source audio file.
4. Check if `destination_path` already exists:
   - If `destination_path` is a directory or symlink outside expectations, or cannot be read $\rightarrow$ exit code `5` (`DESTINATION_ERROR`).
   - If `destination_path` exists as a regular file:
     - Compare size and SHA-256 digest with source.
     - If identical $\rightarrow$ return `status = "already_exists"`, `verified = true`, exit code `0` (both in normal mode and `--dry-run`).
     - If different $\rightarrow$ fail with exit code `5` (`DESTINATION_CONFLICT`), without modifying `destination_path`.
5. If `destination_path` does not exist:
   - If `--dry-run` is set: do **not** create `--output-dir` and do **not** write any file. Return `status = "planned"`, `output_size_bytes = null`, `verified = false`, exit code `0`.
   - Otherwise: create `--output-dir` (`mkdir(parents=True, exist_ok=True)`), copy the file to `destination_path`, compute `output_size_bytes` and destination SHA-256, verify they match `source_size_bytes` and `source_sha256` (failing with exit code `5` if verification fails), and return `status = "exported"`, `verified = true`, exit code `0`.

Success `data` payload:

```json
{
  "recording_id": "9F2C6B8A-1234-4E5A-9B01-ABCDEF012345",
  "state": "active",
  "status": "exported",
  "dry_run": false,
  "source_path": "/Users/example/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/20261017-142210-9F2C6B8A.m4a",
  "destination_path": "/Users/example/exports/9F2C6B8A-1234-4E5A-9B01-ABCDEF012345.m4a",
  "source_size_bytes": 348120,
  "output_size_bytes": 348120,
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "verified": true
}
```

*(Note: `verified` confirms byte-level size and SHA-256 copy integrity; it does not perform audio codec decoding.)*

## 7. Output Envelope & Exit Code Matrix

### 7.1 JSON Envelope (`stdout`)

Every command execution (except `--help` / `--version`) outputs a single JSON document on `stdout`:

- **Success:**
  ```json
  {
    "command": "doctor|list|get|export",
    "data": { ... },
    "error": null
  }
  ```
- **Error:**
  ```json
  {
    "command": "doctor|list|get|export|cli",
    "data": null,
    "error": {
      "code": "ERROR_CODE",
      "message": "Human-readable explanation"
    }
  }
  ```

Diagnostic logs or error summaries may also be printed to `stderr`, but `stdout` must always remain valid JSON on CLI errors.

### 7.2 Exit Codes

| Exit Code | Category | Typical Error Codes |
| --- | --- | --- |
| `0` | Success | — |
| `2` | Bad CLI arguments / flag validation | `INVALID_ARGUMENTS`, `INVALID_DATE`, `INVALID_TIMEZONE`, `INVALID_LIMIT` |
| `3` | Source / database / schema access error | `SOURCE_NOT_FOUND`, `DB_NOT_FOUND`, `DB_OPEN_ERROR`, `SCHEMA_UNSUPPORTED` |
| `4` | Recording missing, deleted, or audio not local | `RECORDING_NOT_FOUND`, `RECORDING_DELETED`, `AUDIO_NOT_LOCAL` |
| `5` | Destination directory, conflict, or copy failure | `DESTINATION_CONFLICT`, `DESTINATION_ERROR`, `COPY_VERIFICATION_FAILED` |
