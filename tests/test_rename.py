import json
import subprocess
from pathlib import Path

import pytest

from voice_memos_skill import rename
from voice_memos_skill.store import CliError, DEFAULT_SOURCE


@pytest.fixture
def live(monkeypatch):
    source = Path(DEFAULT_SOURCE).expanduser().resolve()
    rows = [{"id": "test-id", "title": "Test recording", "state": "active"}]
    monkeypatch.setattr(rename, "load_recordings", lambda *args: rows)
    monkeypatch.setattr(rename.sys, "platform", "darwin")
    return source, source / "CloudRecordings.db", rows


@pytest.mark.parametrize("title", ["", "  ", "a\nb", "a\x00b", "a\tb"])
def test_invalid_title_rejected_before_read(live, title, monkeypatch):
    source, db, _ = live
    monkeypatch.setattr(rename, "load_recordings", lambda *args: pytest.fail("No DB read"))
    with pytest.raises(CliError, match="non-blank"):
        rename.rename_recording(source, db, "test-id", title, False)


def test_alternate_source_or_db_never_targets_real_store(live, tmp_path, monkeypatch):
    source, _, _ = live
    monkeypatch.setattr(rename, "load_recordings", lambda *args: pytest.fail("No DB read"))
    for root, db in [(tmp_path, tmp_path / "CloudRecordings.db"), (source, tmp_path / "other.db")]:
        with pytest.raises(CliError) as error:
            rename.rename_recording(root, db, "test-id", "Title", True)
        assert error.value.code == "RENAME_SOURCE_UNSUPPORTED"


def test_deleted_and_missing_never_call_native(live, monkeypatch):
    source, db, rows = live
    monkeypatch.setattr(rename, "run_native_rename", lambda *args: pytest.fail("No native mutation"))
    rows[0]["state"] = "deleted"
    with pytest.raises(CliError) as error:
        rename.rename_recording(source, db, "test-id", "Title", False)
    assert error.value.code == "RECORDING_DELETED"
    with pytest.raises(CliError) as error:
        rename.rename_recording(source, db, "missing", "Title", False)
    assert error.value.code == "RECORDING_NOT_FOUND"


def test_native_rename_uses_id_and_readback(live, monkeypatch):
    source, db, rows = live
    rows.append({"id": "other-id", "title": "Test recording", "state": "active"})
    def change(path, identifier, new, dry_run):
        assert identifier == "test-id" and path == db and not dry_run
        rows[0]["title"] = new
        return {"status": "renamed", "verified": True}
    monkeypatch.setattr(rename, "run_native_rename", change)
    result = rename.rename_recording(source, db, "test-id", '会议 "主题" 🍎', False)
    assert result["verified"] and rows[1]["title"] == "Test recording"


def test_no_false_success_when_same_id_readback_differs(live, monkeypatch):
    source, db, _ = live
    monkeypatch.setattr(rename, "run_native_rename", lambda *args: {"status": "renamed"})
    with pytest.raises(CliError) as error:
        rename.rename_recording(source, db, "test-id", "New title", False)
    assert error.value.code == "RENAME_VERIFICATION_FAILED"


def test_dry_run_calls_readonly_native_mode(live, monkeypatch):
    source, db, rows = live
    def plan(path, identifier, title, dry_run):
        assert dry_run
        return {"status": "planned", "verified": False}
    monkeypatch.setattr(rename, "run_native_rename", plan)
    assert rename.rename_recording(source, db, "test-id", "Title", True)["status"] == "planned"
    assert rows[0]["title"] == "Test recording"


def test_non_macos_fails_before_native(live, monkeypatch):
    source, db, _ = live
    monkeypatch.setattr(rename.sys, "platform", "linux")
    with pytest.raises(CliError) as error:
        rename.rename_recording(source, db, "test-id", "Title", False)
    assert error.value.code == "RENAME_UNSUPPORTED"


def test_helper_compilation_argument_transport_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(rename.platform, "machine", lambda: "arm64")
    calls = []
    title = '新标题; "quoted" 🍎'
    def run(argv, **kwargs):
        calls.append(argv)
        assert "shell" not in kwargs
        if len(calls) == 1:
            assert argv[:3] == ["/usr/bin/xcrun", "clang", "-target"]
            assert "arm64-apple-ios15.0-macabi" in argv
            assert "rename_native.m" == Path(argv[-3]).name
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[1:] == [str(tmp_path / "db"), "test-id", title, "dry-run"]
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "recording_id": "test-id", "title": title, "old_title": "Old",
            "dry_run": True, "status": "planned", "verified": False,
        }), "")
    monkeypatch.setattr(rename.subprocess, "run", run)
    assert rename.run_native_rename(tmp_path / "db", "test-id", title, True)["status"] == "planned"
    assert not Path(calls[0][-1]).parent.exists()


@pytest.mark.parametrize("failure, expected", [
    ("build", "RENAME_BUILD_FAILED"), ("timeout", "RENAME_TIMEOUT"),
    ("missing-tools", "RENAME_NATIVE_ERROR"), ("bad-json", "RENAME_NATIVE_ERROR"),
    ("wrong-id", "RENAME_NATIVE_ERROR"), ("native-error", "RENAME_SAVE_FAILED"),
])
def test_helper_failures_stay_structured(tmp_path, monkeypatch, failure, expected):
    monkeypatch.setattr(rename.platform, "machine", lambda: "arm64")
    calls = 0
    def run(argv, **kwargs):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if failure == "missing-tools":
            raise FileNotFoundError("xcrun")
        if calls == 1:
            return subprocess.CompletedProcess(argv, 1 if failure == "build" else 0, "", "compiler detail")
        if failure == "bad-json":
            return subprocess.CompletedProcess(argv, -11, "", "crash")
        if failure == "native-error":
            return subprocess.CompletedProcess(argv, 6, json.dumps({"error": {"code": "RENAME_SAVE_FAILED", "message": "Save failed"}}), "")
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "recording_id": "wrong", "title": "Title", "dry_run": False,
            "status": "renamed", "verified": True,
        }), "")
    monkeypatch.setattr(rename.subprocess, "run", run)
    with pytest.raises(CliError) as error:
        rename.run_native_rename(tmp_path / "db", "test-id", "Title", False)
    assert error.value.code == expected
