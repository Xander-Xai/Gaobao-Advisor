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


@pytest.fixture
def oracle(tmp_path: Path):
    trusted = tmp_path / "oracle"
    trusted.mkdir()
    source = trusted / "runner.py"
    source.write_text("print('SYNTHETIC_WORKSPACE_PASS')", encoding="utf-8")
    source.chmod(0o444)
    trusted.chmod(0o555)
    try:
        yield trusted
    finally:
        trusted.chmod(0o755)


def test_verified_workspace_remains_usable(work, oracle):
    assert "--network" in sandbox_command(IMAGE, work, NAME, oracle_dir=oracle)


def test_workspace_directory_symlink_rejected(work, oracle, tmp_path):
    alias = tmp_path / "gaobao-synthetic-alias"
    alias.symlink_to(work, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        sandbox_command(IMAGE, alias, NAME, oracle_dir=oracle)


def test_nested_directory_and_symlink_rejected(work, oracle, tmp_path):
    (work / "nested").mkdir()
    with pytest.raises(ValueError, match="unsafe"):
        sandbox_command(IMAGE, work, NAME, oracle_dir=oracle)
    (work / "nested").rmdir()
    (work / "shortcut").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="unsafe"):
        sandbox_command(IMAGE, work, NAME, oracle_dir=oracle)


def test_marker_is_not_just_presence_check(work, oracle):
    (work / ".synthetic_workspace").write_text("REAL_USER_FILES\n", encoding="utf-8")
    with pytest.raises(ValueError, match="marker"):
        sandbox_command(IMAGE, work, NAME, oracle_dir=oracle)


def test_oversized_file_and_workspace_entry_count_rejected(work, oracle):
    (work / "huge").write_bytes(b"X" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="unsafe"):
        sandbox_command(IMAGE, work, NAME, oracle_dir=oracle)
    (work / "huge").unlink()
    for index in range(33):
        (work / (f"file-{index:02d}")).write_text("synthetic", encoding="utf-8")
    with pytest.raises(ValueError, match="too many"):
        sandbox_command(IMAGE, work, NAME, oracle_dir=oracle)
