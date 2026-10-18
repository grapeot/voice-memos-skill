# Test & Verification Plan — Voice Memos Skill

> **Status:** Core CLI implemented; local tests, cross-platform CI, and read-only macOS metadata smoke verified
> **Test Runner:** `python -m pytest` (offline, zero network access)

The original 16-case core CLI suite passed locally and in Linux/macOS GitHub Actions CI. The native rename extension brings the local suite to 40 cases (six native tests skip on Linux), exercising both CLI entry points, synthetic SQLite/audio fixtures, mocked subprocess boundaries, and compiled-native validation. See `docs/working.md` for current release verification.

## 1. Testing Philosophy & Fixture Rules

### Native rename extension (2026-10-18)

`tests/test_rename.py` covers argument validation, live-source binding, deleted/missing targets, duplicate-title exact-ID selection, read-only dry-run dispatch, same-ID readback mismatch, subprocess argument transport, temporary binary cleanup, and structured compiler/runtime/timeout errors using mocked native calls. `tests/test_native.py` compiles the actual Catalyst helper on macOS and validates missing-store/no-create, invalid-title/no-open, invalid-mode handling, and Python/native Unicode title-validation parity (including combination emoji and zero-width format characters); these native checks skip on Linux.

An explicitly authorized live test renamed a short test recording through the private native model. Same-ID SQLite title readback, unchanged audio SHA-256, unchanged non-title metadata and other recording titles, and idempotent `unchanged` behavior passed. No private recording data is included in the repository. This test validates local persistence, not iCloud delivery to another device. Native model mutation itself is covered by this live test rather than the synthetic SQLite fixtures, which are not real Core Data stores.

1. **100% synthetic fixtures in automated tests:** All unit and integration tests create temporary directories and synthetic SQLite `CloudRecordings.db` databases on the fly using Python's `sqlite3` and `tmp_path`.
2. **Arbitrary byte payloads for audio fixtures:** Test `.m4a` and `.qta` files contain deterministic synthetic byte sequences sufficient to verify file existence, byte size, SHA-256 hashing, and copy integrity. Tests explicitly do **not** run `ffmpeg` or perform audio codec quality decoding.
3. **No real user data:** No personal `CloudRecordings.db` snapshots, real recording titles, UUIDs, or machine-specific paths are stored in the repository.

## 2. Automated Test Suite Coverage (`tests/`)

### 2.1 Deleted-But-Present Regression (`ZEVICTIONDATE` vs. Disk Residue)
- **Setup:** Insert a row in `ZCLOUDRECORDING` with `ZEVICTIONDATE` set to a non-null Core Data timestamp and create a readable `.m4a` file at its `ZPATH` inside `--source`. Also set `ZFLAGS = 0` (or arbitrary values) to verify `ZFLAGS` is ignored.
- **Verified Behavior:**
  - `get <id>` returns `state: "deleted"`, non-null `deleted_at`, and `audio.availability: "local"` with accurate `size_bytes`.
  - Default `list` (`--state active`) excludes the recording.
  - `list --state deleted` and `list --state all` include the recording.
  - `export <id> --output-dir <dir>` fails with exit code `4` (`RECORDING_DELETED`), emits a JSON error envelope on `stdout`, and writes nothing to `--output-dir`.

### 2.2 Active Recording with Missing Local File (`not_local`)
- **Setup:** Insert a row with `ZEVICTIONDATE IS NULL` whose `ZPATH` does not exist on disk.
- **Verified Behavior:**
  - `list` and `get <id>` return `state: "active"`, `audio.availability: "not_local"`, and `audio.size_bytes: null` (never labeling it `"cloud_only"`).
  - `export <id> --output-dir <dir>` fails with exit code `4` (`AUDIO_NOT_LOCAL`).

### 2.3 Arbitrary Filenames, Extensions & Path Containment
- **Setup:**
  - Row A: `ZPATH` is a non-UUID arbitrary filename (`memo_custom_name_01.m4a`).
  - Row B: `ZPATH` uses `.qta` extension (`quicktime_recording.qta`).
  - Row C: `ZPATH` attempts directory traversal (`../outside_secret.m4a`) or points to an absolute path outside `--source`.
- **Verified Behavior:**
  - Rows A and B resolve as `local` and `export` preserves `.m4a` and `.qta` extensions respectively using sanitized `<recording_id><ext>` filenames.
  - Row C is rejected by path containment (`audio.availability: "not_local"`, `audio.path: null`), and `export` fails with exit code `4` without reading outside `--source`.

### 2.4 Core Data Timestamps, Timezones & DST Boundaries
- **Setup:** Insert recordings around UTC midnight and local midnight across a Daylight Saving Time (DST) transition date in an IANA timezone (e.g., `America/Los_Angeles`), using Core Data timestamps (`unix_ts - 978307200`).
- **Verified Behavior:**
  - `created_at` and `deleted_at` are formatted in UTC ISO-8601 (`...Z`).
  - `list --date YYYY-MM-DD` (defaulting to `--timezone UTC`) filters by UTC calendar date.
  - `list --date YYYY-MM-DD --timezone America/Los_Angeles` accurately includes/excludes recordings based on local calendar date across both standard time and DST boundaries.

### 2.5 Deterministic Ordering & Same-Timestamp Tiebreaker
- **Setup:** Insert multiple recordings with different `ZDATE` values plus two recordings with identical `ZDATE` timestamps (within the same second/minute) and distinct `ZUNIQUEID` values (`ID-B`, `ID-A`).
- **Verified Behavior:** `list` orders newer `ZDATE` rows first and breaks ties deterministically by `ZUNIQUEID` (`ID-A` before `ID-B`). `--limit N` truncates the sorted result to `N` items.

### 2.6 CLI Subprocess Envelope & Exit Code Matrix
- **Setup:** Invoke both `voice-memos` and `python -m voice_memos_skill` via `subprocess.run` with root flags placed before the subcommand (`--source <dir> <subcommand> ...`).
- **Verified Behavior:**
  - Exit `0` on `doctor`, `list` (including empty list), `get`, and `export` (`exported`, `already_exists`, `planned`).
  - Exit `2` on invalid arguments (bad `--date`, unknown `--timezone`, `--limit 0`, missing required `--output-dir`).
  - Exit `3` on missing source directory, missing database file, or unsupported schema.
  - Exit `4` on unknown `recording_id`, `export` of a `deleted` recording, or `export` of a `not_local` recording.
  - Exit `5` on destination conflict or unwritable destination.
  - In all command invocations (except `--help` and `--version`), `stdout` parses as a single JSON object `{ "command": ..., "data": ..., "error": ... }` and diagnostics appear only on `stderr`.

### 2.7 `export --dry-run` Semantics
- **Setup:** Specify a non-existent `--output-dir` and pass `--dry-run`.
- **Verified Behavior:** Exits `0` with `status: "planned"`, `dry_run: true`, planned `destination_path`, and `source_size_bytes`. Confirms `--output-dir` is **not** created on disk and no file is written.

### 2.8 Repeat Export Idempotency & Conflict Detection
- **Setup:**
  1. Export an active local recording to `--output-dir`: returns `status: "exported"`, exit `0`.
  2. Re-run the exact same `export` command: returns `status: "already_exists"`, `verified: true`, exit `0`.
  3. Modify a single byte in the exported file at `destination_path` and re-run `export`: fails with exit code `5` (`DESTINATION_CONFLICT`) and leaves the modified destination file untouched.

### 2.9 Live SQLite WAL Visibility
- **Setup:** Open the synthetic `CloudRecordings.db` in `PRAGMA journal_mode=WAL`, insert a new active row, commit without checkpointing (leaving the new row in `CloudRecordings.db-wal` while keeping the writer connection open), and invoke `voice-memos --source <dir> list`.
- **Verified Behavior:** Because `voice-memos` connects via `mode=ro` (and does not set `immutable=1`), the uncheckpointed WAL row is visible in `list` and `doctor`.

### 2.10 Missing DB & Unsupported Schema Handling
- **Setup:**
  - Case A: Point `--db` to a non-existent file path inside an existing directory.
  - Case B: Create a SQLite DB missing `ZCLOUDRECORDING` or missing a required column such as `ZEVICTIONDATE`, even while `.m4a` files exist in `--source`.
- **Verified Behavior:** Both cases fail with exit code `3`. In Case A, SQLite does **not** create a `0`-byte file at `--db`. In Case B, the CLI does **not** fall back to listing `.m4a` files from the filesystem.

### 2.11 Source Directory Immutability
- **Setup:** Snapshot `CloudRecordings.db` and media files inside `--source` before and after running `doctor`, `list`, `get`, and `export`.
- **Verified Behavior:** Source application data (`CloudRecordings.db` and media files) is never mutated. Note that when SQLite opens a live WAL-mode database in read-only mode (`mode=ro`), OS/SQLite shared-memory bookkeeping in `CloudRecordings.db-shm` is managed by the OS/SQLite runtime; read-only verification therefore checks that `CloudRecordings.db` and media files remain unchanged rather than assuming a live `-shm` file hash never changes.

### 2.12 Targeted Error-Handling Regressions (Timezone, Source Access & SQLite Error Detail)
- **Setup & Verified Behavior (3 regression cases):**
  - **Invalid path-like `--timezone` arguments:** Passing a path-like invalid timezone value returns a structured JSON error envelope (exit code `2`) instead of an unhandled Python traceback.
  - **Unreadable audio source hash `OSError`:** When hashing an unreadable source audio file raises `OSError`, the CLI returns a structured JSON error envelope instead of an unhandled traceback.
  - **Underlying SQLite error detail preservation:** Database open and query errors preserve the underlying `sqlite3` error detail in the structured JSON error output.

## 3. Continuous Integration (GitHub Actions)

GitHub Actions is configured in `.github/workflows/` to run the offline `pytest` suite across:
- `ubuntu-latest` (Python 3.11+)
- `macos-latest` (Python 3.11+)

Both checks are configured as required status checks on `master`. During initial scaffold CI, `astral-sh/setup-uv` with `python-version` already created `.venv`, causing a subsequent explicit `uv venv` step to fail; the implementation workflow removes the duplicate environment creation and runs `pytest` unconditionally. Both platform checks (`ubuntu-latest` and `macos-latest`) passed for implementation commit `ba8c07a` (GitHub Actions runs `39047912407` and `39047920607`, 16 `pytest` cases each).

No network access or macOS TCC permissions are required in CI because all CI tests run against synthetic SQLite databases and temporary directories.

## 4. Local macOS Read-Only Smoke Verification

On a real macOS workstation with terminal Full Disk Access granted, read-only smoke verification was executed against the live Voice Memos container:

1. **Completed checks:**
   - `voice-memos doctor`, `voice-memos list`, and `voice-memos get <id>` succeeded against the live `CloudRecordings.db`.
   - Deleted recordings (`ZEVICTIONDATE IS NOT NULL`) that still had local audio files on disk were confirmed and properly rejected by `voice-memos export <id>` with exit code `4` (`RECORDING_DELETED`).
   - The main `CloudRecordings.db` SHA-256 digest was verified identical before and after the smoke commands (while recognizing that WAL `-shm` shared-memory bookkeeping on a live database may be OS-managed).
2. **Current verification boundary on live data:**
   - On the test workstation, active recordings had no downloaded local audio files (`audio.availability == "not_local"`), so exporting a live active recording's audio file has **not** been exercised against real container media.
   - Native `.m4a` / `.qta` byte copying and SHA-256 verification are validated via the synthetic test suite. The tool never performs or claims live audio codec decoding or audio quality verification.
3. **Privacy boundary:** No live recording counts, UUIDs, titles, dates, paths, or smoke logs are stored in repository documentation or test fixtures.
