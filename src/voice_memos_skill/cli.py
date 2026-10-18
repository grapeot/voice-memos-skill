from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from voice_memos_skill import __version__
from voice_memos_skill.rename import rename_recording, validate_title
from voice_memos_skill.store import (
    DEFAULT_SOURCE,
    CliError,
    export_recording,
    find_recording,
    load_recordings,
    public_record,
    resolve_db_path,
    resolve_source,
    select_recordings,
)


class JsonArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, command: str = "cli", **kwargs: Any) -> None:
        self.command_name = command
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        fail(self.command_name, 2, "INVALID_ARGUMENTS", message)


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(prog="voice-memos")
    parser.add_argument("--source", default=DEFAULT_SOURCE, metavar="DIR")
    parser.add_argument("--db", default=None, metavar="PATH")
    parser.add_argument("--version", action="version", version=f"voice-memos {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)
    subcommands.add_parser("doctor", help="Check database readability and recording counts", command="doctor")
    list_parser = subcommands.add_parser("list", help="List recordings", command="list")
    list_parser.add_argument("--state", choices=("active", "deleted", "all"), default="active")
    list_parser.add_argument("--date", default=None, metavar="YYYY-MM-DD")
    list_parser.add_argument("--timezone", default="UTC", metavar="IANA_NAME")
    list_parser.add_argument("--limit", default=50, metavar="N")
    get_parser = subcommands.add_parser("get", help="Inspect one recording", command="get")
    get_parser.add_argument("recording_id")
    export_parser = subcommands.add_parser("export", help="Copy an active local recording", command="export")
    export_parser.add_argument("recording_id")
    export_parser.add_argument("--output-dir", required=True, metavar="DIR")
    export_parser.add_argument("--dry-run", action="store_true")
    rename_parser = subcommands.add_parser("rename", help="Rename an active recording through Voice Memos on macOS", command="rename")
    rename_parser.add_argument("recording_id")
    rename_parser.add_argument("--title", required=True)
    rename_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        data = run_command(args)
    except CliError as exc:
        fail(args.command, exc.exit_code, exc.code, exc.message)
    succeed(args.command, data)


def run_command(args: argparse.Namespace) -> dict[str, Any]:
    on_date = None
    timezone_name = "UTC"
    limit = 50
    if args.command == "list":
        on_date = parse_date(args.date)
        timezone_name = parse_timezone(args.timezone)
        limit = parse_limit(args.limit)
    source = resolve_source(args.source)
    db_path = resolve_db_path(source, args.db)
    if args.command == "rename":
        validate_title(args.title)
        return rename_recording(source, db_path, args.recording_id, args.title, args.dry_run)
    recordings = load_recordings(source, db_path)
    if args.command == "doctor":
        active = sum(recording["state"] == "active" for recording in recordings)
        deleted = sum(recording["state"] == "deleted" for recording in recordings)
        return {
            "source_path": str(source),
            "db_path": str(db_path),
            "db_readable": True,
            "schema_supported": True,
            "counts": {"total": len(recordings), "active": active, "deleted": deleted},
        }
    if args.command == "list":
        matched = select_recordings(
            recordings,
            state=args.state,
            on_date=on_date,
            timezone_name=timezone_name,
            limit=limit,
        )
        public = [public_record(recording) for recording in matched]
        return {
            "recordings": public,
            "count": len(public),
            "filters": {
                "state": args.state,
                "date": None if on_date is None else on_date.isoformat(),
                "timezone": timezone_name,
                "limit": limit,
            },
        }
    if args.command == "get":
        return {"recording": public_record(find_recording(recordings, args.recording_id))}
    if args.command == "export":
        return export_recording(recordings, args.recording_id, args.output_dir, args.dry_run)
    raise CliError(2, "INVALID_ARGUMENTS", f"Unknown command {args.command!r}.")


def parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CliError(2, "INVALID_DATE", f"Invalid date {value!r}; expected YYYY-MM-DD.") from exc


def parse_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CliError(2, "INVALID_TIMEZONE", f"Unknown timezone {value!r}.") from exc
    return value


def parse_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise CliError(2, "INVALID_LIMIT", f"Limit must be a positive integer, got {value!r}.") from exc
    if limit < 1:
        raise CliError(2, "INVALID_LIMIT", f"Limit must be a positive integer, got {limit}.")
    return limit


def succeed(command: str, data: dict[str, Any]) -> None:
    emit({"command": command, "data": data, "error": None})
    raise SystemExit(0)


def fail(command: str, exit_code: int, code: str, message: str) -> None:
    print(f"{code}: {message}", file=sys.stderr)
    emit({"command": command, "data": None, "error": {"code": code, "message": message}})
    raise SystemExit(exit_code)


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, allow_nan=False))
    sys.stdout.write("\n")
