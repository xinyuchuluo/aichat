"""会话侧边栏 — 会话列表 + 新建按钮。"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QLabel, QFrame, QComboBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from ..protocol.types import Session
from .styles import current_colors, is_light_theme, apply_skin_widget

# ===== Sidebar 主题感知样式生成器 =====
# 用 %s / %(k)s 字符串格式化（避免 f-string 和 CSS `}` 冲突）
def _sidebar_styles() -> dict:
    c = current_colors()
    light = is_light_theme()
    a = dict(
        accent=c.accent, accent_h=c.accent_hover, mauve=c.mauve,
        text=c.text, sub=c.subtext, base=c.base,
        surf0=c.surface0, surf1=c.surface1, surf2=c.surface2,
        overlay=c.overlay, red=c.red,
    )
    if light:
        return {
            "title": "font-size:16px; font-weight:900; letter-spacing:3px; padding:6px 4px; color:%(accent)s; background:transparent;" % a,
            "new_btn": (
                "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                " stop:0 %(accent)s, stop:1 %(mauve)s);"
                " color:#ffffff; border: none; border-radius: 8px; padding: 8px;"
                " font-size: 13px; font-weight:800; letter-spacing:1px; }"
                " QPushButton:hover { color:#ffffff; }"
            ) % a,
            "sub_label": "font-size: 10px; color: %(sub)s; padding: 6px 2px 2px 2px; letter-spacing:2px; background:transparent;" % a,
            "combo": (
                "QComboBox { padding: 5px 8px; font-size: 12px; background:#ffffff;"
                " color:%(text)s; border:1px solid %(overlay)s; border-radius: 6px; }"
                " QComboBox:hover { border:1px solid %(accent)s; }"
                " QComboBox::drop-down { width: 18px; }"
            ) % a,
            "list": (
                "QListWidget { background: transparent; border: none; outline:0; }"
                " QListWidget::item { padding: 9px 10px; border-radius: 6px;"
                " color:%(text)s; font-size:12px; letter-spacing:0.3px; margin: 2px 0; }"
                " QListWidget::item:hover { background: %(surf0)s; color:%(text)s;"
                " border: 1px solid %(overlay)s; padding: 8px 9px; }"
                " QListWidget::item:selected { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                " stop:0 %(accent)s, stop:1 %(mauve)s);"
                " color:#ffffff; padding: 8px 9px; border: none; }"
            ) % a,
            "del_btn": (
                "QPushButton { background: transparent;"
                " color: %(red)s; border: 1px solid %(overlay)s; border-radius: 6px; padding: 5px;"
                " font-size: 11px; letter-spacing:1px; }"
                " QPushButton:hover { background: %(red)s; color: #ffffff; border: 1px solid %(red)s; }"
            ) % a,
        }
    return {
        "title": "font-size:16px; font-weight:900; letter-spacing:3px; padding:6px 4px; color:%(accent_h)s; background:transparent;" % a,
        "new_btn": (
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 %(accent)s, stop:1 %(mauve)s);"
            " color:#06122a; border: none; border-radius: 8px; padding: 8px;"
            " font-size: 13px; font-weight:900; letter-spacing:1px; }"
            " QPushButton:hover { color:#000000; }"
        ) % a,
        "sub_label": "font-size: 10px; color: %(sub)s; padding: 6px 2px 2px 2px; letter-spacing:2px; background:transparent;" % a,
        "combo": (
            "QComboBox { padding: 5px 8px; font-size: 12px; background:#0a0f20;"
            " color:#c9f7ff; border:1px solid %(surf1)s; border-radius: 6px; }"
            " QComboBox:hover { border:1px solid %(accent)s; }"
            " QComboBox::drop-down { width: 18px; }"
        ) % a,
        "list": (
            "QListWidget { background: transparent; border: none; outline:0; }"
            " QListWidget::item { padding: 9px 10px; border-radius: 6px;"
            " color:%(text)s; font-size:12px; letter-spacing:0.3px; margin: 2px 0; }"
            " QListWidget::item:hover { background: rgba(0,229,255,0.06);"
            " color:#d7f9ff; border: 1px solid %(surf1)s; padding: 8px 9px; }"
            " QListWidget::item:selected { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 rgba(0,229,255,0.22), stop:1 rgba(181,107,255,0.22));"
            " color:#ffffff; border: 1px solid %(accent)s; padding: 8px 9px; }"
        ) % a,
        "del_btn": (
            "QPushButton { background: transparent; color: %(red)s;"
            " border: 1px solid %(surf2)s; border-radius: 6px; padding: 5px;"
            " font-size: 11px; letter-spacing:1px; }"
            " QPushButton:hover { background: %(red)s; color: #ffffff; border: 1px solid %(red)s; }"
        ) % a,
    }



class SessionSidebar(QWidget):
    """会话侧边栏。"""

    session_created = pyqtSignal(str)   # 新建会话 adapter_key
    session_switched = pyqtSignal(str)  # session_id
    session_deleted = pyqtSignal(str)   # session_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        self.setFixedWidth(240)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # 标题
        title = QLabel("◈  AI · CHAT  ◈")
        self._title = title
        title.setStyleSheet(_sidebar_styles()["title"])
        lay.addWidget(title)

        # 新建会话区
        new_row = QHBoxLayout()
        new_btn = QPushButton("+ 新对话")
        self._new_btn = new_btn
        new_btn.setStyleSheet(_sidebar_styles()["new_btn"])
        new_btn.clicked.connect(self._on_new)
        new_row.addWidget(new_btn)
        lay.addLayout(new_row)

        # 适配器选择
        adapter_label = QLabel("◂ 适配器 ADAPTER")
        self._adapter_lbl = adapter_label
        adapter_label.setStyleSheet(_sidebar_styles()["sub_label"])
        lay.addWidget(adapter_label)
        self._adapter_combo = QComboBox()
        self._adapter_combo.setStyleSheet(_sidebar_styles()["combo"])
        lay.addWidget(self._adapter_combo)

        # 会话列表
        list_label = QLabel("◂ 会话 SESSIONS")
        self._list_lbl = list_label
        list_label.setStyleSheet(_sidebar_styles()["sub_label"])
        lay.addWidget(list_label)

        self._list = QListWidget()
        self._list.setStyleSheet(_sidebar_styles()["list"])
        self._list.itemClicked.connect(self._on_item_click)
        lay.addWidget(self._list, 1)

        # 删除按钮
        del_btn = QPushButton("删除选中会话")
        self._del_btn = del_btn
        del_btn.setStyleSheet(_sidebar_styles()["del_btn"])
        del_btn.clicked.connect(self._on_delete)
        lay.addWidget(del_btn)

    def set_adapters(self, adapters: list[dict]):
        """设置可选适配器列表。"""
        self._adapter_combo.clear()
        for a in adapters:
            self._adapter_combo.addItem(f"{a['name']} ({a['key']})", a["key"])

    def refresh_sessions(self, sessions: list[Session], current_id: str = ""):
        """刷新会话列表。"""
        self._list.clear()
        for s in sessions:
            item = QListWidgetItem(s.title)
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            if s.id == current_id:
                item.setSelected(True)
            self._list.addItem(item)

    def _on_new(self):
        key = self._adapter_combo.currentData() or "generic"
        self.session_created.emit(key)

    def _on_item_click(self, item):
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid:
            self.session_switched.emit(sid)

    def _on_delete(self):
        item = self._list.currentItem()
        if item:
            sid = item.data(Qt.ItemDataRole.UserRole)
            if sid:
                self.session_deleted.emit(sid)

    def reapply_theme(self):
        """切皮肤后刷新所有局部样式。"""
        s = _sidebar_styles()
        if getattr(self, "_title", None): self._title.setStyleSheet(s["title"])
        if getattr(self, "_new_btn", None): self._new_btn.setStyleSheet(s["new_btn"])
        if getattr(self, "_adapter_lbl", None): self._adapter_lbl.setStyleSheet(s["sub_label"])
        if getattr(self, "_list_lbl", None): self._list_lbl.setStyleSheet(s["sub_label"])
        if getattr(self, "_adapter_combo", None): self._adapter_combo.setStyleSheet(s["combo"])
        if getattr(self, "_list", None): self._list.setStyleSheet(s["list"])
        if getattr(self, "_del_btn", None): self._del_btn.setStyleSheet(s["del_btn"])
        apply_skin_widget(self)
