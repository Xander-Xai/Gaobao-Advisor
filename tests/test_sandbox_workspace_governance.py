"""Guard against mounting untrusted scratch contents into Docker."""

from pathlib import Path

import pytest

from benchmarks.sandbox_workspace import sandbox_command

IMAGE = "sha256:" + "a" * 64
NAME = "gaobao-harness-" + "b" * 16


@pytest.fixture
def work(tmp_path: Path):
    path = tmp_path / "gaobao-synthetic-guard"
    path.mkdir()
    (path / ".synthetic_workspace").write_text("SYNTHETIC_ONLY\n", encoding="utf-8")
    (path / "probe.py").write_text("print('ok')", encoding="utf-8")
    return path


def test_verified_workspace_remains_usable(work):
    assert "--network" in sandbox_command(IMAGE, work, NAME)


def test_workspace_directory_symlink_rejected(work, tmp_path):
    alias = tmp_path / "gaobao-synthetic-alias"
    alias.symlink_to(work, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        sandbox_command(IMAGE, alias, NAME)


def test_nested_directory_and_symlink_rejected(work, tmp_path):
    (work / "nested").mkdir()
    with pytest.raises(ValueError, match="unsafe"):
        sandbox_command(IMAGE, work, NAME)
    (work / "nested").rmdir()
    (work / "shortcut").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="unsafe"):
        sandbox_command(IMAGE, work, NAME)


def test_marker_is_not_just_presence_check(work):
    (work / ".synthetic_workspace").write_text("REAL_USER_FILES\n", encoding="utf-8")
    with pytest.raises(ValueError, match="marker"):
        sandbox_command(IMAGE, work, NAME)


def test_oversized_file_and_workspace_entry_count_rejected(work):
    (work / "huge").write_bytes(b"X" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="unsafe"):
        sandbox_command(IMAGE, work, NAME)
    (work / "huge").unlink()
    for index in range(33):
        (work / ("file-%02d" % index)).write_text("synthetic", encoding="utf-8")
    with pytest.raises(ValueError, match="too many"):
        sandbox_command(IMAGE, work, NAME)
