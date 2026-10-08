"""アプリ版の基本情報 (launcher.py と updater_ui.py で共有)。"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

APP_ID = "head3Dv1"
FROZEN = getattr(sys, "frozen", False)


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / APP_ID


def cache_dir() -> Path:
    return Path.home() / "Library" / "Caches" / APP_ID


def build_info() -> dict:
    """Info.plist (frozen 時) から版数と元コードのコミットを読む。"""
    if not FROZEN:
        return {"version": "dev", "upstream": "working tree"}
    plist = Path(sys.executable).resolve().parent.parent / "Info.plist"
    try:
        with open(plist, "rb") as fp:
            info = plistlib.load(fp)
    except OSError:
        return {"version": "?", "upstream": "?"}
    return {"version": info.get("CFBundleShortVersionString", "?"),
            "upstream": info.get("Head3DUpstreamCommit", "?")}
