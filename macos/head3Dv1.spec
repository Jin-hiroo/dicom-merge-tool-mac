# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: upstream/ の元コードを head3Dv1.app にまとめる。

    pyinstaller --clean --noconfirm macos/head3Dv1.spec

環境変数:
    APP_VERSION   CFBundleShortVersionString (既定 1.0.0)
"""
import os
import subprocess
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
UPSTREAM = ROOT / "upstream"
ICON = ROOT / "build" / "icon" / "AppIcon.icns"

APP_NAME = "head3Dv1"
BUNDLE_ID = "io.github.jin-hiroo.head3dv1"
VERSION = os.environ.get("APP_VERSION", "1.0.0")

if not (UPSTREAM / "app" / "main.py").exists():
    raise SystemExit("upstream/ が空です。git submodule update --init を実行してください。")

# collect_submodules("app") が元コードを見つけられるように
sys.path.insert(0, str(UPSTREAM))
sys.path.insert(0, str(ROOT / "macos"))
from check_bundle import inspect as macho_info  # noqa: E402


def upstream_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def minimum_macos(binaries) -> str:
    """同梱バイナリが要求する最も新しい macOS (LC_BUILD_VERSION の minos)。

    依存のホイールはビルドした Mac の OS に合わせて選ばれる (例: SciPy は
    macOS 14 以上なら 14.0 向けのホイールになる) ので、固定値にせず実物から決める。
    """
    newest = (11, 0, 0)                 # Apple Silicon 対応の最初の macOS
    for _dest, src, _kind in binaries:
        info = macho_info(Path(src))
        if info and info[1]:
            newest = max(newest, info[1])
    return f"{newest[0]}.{newest[1]}"


a = Analysis(
    [str(ROOT / "macos" / "launcher.py")],
    pathex=[str(UPSTREAM)],
    binaries=[],
    datas=[],
    hiddenimports=[
        *collect_submodules("app"),
        # `import vtk` は vtkmodules.all 経由で全モジュールを読む
        "vtkmodules.all",
        "vtkmodules.util.numpy_support",
        "vtkmodules.qt.QVTKRenderWindowInteractor",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # requirements にあるがアプリからは使っていないもの
        "skimage",
        "pytest",
        "_pytest",
        # 依存の任意 import で引き込まれがちな大物
        "tkinter",
        "_tkinter",
        "matplotlib",
        "IPython",
        "PyQt5.QtWebEngine",
        "PyQt5.QtWebEngineCore",
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtQml",
        "PyQt5.QtQuick",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch="arm64" if sys.platform == "darwin" else None,
    codesign_identity=None,          # アドホック署名
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    min_macos = minimum_macos(a.binaries)
    print(f"LSMinimumSystemVersion: {min_macos} (同梱バイナリの minos から決定)")
    usage = ("DICOM フォルダの読み込みと、STL / DICOM / セッションの書き出しに使います。"
             "データはこの Mac の中だけで処理され、外部には送信されません。")
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ICON) if ICON.exists() else None,
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleVersion": VERSION,
            "CFBundleShortVersionString": VERSION,
            "CFBundleDevelopmentRegion": "ja",
            "LSApplicationCategoryType": "public.app-category.medical",
            "LSMinimumSystemVersion": min_macos,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "研究・造形補助用。診断用医療機器ではありません。",
            "NSDesktopFolderUsageDescription": usage,
            "NSDocumentsFolderUsageDescription": usage,
            "NSDownloadsFolderUsageDescription": usage,
            "NSRemovableVolumesUsageDescription": usage,
            "NSNetworkVolumesUsageDescription": usage,
            # どの元コードから作ったか (launcher.py がログに出す)
            "Head3DUpstreamCommit": upstream_commit(),
        },
    )
