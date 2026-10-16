"""会话 + 任务卡中心。

- 会话列表（来自 chat 域）
- 任务卡列表（来自 proposal_gate 的 pending 池）
- 双击任务卡 → window_manager 拉起对应域窗口并跳转到该任务
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core import proposal_gate as gate_mod
from core import workspace as ws_mod

logger = logging.getLogger(__name__)


class SessionHub(QDockWidget):
    """底部 dock：会话 + 任务卡"""

    task_card_activated = pyqtSignal(str, str)  # domain, proposal_id

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("任务卡", parent)
        self.setObjectName("SessionHub")
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左：会话列表（来自 chat 域，目前占位）
        self._session_list = QListWidget()
        self._session_list.setObjectName("SessionList")
        sess_label = QLabel("会话")
        sess_label.setObjectName("Caption")
        sess_wrap = QWidget()
        sl = QVBoxLayout(sess_wrap)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(sess_label)
        sl.addWidget(self._session_list)
        splitter.addWidget(sess_wrap)

        # 右：任务卡列表（pending 池）
        self._task_list = QListWidget()
        self._task_list.setObjectName("TaskCardList")
        self._task_list.itemDoubleClicked.connect(self._on_card_double_click)
        task_label = QLabel("待审核任务卡")
        task_label.setObjectName("Caption")
        actions = QHBoxLayout()
        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_apply = QPushButton("通过")
        self._btn_apply.clicked.connect(self._on_apply)
        self._btn_reject = QPushButton("拒绝")
        self._btn_reject.clicked.connect(self._on_reject)
        actions.addWidget(task_label)
        actions.addStretch(1)
        actions.addWidget(self._btn_refresh)
        actions.addWidget(self._btn_apply)
        actions.addWidget(self._btn_reject)
        actions_wrap = QWidget()
        al = QVBoxLayout(actions_wrap)
        al.setContentsMargins(0, 0, 0, 0)
        al.addLayout(actions)
        al.addWidget(self._task_list)
        splitter.addWidget(actions_wrap)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        self.setWidget(container)

    def refresh(self) -> None:
        """刷新任务卡列表"""
        self._task_list.clear()
        ws = ws_mod.current()
        if ws is None:
            return
        gate = gate_mod.for_workspace(ws.root)
        for proposal in gate.list_pending():
            label = f"[{proposal.domain}] {proposal.title or proposal.id}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (proposal.domain, proposal.id))
            self._task_list.addItem(item)

    def _on_card_double_click(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        domain, proposal_id = data
        self.task_card_activated.emit(domain, proposal_id)

    def _on_apply(self) -> None:
        item = self._task_list.currentItem()
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        domain, proposal_id = data
        ws = ws_mod.current()
        if ws is None:
            return
        gate = gate_mod.for_workspace(ws.root)
        result = gate.apply(proposal_id)
        if result.success:
            self.refresh()
        else:
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self, "未通过", result.error or "守卫拦截"
            )

    def _on_reject(self) -> None:
        item = self._task_list.currentItem()
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        domain, proposal_id = data
        ws = ws_mod.current()
        if ws is None:
            return
        from PyQt6.QtWidgets import QInputDialog

        reason, ok = QInputDialog.getText(self, "拒绝理由", "请输入拒绝理由：")
        if not ok:
            return
        gate = gate_mod.for_workspace(ws.root)
        gate.reject(proposal_id, reason)
        self.refresh()
