# Working Status & Engineering Log — Voice Memos Skill

> **Current Phase:** Phase 1 — Documentation & Specification Scaffold Completed (Pre-Implementation)

## Roadmap & Checklist

- [x] **Phase 1: Documentation & Specification Scaffold**
  - [x] `README.md` (user-facing overview, installation, CLI reference, JSON contract, TCC permissions note)
  - [x] `AGENTS.md` (contributor workflow, `uv` environment, privacy rules, branch/PR governance)
  - [x] `docs/prd.md` (problem statement, goals, explicit non-goals, acceptance criteria)
  - [x] `docs/rfc.md` (SQLite `ZCLOUDRECORDING` authority, `mode=ro` WAL discipline, path containment, JSON & exit code contract)
  - [x] `docs/test.md` (synthetic SQLite/audio test matrix, CI on `ubuntu-latest` + `macos-latest`, optional local smoke check rules)
  - [x] `docs/working.md` (roadmap, changelog, lessons learned)
  - [x] `skills/voice-memos/SKILL.md` (concise root skill for AI coding agents)
- [ ] **Phase 2: Core CLI & Test Suite Implementation**
  - [ ] `pyproject.toml` and `src/voice_memos_skill/` package (`voice-memos` & `python -m voice_memos_skill`)
  - [ ] Read-only SQLite repository layer, path containment, and `doctor` / `list` / `get` / `export` commands
  - [ ] Synthetic `pytest` suite covering all contract cases in `docs/test.md`
- [ ] **Phase 3: CI & Public Release**
  - [ ] GitHub Actions workflow (`ubuntu-latest`, `macos-latest`)
  - [ ] Branch protection on `master` (PR required, 0 approvals, enforce admins)

## Changelog

### 2026-10-17 — Phase 1 Specification Scaffold
- Created initial documentation scaffold (`README.md`, `AGENTS.md`, `docs/prd.md`, `docs/rfc.md`, `docs/test.md`, `docs/working.md`, `skills/voice-memos/SKILL.md`) establishing the exact CLI, SQLite schema, JSON output, and test contracts prior to code implementation.

## Lessons Learned

1. **Deletion state lives in `ZEVICTIONDATE`, not on disk or in `ZFLAGS`:**
   In macOS `VoiceMemos.framework`, `RCQueryManager.deletedRecordingsPredicate` checks `evictionDate != nil` (`ZEVICTIONDATE IS NOT NULL` in `ZCLOUDRECORDING`), and `playableRecordingsExcludingDeletedPredicate` excludes rows with an eviction date. Deleted recordings frequently leave their `.m4a` / `.qta` files on disk inside `Recordings/` until macOS purges them. Any tool that scans the filesystem directly or inspects `ZFLAGS` will misclassify deleted recordings.
2. **Recording lifecycle (`state`) and local file presence (`audio.availability`) are orthogonal:**
   A deleted recording can still have `audio.availability = "local"`, and an active recording can have `audio.availability = "not_local"`. Reporting both fields independently prevents callers from conflating disk residue with active status, while `export` enforces both `state == "active"` and `audio.availability == "local"`.
3. **Use `mode=ro` (never `immutable=1` or single-file DB copies) for live SQLite reads:**
   `CloudRecordings.db` runs in WAL mode (`CloudRecordings.db-wal`). Opening with `file:...?mode=ro` prevents SQLite from creating missing database files while preserving visibility of uncheckpointed WAL frames. Using `immutable=1` or copying only `CloudRecordings.db` drops WAL state and can produce stale or corrupted reads.
4. **Missing local audio is `not_local`, not `cloud_only`:**
   When an active database row has no local file on disk, the local SQLite database alone cannot prove whether the file is stored in iCloud or missing for another reason. Labeling availability as `not_local` avoids overclaiming.
