"""head3Dv1.app のエントリポイント。

元リポジトリ (upstream/) のコードには一切手を入れず、起動前に次の 3 点だけを
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

3. メインウィンドウ
   ``app.main`` が作るウィンドウを ``mac_window.MacMainWindow`` (元の MainWindow の
   サブクラス) に替える。小さい画面でもはみ出さないようにし、アプリメニューに
   「アップデートを確認…」を足す。

``--self-test`` / ``--self-test-update`` はビルドしたバンドルの検証用 (build.sh / CI から呼ぶ)。
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

from appinfo import APP_ID, FROZEN, build_info, log_dir

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_DATEFMT = "%H:%M:%S"

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
    from app.ui.main_window import DARK_QSS
    from mac_window import FlowLayout, MacMainWindow

    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts, True)
    qapp = QtWidgets.QApplication([sys.argv[0]])
    qapp.setApplicationName(config.APP_NAME)
    qapp.setStyleSheet(DARK_QSS)
    _check(qapp.platformName() != "", f"Qt プラットフォーム: {qapp.platformName()}")

    window = MacMainWindow()
    window.show()
    window.viewer3d.initialize()
    for _ in range(20):
        qapp.processEvents()

    bar = window.clip_check.parentWidget().parentWidget()
    _check(isinstance(bar.layout(), FlowLayout), "3D ビューのツールバーが折り返し可能")
    min_width = window.minimumSizeHint().width()
    _check(min_width <= 1100, f"ウィンドウの最小幅 {min_width}px (13 インチの画面に収まる)")
    avail = (window.screen() or qapp.primaryScreen()).availableGeometry()
    frame = window.frameGeometry()
    # 最小幅より狭い画面 (CI の仮想ディスプレイなど) では最小幅までは許す
    _check(frame.width() <= max(avail.width(), min_width)
           and frame.height() <= avail.height(),
           f"ウィンドウが画面に収まる ({frame.width()}x{frame.height()} / "
           f"画面 {avail.width()}x{avail.height()})")
    titles = [a.text() for m in window.menuBar().findChildren(QtWidgets.QMenu)
              for a in m.actions()]
    _check("アップデートを確認…" in titles and "head3Dv1 について" in titles,
           "メニューに「head3Dv1 について」「アップデートを確認…」がある")

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


def update_self_test(dmg: Path | None) -> int:
    """GitHub への TLS 接続と、DMG からの入れ替え一式を確かめる (CI 用)。

    入れ替えは実行中の .app ではなく一時フォルダに複製したものに対して行う。
    """
    import ssl
    import subprocess

    import updater

    version = build_info()["version"]
    try:
        release = updater.fetch_latest(version)
        _check(True, f"GitHub の最新リリースを取得: {release.tag}")
    except updater.NoRelease:
        _check(True, "GitHub に接続できた (公開リリースはまだ無い)")
    except updater.UpdateError as exc:
        reason = getattr(exc.__cause__, "reason", exc.__cause__)
        _check(not isinstance(reason, ssl.SSLError), f"TLS 証明書の検証: {reason}")
        log.warning("GitHub に接続できませんでした (ネットワークの問題として続行): %s", exc)

    if dmg is None:
        log.info("アップデートのセルフテスト成功")
        return 0
    app = updater.running_app()
    _check(app is not None, f"実行中の .app: {app}")
    work = Path(tempfile.mkdtemp(prefix="head3Dv1-update-test-"))
    try:
        target = work / "Applications" / "head3Dv1.app"
        target.parent.mkdir()
        subprocess.run(["ditto", str(app), str(target)], check=True)
        _check(updater.install_problem(target) is None, "入れ替え先に書き込める")
        staged = updater.stage_from_dmg(dmg, target, version)
        _check(staged.is_dir(), f"DMG を展開して検証: {staged.name}")
        waiter = subprocess.Popen(["sleep", "1"])
        swap = updater.schedule_swap(staged, target, relaunch=False,
                                     log_file=work / "update.log", pid=waiter.pid)
        waiter.wait()
        swap.wait(timeout=120)
        _check(swap.returncode == 0, "旧版の終了を待ってから入れ替えた")
        leftovers = [p.name for p in target.parent.iterdir() if p != target]
        _check(not leftovers, f"一時ファイルが残っていない {leftovers}")
        with open(target / "Contents" / "Info.plist", "rb") as fp:
            got = plistlib.load(fp)["CFBundleShortVersionString"]
        _check(got == version, f"入れ替え後のバージョン: {got}")
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(target)],
                       check=True)
        _check(True, "入れ替え後の署名が有効")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info("アップデートのセルフテスト成功")
    return 0


# ---------------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(prog=APP_ID, add_help=True)
    parser.add_argument("--self-test", action="store_true",
                        help="バンドルの検証を行って終了する")
    parser.add_argument("--dicom", type=Path,
                        help="--self-test で読込・STL 書出しまで試す DICOM フォルダ")
    parser.add_argument("--no-gui", action="store_true",
                        help="--self-test でウィンドウを開かない")
    parser.add_argument("--self-test-update", action="store_true",
                        help="アップデート機能の検証を行って終了する")
    parser.add_argument("--dmg", type=Path,
                        help="--self-test-update で入れ替えを試す DMG")
    # macOS が付ける -psn_0_xxxx などは Qt 側に任せる
    args, _ = parser.parse_known_args(argv)
    return args


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = parse_args(argv[1:])

    temp_data = None
    if args.self_test or args.self_test_update:
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

    if args.self_test_update:
        try:
            return update_self_test(args.dmg)
        except Exception:                               # noqa: BLE001
            log.exception("アップデートのセルフテスト失敗")
            return 1

    if args.self_test:
        try:
            return self_test(data_dir, args.dicom, gui=not args.no_gui)
        except Exception:                               # noqa: BLE001
            log.exception("セルフテスト失敗")
            return 1
        finally:
            if temp_data is not None:
                shutil.rmtree(temp_data, ignore_errors=True)

    # 元コードの起動処理はそのまま使い、作るウィンドウだけ Mac 版に差し替える
    import app.main as app_main
    from mac_window import MacMainWindow
    app_main.MainWindow = MacMainWindow
    return app_main.main(argv)


if __name__ == "__main__":
    sys.exit(main())
