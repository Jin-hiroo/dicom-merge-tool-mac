"""GitHub Releases からの自己アップデート (Qt に依存しない部分)。

流れ:
  1. fetch_latest()     最新リリースを GitHub API で調べる
  2. is_newer()         今のバージョンと比べる
  3. download()         DMG を落とし、SHA-256 を照合する
  4. stage_from_dmg()   DMG をマウントし、新しい .app をインストール先の隣にコピーして検証する
  5. schedule_swap()    このプロセスが終わったら旧 .app と入れ替えて (必要なら) 再起動する

通信するのはユーザーが「アップデートを確認…」を選んだときだけで、行き先は
GitHub (api.github.com とダウンロード用の github.com / objects.githubusercontent.com)
のみ。患者データやファイルの情報は一切送らない。
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "Jin-hiroo/dicom-merge-tool-mac"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
BUNDLE_ID = "io.github.jin-hiroo.head3dv1"
APP_NAME = "head3Dv1.app"
TIMEOUT = 20


class UpdateError(Exception):
    """利用者に見せる文言を持つ例外。"""


class NoRelease(UpdateError):
    pass


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    page_url: str
    dmg_name: str
    dmg_url: str
    dmg_size: int
    sha256: str | None          # API の digest から (無ければ .sha256 アセットを使う)
    sha256_url: str | None


# ---------------------------------------------------------------------------
# バージョン
# ---------------------------------------------------------------------------
def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2' -> (1, 2, 3) / (1, 2, 0)。読めなければ (0,)。"""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    if not m:
        return (0,)
    parts = tuple(int(p) for p in m.group(1).split("."))
    return parts + (0,) * (3 - len(parts))


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


# ---------------------------------------------------------------------------
# 通信
# ---------------------------------------------------------------------------
def _ssl_context() -> ssl.SSLContext:
    """HTTPS の証明書検証。

    同梱の Python (OpenSSL) は macOS のキーチェーンを読めず、既定の証明書パスも
    ビルド機のものを指している。truststore で OS の信頼設定 (院内プロキシの
    ルート証明書を含む) を使い、無ければ certifi の証明書で検証する。
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _request(url: str, user_agent: str, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": user_agent,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context())


def parse_release(data: dict) -> Release:
    tag = data.get("tag_name") or ""
    dmgs = [a for a in data.get("assets", [])
            if a.get("name", "").endswith(".dmg") and "arm64" in a.get("name", "")]
    if not dmgs:
        raise UpdateError(f"リリース {tag} に Apple Silicon 用の DMG がありません。")
    dmg = dmgs[0]
    digest = dmg.get("digest") or ""
    sha = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None
    sha_asset = next((a for a in data.get("assets", [])
                      if a.get("name") == dmg["name"] + ".sha256"), None)
    return Release(
        version=".".join(str(p) for p in parse_version(tag)),
        tag=tag,
        notes=(data.get("body") or "").strip(),
        page_url=data.get("html_url") or f"https://github.com/{REPO}/releases",
        dmg_name=dmg["name"],
        dmg_url=dmg["browser_download_url"],
        dmg_size=int(dmg.get("size") or 0),
        sha256=sha,
        sha256_url=sha_asset["browser_download_url"] if sha_asset else None,
    )


def fetch_latest(current_version: str) -> Release:
    ua = f"head3Dv1-updater/{current_version}"
    try:
        with _request(LATEST_URL, ua) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NoRelease("公開されているリリースがまだありません。") from exc
        if exc.code == 403:
            raise UpdateError("GitHub への問い合わせ回数の上限に達しました。"
                              "しばらくしてから再度お試しください。") from exc
        raise UpdateError(f"GitHub から応答がありませんでした (HTTP {exc.code})。") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("GitHub に接続できませんでした。ネットワーク接続を確認してください。\n"
                          f"({exc})") from exc
    return parse_release(data)


def download(release: Release, dest: Path, user_agent: str,
             progress_cb=None, cancel_cb=None) -> Path | None:
    """DMG をダウンロードし、SHA-256 を照合する。キャンセル時は None。"""
    expected = release.sha256
    if expected is None and release.sha256_url:
        try:
            with _request(release.sha256_url, user_agent, accept="*/*") as resp:
                expected = resp.read().decode("ascii", "replace").split()[0].lower()
        except (urllib.error.URLError, TimeoutError, OSError, IndexError) as exc:
            raise UpdateError(f"チェックサムを取得できませんでした。\n({exc})") from exc
    if not expected:
        raise UpdateError("リリースにチェックサムが無いため、安全に更新できません。")

    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    done = 0
    try:
        with _request(release.dmg_url, user_agent, accept="application/octet-stream") as resp, \
                open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or release.dmg_size or 0)
            while True:
                if cancel_cb and cancel_cb():
                    out.close()
                    dest.unlink(missing_ok=True)
                    return None
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(int(done * 100 / total))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"ダウンロードに失敗しました。\n({exc})") from exc

    if digest.hexdigest() != expected:
        dest.unlink(missing_ok=True)
        raise UpdateError("ダウンロードしたファイルのチェックサムが一致しません。"
                          "更新を中止しました。")
    return dest


# ---------------------------------------------------------------------------
# インストール先
# ---------------------------------------------------------------------------
def running_app() -> Path | None:
    """実行中の .app (frozen でなければ None)。"""
    if not getattr(sys, "frozen", False):
        return None
    app = Path(sys.executable).resolve().parents[2]
    return app if app.suffix == ".app" else None


def install_problem(app: Path | None) -> str | None:
    """その場で入れ替えられない理由 (問題なければ None)。"""
    if app is None:
        return "開発用の起動方法では自動アップデートは使えません。"
    if "/AppTranslocation/" in str(app):
        return ("アプリがダウンロードした場所から直接起動されています。\n"
                "Finder で head3Dv1 を「アプリケーション」フォルダへ移動してから、"
                "もう一度お試しください。")
    if str(app).startswith("/Volumes/"):
        return ("ディスクイメージ (DMG) の中から起動されています。\n"
                "head3Dv1 を「アプリケーション」フォルダへコピーしてから、もう一度お試しください。")
    if not os.access(app.parent, os.W_OK):
        return (f"{app.parent} に書き込む権限がありません。\n"
                "リリースページから DMG をダウンロードして入れ替えてください。")
    return None


def _read_plist(app: Path) -> dict:
    with open(app / "Contents" / "Info.plist", "rb") as fp:
        return plistlib.load(fp)


def stage_from_dmg(dmg: Path, target: Path, expected_version: str) -> Path:
    """DMG 内の .app を target の隣 (同じボリューム) にコピーして検証し、そのパスを返す。"""
    staged = target.parent / f".head3Dv1-update-{expected_version}.app"
    if staged.exists():
        shutil.rmtree(staged)
    mountpoint = Path(tempfile.mkdtemp(prefix="head3Dv1-dmg-"))
    try:
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
                        "-mountpoint", str(mountpoint), str(dmg)],
                       check=True, capture_output=True)
        try:
            source = mountpoint / APP_NAME
            if not source.is_dir():
                raise UpdateError("ディスクイメージの中に head3Dv1.app がありません。")
            info = _read_plist(source)
            if info.get("CFBundleIdentifier") != BUNDLE_ID:
                raise UpdateError("ディスクイメージの中身が head3Dv1 ではありません。")
            got = info.get("CFBundleShortVersionString", "")
            if parse_version(got) != parse_version(expected_version):
                raise UpdateError(f"ディスクイメージの版 ({got}) がリリース "
                                  f"({expected_version}) と一致しません。")
            subprocess.run(["ditto", str(source), str(staged)],
                           check=True, capture_output=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mountpoint), "-force"],
                           capture_output=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(staged)],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(staged, ignore_errors=True)
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise UpdateError(f"新しいバージョンの展開に失敗しました ({exc.cmd[0]})。\n{detail}") from exc
    except UpdateError:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(mountpoint, ignore_errors=True)
    return staged


# 親プロセスの終了を待ってから入れ替える。失敗したら元に戻す。
SWAP_SCRIPT = r"""#!/bin/sh
pid="$1"; target="$2"; staged="$3"; relaunch="$4"
while kill -0 "$pid" 2>/dev/null; do sleep 0.5; done
backup="$target.old-$$"
echo "$(date) $staged -> $target"
mv "$target" "$backup" || { echo "旧版を退避できませんでした"; exit 1; }
if mv "$staged" "$target"; then
  rm -rf "$backup"
else
  echo "入れ替えに失敗したため元に戻します"
  mv "$backup" "$target"
  exit 1
fi
if command -v xattr >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$target" 2>/dev/null
fi
if [ "$relaunch" = 1 ] && command -v open >/dev/null 2>&1; then
  open "$target"
fi
echo "$(date) 完了"
rm -f "$0"
"""


def schedule_swap(staged: Path, target: Path, relaunch: bool, log_file: Path,
                  pid: int | None = None) -> subprocess.Popen:
    """pid (既定はこのプロセス) の終了後に target を staged で置き換える。"""
    fd, script = tempfile.mkstemp(prefix="head3Dv1-swap-", suffix=".sh")
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write(SWAP_SCRIPT)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    out = open(log_file, "a", encoding="utf-8")         # noqa: SIM115 - 子に渡す
    return subprocess.Popen(
        ["/bin/sh", script, str(pid or os.getpid()), str(target), str(staged),
         "1" if relaunch else "0"],
        stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
        start_new_session=True)
