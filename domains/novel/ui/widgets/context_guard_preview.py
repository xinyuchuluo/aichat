"""
上下文 & 守卫预览面板（ContextGuardPreviewWidget）。

在 UI 中的位置：
    主窗口右侧 Tab 区新增一个 Tab（标签为「上下文预览」），位于「记忆档案」旁。

作用（落实用户第 2 点：人类经 UI 当监督者）：
    生成前，让人类看见「AI 此刻将消费的上下文」与「守卫将强制检查什么」，
    而不必等写完再在 ConsistencyView 里审查。

    - 左：章节选择器 + 上下文窗口大小（模拟不同模型的 window）。
    - 右上「钉顶块」表：列出本章组装出的硬约束块（章节脚本/连续性卡/弧暗线卡/
      实体锚点/批次窗口…）的位置、token 占用、是否 no_truncate（不可截断）。
      位置越靠前 = 注意力越高（对抗 lost-in-the-middle）。
    - 右下「守卫」区：框架约束将检查的 checkpoint（深大纲 beat 必须落地）、
      已故角色不得复活、框架一致性、须承接的上一章实体——即「AI 当建构者，
      本地零 token 关联 + 守卫兜底」的可见证据。

数据来源：
    - pipeline.preview_context(cid)  → AssembledContext（不调 LLM 的组装快照）
    - framework_constraint.extract_chapter_constraints(project_dir, cid) → 硬约束 dict
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QComboBox, QSpinBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit, QLabel,
    QSplitter,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

# 钉顶硬约束块（这些是 AI 记忆建构的精华，no_truncate 保证不被裁）
_PINNED = {"chapter_spec", "entity_anchor", "continuity_state", "arc_subplot", "character_profiles"}
# 展示顺序（其余出现也算）
_ORDER = [
    "chapter_spec", "continuity_state", "arc_subplot", "character_profiles",
    "entity_anchor", "chapter_summaries", "adjacent_outlines", "recent_text",
    "rag_chunks", "inspirations", "hook_anchors", "world_setting", "overview",
    "spine", "event_ledger",
]

_STYLE = (
    "QTableWidget { background-color: #313244; color: #cdd6f4; border: none;"
    "  selection-background-color: #585b70; }"
    "QTableWidget::item { padding: 3px 6px; }"
    "QHeaderView::section { background-color: #1e1e2e; color: #bac2de;"
    "  border: none; padding: 4px 8px; font-size: 11px; }"
    "QTextEdit { background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #45475a;"
    "  border-radius: 4px; padding: 6px; font-size: 12px; }"
    "QLabel { color: #bac2de; font-size: 11px; }"
    "QPushButton { background-color: #89b4fa; color: #11111b; border: none;"
    "  border-radius: 4px; padding: 4px 12px; font-size: 12px; }"
)


class ContextGuardPreviewWidget(QWidget):
    """生成前上下文 & 守卫预览面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pipeline = None
        self._project_root = ""
        self._init_ui()

    def set_pipeline(self, pipeline):
        """注入 pipeline（含 _llm / _memory / _assembler / _project_root）。"""
        self._pipeline = pipeline
        self._project_root = getattr(pipeline, "_project_root", "") or ""
        self._refresh_chapter_list()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 顶部控制栏
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("章节:"))
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(120)
        ctrl.addWidget(self.chapter_combo, stretch=1)
        ctrl.addWidget(QLabel("窗口:"))
        self.window_spin = QSpinBox()
        self.window_spin.setRange(1000, 2000000)
        self.window_spin.setValue(8000)
        self.window_spin.setSingleStep(2000)
        ctrl.addWidget(self.window_spin)
        ctrl.addWidget(QLabel("tok"))
        self.preview_btn = QPushButton("预览")
        self.preview_btn.clicked.connect(self._on_preview)
        ctrl.addWidget(self.preview_btn)
        layout.addLayout(ctrl)

        # 上下分栏：钉顶块表 / 守卫区
        splitter = QSplitter(Qt.Orientation.Vertical)

        # 钉顶块
        pinned_group = QGroupBox("AI 将消费的上下文块（位置越前=注意力越高）")
        pg = QVBoxLayout(pinned_group)
        pg.setContentsMargins(6, 4, 6, 4)
        self.block_table = QTableWidget()
        self.block_table.setColumnCount(4)
        self.block_table.setHorizontalHeaderLabels(["块", "位置", "token", "保护"])
        self.block_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.block_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.block_table.setColumnWidth(1, 56)
        self.block_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.block_table.setColumnWidth(2, 64)
        self.block_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.block_table.verticalHeader().setVisible(False)
        self.block_table.setStyleSheet(_STYLE)
        pg.addWidget(self.block_table)
        splitter.addWidget(pinned_group)

        # 守卫区
        guard_group = QGroupBox("守卫将强制检查（生成前可见）")
        gg = QVBoxLayout(guard_group)
        gg.setContentsMargins(6, 4, 6, 4)
        self.guard_text = QTextEdit()
        self.guard_text.setReadOnly(True)
        self.guard_text.setStyleSheet(_STYLE)
        gg.addWidget(self.guard_text)
        splitter.addWidget(guard_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, stretch=1)

    def _refresh_chapter_list(self):
        self.chapter_combo.blockSignals(True)
        self.chapter_combo.clear()
        if self._pipeline:
            try:
                for ol in self._pipeline.get_chapter_outlines():
                    cid = ol.get("chapter_id", 0)
                    title = ol.get("title", "")
                    self.chapter_combo.addItem(f"第{cid}章 {title}", cid)
            except Exception:
                pass
        self.chapter_combo.blockSignals(False)

    def _on_preview(self):
        if not self._pipeline:
            self.guard_text.setPlainText("（请先打开一个项目）")
            return
        cid = self.chapter_combo.currentData()
        if cid is None:
            self.guard_text.setPlainText("（请先选择章节）")
            return
        window = self.window_spin.value()
        try:
            ctx = self._pipeline.preview_context(cid, model_context_window=window)
        except Exception as e:
            self.guard_text.setPlainText(f"预览失败: {e}")
            return
        self._show_blocks(ctx)
        self._show_guards(cid)

    def _show_blocks(self, ctx):
        usage = ctx.token_usage or {}
        truncated = ctx.truncated or {}
        positions = getattr(ctx, "block_positions", {}) or {}
        rows = []
        seen = set()
        for name in _ORDER:
            if name in usage and usage[name] > 0:
                rows.append(name)
                seen.add(name)
        for name, tok in usage.items():
            if name not in seen and tok > 0:
                rows.append(name)
        self.block_table.setRowCount(len(rows))
        for i, name in enumerate(rows):
            tok = usage.get(name, 0)
            pos = positions.get(name)
            pos_str = f"{pos}" if isinstance(pos, int) else "-"
            is_pinned = name in _PINNED
            if is_pinned and not truncated.get(name):
                prot = "✅ 钉顶·不可截断"
                prot_color = QColor("#a6e3a1")
            elif truncated.get(name):
                prot = "⚠️ 被截断"
                prot_color = QColor("#f9e2af")
            elif is_pinned:
                prot = "钉顶(未知)"
                prot_color = QColor("#89b4fa")
            else:
                prot = "普通"
                prot_color = QColor("#cdd6f4")
            self.block_table.setItem(i, 0, QTableWidgetItem(name))
            self.block_table.setItem(i, 1, QTableWidgetItem(pos_str))
            self.block_table.setItem(i, 2, QTableWidgetItem(str(tok)))
            prot_item = QTableWidgetItem(prot)
            prot_item.setForeground(prot_color)
            self.block_table.setItem(i, 3, prot_item)

    def _show_guards(self, cid):
        try:
            import framework_constraint as fc
            c = fc.extract_chapter_constraints(self._project_root, cid)
        except Exception as e:
            self.guard_text.setPlainText(f"读取框架约束失败: {e}")
            return
        if not c:
            self.guard_text.setPlainText("（该章节无大纲，无框架约束）")
            return
        lines = []
        mi = c.get("must_include") or []
        if mi:
            lines.append("【必须落地的 checkpoint（深大纲 beat / 暗线节点）】")
            for k in mi:
                lines.append(f"  · {k}")
        else:
            lines.append("【必须落地的 checkpoint】无")
        lines.append("")
        mni = c.get("must_not_include") or []
        if mni:
            lines.append(f"【禁止虚构专名】{len(mni)} 条（如 {', '.join(mni[:4])}…）")
        dc = c.get("dead_chars") or []
        if dc:
            lines.append(f"【已故角色不得复活】{', '.join(dc)}")
        else:
            lines.append("【已故角色不得复活】无")
        fi = c.get("framework_integrity") or []
        if fi:
            lines.append(f"【⚠️ 框架不一致（需改大纲）】{'; '.join(fi)}")
        else:
            lines.append("【框架一致性】OK")
        pe = c.get("prev_entities") or []
        if pe:
            lines.append(f"【须承接上一章结尾实体】{', '.join(pe[:6])}")
        tl = c.get("timeline") or {}
        if tl.get("label"):
            lines.append(f"【时间线】{tl['label']}")
        self.guard_text.setPlainText("\n".join(lines))
