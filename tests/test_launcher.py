"""launcher.py の書込先差し替えの回帰テスト。

app.core.session は import 時に SESSIONS_DIR を確定するので、import 順が
崩れると .app の中にセッションを書こうとする。毎回まっさらなプロセスで確かめる。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MACOS = ROOT / "macos"

sys.path.insert(0, str(MACOS))
import launcher  # noqa: E402


def run_isolated(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("HEAD3DV1_DATA_DIR", None)
    prelude = f"import sys; sys.path.insert(0, {str(MACOS)!r}); import launcher\n"
    return subprocess.run([sys.executable, "-c", prelude + code],
                          capture_output=True, text=True, env=env, timeout=120)


def test_paths_are_redirected(tmp_path):
    proc = run_isolated(f"""
import json
data = launcher.configure_paths({str(tmp_path)!r})
from app import config
from app.core import session
print(json.dumps({{
    "data": str(data),
    "root": str(config.PROJECT_ROOT),
    "scratch": str(config.SCRATCH_DIR),
    "sessions": str(session.SESSIONS_DIR),
    "autosave": str(session.AUTOSAVE_DIR),
}}))
""")
    assert proc.returncode == 0, proc.stderr
    paths = json.loads(proc.stdout.strip().splitlines()[-1])
    data = Path(paths["data"])
    assert data == tmp_path.resolve()
    assert Path(paths["root"]) == data
    assert Path(paths["scratch"]) == data / "scratch"
    assert Path(paths["sessions"]) == data / "sessions"
    assert Path(paths["autosave"]) == data / "sessions" / "_autosave"


def test_configure_after_session_import_is_rejected(tmp_path):
    proc = run_isolated(f"""
from app.core import session
try:
    launcher.configure_paths({str(tmp_path)!r})
except RuntimeError:
    print("rejected")
""")
    assert proc.returncode == 0, proc.stderr
    assert "rejected" in proc.stdout


def test_default_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("HEAD3DV1_DATA_DIR", raising=False)
    assert launcher.default_data_dir() == (
        Path.home() / "Library" / "Application Support" / "head3Dv1")

    monkeypatch.setenv("HEAD3DV1_DATA_DIR", str(tmp_path))
    assert launcher.default_data_dir() == tmp_path


@pytest.mark.parametrize("argv, expected", [
    ([], (False, None, False)),
    (["--self-test", "--no-gui"], (True, None, True)),
    (["-psn_0_12345"], (False, None, False)),       # Finder が付ける引数は無視
])
def test_parse_args(argv, expected):
    args = launcher.parse_args(argv)
    assert (args.self_test, args.dicom, args.no_gui) == expected
