"""写作控制台：紧凑控制栏 — 模式/类型 + 走向输入 + 按钮 + 进度。

设计理念（2026-07-06 重构）：
- 去掉冗余的「正文输出」区 — 正文直接显示在编辑器中，不再重复
- 写作控制台只负责：输入参数 + 触发动作 + 显示进度
- 紧凑布局，占少量垂直空间，把更多空间留给编辑器
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel,
    QProgressBar, QComboBox, QTabWidget, QSizePolicy
)
from PyQt6.QtCore import pyqtSignal, Qt

from domains.novel.core.pipeline.prompts import CHAPTER_TYPES
from domains.novel.ui.styles import (
    BTN_PRIMARY, BTN_SECONDARY, HINT_STYLE,
)


class WritingConsoleWidget(QWidget):
    """写作控制台 — 紧凑控制栏，不含输出区。"""

    write_requested = pyqtSignal(int, str, str, str)  # chapter_id, title, instruction, mode
    auto_next_chapter_requested = pyqtSignal()
    revise_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # ===== Tab 区（写作/修订）=====
        self.tab_widget = QTabWidget()
        # 不再硬性限制最大高度，让其随内容自然展开（避免顶部留白）
        self.tab_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

        # --- 写作 Tab ---
        write_tab = QWidget()
        wt_layout = QVBoxLayout(write_tab)
        wt_layout.setContentsMargins(6, 4, 6, 4)
        wt_layout.setSpacing(4)

        # 第一行：章节信息 + 模式 + 类型
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self.chapter_label = QLabel("未选择章节")
        font = self.chapter_label.font()
        font.setBold(True)
        font.setPointSize(10)
        self.chapter_label.setFont(font)
        row1.addWidget(self.chapter_label)
        row1.addStretch()

        row1.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["新写", "续写", "重写"])
        self.mode_combo.setFixedWidth(70)
        row1.addWidget(self.mode_combo)

        row1.addWidget(QLabel("类型:"))
        self.chapter_type_combo = QComboBox()
        self.chapter_type_combo.addItem("自动", "")
        for type_id, type_label in CHAPTER_TYPES.items():
            self.chapter_type_combo.addItem(type_label, type_id)
        self.chapter_type_combo.setFixedWidth(100)
        self.chapter_type_combo.setToolTip(
            "影响 AI 写作方向。主线推进=核心矛盾；情绪缓冲=情感消化；"
            "氛围铺垫=环境描写；内心独白=心理刻画；世界观特写=制度/习俗；过渡转折=节奏转换"
        )
        row1.addWidget(self.chapter_type_combo)
        wt_layout.addLayout(row1)

        # 第二行：走向输入 + 按钮
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self.instruction_input = QTextEdit()
        self.instruction_input.setFixedHeight(56)
        self.instruction_input.setPlaceholderText("故事走向 / 灵感指引（留空则AI自主规划）...")
        self.instruction_input.setStyleSheet(
            "QTextEdit { font-size: 12px; padding: 2px; }"
        )
        row2.addWidget(self.instruction_input, stretch=1)

        self.write_btn = QPushButton("写作")
        self.write_btn.setStyleSheet(BTN_PRIMARY)
        self.write_btn.setFixedHeight(32)
        self.write_btn.setMinimumWidth(64)
        self.write_btn.clicked.connect(self._on_write)
        row2.addWidget(self.write_btn)

        self.auto_next_btn = QPushButton("AI写下一章")
        self.auto_next_btn.setStyleSheet(BTN_SECONDARY)
        self.auto_next_btn.setFixedHeight(32)
        self.auto_next_btn.setToolTip("自动规划章节方向 + 自动写作")
        self.auto_next_btn.clicked.connect(self._on_auto_next_chapter)
        row2.addWidget(self.auto_next_btn)

        self.cancel_btn = QPushButton("停止")
        self.cancel_btn.setStyleSheet(BTN_SECONDARY)
        self.cancel_btn.setFixedHeight(32)
        self.cancel_btn.setMinimumWidth(56)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        row2.addWidget(self.cancel_btn)

        wt_layout.addLayout(row2)
        self.tab_widget.addTab(write_tab, "写作")

        # --- 修订 Tab ---
        revise_tab = QWidget()
        rv_layout = QVBoxLayout(revise_tab)
        rv_layout.setContentsMargins(6, 4, 6, 4)
        rv_layout.setSpacing(4)

        rv_row = QHBoxLayout()
        rv_row.setSpacing(6)

        self.revision_input = QTextEdit()
        self.revision_input.setFixedHeight(56)
        self.revision_input.setPlaceholderText(
            "修改意见: 如 结尾悬念不够 / 对话太长 / 心理描写不够"
        )
        self.revision_input.setStyleSheet(
            "QTextEdit { font-size: 12px; padding: 2px; }"
        )
        rv_row.addWidget(self.revision_input, stretch=1)

        self.revise_btn = QPushButton("AI修订")
        self.revise_btn.setStyleSheet(BTN_SECONDARY)
        self.revise_btn.setFixedHeight(32)
        self.revise_btn.setMinimumWidth(80)
        self.revise_btn.clicked.connect(self._on_revise)
        rv_row.addWidget(self.revise_btn)

        rv_layout.addLayout(rv_row)
        self.tab_widget.addTab(revise_tab, "修订")

        layout.addWidget(self.tab_widget)

        # ===== 进度条 =====
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setVisible(False)
        self.progress_label.setStyleSheet(HINT_STYLE)
        layout.addWidget(self.progress_label)

    def set_chapter_info(self, chapter_id: int, title: str):
        self.chapter_label.setText(f"第 {chapter_id} 章  {title}" if chapter_id else "未选择章节")

    def _on_write(self):
        mode_map = {"新写": "write", "续写": "continue", "重写": "rewrite"}
        mode = mode_map.get(self.mode_combo.currentText(), "write")

        instruction = self.instruction_input.toPlainText().strip()
        if not instruction:
            instruction = "（请根据故事总纲和前文发展自主规划本章情节）"

        chapter_type = self.chapter_type_combo.currentData()
        if chapter_type:
            type_label = self.chapter_type_combo.currentText()
            instruction = f"[章节类型: {type_label}]\n{instruction}"

        self.write_requested.emit(0, "", instruction, mode)

    def _on_auto_next_chapter(self):
        self.auto_next_chapter_requested.emit()

    def _on_revise(self):
        request = self.revision_input.toPlainText().strip()
        if not request:
            return
        self.revise_requested.emit(request)

    def _on_cancel(self):
        pass

    def start_writing(self):
        self.write_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.auto_next_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_label.setVisible(True)
        self.progress_bar.setValue(0)

    def start_revising(self):
        self.revise_btn.setEnabled(False)
        self.revise_btn.setText("修订中...")
        self.progress_bar.setVisible(True)
        self.progress_label.setVisible(True)
        self.progress_bar.setValue(0)
        self.tab_widget.setCurrentIndex(0)

    def on_progress(self, stage_name: str):
        stages = ["预检", "上下文组装", "起草正文", "事实提取", "生成摘要", "向量入库", "提交记忆", "备份", "完成"]
        try:
            idx = stages.index(stage_name)
            progress = int((idx + 1) / len(stages) * 100)
        except ValueError:
            progress = 50
        self.progress_bar.setValue(progress)
        self.progress_label.setText(f"当前阶段: {stage_name}...")

    def on_finished(self, success: bool, error: str = ""):
        self.write_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.auto_next_btn.setEnabled(True)
        self.revise_btn.setEnabled(True)
        self.revise_btn.setText("AI修订")
        self.progress_bar.setValue(100 if success else self.progress_bar.value())
        if success:
            self.progress_label.setText("完成! 可在编辑器中查看正文，或切换到「修订」页给出修改意见")
        else:
            self.progress_label.setText(f"失败: {error}")
