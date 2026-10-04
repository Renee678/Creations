"""Guardrail: no API keys in tracked files (keys belong in the git-ignored .env)."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")


def test_no_anthropic_keys_in_tracked_files():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    offenders = [f for f in files if (ROOT / f).is_file() and KEY_PATTERN.search((ROOT / f).read_text(errors="ignore"))]
    assert not offenders, f"API key found in tracked files: {offenders}"


def test_env_file_is_ignored():
    assert ".env" in (ROOT / ".gitignore").read_text().split()
