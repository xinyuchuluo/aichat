"""可换行的流式布局（PyQt6 版，移植自 Qt 官方 FlowLayout 示例）。

用于在窄容器内放置多个按钮：空间不足时自动换行，而不是把最右侧
按钮挤出/裁切（clip）。这样无论窗口多窄，按钮文字都完整可见。
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, margin: int = 0, spacing: int = -1):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing if spacing >= 0 else 4)
        self._items: list[QLayoutItem] = []  # type: ignore[name-defined]

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def addWidget(self, widget: QWidget):  # noqa: N802
        super().addWidget(widget)

    def _item_at(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def itemAt(self, index: int):  # noqa: N802
        return self._item_at(index)

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def count(self):  # noqa: N802
        return len(self._items)

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect):  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            widget = item.widget()
            if widget is None or widget.isHidden():
                continue
            item_size = item.sizeHint()
            w = item_size.width()
            h = item_size.height()
            next_x = x + w + spacing
            if next_x - spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + spacing
                next_x = x + w + spacing
            if apply:
                item.setGeometry(QRect(x, y, w, h))
            x = next_x
            line_height = max(line_height, h)

        return y + line_height - effective.y() + m.top() + m.bottom()


class _DummyLayoutItem:  # pragma: no cover - 仅类型占位
    minimumSize = lambda self: QSize(0, 0)  # type: ignore
    sizeHint = lambda self: QSize(0, 0)  # type: ignore
    widget = lambda self: None  # type: ignore
    setGeometry = lambda self, *a, **k: None  # type: ignore
