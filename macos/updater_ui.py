"""アプリメニューの「head3Dv1 について」「アップデートを確認…」。

macOS では QAction の menuRole により、Qt がこれらを自動的に
アプリケーションメニュー (画面左上の「head3Dv1」) へ移す。
通信・展開は QThread で行い、UI は止めない。
"""
from __future__ import annotations

import logging

from PyQt5 import QtCore, QtGui, QtWidgets

import updater
from appinfo import build_info, cache_dir, log_dir

log = logging.getLogger("head3Dv1")


class _Job(QtCore.QThread):
    """fn(progress_cb, cancel_cb) を別スレッドで実行する。"""
    progress = QtCore.pyqtSignal(int)
    succeeded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def run(self):
        try:
            self.succeeded.emit(self._fn(self.progress.emit, lambda: self._cancel))
        except updater.UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                        # noqa: BLE001
            log.exception("アップデート処理で予期しないエラー")
            self.failed.emit(f"予期しないエラーが発生しました。\n({exc})")


class UpdateController(QtCore.QObject):
    def __init__(self, window: QtWidgets.QMainWindow):
        super().__init__(window)
        self.window = window
        self.info = build_info()
        self._job: _Job | None = None
        self._dialog: QtWidgets.QProgressDialog | None = None
        # 実行中のスレッドを残したまま終了すると Qt が異常終了するので、止めてから終わる
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._stop)

    def _stop(self):
        if self._job is not None:
            self._job.cancel()
            self._job.wait(30_000)

    # -- 共通 --------------------------------------------------------------
    @property
    def _ua(self) -> str:
        return f"head3Dv1-updater/{self.info['version']}"

    def _run(self, label: str, fn, on_success, determinate: bool):
        dialog = QtWidgets.QProgressDialog(label, "キャンセル", 0,
                                           100 if determinate else 0, self.window)
        dialog.setWindowTitle("アップデート")
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        job = _Job(fn, self)
        job.progress.connect(dialog.setValue)
        job.succeeded.connect(lambda result: self._finish(on_success, result))
        job.failed.connect(lambda message: self._finish(None, message))
        dialog.canceled.connect(job.cancel)
        self._job, self._dialog = job, dialog
        dialog.show()
        job.start()

    def _finish(self, on_success, payload):
        dialog, self._dialog = self._dialog, None
        job, self._job = self._job, None
        cancelled = job is not None and job.cancelled
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        if job is not None:
            job.wait()
            job.deleteLater()
        if cancelled:
            return
        if on_success is None:
            QtWidgets.QMessageBox.warning(self.window, "アップデート", payload)
        else:
            on_success(payload)

    # -- メニュー ------------------------------------------------------------
    def about(self):
        from app import config
        QtWidgets.QMessageBox.about(
            self.window, "head3Dv1 について",
            f"head3Dv1 バージョン {self.info['version']}\n"
            f"元コード: dicom-merge-tool @ {self.info['upstream'][:7]}\n\n"
            f"{config.DISCLAIMER}")

    def check(self):
        if self._job is not None:
            return
        self._run("アップデートを確認しています…",
                  lambda progress, cancel: updater.fetch_latest(self.info["version"]),
                  self._on_checked, determinate=False)

    # -- 確認 → ダウンロード → 入れ替え ---------------------------------------
    def _on_checked(self, release: updater.Release):
        current = self.info["version"]
        if not updater.is_newer(release.version, current):
            QtWidgets.QMessageBox.information(
                self.window, "アップデート",
                f"head3Dv1 は最新です (バージョン {current})。")
            return

        box = QtWidgets.QMessageBox(self.window)
        box.setIcon(QtWidgets.QMessageBox.Information)
        box.setWindowTitle("アップデート")
        box.setText(f"新しいバージョン {release.version} があります "
                    f"(現在 {current})。")
        # 詳細表示 (setDetailedText) のボタンは Qt の英語表記のままになるので本文に含める
        notes = release.notes if len(release.notes) <= 600 else release.notes[:600] + " …"
        box.setInformativeText(
            (f"変更点:\n{notes}\n\n" if notes else "")
            + "ダウンロードしてインストールしますか？\n"
            f"ダウンロード量は約 {release.dmg_size / 1024**2:.0f} MB です。"
            "作業中の内容は終了時に自動保存され、再起動後に復元できます。")
        install = box.addButton("インストール", QtWidgets.QMessageBox.AcceptRole)
        page = box.addButton("リリースページ", QtWidgets.QMessageBox.HelpRole)
        box.addButton("あとで", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(install)
        box.exec_()
        if box.clickedButton() is page:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(release.page_url))
        elif box.clickedButton() is install:
            self._install(release)

    def _install(self, release: updater.Release):
        target = updater.running_app()
        problem = updater.install_problem(target)
        if problem:
            answer = QtWidgets.QMessageBox.warning(
                self.window, "アップデート", problem,
                QtWidgets.QMessageBox.Open | QtWidgets.QMessageBox.Close)
            if answer == QtWidgets.QMessageBox.Open:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(release.page_url))
            return

        dmg = cache_dir() / "updates" / release.dmg_name

        def work(progress, cancel):
            path = updater.download(release, dmg, self._ua, progress, cancel)
            if path is None:
                return None
            progress(100)
            try:
                return updater.stage_from_dmg(path, target, release.version)
            finally:
                path.unlink(missing_ok=True)

        self._run(f"head3Dv1 {release.version} をダウンロードしています…", work,
                  lambda staged: self._on_staged(staged, target, release),
                  determinate=True)

    def _on_staged(self, staged, target, release: updater.Release):
        if staged is None:
            return
        busy = getattr(getattr(self.window, "runner", None), "busy", False)
        box = QtWidgets.QMessageBox(self.window)
        box.setIcon(QtWidgets.QMessageBox.Information)
        box.setWindowTitle("アップデート")
        box.setText(f"head3Dv1 {release.version} をインストールする準備ができました。")
        if busy:
            box.setInformativeText("処理の実行中です。処理が終わってからアプリを終了すると、"
                                   "終了時に新しいバージョンへ入れ替わります。")
            now = None
        else:
            box.setInformativeText("今すぐ再起動して更新しますか？\n"
                                   "「終了時に更新」を選ぶと、次にアプリを終了したときに入れ替わります。")
            now = box.addButton("今すぐ再起動", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("終了時に更新", QtWidgets.QMessageBox.RejectRole)
        if now is not None:
            box.setDefaultButton(now)
        box.exec_()
        relaunch = now is not None and box.clickedButton() is now
        updater.schedule_swap(staged, target, relaunch=relaunch,
                              log_file=log_dir() / "update.log")
        log.info("アップデートを予約しました: %s -> %s (再起動=%s)",
                 staged, target, relaunch)
        if relaunch:
            # 元コードの closeEvent が作業内容を自動保存してから終了する
            self.window.close()


def install_update_action(window: QtWidgets.QMainWindow) -> UpdateController:
    controller = UpdateController(window)
    menus = [a.menu() for a in window.menuBar().actions() if a.menu() is not None]
    menu = menus[0] if menus else window.menuBar().addMenu("ヘルプ")

    about = QtWidgets.QAction("head3Dv1 について", window)
    about.setMenuRole(QtWidgets.QAction.AboutRole)
    about.triggered.connect(controller.about)

    check = QtWidgets.QAction("アップデートを確認…", window)
    check.setMenuRole(QtWidgets.QAction.ApplicationSpecificRole)
    check.triggered.connect(controller.check)

    menu.addSeparator()
    menu.addAction(about)
    menu.addAction(check)
    window._update_controller = controller          # GC されないように保持
    return controller
