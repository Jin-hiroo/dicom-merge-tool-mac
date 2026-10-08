"""updater.py: バージョン比較・リリース情報の解釈・ダウンロード検証・入れ替え。

ネットワークには出ず、ローカルの HTTP サーバーで GitHub の代わりをする。
"""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "macos"))

import updater  # noqa: E402

UA = "head3Dv1-updater/test"


# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("v1.2.3", (1, 2, 3)),
    ("1.2", (1, 2, 0)),
    ("v2", (2, 0, 0)),
    ("1.10.0", (1, 10, 0)),
    ("dev", (0,)),
    ("?", (0,)),
])
def test_parse_version(text, expected):
    assert updater.parse_version(text) == expected


def test_is_newer():
    assert updater.is_newer("v1.0.1", "1.0.0")
    assert updater.is_newer("1.10.0", "1.9.9")             # 文字列比較ではない
    assert not updater.is_newer("v1.0.0", "1.0.0")
    assert not updater.is_newer("0.9.0", "1.0.0")
    assert updater.is_newer("1.0.0", "dev")


# ---------------------------------------------------------------------------
def release_json(base, name="head3Dv1-1.2.0-arm64.dmg", digest=None, with_sha_file=False,
                 size=0):
    assets = [{"name": name, "browser_download_url": f"{base}/{name}", "size": size,
               "digest": digest}]
    if with_sha_file:
        assets.append({"name": name + ".sha256",
                       "browser_download_url": f"{base}/{name}.sha256"})
    return {"tag_name": "v1.2.0", "body": "変更点", "html_url": f"{base}/release",
            "assets": assets}


def test_parse_release_prefers_api_digest():
    rel = updater.parse_release(release_json("http://x", digest="sha256:ABCD",
                                             with_sha_file=True))
    assert rel.version == "1.2.0" and rel.tag == "v1.2.0"
    assert rel.sha256 == "abcd"
    assert rel.sha256_url.endswith(".dmg.sha256")
    assert rel.notes == "変更点"


def test_parse_release_without_dmg_is_an_error():
    data = release_json("http://x")
    data["assets"] = [{"name": "notes.txt", "browser_download_url": "http://x/n"}]
    with pytest.raises(updater.UpdateError):
        updater.parse_release(data)


# ---------------------------------------------------------------------------
@pytest.fixture
def server(tmp_path):
    """tmp_path の中身を配る HTTP サーバー。routes で任意の応答も返せる。"""
    routes = {}

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(tmp_path), **k)

        def do_GET(self):                                   # noqa: N802
            if self.path in routes:
                code, body = routes[self.path]
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, *a):
            pass

    class Server(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass                  # キャンセル試験で接続を途中で切るのは想定どおり

    httpd = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, tmp_path, routes
    httpd.shutdown()


def test_fetch_latest_no_release(server, monkeypatch):
    base, _, routes = server
    routes["/latest"] = (404, b'{"message": "Not Found"}')
    monkeypatch.setattr(updater, "LATEST_URL", base + "/latest")
    with pytest.raises(updater.NoRelease):
        updater.fetch_latest("1.0.0")


def test_fetch_latest_parses_release(server, monkeypatch):
    base, _, routes = server
    routes["/latest"] = (200, json.dumps(release_json(base, digest="sha256:00")).encode())
    monkeypatch.setattr(updater, "LATEST_URL", base + "/latest")
    rel = updater.fetch_latest("1.0.0")
    assert rel.version == "1.2.0" and rel.dmg_url.startswith(base)


def test_fetch_latest_unreachable(monkeypatch):
    monkeypatch.setattr(updater, "LATEST_URL", "http://127.0.0.1:9/latest")
    with pytest.raises(updater.UpdateError) as info:
        updater.fetch_latest("1.0.0")
    assert not isinstance(info.value, updater.NoRelease)


def _publish(root: Path, payload: bytes, name="head3Dv1-1.2.0-arm64.dmg"):
    (root / name).write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_download_verifies_checksum(server, tmp_path_factory):
    base, root, _ = server
    sha = _publish(root, os.urandom(3 * 1024 * 1024 + 7))
    rel = updater.parse_release(release_json(base, digest=f"sha256:{sha}"))
    dest = tmp_path_factory.mktemp("dl") / rel.dmg_name
    seen = []
    assert updater.download(rel, dest, UA, progress_cb=seen.append) == dest
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == sha
    assert seen and seen[-1] == 100


def test_download_uses_sha256_file_when_api_has_no_digest(server, tmp_path_factory):
    base, root, _ = server
    sha = _publish(root, b"dmg-bytes")
    (root / "head3Dv1-1.2.0-arm64.dmg.sha256").write_text(
        f"{sha}  head3Dv1-1.2.0-arm64.dmg\n")
    rel = updater.parse_release(release_json(base, with_sha_file=True))
    dest = tmp_path_factory.mktemp("dl") / rel.dmg_name
    assert updater.download(rel, dest, UA) == dest


def test_download_rejects_tampered_file(server, tmp_path_factory):
    base, root, _ = server
    _publish(root, b"real")
    rel = updater.parse_release(release_json(base, digest="sha256:" + "0" * 64))
    dest = tmp_path_factory.mktemp("dl") / rel.dmg_name
    with pytest.raises(updater.UpdateError):
        updater.download(rel, dest, UA)
    assert not dest.exists()                                 # 壊れたファイルは残さない


def test_download_requires_a_checksum(server, tmp_path_factory):
    base, root, _ = server
    _publish(root, b"x")
    rel = updater.parse_release(release_json(base))
    with pytest.raises(updater.UpdateError):
        updater.download(rel, tmp_path_factory.mktemp("dl") / "x.dmg", UA)


def test_download_can_be_cancelled(server, tmp_path_factory):
    base, root, _ = server
    sha = _publish(root, os.urandom(4 * 1024 * 1024))
    rel = updater.parse_release(release_json(base, digest=f"sha256:{sha}"))
    dest = tmp_path_factory.mktemp("dl") / rel.dmg_name
    assert updater.download(rel, dest, UA, cancel_cb=lambda: True) is None
    assert not dest.exists()


# ---------------------------------------------------------------------------
def test_install_problem():
    assert updater.install_problem(None)                    # 開発用起動
    assert "移動" in updater.install_problem(
        Path("/private/var/folders/x/AppTranslocation/ABC/d/head3Dv1.app"))
    assert "DMG" in updater.install_problem(Path("/Volumes/head3Dv1/head3Dv1.app"))


def test_running_app_is_none_when_not_frozen():
    assert updater.running_app() is None


def _fake_app(path: Path, marker: str):
    (path / "Contents").mkdir(parents=True)
    (path / "Contents" / "marker").write_text(marker)


def test_schedule_swap_waits_for_exit_then_replaces(tmp_path):
    target = tmp_path / "Applications" / "head3Dv1.app"
    staged = tmp_path / "Applications" / ".head3Dv1-update-1.2.0.app"
    _fake_app(target, "old")
    _fake_app(staged, "new")

    waiter = subprocess.Popen(["sleep", "1"])
    swap = updater.schedule_swap(staged, target, relaunch=False,
                                 log_file=tmp_path / "update.log", pid=waiter.pid)
    # 親 (waiter) が生きている間は入れ替えない
    assert (target / "Contents" / "marker").read_text() == "old"
    waiter.wait()
    assert swap.wait(timeout=30) == 0
    assert (target / "Contents" / "marker").read_text() == "new"
    assert sorted(p.name for p in target.parent.iterdir()) == ["head3Dv1.app"]
    assert "完了" in (tmp_path / "update.log").read_text()
