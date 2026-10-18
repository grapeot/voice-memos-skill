from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CORE_DATA_EPOCH_OFFSET = 978307200
DEFAULT_SOURCE = "~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
REQUIRED_COLUMNS = ("ZUNIQUEID", "ZPATH", "ZDATE", "ZDURATION", "ZEVICTIONDATE")
OPTIONAL_COLUMNS = ("ZCUSTOMLABEL", "ZENCRYPTEDTITLE")
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


class CliError(Exception):
    def __init__(self, exit_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code
        self.message = message


def resolve_source(source: str) -> Path:
    path = Path(source).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise CliError(3, "SOURCE_NOT_FOUND", f"Source directory not found: {path}")
    try:
        next(path.iterdir(), None)
    except PermissionError as exc:
        raise CliError(3, "SOURCE_ACCESS_ERROR", f"Source directory is not readable: {path}") from exc
    return path


def resolve_db_path(source: Path, db: str | None) -> Path:
    if db is None:
        return source / "CloudRecordings.db"
    return Path(db).expanduser().resolve()


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise CliError(3, "DB_NOT_FOUND", f"Database file not found: {db_path}")
    uri = f"{db_path.as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=2000")
        connection.execute("PRAGMA query_only=ON")
    except sqlite3.Error as exc:
        raise CliError(3, "DB_OPEN_ERROR", f"Unable to open database read-only: {db_path}: {exc}") from exc
    return connection


def load_recordings(source: Path, db_path: Path) -> list[dict[str, Any]]:
    connection = connect_readonly(db_path)
    try:
        names = _require_schema(connection)
        selected = [column for column in REQUIRED_COLUMNS if column in names]
        selected.extend(column for column in OPTIONAL_COLUMNS if column in names)
        rows = connection.execute(
            "SELECT " + ", ".join(selected) + " FROM ZCLOUDRECORDING"
        ).fetchall()
    except CliError:
        raise
    except sqlite3.Error as exc:
        raise CliError(3, "DB_OPEN_ERROR", f"Unable to read ZCLOUDRECORDING: {db_path}: {exc}") from exc
    finally:
        connection.close()
    recordings = []
    for row in rows:
        recording = _recording_from_row(row, names, source)
        if recording is not None:
            recordings.append(recording)
    return recordings


def public_record(recording: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in recording.items() if not key.startswith("_")}


def select_recordings(
    recordings: list[dict[str, Any]],
    *,
    state: str,
    on_date: Any,
    timezone_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    zone = ZoneInfo(timezone_name)
    matched = []
    for recording in recordings:
        if state != "all" and recording["state"] != state:
            continue
        if on_date is not None:
            zdate = recording["_zdate"]
            if zdate is None:
                continue
            created = datetime.fromtimestamp(zdate + CORE_DATA_EPOCH_OFFSET, tz=timezone.utc)
            if created.astimezone(zone).date() != on_date:
                continue
        matched.append(recording)
    matched.sort(key=_sort_key)
    return matched[:limit]


def find_recording(recordings: list[dict[str, Any]], recording_id: str) -> dict[str, Any]:
    for recording in recordings:
        if recording["id"] == recording_id:
            return recording
    raise CliError(4, "RECORDING_NOT_FOUND", f"Recording {recording_id!r} was not found.")


def export_recording(
    recordings: list[dict[str, Any]],
    recording_id: str,
    output_dir: str,
    dry_run: bool,
) -> dict[str, Any]:
    recording = find_recording(recordings, recording_id)
    if recording["state"] == "deleted":
        raise CliError(
            4,
            "RECORDING_DELETED",
            f"Recording {recording_id!r} is in deleted state and cannot be exported.",
        )
    audio = recording["audio"]
    if audio["availability"] != "local" or not audio["path"]:
        raise CliError(
            4,
            "AUDIO_NOT_LOCAL",
            f"Recording {recording_id!r} does not have a local audio file.",
        )
    source_path = Path(audio["path"])
    if not source_path.is_file():
        raise CliError(
            4,
            "AUDIO_NOT_LOCAL",
            f"Recording {recording_id!r} does not have a local audio file.",
        )
    destination = Path(output_dir).expanduser().resolve() / f"{_sanitize_id(recording_id)}{source_path.suffix}"
    try:
        source_size = source_path.stat().st_size
        source_hash = _sha256(source_path)
    except OSError as exc:
        raise CliError(3, "SOURCE_ACCESS_ERROR", f"Unable to read source audio file: {source_path}: {exc}") from exc
    if destination.exists() or destination.is_symlink():
        return _existing_destination(
            recording_id,
            recording["state"],
            dry_run,
            source_path,
            destination,
            source_size,
            source_hash,
        )
    if dry_run:
        return _export_payload(
            recording_id,
            recording["state"],
            "planned",
            True,
            source_path,
            destination,
            source_size,
            None,
            source_hash,
            False,
        )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError(5, "DESTINATION_ERROR", f"Unable to create output directory: {destination.parent}") from exc
    if not destination.parent.is_dir():
        raise CliError(5, "DESTINATION_ERROR", f"Output path is not a directory: {destination.parent}")
    try:
        _copy_exclusive(source_path, destination)
        output_size = destination.stat().st_size
        output_hash = _sha256(destination)
    except OSError as exc:
        raise CliError(5, "DESTINATION_ERROR", f"Unable to copy audio file to {destination}: {exc}") from exc
    if output_size != source_size or output_hash != source_hash:
        destination.unlink(missing_ok=True)
        raise CliError(5, "COPY_VERIFICATION_FAILED", f"Copied file did not match source digest: {destination}")
    return _export_payload(
        recording_id,
        recording["state"],
        "exported",
        False,
        source_path,
        destination,
        source_size,
        output_size,
        source_hash,
        True,
    )


def _require_schema(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("PRAGMA table_info(ZCLOUDRECORDING)").fetchall()
    names = {row[1] for row in rows}
    if not names:
        raise CliError(3, "SCHEMA_UNSUPPORTED", "Table ZCLOUDRECORDING was not found.")
    missing = [column for column in REQUIRED_COLUMNS if column not in names]
    if missing:
        raise CliError(
            3,
            "SCHEMA_UNSUPPORTED",
            "ZCLOUDRECORDING is missing required columns: " + ", ".join(missing),
        )
    return names


def _recording_from_row(row: sqlite3.Row, names: set[str], source: Path) -> dict[str, Any] | None:
    recording_id = row["ZUNIQUEID"]
    if not isinstance(recording_id, str) or not recording_id:
        return None
    eviction = row["ZEVICTIONDATE"]
    zdate = row["ZDATE"]
    return {
        "id": recording_id,
        "title": _title(row, names),
        "created_at": _format_timestamp(zdate),
        "deleted_at": _format_timestamp(eviction) if eviction is not None else None,
        "duration_seconds": _duration(row["ZDURATION"]),
        "state": "deleted" if eviction is not None else "active",
        "audio": _audio(source, row["ZPATH"]),
        "_zdate": None if zdate is None else float(zdate),
    }


def _title(row: sqlite3.Row, names: set[str]) -> str | None:
    # Modern Voice Memos uses encryptedTitle as the display title even when
    # customLabel still contains a migration/date placeholder.
    if "ZENCRYPTEDTITLE" in names:
        encrypted = row["ZENCRYPTEDTITLE"]
        if isinstance(encrypted, str) and encrypted:
            return encrypted
    if "ZCUSTOMLABEL" in names:
        label = row["ZCUSTOMLABEL"]
        if isinstance(label, str) and label:
            return label
    return None


def _duration(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _audio(source: Path, zpath: Any) -> dict[str, Any]:
    empty = {"availability": "not_local", "path": None, "size_bytes": None}
    if not isinstance(zpath, str) or not zpath:
        return empty
    raw = Path(zpath)
    candidate = raw.resolve() if raw.is_absolute() else (source / raw).resolve()
    if candidate == source or not candidate.is_relative_to(source):
        return empty
    if candidate.is_file():
        return {"availability": "local", "path": str(candidate), "size_bytes": candidate.stat().st_size}
    return {"availability": "not_local", "path": str(candidate), "size_bytes": None}


def _format_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    number = float(value)
    unix = number + CORE_DATA_EPOCH_OFFSET
    if abs(number - round(number)) < 1e-6:
        return datetime.fromtimestamp(round(unix), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = datetime.fromtimestamp(unix, tz=timezone.utc).isoformat(timespec="microseconds").removesuffix("+00:00")
    head, frac = text.split(".", 1)
    frac = frac.rstrip("0")
    if not frac:
        return f"{head}Z"
    return f"{head}.{frac}Z"


def _sort_key(recording: dict[str, Any]) -> tuple[float, str]:
    zdate = recording["_zdate"]
    primary = -zdate if zdate is not None else float("inf")
    return (primary, recording["id"])


def _sanitize_id(recording_id: str) -> str:
    cleaned = _UNSAFE_ID.sub("_", recording_id).lstrip(".")
    if not cleaned or set(cleaned) <= {"."}:
        return "recording"
    return cleaned


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_exclusive(source: Path, destination: Path) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with source.open("rb") as src, os.fdopen(os.open(destination, flags, 0o644), "wb") as dst:
        shutil.copyfileobj(src, dst)


def _existing_destination(
    recording_id: str,
    state: str,
    dry_run: bool,
    source_path: Path,
    destination: Path,
    source_size: int,
    source_hash: str,
) -> dict[str, Any]:
    if destination.is_symlink() or not destination.is_file():
        raise CliError(5, "DESTINATION_ERROR", f"Destination path is not a regular file: {destination}")
    try:
        dest_size = destination.stat().st_size
        dest_hash = _sha256(destination)
    except OSError as exc:
        raise CliError(5, "DESTINATION_ERROR", f"Unable to read destination file: {destination}") from exc
    if dest_size == source_size and dest_hash == source_hash:
        return _export_payload(
            recording_id,
            state,
            "already_exists",
            dry_run,
            source_path,
            destination,
            source_size,
            dest_size,
            source_hash,
            True,
        )
    raise CliError(
        5,
        "DESTINATION_CONFLICT",
        f"Destination file already exists with different content: {destination}",
    )


def _export_payload(
    recording_id: str,
    state: str,
    status: str,
    dry_run: bool,
    source_path: Path,
    destination: Path,
    source_size: int,
    output_size: int | None,
    source_hash: str,
    verified: bool,
) -> dict[str, Any]:
    return {
        "recording_id": recording_id,
        "state": state,
        "status": status,
        "dry_run": dry_run,
        "source_path": str(source_path),
        "destination_path": str(destination),
        "source_size_bytes": source_size,
        "output_size_bytes": output_size,
        "sha256": source_hash,
        "verified": verified,
    }
