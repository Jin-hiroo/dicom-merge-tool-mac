"""head3Dv1.app のエントリポイント。

元リポジトリ (upstream/) のコードには一切手を入れず、起動前に次の 2 点だけを
macOS アプリ向けに差し替えてから ``app.main.main()`` を呼ぶ。

1. 書込先
   元コードは ``app/config.py`` の位置を基準に ``scratch/`` と ``sessions/`` を
   作る。.app の中に数 GB の memmap を書くと署名が壊れ、/Applications や
   App Translocation 下では書込自体ができないので、
   ``~/Library/Application Support/head3Dv1/`` に移す。
   ``app.core.session`` は import 時に ``config.PROJECT_ROOT`` から
   ``SESSIONS_DIR`` を確定するため、**session が import される前に**
   ``app.config`` の属性を差し替える必要がある (configure_paths)。

2. ログ
   元コードは stderr にしかログを出さず、Finder から起動すると何も残らない。
   root logger に先にファイルハンドラを付けておく (以降の basicConfig は no-op)。

``--self-test`` はビルドしたバンドルの検証用 (build.sh / CI から呼ぶ)。
"""
from __future__ import annotations

import argparse
import faulthandler
import logging
import logging.handlers
import os
import plistlib
import shutil
import sys
import tempfile
from pathlib import Path

APP_ID = "head3Dv1"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_DATEFMT = "%H:%M:%S"

FROZEN = getattr(sys, "frozen", False)
HERE = Path(__file__).resolve().parent

if not FROZEN:
    # 開発時はサブモジュールの元コードをそのまま import する
    sys.path.insert(0, str(HERE.parent / "upstream"))

log = logging.getLogger(APP_ID)


# ---------------------------------------------------------------------------
# 書込先
# ---------------------------------------------------------------------------
def default_data_dir() -> Path:
    """セッション・scratch の置き場。iCloud 同期されない Application Support。"""
    env = os.environ.get("HEAD3DV1_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Library" / "Application Support" / APP_ID


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / APP_ID


def configure_paths(data_dir: Path) -> Path:
    """元コードの書込先を data_dir 配下に向ける。

    必ず ``app.core.session`` (と、それを import する ``app.main``) より前に呼ぶこと。
    """
    if "app.core.session" in sys.modules:
        raise RuntimeError("app.core.session が既に import されています。"
                           "configure_paths() はそれより前に呼んでください。")
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    from app import config
    config.PROJECT_ROOT = data_dir                # -> sessions/ (session.py)
    config.SCRATCH_DIR = data_dir / "scratch"     # -> memmap の実体
    return data_dir


# ---------------------------------------------------------------------------
# ログ
# ---------------------------------------------------------------------------
def configure_logging() -> Path:
    folder = log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{APP_ID}.log"

    fmt = logging.Formatter(LOG_FORMAT, LOG_DATEFMT)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=5 * 1024**2, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)

    # SIGBUS などネイティブのクラッシュもログに残す (ファイルは開いたままにする)
    crash = open(folder / "crash.log", "a", encoding="utf-8")   # noqa: SIM115
    faulthandler.enable(file=crash, all_threads=True)

    # Finder 起動では stderr が見えないので、未捕捉例外はログへ。
    # (既定の excepthook のままだと PyQt5 はスロット内の例外でアプリを即終了させる)
    def excepthook(exc_type, exc, tb):
        log.critical("未捕捉の例外", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook
    return path


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


# ---------------------------------------------------------------------------
# セルフテスト (ビルド検証用)
# ---------------------------------------------------------------------------
def _check(cond: bool, message: str):
    if not cond:
        raise AssertionError(message)
    log.info("OK  %s", message)


def self_test(data_dir: Path, dicom: Path | None, gui: bool) -> int:
    import pkgutil

    import app
    from app import config

    names = sorted(m.name for m in pkgutil.walk_packages(app.__path__, "app."))
    for name in names:
        __import__(name)
    _check("app.ui.main_window" in names and "app.core.session" in names,
           f"app パッケージの全モジュールを import ({len(names)} 件)")

    from app.core import session
    for label, path in (("scratch", config.SCRATCH_DIR),
                        ("sessions", session.SESSIONS_DIR),
                        ("autosave", session.AUTOSAVE_DIR)):
        _check(Path(path).resolve().is_relative_to(data_dir),
               f"{label} の書込先がデータ置き場の配下: {path}")
    if FROZEN:
        bundle = Path(sys.executable).resolve().parents[2]
        _check(not data_dir.is_relative_to(bundle),
               f"データ置き場がバンドルの外: {data_dir}")
    config.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    probe = config.SCRATCH_DIR / ".write_probe"
    probe.write_bytes(b"ok")
    probe.unlink()
    _check(True, "scratch に書込可能")

    if dicom is not None:
        from app.core import dicom_io
        from app.core.export_stl import export_volume_to_stl

        series = dicom_io.scan_folder(dicom)
        _check(len(series) > 0, f"DICOM シリーズを検出 ({len(series)} 本)")
        volume = dicom_io.load_series(series[0], config.SCRATCH_DIR)
        _check(volume is not None and volume.array.size > 0,
               f"シリーズを読込 shape={volume.shape_xyz}")
        _check(volume.path is not None
               and Path(volume.path).resolve().is_relative_to(data_dir),
               f"memmap がデータ置き場に作られた: {volume.path}")
        stats = export_volume_to_stl(volume, data_dir / "selftest.stl",
                                     threshold=250)
        _check(stats["triangles"] > 0 and stats["file_bytes"] > 84,
               f"STL を書出し ({stats['triangles']} 三角形)")

    if gui:
        _gui_self_test()
    log.info("セルフテスト成功")
    return 0


def _gui_self_test():
    """app/main.py と同じ手順でメインウィンドウを出し、VTK が描画できるか確かめる。"""
    import numpy as np
    import vtk
    from PyQt5 import QtCore, QtWidgets
    from vtkmodules.util import numpy_support

    from app import config
    from app.ui.main_window import DARK_QSS, MainWindow

    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts, True)
    qapp = QtWidgets.QApplication([sys.argv[0]])
    qapp.setApplicationName(config.APP_NAME)
    qapp.setStyleSheet(DARK_QSS)
    _check(qapp.platformName() != "", f"Qt プラットフォーム: {qapp.platformName()}")

    window = MainWindow()
    window.show()
    window.viewer3d.initialize()
    for _ in range(20):
        qapp.processEvents()

    render_window = window.viewer3d.interactor.GetRenderWindow()
    render_window.Render()
    grabber = vtk.vtkWindowToImageFilter()
    grabber.SetInput(render_window)
    grabber.ReadFrontBufferOff()
    grabber.Update()
    image = grabber.GetOutput()
    pixels = numpy_support.vtk_to_numpy(image.GetPointData().GetScalars())
    _check(pixels.size > 0 and int(np.max(pixels)) > 0,
           f"VTK が描画できた ({image.GetDimensions()[0]}x{image.GetDimensions()[1]})")

    QtCore.QTimer.singleShot(1500, window.close)
    QtCore.QTimer.singleShot(2000, qapp.quit)
    qapp.exec_()
    _check(True, "メインウィンドウを開いて閉じた")


# ---------------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(prog=APP_ID, add_help=True)
    parser.add_argument("--self-test", action="store_true",
                        help="バンドルの検証を行って終了する")
    parser.add_argument("--dicom", type=Path,
                        help="--self-test で読込・STL 書出しまで試す DICOM フォルダ")
    parser.add_argument("--no-gui", action="store_true",
                        help="--self-test でウィンドウを開かない")
    # macOS が付ける -psn_0_xxxx などは Qt 側に任せる
    args, _ = parser.parse_known_args(argv)
    return args


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = parse_args(argv[1:])

    temp_data = None
    if args.self_test:
        # ユーザーの設定・セッションに触れない
        os.environ["HEAD3DV1_NO_RESTORE"] = "1"
        os.environ["HEAD3DV1_SETTINGS_SCOPE"] = "selftest"
        if not os.environ.get("HEAD3DV1_DATA_DIR"):
            temp_data = Path(tempfile.mkdtemp(prefix="head3Dv1-selftest-"))
            os.environ["HEAD3DV1_DATA_DIR"] = str(temp_data)

    log_path = configure_logging()
    data_dir = configure_paths(default_data_dir())
    info = build_info()
    log.info("head3Dv1 %s (upstream %s) data=%s log=%s",
             info["version"], info["upstream"], data_dir, log_path)

    if args.self_test:
        try:
            return self_test(data_dir, args.dicom, gui=not args.no_gui)
        except Exception:                               # noqa: BLE001
            log.exception("セルフテスト失敗")
            return 1
        finally:
            if temp_data is not None:
                shutil.rmtree(temp_data, ignore_errors=True)

    from app.main import main as app_main
    return app_main(argv)


if __name__ == "__main__":
    sys.exit(main())
