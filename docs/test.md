# Test & Verification Plan — Voice Memos Skill

> **Status:** Phase 1 Verification Contract (Pre-Implementation Specification)
> **Test Runner:** `python -m pytest` (offline, zero network access)

*Note: Because this repository is currently at Phase 1 (specification scaffold prior to Phase 2 code implementation), this document defines the required test suite contract rather than reporting completed test execution counts.*

## 1. Testing Philosophy & Fixture Rules

1. **100% synthetic fixtures in automated tests:** All unit and integration tests create temporary directories and synthetic SQLite `CloudRecordings.db` databases on the fly using Python's `sqlite3` and `tmp_path`.
2. **Arbitrary byte payloads for audio fixtures:** Test `.m4a` and `.qta` files contain deterministic synthetic byte sequences sufficient to verify file existence, byte size, SHA-256 hashing, and copy integrity. Tests explicitly do **not** run `ffmpeg` or perform audio codec quality decoding.
3. **No real user data:** No personal `CloudRecordings.db` snapshots, real recording titles, UUIDs, or machine-specific paths are stored in the repository.

## 2. Required Automated Test Cases

### 2.1 Deleted-But-Present Regression (`ZEVICTIONDATE` vs. Disk Residue)
- **Setup:** Insert a row in `ZCLOUDRECORDING` with `ZEVICTIONDATE` set to a non-null Core Data timestamp and create a readable `.m4a` file at its `ZPATH` inside `--source`. Also set `ZFLAGS = 0` (or arbitrary values) to verify `ZFLAGS` is ignored.
- **Expected:**
  - `get <id>` returns `state: "deleted"`, non-null `deleted_at`, and `audio.availability: "local"` with accurate `size_bytes`.
  - Default `list` (`--state active`) excludes the recording.
  - `list --state deleted` and `list --state all` include the recording.
  - `export <id> --output-dir <dir>` fails with exit code `4` (`RECORDING_DELETED`), emits a JSON error envelope on `stdout`, and writes nothing to `--output-dir`.

### 2.2 Active Recording with Missing Local File (`not_local`)
- **Setup:** Insert a row with `ZEVICTIONDATE IS NULL` whose `ZPATH` does not exist on disk.
- **Expected:**
  - `list` and `get <id>` return `state: "active"`, `audio.availability: "not_local"`, and `audio.size_bytes: null` (never labeling it `"cloud_only"`).
  - `export <id> --output-dir <dir>` fails with exit code `4` (`AUDIO_NOT_LOCAL`).

### 2.3 Arbitrary Filenames, Extensions & Path Containment
- **Setup:**
  - Row A: `ZPATH` is a non-UUID arbitrary filename (`memo_custom_name_01.m4a`).
  - Row B: `ZPATH` uses `.qta` extension (`quicktime_recording.qta`).
  - Row C: `ZPATH` attempts directory traversal (`../outside_secret.m4a`) or points to an absolute path outside `--source`.
- **Expected:**
  - Rows A and B resolve as `local` and `export` preserves `.m4a` and `.qta` extensions respectively using sanitized `<recording_id><ext>` filenames.
  - Row C is rejected by path containment (`audio.availability: "not_local"`, `audio.path: null`), and `export` fails with exit code `4` without reading outside `--source`.

### 2.4 Core Data Timestamps, Timezones & DST Boundaries
- **Setup:** Insert recordings around UTC midnight and local midnight across a Daylight Saving Time (DST) transition date in an IANA timezone (e.g., `America/Los_Angeles`), using Core Data timestamps (`unix_ts - 978307200`).
- **Expected:**
  - `created_at` and `deleted_at` are formatted in UTC ISO-8601 (`...Z`).
  - `list --date YYYY-MM-DD` (defaulting to `--timezone UTC`) filters by UTC calendar date.
  - `list --date YYYY-MM-DD --timezone America/Los_Angeles` accurately includes/excludes recordings based on local calendar date across both standard time and DST boundaries.

### 2.5 Deterministic Ordering & Same-Timestamp Tiebreaker
- **Setup:** Insert multiple recordings with different `ZDATE` values plus two recordings with identical `ZDATE` timestamps (within the same second/minute) and distinct `ZUNIQUEID` values (`ID-B`, `ID-A`).
- **Expected:** `list` orders newer `ZDATE` rows first and breaks ties deterministically by `ZUNIQUEID` (`ID-A` before `ID-B`). `--limit N` truncates the sorted result to `N` items.

### 2.6 CLI Subprocess Envelope & Exit Code Matrix
- **Setup:** Invoke both `voice-memos` and `python -m voice_memos_skill` via `subprocess.run` with root flags placed before the subcommand (`--source <dir> <subcommand> ...`).
- **Expected:**
  - Exit `0` on `doctor`, `list` (including empty list), `get`, and `export` (`exported`, `already_exists`, `planned`).
  - Exit `2` on invalid arguments (bad `--date`, unknown `--timezone`, `--limit 0`, missing required `--output-dir`).
  - Exit `3` on missing source directory, missing database file, or unsupported schema.
  - Exit `4` on unknown `recording_id`, `export` of a `deleted` recording, or `export` of a `not_local` recording.
  - Exit `5` on destination conflict or unwritable destination.
  - In all cases, `stdout` parses as a single JSON object `{ "command": ..., "data": ..., "error": ... }` and diagnostics appear only on `stderr`.

### 2.7 `export --dry-run` Semantics
- **Setup:** Specify a non-existent `--output-dir` and pass `--dry-run`.
- **Expected:** Exits `0` with `status: "planned"`, `dry_run: true`, planned `destination_path`, and `source_size_bytes`. Confirm `--output-dir` is **not** created on disk and no file is written.

### 2.8 Repeat Export Idempotency & Conflict Detection
- **Setup:**
  1. Export an active local recording to `--output-dir`: returns `status: "exported"`, exit `0`.
  2. Re-run the exact same `export` command: returns `status: "already_exists"`, `verified: true`, exit `0`.
  3. Modify a single byte in the exported file at `destination_path` and re-run `export`: fails with exit code `5` (`DESTINATION_CONFLICT`) and leaves the modified destination file untouched.

### 2.9 Live SQLite WAL Visibility
- **Setup:** Open the synthetic `CloudRecordings.db` in `PRAGMA journal_mode=WAL`, insert a new active row, commit without checkpointing (leaving the new row in `CloudRecordings.db-wal` while keeping the writer connection open), and invoke `voice-memos --source <dir> list`.
- **Expected:** Because `voice-memos` connects via `mode=ro` (and does not set `immutable=1`), the uncheckpointed WAL row is visible in `list` and `doctor`.

### 2.10 Missing DB & Unsupported Schema Handling
- **Setup:**
  - Case A: Point `--db` to a non-existent file path inside an existing directory.
  - Case B: Create a SQLite DB missing `ZCLOUDRECORDING` or missing a required column such as `ZEVICTIONDATE`, even while `.m4a` files exist in `--source`.
- **Expected:** Both cases fail with exit code `3`. In Case A, verify SQLite did **not** create a `0`-byte file at `--db`. In Case B, verify the CLI does **not** fall back to listing `.m4a` files from the filesystem.

### 2.11 Source Directory Immutability
- **Setup:** Snapshot the file list, byte sizes, and SHA-256 digests of all files inside `--source` before and after running `doctor`, `list`, `get`, and `export`.
- **Expected:** Every file in `--source` is byte-for-byte identical before and after execution.

## 3. Continuous Integration (GitHub Actions)

GitHub Actions runs the offline `pytest` suite automatically on every push and pull request across:
- `ubuntu-latest` (Python 3.11+)
- `macos-latest` (Python 3.11+)

No network access or macOS TCC permissions are required in CI because all CI tests run against synthetic SQLite databases and temporary directories.

## 4. Optional Local macOS Smoke Verification

On a real macOS workstation with terminal Full Disk Access granted, maintainers can optionally perform a read-only smoke test against the live Voice Memos container or a local scratch copy:

1. Record SHA-256 hashes of `CloudRecordings.db` (and `-wal` if present) and a sample media file before running commands.
2. Run read-only commands (`voice-memos doctor`, `voice-memos list --limit 5`, `voice-memos list --state deleted --limit 5`, `voice-memos get <id>`) and `voice-memos export <id> --output-dir <scratch_dir>`.
3. Re-compute the SHA-256 hashes of the source database and media files to verify the source container was not modified.
4. **Privacy boundary:** Any live recording IDs, titles, timestamps, or smoke logs must stay in gitignored local scratch files and must never be committed to documentation or test fixtures.
