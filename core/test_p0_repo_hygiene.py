"""Phase 0 / U5, T-SEC-1: repository hygiene."""
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = ["*.sqlite3", "media/", "protected_media/", "staticfiles/", "__pycache__/",
            "*.pyc", ".env", ".DS_Store", "node_modules/", "*.log"]


def test_u5_repo_hygiene_gitignore_patterns():
    lines = {l.strip() for l in (ROOT / ".gitignore").read_text().splitlines()}
    missing = [p for p in REQUIRED if p not in lines]
    assert not missing, f".gitignore lacks {missing}"
    assert "!.env.example" in lines


def _git_ok():
    return shutil.which("git") and subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _git_ok(), reason="not a git checkout")
def test_u5_no_tracked_databases_or_media():
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True,
                           text=True, check=True).stdout.splitlines()
    bad = [f for f in files if ".sqlite3" in f or f.startswith(("media/", "protected_media/"))
           or f == ".env"]
    assert not bad, bad


@pytest.mark.skipif(not _git_ok(), reason="not a git checkout")
def test_u5_share_zip_has_no_db_or_media(tmp_path):
    out = tmp_path / "share.zip"
    subprocess.run(["bash", str(ROOT / "scripts" / "make_share_zip.sh"), "-o", str(out)],
                   cwd=ROOT, check=True, capture_output=True, env={**os.environ})
    names = zipfile.ZipFile(out).namelist()
    assert "manage.py" in names
    assert not [n for n in names if ".sqlite3" in n or n.startswith(("media/", "protected_media/"))
                or n == ".env"]
