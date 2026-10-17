# AGENTS.md — Contributor & Agent Guide

## Project Overview

`voice-memos-skill` (public GitHub repository: `grapeot/voice-memos-skill`) is a small, standalone MIT-licensed Python CLI (`voice-memos` / `python -m voice_memos_skill`) and AI agent skill (`skills/voice-memos/SKILL.md`) for inspecting and exporting local macOS Apple Voice Memos.

> **Current Status:** Core CLI (`src/voice_memos_skill/{__init__,__main__,cli,store}.py`) and offline synthetic `pytest` suite (`tests/`) are implemented and verified locally on a feature branch. `master` is protected (pull request required, `0` approvals, `enforce_admins=true`, and Linux/macOS CI checks required); remote CI execution and PR merge for the implementation branch are pending.

## Core Scope Discipline

Keep the project minimal and focused on its exact contract:
- **Runtime:** Python 3.11+ using the Python standard library only (`sqlite3`, `pathlib`, `hashlib`, `shutil`, `zoneinfo`, `datetime`, `argparse`, `json`). `pytest` is the only development test dependency.
- **Entry points:** `voice-memos` and `python -m voice_memos_skill`.
- **Root options (must appear before the subcommand):** `--source DIR`, `--db PATH`, `--version`.
- **Subcommands (exact set):** `doctor`, `list`, `get`, `export`.
- **Non-goals:** Do not add deleted-recording export opt-in flags, background daemons, iCloud download triggers, audio transcoding (`ffmpeg`), speech-to-text/ASR, batch orchestration, manifest frameworks, or integration with external pipelines.

## Repository Layout

```text
.
├── README.md                  # User-facing overview and CLI quickstart
├── AGENTS.md                  # Contributor rules, dev workflow, and privacy hygiene
├── pyproject.toml             # Package metadata and entry point configuration
├── .github/
│   └── workflows/             # Cross-platform CI workflow (ubuntu-latest, macos-latest)
├── docs/
│   ├── prd.md                 # Product requirements and scope boundaries
│   ├── rfc.md                 # Technical design, SQLite schema, JSON & exit code contract
│   ├── test.md                # Test plan & verification scope (synthetic fixtures, CI, smoke check)
│   └── working.md             # Task status, Changelog, and Lessons Learned
├── skills/
│   └── voice-memos/
│       └── SKILL.md           # Single root skill for AI coding agents
├── src/
│   └── voice_memos_skill/     # Core CLI implementation (__init__.py, __main__.py, cli.py, store.py)
└── tests/                     # Offline synthetic pytest suite
```

## Python Environment & Commands

Always use `uv` with a local `.venv` in the repository root:

```bash
uv venv
source .venv/bin/activate
uv pip install -e '.[dev]'
python -m pytest
```

Do not use bare `pip install` or global Python environments.

## Contributor Privacy & Data Hygiene Rules

This repository is public (`grapeot/voice-memos-skill`). Every contributor and agent must enforce these rules:

1. **English only:** All code, comments, documentation, commit messages, and test fixtures must be written in English.
2. **No real user data or audio fixtures:** Never copy real `CloudRecordings.db` files, `.m4a` / `.qta` voice recordings, recording titles, UUIDs, or timestamps from a real user machine into the repository or test fixtures. All automated tests must build synthetic SQLite databases and temporary byte files at test runtime.
3. **No machine-specific paths:** Never commit literal home directory paths (such as `/Users/<real-username>/...`) or hostnames. Use `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings` or generic placeholders such as `/Users/example/...` and `/path/to/Recordings`.
4. **Local smoke outputs stay ignored:** If an optional read-only smoke test is run against a live macOS Voice Memos container, any captured JSON output, hashes, or scratch copies must remain in gitignored local scratch directories outside version control.

## Working Doc & Version Control Governance

- **Update `docs/working.md`:** Whenever meaningful design updates, implementation milestones, or bug lessons occur, update `docs/working.md` (including its `Changelog` and `Lessons Learned` sections).
- **Branch & PR policy:** The public MIT repository uses `master` as the default branch with branch protection enabled (pull request required, `0` required approval reviewers, `enforce_admins` enabled). Subsequent implementation work goes through a branch and pull request.
- **Explicit authorization required for git/remote actions:** Do not initialize git repositories, create commits, push branches, open pull requests, or modify remote settings unless the user explicitly authorizes that action in the current task.

## Agent Skill Registration

When a user gives this repository URL (`https://github.com/grapeot/voice-memos-skill`) to an AI coding agent (Codex, Claude Code, Cursor, OpenCode) to install:
1. Inspect the target workspace's `AGENTS.md` / `CLAUDE.md` and skill routing index first.
2. Set up `.venv` via `uv` and install the package (`uv pip install -e '.[dev]'`).
3. Register **only** the single root skill [`skills/voice-memos/SKILL.md`](skills/voice-memos/SKILL.md) in the target workspace's skill directory or routing table.
