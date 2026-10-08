"""macOS アプリ版のメインウィンドウ。元コードの MainWindow を継承して最小限だけ足す。

元コードには手を入れず、launcher.py が ``app.main.MainWindow`` をこのクラスに
差し替える。足しているのは次の 3 点だけ。

1. 3D ビュー上のツールバーを折り返せるようにする
   元コードのツールバーは固定幅のボタン・スライダーを 1 行に並べるため最小幅が
   約 970px あり、左右パネルの最小幅と合わせるとウィンドウが 1600px 未満に
   縮められない。13/14 インチの MacBook (1470/1512pt) では右パネルが画面外に
   はみ出していた。幅が足りないときは 2 行目に折り返す。
2. ウィンドウを画面内に収める
   前回終了時の位置・サイズ (QSettings) が今の画面より大きい場合に備える。
3. 「アップデートを確認…」メニュー (updater.py)
"""
from __future__ import annotations

import logging
import sys

from PyQt5 import QtCore, QtWidgets

from app.ui.main_window import MainWindow
from updater_ui import install_update_action

log = logging.getLogger("head3Dv1")


class FlowLayout(QtWidgets.QLayout):
    """幅が足りなければ次の行に折り返すレイアウト。項目ごとに手前の間隔を持つ。"""

    def __init__(self, parent=None, vspacing: int = 2):
        super().__init__(parent)
        self._items: list[QtWidgets.QLayoutItem] = []
        self._gaps: list[int] = []
        self._vspacing = vspacing

    # -- QLayout の必須メソッド ---------------------------------------------
    def addItem(self, item):                                   # noqa: N802
        self._items.append(item)
        self._gaps.append(self.spacing() if self.spacing() >= 0 else 4)

    def add_widget(self, widget: QtWidgets.QWidget, gap: int):
        self.addWidget(widget)
        self._gaps[-1] = gap

    def count(self):
        return len(self._items)

    def itemAt(self, index):                                   # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):                                   # noqa: N802
        if 0 <= index < len(self._items):
            self._gaps.pop(index)
            return self._items.pop(index)
        return None

    def expandingDirections(self):                             # noqa: N802
        return QtCore.Qt.Orientations(0)

    def hasHeightForWidth(self):                               # noqa: N802
        return True

    def heightForWidth(self, width):                           # noqa: N802
        return self._layout(QtCore.QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):                               # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self):                                        # noqa: N802
        """1 行に並べたときの大きさ (幅に余裕があれば元の見た目と同じになる)。"""
        m = self.contentsMargins()
        width = sum(item.sizeHint().width() for item in self._items)
        width += sum(self._gaps[1:])
        height = max((item.sizeHint().height() for item in self._items), default=0)
        return QtCore.QSize(width + m.left() + m.right(), height + m.top() + m.bottom())

    def minimumSize(self):                                     # noqa: N802
        """最も幅の広い 1 項目が収まれば良い。"""
        m = self.contentsMargins()
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect: QtCore.QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item, gap in zip(self._items, self._gaps):
            hint = item.sizeHint()
            if line_height and x + gap + hint.width() > area.right() + 1:
                x, y, line_height = area.x(), y + line_height + self._vspacing, 0
            elif line_height:
                x += gap
            if apply:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width()
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


def make_view_toolbar_wrap(window: MainWindow) -> bool:
    """3D ビュー上のツールバーを、幅が足りないときに折り返すようにする。

    視点ボタン (前/後/…/全体表示) は 1 つずつ折り返してよいが、
    [Fixed Moving 透過━] と [断面 Z ━] はラベルとスライダーの組なので塊のまま扱う。
    塊の先頭は元コードの公開属性 show_fixed / clip_check で判定する。
    元コードの作りが変わっていたら何もしない (折り返さないだけで動作には影響しない)。
    """
    starts = [getattr(window, name, None) for name in ("show_fixed", "clip_check")]
    if not all(isinstance(w, QtWidgets.QWidget) for w in starts):
        log.warning("ツールバーの構成が想定と異なるため折り返しを無効にします")
        return False
    bar = starts[0].parentWidget()
    row = bar.layout() if bar is not None else None
    if not isinstance(row, QtWidgets.QHBoxLayout) or starts[1].parentWidget() is not bar:
        log.warning("ツールバーの構成が想定と異なるため折り返しを無効にします")
        return False

    spacing = row.spacing()
    flow = FlowLayout()
    flow.setContentsMargins(row.contentsMargins())
    group_layout: QtWidgets.QHBoxLayout | None = None
    pending = 0                           # 直前の addSpacing の合計
    while row.count():
        item = row.takeAt(0)
        widget = item.widget()
        if widget is None:
            spacer = item.spacerItem()
            if spacer is not None and not (
                    spacer.expandingDirections() & QtCore.Qt.Horizontal):
                pending += spacer.sizeHint().width()
            continue                      # addStretch は捨てる (左詰めは同じ)
        if widget in starts:
            group = QtWidgets.QWidget()
            group_layout = QtWidgets.QHBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(spacing)
            group_layout.addWidget(widget)
            flow.add_widget(group, gap=spacing + pending)
        elif group_layout is not None:
            if pending:
                group_layout.addSpacing(pending)
            group_layout.addWidget(widget)
        else:
            flow.add_widget(widget, gap=spacing + pending)
        pending = 0

    # 元の QHBoxLayout を外してから付け替える (QWidget はレイアウトを 1 つしか持てない)
    QtWidgets.QWidget().setLayout(row)
    bar.setLayout(flow)

    # 祖先のレイアウトは古い最小幅 (約 1600px) を覚えていて、そのまま show() すると
    # ウィンドウがその幅まで広げられてしまう。計算し直させる。
    widget = bar
    while widget is not None:
        if widget.layout() is not None:
            widget.layout().invalidate()
        widget.updateGeometry()
        widget = widget.parentWidget()
    window.layout().activate()
    return True


def fit_to_screen(window: QtWidgets.QWidget):
    """ウィンドウ (タイトルバー込み) が、いま載っている画面の作業領域に収まるようにする。"""
    screen = (QtWidgets.QApplication.screenAt(window.geometry().center())
              or QtWidgets.QApplication.primaryScreen())
    if screen is None:
        return
    avail = screen.availableGeometry()
    # 表示前は枠の大きさが分からないので、macOS のタイトルバー分を見込む
    title = 28 if sys.platform == "darwin" else 0
    width = min(window.width(), avail.width())
    height = min(window.height(), avail.height() - title)
    window.resize(width, height)
    x = min(max(window.x(), avail.left()), avail.left() + avail.width() - width)
    y = min(max(window.y(), avail.top()), avail.top() + avail.height() - height - title)
    window.move(x, y)


class MacMainWindow(MainWindow):
    def __init__(self):
        super().__init__()                # 元コードがサイズ決定と前回位置の復元まで行う
        make_view_toolbar_wrap(self)
        fit_to_screen(self)
        install_update_action(self)
