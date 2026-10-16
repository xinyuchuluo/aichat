"""多窗口/模块拉起管理。

每个域可以申请独立窗口（用户双击任务卡 / 点域标题栏的"独立窗口"按钮）。
域通过 DomainManifest + Facet 声明 supports_standalone_window，壳按需挂界面。
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMainWindow, QWidget

logger = logging.getLogger(__name__)


class WindowManager(QObject):
    """管理所有独立窗口"""

    window_opened = pyqtSignal(str)   # window_id
    window_closed = pyqtSignal(str)   # window_id

    def __init__(self) -> None:
        super().__init__()
        # window_id → QMainWindow
        self._windows: dict[str, QMainWindow] = {}

    def open_domain_window(
        self,
        domain: str,
        widget: QWidget,
        *,
        title: str = "",
        width: int = 1200,
        height: int = 800,
    ) -> str:
        """为某个域拉起独立窗口，返回 window_id"""
        window_id = f"{domain}__{id(widget)}"
        if window_id in self._windows:
            # 已开：前置
            w = self._windows[window_id]
            w.raise_()
            w.activateWindow()
            return window_id
        win = QMainWindow()
        win.setWindowTitle(title or domain)
        win.resize(width, height)
        win.setCentralWidget(widget)
        win.destroyed.connect(lambda *_: self._on_closed(window_id))
        self._windows[window_id] = win
        win.show()
        self.window_opened.emit(window_id)
        return window_id

    def close_domain_window(self, domain: str) -> None:
        """关闭某域所有独立窗口"""
        to_close = [wid for wid in self._windows if wid.startswith(f"{domain}__")]
        for wid in to_close:
            win = self._windows.pop(wid, None)
            if win is not None:
                win.close()

    def close_all(self) -> None:
        """切换工作区时关闭所有独立窗口"""
        for win in list(self._windows.values()):
            try:
                win.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("close window failed: %s", e)
        self._windows.clear()

    def _on_closed(self, window_id: str) -> None:
        self._windows.pop(window_id, None)
        self.window_closed.emit(window_id)

    def list_open(self) -> list[str]:
        return list(self._windows.keys())


# 模块级单例
_manager: Optional[WindowManager] = None


def get_manager() -> WindowManager:
    global _manager
    if _manager is None:
        _manager = WindowManager()
    return _manager
