from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from voice_memos_skill.store import DEFAULT_SOURCE, CliError, find_recording, load_recordings


def validate_title(title: str) -> str:
    if not title.strip() or any(unicodedata.category(char) == "Cc" for char in title):
        raise CliError(2, "INVALID_TITLE", "Title must be non-blank and contain no control characters.")
    return title


def rename_recording(
    source: Path, db_path: Path, recording_id: str, title: str, dry_run: bool,
) -> dict[str, Any]:
    title = validate_title(title)
    if source != Path(DEFAULT_SOURCE).expanduser().resolve() or db_path != source / "CloudRecordings.db":
        raise CliError(2, "RENAME_SOURCE_UNSUPPORTED", "Rename requires the default live Voice Memos source and database.")
    if sys.platform != "darwin":
        raise CliError(6, "RENAME_UNSUPPORTED", "Rename requires macOS and Voice Memos.")
    recording = find_recording(load_recordings(source, db_path), recording_id)
    if recording["state"] != "active":
        raise CliError(4, "RECORDING_DELETED", "Deleted recordings cannot be renamed.")
    result = run_native_rename(db_path, recording_id, title, dry_run)
    if not dry_run:
        updated = find_recording(load_recordings(source, db_path), recording_id)
        if updated["state"] != "active" or updated["title"] != title:
            raise CliError(6, "RENAME_VERIFICATION_FAILED", "The requested title was not read back for the same recording ID. Inspect Voice Memos before retrying.")
    return result


def run_native_rename(db_path: Path, recording_id: str, title: str, dry_run: bool) -> dict[str, Any]:
    arch = platform.machine()
    if arch not in ("arm64", "x86_64"):
        raise CliError(6, "RENAME_UNSUPPORTED", f"Unsupported macOS architecture: {arch}")
    native_source = Path(__file__).with_name("rename_native.m")
    try:
        # Compiling into a private temporary directory avoids a writable shared
        # executable cache. No third-party Objective-C bridge dependency needed.
        with tempfile.TemporaryDirectory(prefix="voice-memos-rename-") as directory:
            helper = Path(directory) / "rename-native"
            compiled = subprocess.run(
                ["/usr/bin/xcrun", "clang", "-target", f"{arch}-apple-ios15.0-macabi",
                 "-fobjc-arc", "-framework", "Foundation", "-framework", "CoreData",
                 str(native_source), "-o", str(helper)],
                capture_output=True, text=True, timeout=60, check=False,
            )
            if compiled.returncode:
                raise CliError(6, "RENAME_BUILD_FAILED", "Native helper compilation failed. Install Xcode Command Line Tools. " + compiled.stderr.strip())
            executed = subprocess.run(
                [str(helper), str(db_path), recording_id, title, "dry-run" if dry_run else "rename"],
                capture_output=True, text=True, timeout=20, check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise CliError(6, "RENAME_TIMEOUT", "Native rename timed out; inspect the recording before retrying.") from exc
    except OSError as exc:
        raise CliError(6, "RENAME_NATIVE_ERROR", f"Unable to run native rename: {exc}") from exc
    try:
        payload = json.loads(executed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("Expected an object")
        if executed.returncode:
            raise CliError(6, str(payload["error"]["code"]), str(payload["error"]["message"]))
        expected_status = ("planned", "unchanged") if dry_run else ("renamed", "unchanged")
        if (payload["recording_id"] != recording_id or payload["title"] != title
                or payload["dry_run"] is not dry_run or payload["status"] not in expected_status
                or payload["verified"] is not (payload["status"] != "planned")):
            raise ValueError("Native result did not match the request")
        return payload
    except (ValueError, KeyError, TypeError) as exc:
        raise CliError(6, "RENAME_NATIVE_ERROR", "Native helper returned an invalid result. " + executed.stderr.strip()) from exc
