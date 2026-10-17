# Working Status & Engineering Log — Voice Memos Skill

> **Current Status:** Phase 2 Core CLI & Local Verification Completed — Pending Remote Implementation CI & Pull Request Merge

## Roadmap & Checklist

- [x] **Phase 1: Documentation & Specification Scaffold**
  - [x] `README.md` (user-facing overview, installation, CLI reference, JSON contract, TCC permissions note)
  - [x] `AGENTS.md` (contributor workflow, `uv` environment, privacy rules, branch/PR governance)
  - [x] `docs/prd.md` (problem statement, goals, explicit non-goals, acceptance criteria)
  - [x] `docs/rfc.md` (SQLite `ZCLOUDRECORDING` authority, `mode=ro` WAL discipline, path containment, JSON & exit code contract)
  - [x] `docs/test.md` (synthetic SQLite/audio test matrix, CI on `ubuntu-latest` + `macos-latest`, local smoke check rules)
  - [x] `docs/working.md` (roadmap, changelog, lessons learned)
  - [x] `skills/voice-memos/SKILL.md` (concise root skill for AI coding agents)
- [x] **Phase 2: Core CLI & Test Suite Implementation**
  - [x] `pyproject.toml` and `src/voice_memos_skill/` package (`__init__.py`, `__main__.py`, `cli.py`, `store.py`; entry points `voice-memos` & `python -m voice_memos_skill`)
  - [x] Read-only SQLite repository layer, path containment, and `doctor` / `list` / `get` / `export` commands
  - [x] Offline synthetic `pytest` suite covering all contract cases in `docs/test.md` (passing locally; targeted JSON error-handling test review in progress)
  - [x] Read-only macOS live container smoke verification (`doctor`, `list`, `get`, deleted-with-local-file `export` rejection with exit code `4`, and unchanged main `CloudRecordings.db` SHA-256 hash; live active-audio export not validated because active rows on the machine were `not_local`)
- [ ] **Phase 3: CI & Public Release**
  - [x] Public GitHub repository (`grapeot/voice-memos-skill`) created and `master` branch protection configured (PR required, `0` approvals, `enforce_admins=true`, and Linux/macOS status checks required)
  - [x] GitHub Actions workflow configured for `ubuntu-latest` and `macos-latest` (removed duplicate `uv venv` step and enabled unconditional `pytest` execution)
  - [ ] Commit/push local feature branch, run remote cross-platform implementation CI, and merge pull request to `master`

## Changelog

### 2026-10-17 — Phase 2 Core CLI Implementation, Local Verification & CI Setup Fix
- Implemented the core CLI package (`src/voice_memos_skill/cli.py`, `store.py`, `__main__.py`, `__init__.py`) and installed it via `uv` (`voice-memos` and `python -m voice_memos_skill`).
- Implemented and passed the offline synthetic `pytest` suite covering deleted+local regression, active+`not_local`, arbitrary filenames and path containment, UTC/local/DST date boundaries, same-timestamp deterministic tiebreaker ordering, `--dry-run` without directory creation, native `.m4a`/`.qta` byte copy and SHA-256 verification, repeat export idempotency and conflict detection, live SQLite WAL visibility, missing DB/schema errors, and source immutability (additional targeted JSON error-handling test review in progress).
- Completed read-only macOS smoke verification against the live Voice Memos container: `doctor`, `list`, and `get` succeeded, deleted recordings with leftover local audio files on disk were rejected by `export` with exit code `4`, and the main `CloudRecordings.db` SHA-256 hash remained unchanged. Active recordings on the test machine had no local audio files (`not_local`), so live active-audio export was not validated on real data (byte-accurate export is validated via synthetic fixtures).
- Configured public GitHub repository `grapeot/voice-memos-skill` with `master` branch protection (`0` approvals, `enforce_admins=true`, required `ubuntu-latest` and `macos-latest` checks). Fixed the GitHub Actions workflow after scaffold CI failed because `astral-sh/setup-uv` with `python-version` already created `.venv` before a second `uv venv` call; removed duplicate environment creation and configured `pytest` to run unconditionally. Remote CI run and PR merge for the feature branch remain pending.
- Updated repository documentation (`README.md`, `AGENTS.md`, `docs/prd.md`, `docs/rfc.md`, `docs/test.md`, `docs/working.md`, `skills/voice-memos/SKILL.md`) to reflect the implemented CLI and exact verification scope.

### 2026-10-17 — Phase 1 Specification Scaffold
- Created initial documentation scaffold (`README.md`, `AGENTS.md`, `docs/prd.md`, `docs/rfc.md`, `docs/test.md`, `docs/working.md`, `skills/voice-memos/SKILL.md`) establishing the exact CLI, SQLite schema, JSON output, and test contracts prior to code implementation.

## Lessons Learned

1. **Deletion state lives in `ZEVICTIONDATE`, not on disk or in `ZFLAGS`:**
   In macOS `VoiceMemos.framework`, `RCQueryManager.deletedRecordingsPredicate` checks `evictionDate != nil` (`ZEVICTIONDATE IS NOT NULL` in `ZCLOUDRECORDING`), and `playableRecordingsExcludingDeletedPredicate` excludes rows with an eviction date. Deleted recordings frequently leave their `.m4a` / `.qta` files on disk inside `Recordings/` until macOS purges them. Any tool that scans the filesystem directly or inspects `ZFLAGS` will misclassify deleted recordings.
2. **Recording lifecycle (`state`) and local file presence (`audio.availability`) are orthogonal:**
   A deleted recording can still have `audio.availability = "local"`, and an active recording can have `audio.availability = "not_local"`. Reporting both fields independently prevents callers from conflating disk residue with active status, while `export` enforces both `state == "active"` and `audio.availability == "local"`.
3. **Use `mode=ro` (never `immutable=1` or single-file DB copies) for live SQLite reads, and account for `-shm` bookkeeping:**
   `CloudRecordings.db` runs in WAL mode (`CloudRecordings.db-wal`). Opening with `file:...?mode=ro` prevents SQLite from creating missing database files while preserving visibility of uncheckpointed WAL frames. Using `immutable=1` or copying only `CloudRecordings.db` drops WAL state and can produce stale or corrupted reads. When verifying read-only behavior against a live WAL database, check that application data (`CloudRecordings.db` and media files) is unmutated rather than assuming OS/SQLite shared-memory bookkeeping in `CloudRecordings.db-shm` never changes.
4. **Missing local audio is `not_local`, not `cloud_only`:**
   When an active database row has no local file on disk, the local SQLite database alone cannot prove whether the file is stored in iCloud or missing for another reason. Labeling availability as `not_local` avoids overclaiming.
5. **`astral-sh/setup-uv` with `python-version` already creates `.venv` in GitHub Actions:**
   Running an explicit `uv venv` step after `setup-uv` (when configured with `python-version`) fails in CI because the virtual environment already exists; `uv pip install` and `pytest` should use the environment directly.
