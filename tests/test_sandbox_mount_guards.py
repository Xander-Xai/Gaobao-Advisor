"""Reject ambiguous bind-mount sources and hard links before Docker runs."""

import os

import pytest

from benchmarks.sandbox_workspace import sandbox_command


def make_workspace(tmp_path, name="gaobao-synthetic-test"):
    root = tmp_path / name
    root.mkdir()
    (root / ".synthetic_workspace").write_text("SYNTHETIC_ONLY", encoding="utf-8")
    return root


def command(root):
    return sandbox_command("sha256:" + "a" * 64, root, "gaobao-harness-" + "a" * 16)


def test_reject_hardlink_to_external_file(tmp_path):
    root = make_workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    os.link(outside, root / "linked.txt")
    with pytest.raises(ValueError, match="unsafe entry"):
        command(root)
    assert outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.parametrize("suffix", [",readonly", '"quoted"'])
def test_reject_mount_delimiter(tmp_path, suffix):
    root = make_workspace(tmp_path, "gaobao-synthetic-" + suffix)
    with pytest.raises(ValueError, match="mount delimiter"):
        command(root)


def test_valid_workspace_is_accepted(tmp_path):
    root = make_workspace(tmp_path)
    assert f"type=bind,src={root.resolve()},dst=/workspace" in command(root)
