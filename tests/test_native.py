"""Compile and validate the native boundary without touching real Voice Memos data."""
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from voice_memos_skill import rename


@pytest.fixture(scope="module")
def native_helper(tmp_path_factory):
    if sys.platform != "darwin" or not shutil.which("xcrun"):
        pytest.skip("Native compilation requires macOS and Xcode Command Line Tools")
    binary = tmp_path_factory.mktemp("native-helper") / "rename-native"
    result = subprocess.run([
        "/usr/bin/xcrun", "clang", "-target", f"{platform.machine()}-apple-ios15.0-macabi",
        "-fobjc-arc", "-framework", "Foundation", "-framework", "CoreData",
        str(Path(rename.__file__).with_name("rename_native.m")), "-o", str(binary),
    ], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return binary


def test_native_missing_store_never_created(native_helper, tmp_path):
    db = tmp_path / "missing.db"
    result = subprocess.run([str(native_helper), str(db), "synthetic-id", "Title", "rename"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 6
    assert json.loads(result.stdout)["error"]["code"] == "DB_NOT_FOUND"
    assert not db.exists()


@pytest.mark.parametrize("title", ["", "  ", "line\nbreak"])
def test_native_invalid_title_does_not_open_store(native_helper, tmp_path, title):
    db = tmp_path / "not-a-store.db"
    db.write_bytes(b"not a Core Data store")
    result = subprocess.run([str(native_helper), str(db), "synthetic-id", title, "rename"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 6
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_TITLE"
    assert db.read_bytes() == b"not a Core Data store"


def test_native_rejects_invalid_mode(native_helper, tmp_path):
    db = tmp_path / "missing.db"
    result = subprocess.run([str(native_helper), str(db), "synthetic-id", "Title", "unexpected"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 6
    assert json.loads(result.stdout)["error"]["code"] == "RENAME_NATIVE_ARGUMENTS"
    assert not db.exists()


def test_native_and_python_title_validation_agree(native_helper, tmp_path):
    # Compile the actual native validation function into a standalone harness,
    # avoiding any framework/database access for accepted input test cases.
    source = Path(rename.__file__).with_name("rename_native.m")
    harness = tmp_path / "validate.m"
    harness.write_text(
        '#define main vmRenameMain\n#include "' + str(source) + '"\n#undef main\n'
        'int main(int argc, const char *argv[]) { @autoreleasepool { '
        'return validTitle([NSString stringWithUTF8String:argv[1]]) ? 0 : 2; } }\n'
    )
    binary = tmp_path / "validate-title"
    compiled = subprocess.run([
        "/usr/bin/xcrun", "clang", "-target", f"{platform.machine()}-apple-ios15.0-macabi",
        "-fobjc-arc", "-framework", "Foundation", "-framework", "CoreData",
        str(harness), "-o", str(binary),
    ], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stderr
    values = ["家庭 👨‍👩‍👧", "\u200b", "\u200d", "", " ", "a\nb", "a\x7fb", "a\x85b",
              "\u00a0\u1680\u2000\u2028\u2029\u202f\u205f\u3000", "会议 🍎"]
    for title in values:
        try:
            rename.validate_title(title)
            expected = 0
        except rename.CliError:
            expected = 2
        result = subprocess.run([str(binary), title], capture_output=True, text=True, timeout=10)
        assert result.returncode == expected, repr(title)
