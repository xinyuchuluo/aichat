"""记忆面板 — 四层记忆架构可视化检查器。"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QGroupBox, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer
from .styles import current_colors, is_light_theme, apply_skin_widget


def _mem_styles() -> dict:
    """MemoryPanel 主题感知样式（% 格式化，避免 CSS 花括号和 f-string 冲突）。"""
    c = current_colors()
    light = is_light_theme()
    a = dict(
        accent=c.accent, accent_h=c.accent_hover, mauve=c.mauve,
        text=c.text, sub=c.subtext, green=c.green, yellow=c.yellow,
        surf0=c.surface0, surf1=c.surface1, overlay=c.overlay, red=c.red,
    )
    if light:
        return {
            "title": "font-size:13px; font-weight:900; letter-spacing:2px; color:%(mauve)s; padding: 2px 0; background:transparent;" % a,
            "stats_group": (
                "QGroupBox { margin-top:10px; border:1px solid %(overlay)s; border-radius:8px;"
                " padding:8px 8px 8px 8px; color:%(text)s; font-weight:900; letter-spacing:1px; }"
                " QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 6px;"
                " color:%(accent)s; font-size:11px; }"
            ) % a,
            "stat_num": "font-size:12px; font-weight:900; color:%(accent)s; letter-spacing:0.3px; background:transparent;" % a,
            "stat_desc": "font-size: 10px; color: %(sub)s; background:transparent;" % a,
            "vec_on":  "font-size: 11px; color:%(green)s; letter-spacing:0.5px; background:transparent;" % a,
            "vec_off": "font-size: 11px; color:%(yellow)s; letter-spacing:0.5px; background:transparent;" % a,
            "tree_group": (
                "QGroupBox { margin-top:10px; border:1px solid %(overlay)s; border-radius:8px;"
                " padding:8px; color:%(text)s; font-weight:900; }"
                " QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 6px;"
                " color:%(mauve)s; font-size:11px; letter-spacing:1px; }"
            ) % a,
            "tree": (
                "QTreeWidget { background:#ffffff; color:%(text)s;"
                " border:1px solid %(overlay)s; border-radius:6px; font-size:11px;"
                " gridline-color: %(surf0)s; }"
                " QHeaderView::section { background:%(surf0)s; color:%(accent)s;"
                " padding: 3px 6px; border: 1px solid %(overlay)s; font-weight:900; }"
                " QTreeWidget::item { padding: 2px; }"
                " QTreeWidget::item:selected { background:%(accent)s; color:#ffffff; }"
            ) % a,
            "btn_refresh": (
                "QPushButton { background: transparent; color:%(accent)s;"
                " border:1px solid %(overlay)s; border-radius:6px;"
                " padding:5px 12px; font-size:11px; font-weight:800; letter-spacing:1px; }"
                " QPushButton:hover { background:%(surf0)s; border-color:%(accent)s; }"
            ) % a,
            "btn_summarize": (
                "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                " stop:0 %(mauve)s, stop:1 %(red)s);"
                " color:#ffffff; border:none; border-radius:6px;"
                " padding:5px 12px; font-size:11px; font-weight:800; letter-spacing:1px; }"
                " QPushButton:hover { color:#ffffff; }"
            ) % a,
            "vec_status_base": "font-size: 11px; color:%(sub)s; letter-spacing:0.5px; background:transparent;" % a,
        }
    return {
        "title": "font-size:13px; font-weight:900; letter-spacing:2px; color:%(mauve)s; padding: 2px 0; background:transparent;" % a,
        "stats_group": (
            "QGroupBox { margin-top:10px; border:1px solid #2a2450; border-radius:8px;"
            " padding:8px 8px 8px 8px; color:#9fe9ff; font-weight:900; letter-spacing:1px; }"
            " QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 6px;"
            " color:%(accent_h)s; font-size:11px; }"
        ) % a,
        "stat_num": "font-size:12px; font-weight:900; color:%(accent)s; letter-spacing:0.3px; background:transparent;" % a,
        "stat_desc": "font-size: 10px; color: %(sub)s; background:transparent;" % a,
        "vec_on":  "font-size: 11px; color:%(green)s; letter-spacing:0.5px; background:transparent;" % a,
        "vec_off": "font-size: 11px; color:%(yellow)s; letter-spacing:0.5px; background:transparent;" % a,
        "tree_group": (
            "QGroupBox { margin-top:10px; border:1px solid #2a2450; border-radius:8px;"
            " padding:8px; color:#9fe9ff; font-weight:900; }"
            " QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 6px;"
            " color:%(mauve)s; font-size:11px; letter-spacing:1px; }"
        ) % a,
        "tree": (
            "QTreeWidget { background:#050812; color:#c9f7ff;"
            " border:1px solid %(surf1)s; border-radius:6px; font-size:11px;"
            " gridline-color: #111a30; }"
            " QHeaderView::section { background:#0a1022; color:%(accent_h)s;"
            " padding: 3px 6px; border: 1px solid %(surf1)s; font-weight:900; }"
            " QTreeWidget::item { padding: 2px; }"
            " QTreeWidget::item:selected { background:rgba(0,229,255,0.18); color:#ffffff; }"
        ) % a,
        "btn_refresh": (
            "QPushButton { background: transparent; color:%(accent)s;"
            " border:1px solid %(surf1)s; border-radius:6px;"
            " padding:5px 12px; font-size:11px; font-weight:900; letter-spacing:1px; }"
            " QPushButton:hover { background: rgba(0,229,255,0.1); border-color:%(accent)s; }"
        ) % a,
        "btn_summarize": (
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 %(mauve)s, stop:1 %(red)s);"
            " color:#ffffff; border:none; border-radius:6px;"
            " padding:5px 12px; font-size:11px; font-weight:900; letter-spacing:1px; }"
            " QPushButton:hover { color:#ffffff; }"
        ) % a,
        "vec_status_base": "font-size: 11px; color:#7afcff; letter-spacing:0.5px; background:transparent;" % a,
    }



class MemoryPanel(QWidget):
    """记忆系统检查面板。"""

    def __init__(self, memory_engine, parent=None):
        super().__init__(parent)
        self._memory = memory_engine
        self._build()
        # 定时刷新
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(3000)  # 3秒刷新

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        title = QLabel("◈  MEMORY · ENGINE  ◈")
        self._title_lbl = title
        title.setStyleSheet(_mem_styles()["title"])
        lay.addWidget(title)

        # 四层统计
        stats_group = QGroupBox("⟐ 四层架构 LAYERED STACK")
        self._stats_group = stats_group
        stats_lay = QVBoxLayout(stats_group)
        stats_group.setStyleSheet(_mem_styles()["stats_group"])

        self._stat_labels: dict[str, QLabel] = {}
        layers = [
            ("L1 工作记忆", "working_messages", "内存临时，不持久化"),
            ("L2 SQLite 原始库", "sqlite_records", "唯一事实源，只追加"),
            ("L3 树形记忆", "tree_nodes", "异步批量归纳"),
            ("L4 向量索引", "vector_count", "UUID召回，回查SQLite"),
        ]
        for name, key, desc in layers:
            row = QHBoxLayout()
            lbl = QLabel(f"{name}: —")
            lbl.setStyleSheet(_mem_styles()["stat_num"])
            row.addWidget(lbl)
            d = QLabel(desc)
            d.setStyleSheet(_mem_styles()["stat_desc"])
            row.addWidget(d, 1)
            stats_lay.addLayout(row)
            self._stat_labels[key] = lbl

        # 向量库状态
        self._vector_status = QLabel("▸ 向量库 VECTOR: 初始化中...")
        self._vector_status.setStyleSheet(_mem_styles()["vec_status_base"])
        stats_lay.addWidget(self._vector_status)

        lay.addWidget(stats_group)

        # 树形记忆树视图
        tree_group = QGroupBox("⎔ 树形记忆 TREE STRUCTURE")
        self._tree_group = tree_group
        tree_group.setStyleSheet(_mem_styles()["tree_group"])
        tree_lay = QVBoxLayout(tree_group)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["节点 ID", "LEVEL", "摘要 (前 60 字)", "关联"])
        self._tree.setStyleSheet(_mem_styles()["tree"])
        tree_lay.addWidget(self._tree)
        lay.addWidget(tree_group, 1)

        # 操作按钮
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        self._refresh_btn = refresh_btn
        refresh_btn.setStyleSheet(_mem_styles()["btn_refresh"])
        refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(refresh_btn)

        summarize_btn = QPushButton("触发归纳")
        self._summarize_btn = summarize_btn
        summarize_btn.setStyleSheet(_mem_styles()["btn_summarize"])
        summarize_btn.clicked.connect(self._on_summarize)
        btn_row.addWidget(summarize_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def refresh(self):
        """刷新统计和树视图。"""
        try:
            stats = self._memory.get_stats()
            for key, lbl in self._stat_labels.items():
                val = stats.get(key, "—")
                lbl.setText(lbl.text().split(":")[0] + f": {val}")

            if stats.get("vector_available"):
                self._vector_status.setText("▸ 向量库 VECTOR: ONLINE (ChromaDB)")
                self._vector_status.setStyleSheet(_mem_styles()["vec_on"])
            else:
                self._vector_status.setText("▸ 向量库 VECTOR: OFFLINE (未安装 ChromaDB)")
                self._vector_status.setStyleSheet(_mem_styles()["vec_off"])

            # 刷新树视图
            self._tree.clear()
            # 遍历所有 scope 的树节点
            from ..memory.tree_store import TreeMemoryStore
            tree_store: TreeMemoryStore = self._memory.tree
            # 获取所有 scope
            import sqlite3
            conn = sqlite3.connect(tree_store._db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT DISTINCT scope FROM tree_nodes WHERE scope != ''").fetchall()
            for row in rows:
                scope = row["scope"]
                scope_item = QTreeWidgetItem([scope, "scope", "", ""])
                scope_item.setForeground(0, Qt.GlobalColor.cyan)
                self._tree.addTopLevelItem(scope_item)
                # 获取该 scope 的节点
                nodes = tree_store.get_by_scope(scope)
                for node in nodes:
                    summary_preview = node.summary[:60] + ("..." if len(node.summary) > 60 else "")
                    item = QTreeWidgetItem([
                        node.node_id[:8],
                        node.level,
                        summary_preview,
                        str(len(node.raw_uuids)),
                    ])
                    scope_item.addChild(item)
            conn.close()
        except Exception:
            pass

    def _on_summarize(self):
        """手动触发树形归纳。"""
        try:
            self._memory.trigger_summarize()
            self.refresh()
        except Exception:
            pass


    def reapply_theme(self):
        """切皮肤后刷新 memory panel 局部样式。"""
        s = _mem_styles()
        if getattr(self, "_title_lbl", None): self._title_lbl.setStyleSheet(s["title"])
        if getattr(self, "_stats_group", None): self._stats_group.setStyleSheet(s["stats_group"])
        if getattr(self, "_tree_group", None): self._tree_group.setStyleSheet(s["tree_group"])
        if getattr(self, "_tree", None): self._tree.setStyleSheet(s["tree"])
        if getattr(self, "_refresh_btn", None): self._refresh_btn.setStyleSheet(s["btn_refresh"])
        if getattr(self, "_summarize_btn", None): self._summarize_btn.setStyleSheet(s["btn_summarize"])
        if getattr(self, "_vector_status", None): self._vector_status.setStyleSheet(s["vec_status_base"])
        apply_skin_widget(self)
