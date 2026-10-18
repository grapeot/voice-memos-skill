import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

OFFSET = 978307200
LA = ZoneInfo("America/Los_Angeles")
SCHEMA = """
CREATE TABLE ZCLOUDRECORDING (
    Z_PK INTEGER PRIMARY KEY,
    ZUNIQUEID TEXT,
    ZPATH TEXT,
    ZDATE REAL,
    ZDURATION REAL,
    ZEVICTIONDATE REAL,
    ZCUSTOMLABEL TEXT,
    ZENCRYPTEDTITLE,
    ZFLAGS INTEGER
);
"""


def zdate(moment: datetime) -> float:
    return moment.timestamp() - OFFSET


def write_db(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(SCHEMA)
    connection.executemany(
        """
        INSERT INTO ZCLOUDRECORDING (
            ZUNIQUEID, ZPATH, ZDATE, ZDURATION, ZEVICTIONDATE,
            ZCUSTOMLABEL, ZENCRYPTEDTITLE, ZFLAGS
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    connection.close()


def row(
    recording_id,
    path,
    created,
    duration=42.5,
    eviction=None,
    label=None,
    encrypted=None,
    flags=0,
):
    return (recording_id, path, created, duration, eviction, label, encrypted, flags)


def run(args: list[str], entry: str = "module") -> subprocess.CompletedProcess[str]:
    if entry == "script":
        command = [str(Path(sys.executable).parent / "voice-memos"), *args]
    else:
        command = [sys.executable, "-m", "voice_memos_skill", *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def body(proc: subprocess.CompletedProcess[str]) -> dict:
    parsed = json.loads(proc.stdout)
    assert set(parsed) == {"command", "data", "error"}
    return parsed


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, bytes]:
    found = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            found[str(path.relative_to(root))] = path.read_bytes()
    return found


def test_version_is_plain_text():
    for entry in ("module", "script"):
        proc = run(["--version"], entry)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "voice-memos 0.1.0"
        assert proc.stderr == ""


@pytest.mark.parametrize("entry", ["module", "script"])
def test_subprocess_envelope_and_root_flag_placement(tmp_path, entry):
    source = tmp_path / "Recordings"
    created = zdate(datetime(2026, 10, 17, 14, 22, 10, tzinfo=timezone.utc))
    write_db(source / "CloudRecordings.db", [row("active-1", "note.m4a", created, label="Note")])
    (source / "note.m4a").write_bytes(b"active-bytes")
    ok = run(["--source", str(source), "--db", str(source / "CloudRecordings.db"), "doctor"], entry)
    assert ok.returncode == 0
    assert ok.stderr == ""
    parsed = body(ok)
    assert parsed["command"] == "doctor"
    assert parsed["error"] is None
    assert parsed["data"]["counts"] == {"total": 1, "active": 1, "deleted": 0}
    bad_date = run(["--source", str(source), "list", "--date", "2026/10/17"], entry)
    assert bad_date.returncode == 2
    assert body(bad_date)["error"]["code"] == "INVALID_DATE"
    assert bad_date.stderr
    assert "Traceback" not in bad_date.stdout
    late_flag = run(["list", "--source", str(source)], entry)
    assert late_flag.returncode == 2
    assert body(late_flag)["command"] == "cli"
    late_db = run(["--source", str(source), "doctor", "--db", str(source / "CloudRecordings.db")], entry)
    assert late_db.returncode == 2


def test_deleted_but_present_is_not_exportable(tmp_path):
    source = tmp_path / "Recordings"
    created = zdate(datetime(2026, 10, 17, 14, 22, 10, tzinfo=timezone.utc))
    eviction = zdate(datetime(2026, 10, 18, 1, 2, 3, tzinfo=timezone.utc))
    payload = b"deleted-but-present"
    write_db(
        source / "CloudRecordings.db",
        [
            row("deleted-local", "leftover.m4a", created, eviction=eviction, flags=0, encrypted=b"\x01cipher"),
            row("active-local", "kept.m4a", created - 10, label="Kept", flags=7),
        ],
    )
    (source / "leftover.m4a").write_bytes(payload)
    (source / "kept.m4a").write_bytes(b"kept")
    before = snapshot(source)
    inspected = body(run(["--source", str(source), "get", "deleted-local"]))
    recording = inspected["data"]["recording"]
    assert recording["state"] == "deleted"
    assert recording["deleted_at"] == "2026-10-18T01:02:03Z"
    assert recording["created_at"] == "2026-10-17T14:22:10Z"
    assert recording["title"] is None
    assert recording["audio"] == {
        "availability": "local",
        "path": str((source / "leftover.m4a").resolve()),
        "size_bytes": len(payload),
    }
    assert "cloud_only" not in json.dumps(inspected)
    default_list = body(run(["--source", str(source), "list"]))
    assert [item["id"] for item in default_list["data"]["recordings"]] == ["active-local"]
    deleted_list = body(run(["--source", str(source), "list", "--state", "deleted"]))
    assert [item["id"] for item in deleted_list["data"]["recordings"]] == ["deleted-local"]
    all_list = body(run(["--source", str(source), "list", "--state", "all"]))
    assert {item["id"] for item in all_list["data"]["recordings"]} == {"deleted-local", "active-local"}
    output = tmp_path / "should-not-exist"
    exported = run(["--source", str(source), "export", "deleted-local", "--output-dir", str(output)])
    assert exported.returncode == 4
    parsed = body(exported)
    assert parsed["command"] == "export"
    assert parsed["data"] is None
    assert parsed["error"]["code"] == "RECORDING_DELETED"
    assert not output.exists()
    assert snapshot(source) == before


def test_active_missing_file_is_not_local(tmp_path):
    source = tmp_path / "Recordings"
    created = zdate(datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc))
    write_db(source / "CloudRecordings.db", [row("active-missing", "absent.m4a", created, flags=3)])
    inspected = body(run(["--source", str(source), "get", "active-missing"]))
    audio = inspected["data"]["recording"]["audio"]
    assert inspected["data"]["recording"]["state"] == "active"
    assert audio["availability"] == "not_local"
    assert audio["size_bytes"] is None
    assert audio["path"] == str((source / "absent.m4a").resolve())
    assert "cloud_only" not in json.dumps(inspected)
    output = tmp_path / "exports"
    exported = run(["--source", str(source), "export", "active-missing", "--output-dir", str(output)])
    assert exported.returncode == 4
    assert body(exported)["error"]["code"] == "AUDIO_NOT_LOCAL"
    assert not output.exists()


def test_arbitrary_names_extensions_and_containment(tmp_path):
    source = tmp_path / "Recordings"
    outside = tmp_path / "outside_secret.m4a"
    outside.write_bytes(b"outside-secret")
    created = zdate(datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc))
    nested = source / "nested" / "memo_custom_name_01.m4a"
    qta = source / "quicktime_recording.qta"
    inside = source / "inside.m4a"
    link = source / "linked.m4a"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"custom-m4a")
    qta.write_bytes(b"qta-bytes")
    inside.write_bytes(b"inside-bytes")
    link.symlink_to(outside)
    write_db(
        source / "CloudRecordings.db",
        [
            row("memo-a", "nested/memo_custom_name_01.m4a", created),
            row("memo-b", "quicktime_recording.qta", created - 1),
            row("weird id", "inside.m4a", created - 2),
            row(".hidden", str(inside.resolve()), created - 3),
            row("escape-rel", "../outside_secret.m4a", created - 4),
            row("escape-abs", str(outside.resolve()), created - 5),
            row("escape-link", "linked.m4a", created - 6),
        ],
    )
    listed = body(run(["--source", str(source), "list", "--state", "all", "--limit", "10"]))
    by_id = {item["id"]: item for item in listed["data"]["recordings"]}
    assert by_id["memo-a"]["audio"]["availability"] == "local"
    assert by_id["memo-b"]["audio"]["availability"] == "local"
    assert by_id["weird id"]["audio"]["path"] == str(inside.resolve())
    for escaped in ("escape-rel", "escape-abs", "escape-link"):
        assert by_id[escaped]["audio"] == {"availability": "not_local", "path": None, "size_bytes": None}
    output = tmp_path / "exports"
    for recording_id, filename in (
        ("memo-a", "memo-a.m4a"),
        ("memo-b", "memo-b.qta"),
        ("weird id", "weird_id.m4a"),
        (".hidden", "hidden.m4a"),
    ):
        proc = run(["--source", str(source), "export", recording_id, "--output-dir", str(output)])
        assert proc.returncode == 0, proc.stdout
        data = body(proc)["data"]
        assert Path(data["destination_path"]).name == filename
        assert Path(data["destination_path"]).read_bytes() == Path(data["source_path"]).read_bytes()
    blocked = tmp_path / "blocked-export"
    for recording_id in ("escape-rel", "escape-abs", "escape-link"):
        proc = run(["--source", str(source), "export", recording_id, "--output-dir", str(blocked)])
        assert proc.returncode == 4
        assert body(proc)["error"]["code"] == "AUDIO_NOT_LOCAL"
        assert outside.read_bytes() == b"outside-secret"
        assert not blocked.exists()
    assert "outside_secret" not in run(["--source", str(source), "get", "escape-abs"]).stdout


def test_utc_local_and_dst_dates(tmp_path):
    source = tmp_path / "Recordings"
    moments = {
        "edge": datetime(2026, 10, 17, 6, 30, tzinfo=timezone.utc),
        "spring-before": datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc),
        "spring-after": datetime(2026, 3, 8, 10, 30, tzinfo=timezone.utc),
        "spring-prev-local": datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc),
        "fall-prev-local": datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc),
        "fall-pdt": datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc),
        "fall-pst": datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc),
    }
    assert moments["edge"].astimezone(LA).date().isoformat() == "2026-10-16"
    assert moments["spring-prev-local"].astimezone(LA).date().isoformat() == "2026-03-07"
    assert moments["spring-before"].astimezone(LA).date().isoformat() == "2026-03-08"
    assert moments["spring-after"].astimezone(LA).date().isoformat() == "2026-03-08"
    assert moments["fall-prev-local"].astimezone(LA).date().isoformat() == "2026-10-31"
    assert moments["fall-pdt"].astimezone(LA).date().isoformat() == "2026-11-01"
    assert moments["fall-pst"].astimezone(LA).date().isoformat() == "2026-11-01"
    rows = [row(recording_id, f"{recording_id}.m4a", zdate(moment)) for recording_id, moment in moments.items()]
    fractional = zdate(datetime(2026, 10, 17, 14, 22, 10, tzinfo=timezone.utc)) + 0.5
    deleted = zdate(datetime(2026, 10, 18, 1, 2, 3, tzinfo=timezone.utc))
    rows.append(row("fractional", "fractional.m4a", fractional))
    rows.append(row("deleted-stamp", "deleted.m4a", zdate(moments["edge"]), eviction=deleted))
    write_db(source / "CloudRecordings.db", rows)
    fractional_get = body(run(["--source", str(source), "get", "fractional"]))
    assert fractional_get["data"]["recording"]["created_at"] == "2026-10-17T14:22:10.5Z"
    deleted_get = body(run(["--source", str(source), "get", "deleted-stamp"]))
    assert deleted_get["data"]["recording"]["created_at"] == "2026-10-17T06:30:00Z"
    assert deleted_get["data"]["recording"]["deleted_at"] == "2026-10-18T01:02:03Z"

    def ids_for(day: str, timezone_name: str | None = None) -> set[str]:
        args = ["--source", str(source), "list", "--state", "all", "--date", day, "--limit", "20"]
        if timezone_name:
            args.extend(["--timezone", timezone_name])
        return {item["id"] for item in body(run(args))["data"]["recordings"]}

    assert "edge" in ids_for("2026-10-17")
    assert "edge" not in ids_for("2026-10-17", "America/Los_Angeles")
    assert ids_for("2026-10-16", "America/Los_Angeles") == {"edge", "deleted-stamp"}
    assert ids_for("2026-03-08", "America/Los_Angeles") == {"spring-before", "spring-after"}
    assert "spring-prev-local" in ids_for("2026-03-08")
    assert "spring-prev-local" not in ids_for("2026-03-08", "America/Los_Angeles")
    assert ids_for("2026-11-01", "America/Los_Angeles") == {"fall-pdt", "fall-pst"}
    assert {"fall-prev-local", "fall-pdt", "fall-pst"} <= ids_for("2026-11-01")
    assert ids_for("2026-10-31", "America/Los_Angeles") == {"fall-prev-local"}
    invalid_zone = run(["--source", str(source), "list", "--timezone", "Not/AZone"])
    assert invalid_zone.returncode == 2
    assert body(invalid_zone)["error"]["code"] == "INVALID_TIMEZONE"


def test_same_minute_tiebreaker_and_limit(tmp_path):
    source = tmp_path / "Recordings"
    same = zdate(datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc))
    write_db(
        source / "CloudRecordings.db",
        [
            row("older", "older.m4a", same - 120),
            row("ID-B", "b.m4a", same),
            row("ID-A", "a.m4a", same),
            row("newer", "newer.m4a", same + 180),
        ],
    )
    listed = body(run(["--source", str(source), "list", "--limit", "3"]))
    assert [item["id"] for item in listed["data"]["recordings"]] == ["newer", "ID-A", "ID-B"]
    assert listed["data"]["count"] == 3
    assert listed["data"]["filters"]["limit"] == 3
    base = zdate(datetime(2026, 1, 1, tzinfo=timezone.utc))
    many = tmp_path / "many"
    write_db(
        many / "CloudRecordings.db",
        [row(f"id-{index:03d}", f"{index}.m4a", base + index) for index in range(51)],
    )
    limited = body(run(["--source", str(many), "list"]))
    ids = [item["id"] for item in limited["data"]["recordings"]]
    assert limited["data"]["count"] == 50
    assert ids[0] == "id-050"
    assert ids[-1] == "id-001"
    assert "id-000" not in ids
    for raw in ("0", "-1", "abc"):
        proc = run(["--source", str(source), "list", "--limit", raw])
        assert proc.returncode == 2
        assert body(proc)["error"]["code"] == "INVALID_LIMIT"


def test_export_dry_run_repeat_conflict_and_source_unchanged(tmp_path):
    source = tmp_path / "Recordings"
    created = zdate(datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc))
    payload = b"native-export-bytes"
    write_db(source / "CloudRecordings.db", [row("export-me", "memo.m4a", created, label="Export")])
    audio = source / "memo.m4a"
    audio.write_bytes(payload)
    source_hash = sha256(audio)
    output = tmp_path / "new" / "exports"
    dry = run(["--source", str(source), "export", "export-me", "--output-dir", str(output), "--dry-run"])
    assert dry.returncode == 0
    planned = body(dry)["data"]
    assert planned["status"] == "planned"
    assert planned["dry_run"] is True
    assert planned["verified"] is False
    assert planned["output_size_bytes"] is None
    assert planned["source_size_bytes"] == len(payload)
    assert planned["sha256"] == source_hash
    assert Path(planned["destination_path"]).name == "export-me.m4a"
    assert not output.exists()
    first = run(["--source", str(source), "export", "export-me", "--output-dir", str(output)])
    assert first.returncode == 0
    exported = body(first)["data"]
    assert exported["status"] == "exported"
    assert exported["verified"] is True
    assert exported["dry_run"] is False
    destination = Path(exported["destination_path"])
    assert destination.read_bytes() == payload
    assert sha256(audio) == source_hash
    second = run(["--source", str(source), "export", "export-me", "--output-dir", str(output)])
    assert second.returncode == 0
    assert body(second)["data"]["status"] == "already_exists"
    assert body(second)["data"]["verified"] is True
    dry_existing = run(
        ["--source", str(source), "export", "export-me", "--output-dir", str(output), "--dry-run"]
    )
    assert dry_existing.returncode == 0
    assert body(dry_existing)["data"]["status"] == "already_exists"
    original = bytearray(destination.read_bytes())
    original[0] ^= 0x01
    destination.write_bytes(original)
    conflict = run(["--source", str(source), "export", "export-me", "--output-dir", str(output), "--dry-run"])
    assert conflict.returncode == 5
    assert body(conflict)["error"]["code"] == "DESTINATION_CONFLICT"
    assert destination.read_bytes() == original
    assert audio.read_bytes() == payload
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file")
    unwritable = run(["--source", str(source), "export", "export-me", "--output-dir", str(blocked)])
    assert unwritable.returncode == 5
    assert body(unwritable)["error"]["code"] == "DESTINATION_ERROR"
    assert blocked.read_text() == "file"
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        denied = run(["--source", str(source), "export", "export-me", "--output-dir", str(locked)])
        assert denied.returncode == 5
        assert body(denied)["error"]["code"] == "DESTINATION_ERROR"
        assert list(locked.iterdir()) == []
    finally:
        locked.chmod(0o700)


def test_wal_row_is_visible_without_checkpoint(tmp_path):
    source = tmp_path / "Recordings"
    source.mkdir()
    db_path = source / "CloudRecordings.db"
    created = zdate(datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc))
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        connection.execute(SCHEMA)
        connection.execute(
            """
            INSERT INTO ZCLOUDRECORDING (
                ZUNIQUEID, ZPATH, ZDATE, ZDURATION, ZEVICTIONDATE, ZFLAGS
            ) VALUES (?, ?, ?, ?, NULL, 0)
            """,
            ("checkpointed", "old.m4a", created, 1),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute(
            """
            INSERT INTO ZCLOUDRECORDING (
                ZUNIQUEID, ZPATH, ZDATE, ZDURATION, ZEVICTIONDATE, ZFLAGS
            ) VALUES (?, ?, ?, ?, NULL, 0)
            """,
            ("wal-only", "new.m4a", created + 5, 1),
        )
        connection.commit()
        db_bytes = db_path.read_bytes()
        wal_path = source / "CloudRecordings.db-wal"
        assert wal_path.exists()
        immutable = sqlite3.connect(f"{db_path.as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            immutable_ids = {row[0] for row in immutable.execute("SELECT ZUNIQUEID FROM ZCLOUDRECORDING")}
        finally:
            immutable.close()
        assert "wal-only" not in immutable_ids
        listed = run(["--source", str(source), "list", "--state", "all"])
        assert listed.returncode == 0, listed.stdout
        assert "wal-only" in {item["id"] for item in body(listed)["data"]["recordings"]}
        doctor = body(run(["--source", str(source), "doctor"]))
        assert doctor["data"]["counts"]["total"] == 2
        assert db_path.read_bytes() == db_bytes
    finally:
        connection.close()


def test_missing_db_is_not_created_and_schema_does_not_scan_files(tmp_path):
    source = tmp_path / "Recordings"
    source.mkdir()
    (source / "leftover.m4a").write_bytes(b"do-not-list")
    missing = source / "CloudRecordings.db"
    missing_proc = run(["--source", str(source), "doctor"])
    assert missing_proc.returncode == 3
    assert body(missing_proc)["error"]["code"] == "DB_NOT_FOUND"
    assert not missing.exists()
    assert not Path(str(missing) + "-wal").exists()
    explicit = tmp_path / "elsewhere" / "missing.db"
    explicit_proc = run(["--source", str(source), "--db", str(explicit), "list"])
    assert explicit_proc.returncode == 3
    assert body(explicit_proc)["error"]["code"] == "DB_NOT_FOUND"
    assert not explicit.exists()
    absent_source = tmp_path / "no-source"
    absent_db = tmp_path / "no-db.sqlite"
    absent = run(["--source", str(absent_source), "--db", str(absent_db), "doctor"])
    assert absent.returncode == 3
    assert body(absent)["error"]["code"] == "SOURCE_NOT_FOUND"
    assert not absent_source.exists()
    assert not absent_db.exists()
    incomplete = source / "incomplete.db"
    connection = sqlite3.connect(incomplete)
    connection.execute("CREATE TABLE ZCLOUDRECORDING (ZUNIQUEID TEXT, ZPATH TEXT, ZDATE REAL, ZDURATION REAL)")
    connection.execute("INSERT INTO ZCLOUDRECORDING VALUES ('x', 'leftover.m4a', 1, 1)")
    connection.commit()
    connection.close()
    schema = run(["--source", str(source), "--db", str(incomplete), "list"])
    assert schema.returncode == 3
    parsed = body(schema)
    assert parsed["data"] is None
    assert parsed["error"]["code"] == "SCHEMA_UNSUPPORTED"
    assert "leftover.m4a" not in schema.stdout
    empty = source / "empty.db"
    sqlite3.connect(empty).close()
    no_table = run(["--source", str(source), "--db", str(empty), "doctor"])
    assert no_table.returncode == 3
    assert body(no_table)["error"]["code"] == "SCHEMA_UNSUPPORTED"


def test_titles_empty_ids_unknown_recording_and_source_immutability(tmp_path):
    source = tmp_path / "Recordings"
    created = zdate(datetime(2026, 10, 17, 11, 0, tzinfo=timezone.utc))
    write_db(
        source / "CloudRecordings.db",
        [
            row("labeled", "labeled.m4a", created, label="Morning Idea", encrypted=b"\x02blob"),
            row("fallback", "fallback.m4a", created - 1, label="2026-10-17T14:22:10Z", encrypted="Fallback title"),
            row("blob-only", "blob.m4a", created - 2, encrypted=b"\x03blob"),
            row("", "blank.m4a", created - 3),
            row(None, "null.m4a", created - 4),
        ],
    )
    (source / "labeled.m4a").write_bytes(b"labeled")
    before = snapshot(source)
    doctor = body(run(["--source", str(source), "doctor"]))
    assert doctor["data"]["counts"]["total"] == 3
    labeled = body(run(["--source", str(source), "get", "labeled"]))["data"]["recording"]
    assert labeled["title"] == "Morning Idea"
    assert labeled["duration_seconds"] == 42.5
    fallback = body(run(["--source", str(source), "get", "fallback"]))["data"]["recording"]
    assert fallback["title"] == "Fallback title"
    blob_only = body(run(["--source", str(source), "get", "blob-only"]))["data"]["recording"]
    assert blob_only["title"] is None
    missing = run(["--source", str(source), "get", "missing-id"])
    assert missing.returncode == 4
    assert body(missing)["error"]["code"] == "RECORDING_NOT_FOUND"
    export_missing = run(["--source", str(source), "export", "missing-id", "--output-dir", str(tmp_path / "out")])
    assert export_missing.returncode == 4
    assert body(export_missing)["error"]["code"] == "RECORDING_NOT_FOUND"
    assert not (tmp_path / "out").exists()
    empty_list = body(run(["--source", str(source), "list", "--date", "1999-01-01"]))
    assert empty_list["data"]["recordings"] == []
    assert empty_list["data"]["count"] == 0
    output = tmp_path / "copied"
    exported = run(["--source", str(source), "export", "labeled", "--output-dir", str(output)])
    assert exported.returncode == 0
    assert snapshot(source) == before
    assert sha256(source / "labeled.m4a") == sha256(output / "labeled.m4a")
    no_dir = run(["--source", str(source), "export", "labeled"])
    assert no_dir.returncode == 2
    assert body(no_dir)["command"] == "export"
    spaced = tmp_path / "voice memos"
    write_db(spaced / "CloudRecordings.db", [row("spaced", "a.m4a", created)])
    (spaced / "a.m4a").write_bytes(b"spaced")
    spaced_proc = run(["--source", str(spaced), "get", "spaced"])
    assert spaced_proc.returncode == 0
    assert body(spaced_proc)["data"]["recording"]["audio"]["availability"] == "local"


def test_deleted_missing_file_is_still_deleted(tmp_path):
    source = tmp_path / "Recordings"
    created = zdate(datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc))
    write_db(
        source / "CloudRecordings.db",
        [row("deleted-missing", "gone.m4a", created, eviction=created + 10)],
    )
    exported = run(["--source", str(source), "export", "deleted-missing", "--output-dir", str(tmp_path / "out")])
    assert exported.returncode == 4
    assert body(exported)["error"]["code"] == "RECORDING_DELETED"


def test_path_like_timezones_are_json_errors(tmp_path):
    source = tmp_path / "Recordings"
    source.mkdir()
    write_db(source / "CloudRecordings.db", [])
    absolute = str(tmp_path / "not-a-zone")
    for value in (absolute, "../UTC"):
        proc = run(["--source", str(source), "list", "--timezone", value])
        assert proc.returncode == 2, proc.stderr
        parsed = body(proc)
        assert parsed["command"] == "list"
        assert parsed["data"] is None
        assert parsed["error"]["code"] == "INVALID_TIMEZONE"
        assert "Traceback" not in proc.stdout
        assert "Traceback" not in proc.stderr


def test_unreadable_local_audio_export_is_json_error(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can read mode 000 files")
    source = tmp_path / "Recordings"
    created = zdate(datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc))
    payload = b"unreadable-bytes"
    write_db(source / "CloudRecordings.db", [row("locked-audio", "note.m4a", created)])
    audio = source / "note.m4a"
    audio.write_bytes(payload)
    audio.chmod(0)
    output = tmp_path / "exports"
    try:
        listed = body(run(["--source", str(source), "list"]))
        assert listed["data"]["recordings"][0]["audio"]["availability"] == "local"
        assert listed["data"]["recordings"][0]["audio"]["size_bytes"] == len(payload)
        for extra in ([], ["--dry-run"]):
            proc = run(
                ["--source", str(source), "export", "locked-audio", "--output-dir", str(output), *extra]
            )
            assert proc.returncode == 3, proc.stderr
            parsed = body(proc)
            assert parsed["command"] == "export"
            assert parsed["data"] is None
            assert parsed["error"]["code"] == "SOURCE_ACCESS_ERROR"
            assert "Permission denied" in parsed["error"]["message"]
            assert "Traceback" not in proc.stdout
            assert "Traceback" not in proc.stderr
            assert not output.exists()
            assert audio.stat().st_size == len(payload)
            assert audio.stat().st_mode & 0o777 == 0
    finally:
        audio.chmod(0o644)


def test_sqlite_failure_keeps_driver_detail(tmp_path):
    source = tmp_path / "Recordings"
    source.mkdir()
    (source / "leftover.m4a").write_bytes(b"do-not-scan")
    bogus = source / "CloudRecordings.db"
    bogus.write_text("not a database")
    proc = run(["--source", str(source), "doctor"])
    assert proc.returncode == 3
    parsed = body(proc)
    assert parsed["data"] is None
    assert parsed["error"]["code"] == "DB_OPEN_ERROR"
    assert "file is not a database" in parsed["error"]["message"]
    assert "Traceback" not in proc.stdout
    assert "Traceback" not in proc.stderr
    assert "leftover.m4a" not in proc.stdout
    assert not (source / "CloudRecordings.db-wal").exists()
    if os.geteuid() == 0:
        return
    locked = source / "locked.db"
    connection = sqlite3.connect(locked)
    connection.execute(SCHEMA)
    connection.commit()
    connection.close()
    locked.chmod(0)
    try:
        denied = run(["--source", str(source), "--db", str(locked), "list"])
        assert denied.returncode == 3, denied.stderr
        denied_body = body(denied)
        assert denied_body["error"]["code"] == "DB_OPEN_ERROR"
        assert "unable to open database file" in denied_body["error"]["message"]
        assert "Traceback" not in denied.stdout
        assert "Traceback" not in denied.stderr
    finally:
        locked.chmod(0o644)
