"""Generated Catalog reader secrets do not overwrite existing full envs."""

import re
import stat

from scripts.setup_full_env import main


def test_add_java_reader_preserves_existing_credentials_and_is_idempotent(tmp_path):
    output = tmp_path / ".env.full"
    initial = "MYSQL_DATABASE=gaobao\nMYSQL_ROOT_PASSWORD=existing\n"
    output.write_text(initial, encoding="utf-8")
    assert main(["--out", str(output), "--add-java-reader"]) == 0
    result = output.read_text(encoding="utf-8")
    assert result.startswith(initial)
    match = re.search(r"^CATALOG_READ_PASSWORD=([a-f0-9]{48})$", result, re.MULTILINE)
    assert match is not None
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert main(["--out", str(output), "--add-java-reader"]) == 0
    assert output.read_text(encoding="utf-8") == result


def test_add_java_reader_missing_file_does_not_create(tmp_path):
    output = tmp_path / "missing.env"
    assert main(["--out", str(output), "--add-java-reader"]) == 2
    assert not output.exists()
