"""The experimental code executor must not inherit the application image.

This is a static, no-Docker/no-network gate. Actual OCI isolation is checked
in CI's No-Key Container Smoke Test with the image produced by Dockerfile.sandbox.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dedicated_sandbox_image_does_not_copy_app_or_secrets():
    dockerfile = (ROOT / "Dockerfile.sandbox").read_text(encoding="utf-8")
    active = "\n".join(line for line in dockerfile.splitlines() if not line.lstrip().startswith("#"))
    assert re.search(r"^FROM python:3[.]11-slim$", active, re.MULTILINE)
    assert not re.search(r"^(COPY|ADD|ARG|RUN|EXPOSE|VOLUME)\b", active, re.MULTILINE)
    assert "USER 65534:65534" in active
    assert "WORKDIR /workspace" in active


def test_ci_executes_synthetic_code_only_in_minimal_image():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "docker build -f Dockerfile.sandbox -t gb-advisor:sandbox-ci ." in workflow
    assert workflow.count("docker image inspect --format '{{.Id}}' gb-advisor:sandbox-ci") == 2
    assert "assert not Path('/app').exists()" in workflow
