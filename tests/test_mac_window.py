"""mac_window.py: ツールバーの折り返しと、ウィンドウを画面に収める処理。"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "macos"))

# 画面の無い Linux では offscreen で動かす (macOS ではそのまま)
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") \
        and not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

import launcher  # noqa: E402

launcher.configure_paths(Path(tempfile.mkdtemp(prefix="head3Dv1-test-")))
os.environ["HEAD3DV1_NO_RESTORE"] = "1"
os.environ["HEAD3DV1_SETTINGS_SCOPE"] = "test-mac-window"

from PyQt5 import QtCore, QtWidgets  # noqa: E402

import mac_window  # noqa: E402
from mac_window import FlowLayout, fit_to_screen  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    QtCore.QSettings("head3Dv1", "test-mac-window").clear()


def _buttons(n, width=100):
    host = QtWidgets.QWidget()
    flow = FlowLayout(host)
    flow.setContentsMargins(0, 0, 0, 0)
    for i in range(n):
        b = QtWidgets.QPushButton(f"b{i}")
        b.setFixedWidth(width)
        flow.add_widget(b, gap=10)
    return host, flow


def test_flow_layout_sizes(qapp):
    host, flow = _buttons(5)
    assert flow.sizeHint().width() == 5 * 100 + 4 * 10       # 1 行に並べた幅
    assert flow.minimumSize().width() == 100                 # 1 項目が入れば良い
    one_row = flow.heightForWidth(600)
    two_rows = flow.heightForWidth(320)                       # 3 個 + 2 個
    five_rows = flow.heightForWidth(100)
    assert one_row < two_rows < five_rows


def test_flow_layout_places_items_in_rows(qapp):
    host, flow = _buttons(5)
    flow.setGeometry(QtCore.QRect(0, 0, 320, flow.heightForWidth(320)))
    rows = sorted({flow.itemAt(i).geometry().y() for i in range(flow.count())})
    assert len(rows) == 2
    for i in range(flow.count()):
        assert flow.itemAt(i).geometry().right() < 320          # はみ出さない


def test_fit_to_screen_clamps_oversized_window(qapp):
    window = QtWidgets.QMainWindow()
    window.resize(9000, 9000)
    window.move(-500, -500)
    fit_to_screen(window)
    avail = qapp.primaryScreen().availableGeometry()
    assert window.width() <= avail.width()
    assert window.height() <= avail.height()
    assert window.x() >= avail.left() and window.y() >= avail.top()


@pytest.mark.skipif(os.environ.get("QT_QPA_PLATFORM") == "offscreen",
                    reason="元コードのメインウィンドウ (VTK) は実画面が必要")
def test_main_window_toolbar_wraps_and_still_works(qapp):
    from app.ui.main_window import MainWindow

    window = MainWindow()
    before = window.minimumSizeHint().width()
    controls = [window.show_fixed, window.show_moving, window.moving_opacity,
                window.clip_check, window.clip_axis, window.clip_slider]

    assert mac_window.make_view_toolbar_wrap(window)
    bar = window.clip_check.parentWidget().parentWidget()
    assert isinstance(bar.layout(), FlowLayout)
    assert all(bar.isAncestorOf(c) for c in controls)        # 部品は 1 つも失われない
    assert window.minimumSizeHint().width() < min(before, 1200)

    # 元コードのシグナル接続が生きている (断面チェックでスライダーが有効になる)
    assert not window.clip_slider.isEnabled()
    window.clip_check.setChecked(True)
    assert window.clip_slider.isEnabled()
    window.close()
