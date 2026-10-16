"""工作区切换器。

顶部下拉，切换工作区 = 切数据目录 + 切可见域。
切换时关闭所有域窗口、重新挂载面板。
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from core import workspace as ws_mod

logger = logging.getLogger(__name__)


class WorkspaceSwitcher(QWidget):
    """顶部工作区切换条"""

    workspace_changed = pyqtSignal(str)  # workspace_id

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkspaceSwitcher")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        title = QLabel("工作区")
        title.setObjectName("Caption")
        layout.addWidget(title)

        self._combo = QComboBox()
        layout.addWidget(self._combo, stretch=1)

        self._new_btn = QPushButton("新建")
        self._new_btn.clicked.connect(self._on_new_workspace)
        layout.addWidget(self._new_btn)

        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(self._refresh_btn)

        self._combo.currentIndexChanged.connect(self._on_select)

        self.refresh()

    def refresh(self) -> None:
        """重新加载工作区列表"""
        self._combo.blockSignals(True)
        self._combo.clear()
        cur_id = ws_mod.current().meta.id if ws_mod.current() else None
        for meta in ws_mod.manager.list_workspaces():
            label = f"{meta.display_name}  ({meta.id})"
            self._combo.addItem(label, userData=meta.id)
            if meta.id == cur_id:
                self._combo.setCurrentIndex(self._combo.count() - 1)
        self._combo.blockSignals(False)

    def _on_select(self, idx: int) -> None:
        if idx < 0:
            return
        wid = self._combo.itemData(idx)
        if not wid:
            return
        try:
            ws_mod.manager.switch(wid)
            self.workspace_changed.emit(wid)
        except Exception as e:  # noqa: BLE001
            logger.error("switch workspace failed: %s", e)
            QMessageBox.critical(self, "切换失败", str(e))

    def _on_new_workspace(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建工作区", "工作区显示名：")
        if not ok or not name.strip():
            return
        wid = name.strip().replace(" ", "_").lower()
        try:
            ws_mod.manager.create(wid, name.strip())
            ws_mod.manager.switch(wid)
            self.refresh()
            self.workspace_changed.emit(wid)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "创建失败", str(e))
