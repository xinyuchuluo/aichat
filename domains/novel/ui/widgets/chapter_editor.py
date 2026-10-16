"""章节编辑器：纯写作区（章节导航已统一到左侧项目树）。

历史：原本编辑器内含一个独立的「章节列表」(QTreeWidget)，与左侧
ProjectListPanel 的「项目→卷→章」结构重复。现将其全部功能（打开/插入/确认
大纲/生成正文/删除/批量重写）下沉到 ProjectListPanel，本组件只负责：
  - 正文编辑（QPlainTextEdit）
  - 大纲详情展示 + 确认/生成/修订/校验/删除按钮
  - 章节导航（上一章 / 下一章，无下一章时显示「生成下一章大纲」）
  - 项目级批量操作按钮（新建/生成大纲/批量写作/重新生成大纲/批量重写）
  - 自动保存（正文改动防抖落盘）

旧的「写作/修订控制台」(WritingConsoleWidget) 已移除，所有操作统一到顶部按钮。
修订改为「位置精确」：选中某段（或光标附近窗口）→ 弹窗填修改意见 → AI 只改那一段。
章节选择由主窗口通过 select_chapter(chapter_id) 直接驱动。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QMessageBox,
    QTextEdit, QPlainTextEdit, QFrame,
    QDialog, QDialogButtonBox, QVBoxLayout as _QV,
    QToolButton, QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent

import logging

from domains.novel.core.project.manager import ProjectManager
from domains.novel.ui.widgets.flow_layout import FlowLayout
from domains.novel.ui.styles import (
    TOOLBAR_BTN, TOOLBAR_BTN_ACCENT, BTN_SM, BTN_SM_DANGER, BTN_SECONDARY, HINT_STYLE
)

logger = logging.getLogger(__name__)


class ReviseDialog(QDialog):
    """局部修订对话框：展示上下文 + 待修订片段 + 收集修改意见。"""

    def __init__(self, segment: str, context_before: str = "", context_after: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("局部修订")
        self.setMinimumWidth(520)
        self.setMinimumHeight(320)
        layout = _QV(self)
        layout.setSpacing(6)

        layout.addWidget(QLabel("上下文（仅供参考，AI 不会改动这部分）:"))

        ctx = QTextEdit()
        ctx.setReadOnly(True)
        cb = (context_before or "")[-300:]
        ca = (context_after or "")[:300]
        ctx.setPlainText(
            (cb + "\n" if cb else "")
            + "〔以下为待修订片段〕\n"
            + (ca if ca else "")
        )
        ctx.setMaximumHeight(64)
        ctx.setStyleSheet(
            "QTextEdit { background-color: #1e1e2e; color: #6c7086; border: none; font-size: 11px; }"
        )
        layout.addWidget(ctx)

        layout.addWidget(QLabel("待修订片段（AI 只会改写这一段）:"))

        seg_view = QTextEdit()
        seg_view.setReadOnly(True)
        seg_view.setPlainText(segment)
        seg_view.setMaximumHeight(120)
        seg_view.setStyleSheet(
            "QTextEdit { background-color: #252536; color: #cdd6f4; border: none; font-size: 12px; }"
        )
        layout.addWidget(seg_view)

        layout.addWidget(QLabel("修改意见:"))
        self.inst_input = QTextEdit()
        self.inst_input.setPlaceholderText("例如：这段对话太生硬，改得更自然 / 增加心理描写 / 节奏太快，放慢")
        self.inst_input.setMaximumHeight(80)
        self.inst_input.setStyleSheet(
            "QTextEdit { font-size: 12px; padding: 2px; }"
        )
        layout.addWidget(self.inst_input)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def instruction(self) -> str:
        return self.inst_input.toPlainText().strip()


class ChapterEditorWidget(QWidget):
    """章节编辑器：仅负责正文编辑 + 大纲操作按钮 + 章节导航。

    信号：
        chapter_selected(int, str): 已加载某章（chapter_id, title）
        chapter_saved(int, str, str): 已保存（chapter_id, title, text）
        text_changed(str): 编辑器文本变化
        generate_outlines_requested(): 请求生成大纲
        batch_write_requested(): 请求批量写作
        confirm_outline_requested(int, bool): 确认/取消确认大纲
        write_single_chapter_requested(int): 生成单章正文
        regenerate_outlines_requested(): 重新生成大纲（范围对话框）
        batch_rewrite_requested(): 批量重写（主窗口读取左侧项目树多选）
        new_chapter_requested(): 新建空白章节（主窗口在末尾创建并定位）
        delete_chapter_requested(int): 删除当前章节
        validate_chapter_requested(int): 校验当前章节
        navigate_requested(int): 跳转到某章（上一章/下一章）
        generate_next_outline_requested(): 生成下一章大纲（无下一章时）
        revise_segment_requested(int, int, int, str, str, str, str):
            位置精确修订（chapter_id, sel_start, sel_end, segment, ctx_before, ctx_after, instruction）
    """

    chapter_selected = pyqtSignal(int, str)
    chapter_saved = pyqtSignal(int, str, str)
    text_changed = pyqtSignal(str)
    generate_outlines_requested = pyqtSignal()
    confirm_all_outlines_requested = pyqtSignal(bool)
    batch_write_requested = pyqtSignal()
    confirm_outline_requested = pyqtSignal(int, bool)
    write_single_chapter_requested = pyqtSignal(int)
    regenerate_outlines_requested = pyqtSignal()
    # 单章生成 / 重新生成（中间三区块各自上方按钮触发，带依赖守卫）
    gen_short_outline_requested = pyqtSignal(int)
    regen_short_outline_requested = pyqtSignal(int)
    gen_deep_outline_requested = pyqtSignal(int)
    regen_deep_outline_requested = pyqtSignal(int)
    batch_rewrite_requested = pyqtSignal()
    # 顶部工具栏「批量生产深大纲」按钮：复用主窗口的 _on_menu_batch_deep_outline
    # （弹范围对话框，串行调 DeepOutlineSingleWorker 单章 worker）
    batch_deep_outline_requested = pyqtSignal()
    new_chapter_requested = pyqtSignal()
    delete_chapter_requested = pyqtSignal(int)
    validate_chapter_requested = pyqtSignal(int)
    navigate_requested = pyqtSignal(int)
    generate_next_outline_requested = pyqtSignal()
    revise_segment_requested = pyqtSignal(int, int, int, str, str, str, str)

    def __init__(self, project_manager: ProjectManager | None = None, parent=None):
        super().__init__(parent)
        self._pm = project_manager
        self._pipeline = None
        self._project_root = ""
        self._current_chapter_id = 0
        self._current_title = ""
        self._current_outline = None
        self._has_outline = False
        self._has_content = False
        self._has_deep = False
        self._prev_id = None
        self._next_id = None
        self._next_mode = None  # "navigate" | "outline"
        self._cached_sel = None  # (start, end) 失焦前缓存的选中区间
        self._dirty = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._on_save)
        self._init_ui()

    def set_project_manager(self, pm: ProjectManager, project_root: str):
        self._pm = pm
        self._project_root = project_root
        self.refresh_chapters()

    def set_pipeline(self, pipeline):
        """设置写作流水线引用（用于读取大纲）。"""
        self._pipeline = pipeline
        self.refresh_chapters()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # --- 顶部操作按钮行（项目级批量操作） ---
        # 用流式布局：窗口变窄时按钮自动换行，而不是把最右侧按钮裁切掉
        action_row = FlowLayout(margin=0, spacing=4)

        add_btn = QPushButton("新建")
        add_btn.setFixedHeight(26)
        add_btn.setStyleSheet(TOOLBAR_BTN)
        add_btn.setToolTip("在末尾新建空白章节")
        add_btn.clicked.connect(lambda: self.new_chapter_requested.emit())
        action_row.addWidget(add_btn)

        # 「批量生产 ▾」下拉：把散在顶部的批量/重生成操作收进一个功能组，
        # 分「生产族（首次生成）」与「重做族（重新生成/重写）」两组，顺语义排序。
        batch_btn = QToolButton()
        batch_btn.setText("批量生产 ▾")
        batch_btn.setFixedHeight(26)
        batch_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
        batch_btn.setToolTip("批量生成 / 重新生成章节大纲、深大纲、正文")
        batch_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        batch_menu = QMenu(batch_btn)
        a_gen_short = batch_menu.addAction("批量生产简大纲")
        a_gen_short.triggered.connect(lambda _=False: self.generate_outlines_requested.emit())
        a_gen_deep = batch_menu.addAction("批量生产深大纲")
        a_gen_deep.triggered.connect(lambda _=False: self.batch_deep_outline_requested.emit())
        a_gen_body = batch_menu.addAction("批量生产正文")
        a_gen_body.triggered.connect(lambda _=False: self.batch_write_requested.emit())
        batch_menu.addSeparator()
        a_regen = batch_menu.addAction("重新生成简大纲")
        a_regen.triggered.connect(lambda _=False: self.regenerate_outlines_requested.emit())
        a_rewrite = batch_menu.addAction("批量重写")
        a_rewrite.triggered.connect(lambda _=False: self.batch_rewrite_requested.emit())
        batch_btn.setMenu(batch_menu)
        action_row.addWidget(batch_btn)

        self.confirm_all_btn = QPushButton("全部确认")
        self.confirm_all_btn.setFixedHeight(26)
        self.confirm_all_btn.setStyleSheet(TOOLBAR_BTN)
        self.confirm_all_btn.setToolTip("一键确认/取消确认所有大纲")
        self.confirm_all_btn.setCheckable(True)
        self.confirm_all_btn.clicked.connect(self._on_confirm_all)
        action_row.addWidget(self.confirm_all_btn)

        layout.addLayout(action_row)

        self.title_label = QLabel("请选择或新建章节")
        self.title_label.setStyleSheet("font-size: 13px; font-weight: bold; padding: 2px 4px;")
        layout.addWidget(self.title_label)

        # --- 章节导航行（上一章 / 下一章，无下一章时显示「生成下一章大纲」） ---
        nav_row = FlowLayout(margin=0, spacing=4)

        self.prev_btn = QPushButton("← 上一章")
        self.prev_btn.setFixedHeight(24)
        self.prev_btn.setStyleSheet(TOOLBAR_BTN)
        self.prev_btn.setToolTip("跳到上一章")
        self.prev_btn.setEnabled(False)
        self.prev_btn.clicked.connect(self._on_prev)
        nav_row.addWidget(self.prev_btn)

        self.next_btn = QPushButton("下一章 →")
        self.next_btn.setFixedHeight(24)
        self.next_btn.setStyleSheet(TOOLBAR_BTN)
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self._on_next)
        nav_row.addWidget(self.next_btn)

        layout.addLayout(nav_row)

        # --- 逐章三区块：简大纲 / 深大纲 / 正文，各自上方带「生成 / 重新生成」按钮 ---
        # 依赖链守卫见 _update_gen_buttons：框架(阶段0) → 简大纲 → 深大纲 → 正文。
        # 前置内容缺失时按钮自动禁用并给出 tooltip；点击也会再拦截一次。

        # —— 区块1：简大纲（项目级指令 · 时间线锚点）——
        short_frame = QFrame()
        short_frame.setStyleSheet("QFrame { background-color: #1e1e2e; border: 1px solid #313244; border-radius: 4px; }")
        short_layout = QVBoxLayout(short_frame)
        short_layout.setContentsMargins(8, 6, 8, 6)
        short_layout.setSpacing(4)

        short_header = FlowLayout(margin=0, spacing=4)
        short_header.addWidget(QLabel("📋 简大纲（项目级指令 · 时间线锚点）"))
        self.gen_short_btn = QPushButton("生成简大纲")
        self.gen_short_btn.setFixedHeight(22)
        self.gen_short_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
        self.gen_short_btn.setToolTip("根据框架生成本章简大纲")
        self.gen_short_btn.clicked.connect(self._on_gen_short)
        short_header.addWidget(self.gen_short_btn)
        self.regen_short_btn = QPushButton("重新生成简大纲")
        self.regen_short_btn.setFixedHeight(22)
        self.regen_short_btn.setStyleSheet(BTN_SM)
        self.regen_short_btn.setToolTip("重新生成本章简大纲")
        self.regen_short_btn.clicked.connect(self._on_regen_short)
        short_header.addWidget(self.regen_short_btn)
        self.confirm_outline_btn = QPushButton("确认大纲")
        self.confirm_outline_btn.setFixedHeight(22)
        self.confirm_outline_btn.setStyleSheet(BTN_SM)
        self.confirm_outline_btn.setToolTip("确认/取消确认此章大纲")
        self.confirm_outline_btn.clicked.connect(self._on_toggle_confirm_outline)
        self.confirm_outline_btn.setVisible(False)
        short_header.addWidget(self.confirm_outline_btn)
        self.delete_chapter_btn = QPushButton("删除章节")
        self.delete_chapter_btn.setFixedHeight(22)
        self.delete_chapter_btn.setStyleSheet(BTN_SM_DANGER)
        self.delete_chapter_btn.setToolTip("删除此章（正文+大纲+记忆，不可撤销）")
        self.delete_chapter_btn.clicked.connect(self._on_delete_chapter)
        self.delete_chapter_btn.setVisible(False)
        short_header.addWidget(self.delete_chapter_btn)
        short_layout.addLayout(short_header)

        self.short_outline_detail = QTextEdit()
        self.short_outline_detail.setReadOnly(True)
        self.short_outline_detail.setMinimumHeight(90)
        self.short_outline_detail.setMaximumHeight(150)
        self.short_outline_detail.setPlaceholderText("选中章节后，这里将显示该章节的简大纲（项目级指令）...")
        self.short_outline_detail.setStyleSheet(
            "QTextEdit { background-color: #252536; color: #a6adc8; border: none; font-size: 12px; }"
        )
        short_layout.addWidget(self.short_outline_detail)
        layout.addWidget(short_frame)

        # —— 区块2：深大纲（故事内容 · 文笔落点 · 事件进展）——
        deep_frame = QFrame()
        deep_frame.setStyleSheet("QFrame { background-color: #1e1e2e; border: 1px solid #313244; border-radius: 4px; }")
        deep_layout = QVBoxLayout(deep_frame)
        deep_layout.setContentsMargins(8, 6, 8, 6)
        deep_layout.setSpacing(4)

        deep_header = FlowLayout(margin=0, spacing=4)
        deep_header.addWidget(QLabel("📖 深大纲（故事内容 · 写作重心 · 事件进展）"))
        self.gen_deep_btn = QPushButton("生成深大纲")
        self.gen_deep_btn.setFixedHeight(22)
        self.gen_deep_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
        self.gen_deep_btn.setToolTip("将本章简大纲展开为深大纲（事件进展 + 写作重心）")
        self.gen_deep_btn.clicked.connect(self._on_gen_deep)
        deep_header.addWidget(self.gen_deep_btn)
        self.regen_deep_btn = QPushButton("重新生成深大纲")
        self.regen_deep_btn.setFixedHeight(22)
        self.regen_deep_btn.setStyleSheet(BTN_SM)
        self.regen_deep_btn.setToolTip("重新生成本章深大纲")
        self.regen_deep_btn.clicked.connect(self._on_regen_deep)
        deep_header.addWidget(self.regen_deep_btn)
        deep_layout.addLayout(deep_header)

        self.deep_outline_detail = QTextEdit()
        self.deep_outline_detail.setReadOnly(True)
        self.deep_outline_detail.setMinimumHeight(120)
        self.deep_outline_detail.setMaximumHeight(240)
        self.deep_outline_detail.setPlaceholderText("选中章节后，这里将显示该章节的深大纲（故事内容 + 写作重心 + 事件进展）...")
        self.deep_outline_detail.setStyleSheet(
            "QTextEdit { background-color: #252536; color: #a6adc8; border: none; font-size: 12px; }"
        )
        deep_layout.addWidget(self.deep_outline_detail)
        layout.addWidget(deep_frame)

        # —— 区块3：正文（生成 / 重新生成 / 修订 / 校验）——
        body_header = FlowLayout(margin=0, spacing=4)
        body_header.addWidget(QLabel("✍️ 正文"))
        self.gen_body_btn = QPushButton("写此章正文")
        self.gen_body_btn.setFixedHeight(22)
        self.gen_body_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
        self.gen_body_btn.setToolTip("根据本章简大纲/深大纲AI生成此章正文")
        self.gen_body_btn.clicked.connect(self._on_write_single_chapter_btn)
        body_header.addWidget(self.gen_body_btn)
        self.regen_body_btn = QPushButton("重新写正文")
        self.regen_body_btn.setFixedHeight(22)
        self.regen_body_btn.setStyleSheet(BTN_SM)
        self.regen_body_btn.setToolTip("重新生成此章正文（覆盖现有）")
        self.regen_body_btn.clicked.connect(self._on_write_single_chapter_btn)
        body_header.addWidget(self.regen_body_btn)
        self.revise_btn = QPushButton("修订")
        self.revise_btn.setFixedHeight(22)
        self.revise_btn.setStyleSheet(BTN_SM)
        self.revise_btn.setToolTip("选中一段（或把光标放在要改的位置附近），点此让 AI 只改这一段")
        self.revise_btn.clicked.connect(self._on_revise)
        self.revise_btn.setVisible(False)
        body_header.addWidget(self.revise_btn)
        self.validate_btn = QPushButton("校验本章")
        self.validate_btn.setFixedHeight(22)
        self.validate_btn.setStyleSheet(TOOLBAR_BTN)
        self.validate_btn.setToolTip("校验本章情节合理性与设定一致性（AI 审稿）")
        self.validate_btn.clicked.connect(self._on_validate_chapter)
        self.validate_btn.setVisible(False)
        body_header.addWidget(self.validate_btn)
        layout.addLayout(body_header)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("章节正文将在这里显示...\n你可以手动编辑，或选中一段后用「修订」让AI局部改写。")
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.installEventFilter(self)
        layout.addWidget(self.editor, stretch=1)

        # 底部：字数 + 保存（自动保存已开启，此按钮用于手动立即保存）
        save_layout = QHBoxLayout()
        self.word_count_label = QLabel("0 字")
        self.word_count_label.setStyleSheet(HINT_STYLE)
        save_layout.addWidget(self.word_count_label)
        self.auto_save_label = QLabel("（改动自动保存）")
        self.auto_save_label.setStyleSheet(HINT_STYLE)
        save_layout.addWidget(self.auto_save_label)
        save_layout.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet(BTN_SECONDARY)
        save_btn.setFixedHeight(24)
        save_btn.clicked.connect(self._on_save)
        save_layout.addWidget(save_btn)
        layout.addLayout(save_layout)

    # ============================================================
    # 章节加载（直接按 chapter_id 从 pm + pipeline 取数，不依赖内部树）
    # ============================================================

    def eventFilter(self, obj, event):
        """编辑器失焦时缓存当前选区，避免点击按钮后选区被清空导致修订丢失目标。"""
        if obj is self.editor and event.type() == QEvent.Type.FocusOut:
            cur = self.editor.textCursor()
            if cur.hasSelection():
                self._cached_sel = (cur.selectionStart(), cur.selectionEnd())
        return super().eventFilter(obj, event)

    def refresh_chapters(self):
        """刷新：重新加载当前选中章节（章节列表已统一到项目树，这里无树可刷）。"""
        if self._current_chapter_id:
            self._load_chapter(self._current_chapter_id)

    def select_chapter(self, chapter_id: int):
        """选中指定章节（用于插入/新增后定位，或项目树双击打开）。"""
        self._load_chapter(chapter_id)

    def _load_chapter(self, chapter_id: int):
        """加载指定章节到编辑器。"""
        if not self._pm or not self._project_root:
            return

        # 切换章节前，若有未保存改动先落盘，避免丢失
        if self._dirty and self._current_chapter_id:
            self._flush_save()

        # 收集章节数据（优先大纲标题）
        chapters = self._pm.get_chapter_list(self._project_root)
        ch = next((c for c in chapters if c["chapter_id"] == chapter_id), None)
        outlines = self._pipeline.get_chapter_outlines() if self._pipeline else []
        outline_map = {ol.get("chapter_id"): ol for ol in outlines}
        ol = outline_map.get(chapter_id)
        has_outline = ol is not None
        has_content = (ch["word_count"] > 0) if ch else False
        confirmed = bool(ol.get("confirmed")) if ol else False

        # 标题：优先大纲标题（修复插入移位后显示陈旧「新章节N」的问题）
        if ol and ol.get("title"):
            title = ol["title"]
        elif ch and ch.get("title"):
            title = ch["title"]
        else:
            title = f"第{chapter_id}章"

        self._current_chapter_id = chapter_id
        self._current_title = title
        self._current_outline = ol
        self._cached_sel = None  # 切换章节后旧选区失效
        self._has_outline = has_outline
        self._has_content = has_content
        self._has_deep = bool(ol.get("event_progress")) if ol else False

        if has_content:
            text = self._pm.get_chapter_text(self._project_root, chapter_id)
            self.editor.setPlainText(text)
        else:
            self.editor.setPlainText("")
            if has_outline:
                self.editor.setPlaceholderText(
                    "此章节已有大纲但尚未生成正文。\n"
                    "点击上方「生成此章正文」按钮，或使用「批量写作」来生成正文。"
                )
            else:
                self.editor.setPlaceholderText("章节正文将在这里显示...")

        self.title_label.setText(f"第{chapter_id}章 - {title}")
        self._update_word_count()

        # 更新大纲操作按钮可见性
        self._update_outline_buttons(has_outline, has_content)
        # 更新三区块「生成 / 重新生成」按钮的依赖守卫
        self._update_gen_buttons()

        # 更新导航按钮（上一章 / 下一章 / 无下一章时生成下一章大纲）
        outline_ids = {ol.get("chapter_id") for ol in outlines}
        self._update_nav(chapters, outline_ids)

        self.chapter_selected.emit(chapter_id, title)

    def _update_nav(self, chapters: list, outline_ids: set | None = None):
        """根据「正文文件 ∪ 大纲」全集更新导航按钮。

        下一章按钮两态：
        - 有下一章（无论已有正文还是有大纲待写）→ 「下一章 →」（跳转过去看大纲/正文）
        - 完全没有下一章 → 「生成下一章大纲」（规划它）
        """
        outline_ids = outline_ids or set()
        self._prev_id = None
        self._next_id = None
        self._next_mode = None

        all_ids = sorted({c["chapter_id"] for c in chapters} | set(outline_ids))
        if not all_ids or self._current_chapter_id not in all_ids:
            self.prev_btn.setEnabled(False)
            self.next_btn.setText("生成下一章大纲")
            self.next_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
            self.next_btn.setEnabled(True)
            self._next_mode = "outline"
            return

        idx = all_ids.index(self._current_chapter_id)
        self._prev_id = all_ids[idx - 1] if idx > 0 else None
        next_id = all_ids[idx + 1] if idx < len(all_ids) - 1 else None

        self.prev_btn.setEnabled(self._prev_id is not None)

        if next_id is None:
            self.next_btn.setText("生成下一章大纲")
            self.next_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
            self._next_mode = "outline"
        else:
            # 有下一章（大纲或正文）→ 直接跳过去，在那里看大纲并决定是否生成正文
            self.next_btn.setText("下一章 →")
            self.next_btn.setStyleSheet(TOOLBAR_BTN)
            self._next_id = next_id
            self._next_mode = "navigate"
        self.next_btn.setEnabled(True)

    def _on_prev(self):
        if self._prev_id is not None:
            self.navigate_requested.emit(self._prev_id)

    def _on_next(self):
        if self._next_mode == "navigate" and self._next_id is not None:
            self.navigate_requested.emit(self._next_id)
        else:
            self.generate_next_outline_requested.emit()

    def _update_outline_buttons(self, has_outline: bool, has_content: bool):
        """更新大纲相关操作按钮的可见性（确认/修订/校验/删除）。

        三区块的「生成 / 重新生成」按钮由 _update_gen_buttons 按依赖链统一管控。
        """
        if not has_outline or not self._current_outline:
            self.confirm_outline_btn.setVisible(False)
            self.revise_btn.setVisible(False)
            self.validate_btn.setVisible(False)
            # 删除按钮不依赖大纲：只要有正文或大纲之一就可以删除
            self.delete_chapter_btn.setVisible(bool(has_content or has_outline))
            return

        # 确认按钮
        confirmed = self._current_outline.get("confirmed", False)
        if confirmed:
            self.confirm_outline_btn.setText("取消确认")
            self.confirm_outline_btn.setStyleSheet(BTN_SM_DANGER)
        else:
            self.confirm_outline_btn.setText("确认大纲")
            self.confirm_outline_btn.setStyleSheet(BTN_SM)
        self.confirm_outline_btn.setVisible(True)

        # 修订：只要有正文即可局部改
        self.revise_btn.setVisible(bool(has_content))

        # 校验本章按钮 — 仅在有正文时可用
        self.validate_btn.setVisible(bool(has_content))

        # 删除本章按钮 — 只要有大纲或正文就可用
        self.delete_chapter_btn.setVisible(bool(has_outline or has_content))

    def _update_gen_buttons(self):
        """按依赖链启用/禁用三区块的「生成 / 重新生成」按钮。

        链：框架(阶段0) → 简大纲 → 深大纲 → 正文。
        前置内容缺失时按钮禁用，并给出 tooltip 解释；这样「没有前面内容就不会误生成」。
        """
        btns = (
            self.gen_short_btn, self.regen_short_btn,
            self.gen_deep_btn, self.regen_deep_btn,
            self.gen_body_btn, self.regen_body_btn,
        )
        if not self._current_chapter_id:
            for b in btns:
                b.setEnabled(False)
            return

        foundation = bool(
            self._pipeline and getattr(self._pipeline, "has_foundation", lambda: False)()
        )
        has_short = self._has_outline
        has_deep = self._has_deep
        has_body = self._has_content

        # 简大纲：前置 = 阶段0 框架
        self.gen_short_btn.setEnabled(foundation and not has_short)
        self.regen_short_btn.setEnabled(has_short)
        if not foundation:
            self.gen_short_btn.setToolTip("请先在右侧「管理」面板生成整本书框架（阶段0），再生成简大纲")

        # 深大纲：前置 = 本章简大纲
        self.gen_deep_btn.setEnabled(has_short and not has_deep)
        self.regen_deep_btn.setEnabled(has_deep)
        if not has_short:
            self.gen_deep_btn.setToolTip("请先生成本章简大纲，再生成深大纲")

        # 正文：前置 = 本章简大纲（或深大纲）
        self.gen_body_btn.setEnabled(has_short and not has_body)
        self.regen_body_btn.setEnabled(has_body)
        if not has_short:
            self.gen_body_btn.setToolTip("请先生成本章简大纲（或深大纲），再生成正文")

    def _on_gen_short(self):
        """请求生成当前章简大纲（前置守卫：阶段0 框架）。"""
        if not self._current_chapter_id:
            return
        if not (self._pipeline and self._pipeline.has_foundation()):
            QMessageBox.information(
                self, "提示",
                "请先在右侧「管理」面板生成整本书框架（阶段0），再生成简大纲。"
            )
            return
        self.gen_short_outline_requested.emit(self._current_chapter_id)

    def _on_regen_short(self):
        """请求重新生成当前章简大纲。"""
        if self._current_chapter_id:
            self.regen_short_outline_requested.emit(self._current_chapter_id)

    def _on_gen_deep(self):
        """请求生成当前章深大纲（前置守卫：本章简大纲）。"""
        if not self._current_chapter_id:
            return
        if not self._has_outline:
            QMessageBox.information(self, "提示", "请先生成本章简大纲，再生成深大纲。")
            return
        self.gen_deep_outline_requested.emit(self._current_chapter_id)

    def _on_regen_deep(self):
        """请求重新生成当前章深大纲。"""
        if self._current_chapter_id:
            self.regen_deep_outline_requested.emit(self._current_chapter_id)

    def _on_write_single_chapter_btn(self):
        """请求生成/重新生成当前章正文（前置守卫：本章简大纲）。"""
        if not self._current_chapter_id:
            return
        if not self._has_outline:
            QMessageBox.information(
                self, "提示", "请先生成本章简大纲（或深大纲），再生成正文。"
            )
            return
        self.write_single_chapter_requested.emit(self._current_chapter_id)

    def _on_toggle_confirm_outline(self):
        """确认/取消确认大纲。"""
        if not self._current_chapter_id or not self._current_outline:
            return
        confirmed = self._current_outline.get("confirmed", False)
        self.confirm_outline_requested.emit(self._current_chapter_id, not confirmed)

    def _on_confirm_all(self):
        """全部确认/全部取消确认。"""
        if not self._pipeline:
            return
        outlines = self._pipeline.get_chapter_outlines()
        if not outlines:
            return
        # 检查当前状态：如果全部已确认则取消，否则全部确认
        all_confirmed = all(ol.get("confirmed", False) for ol in outlines)
        new_state = not all_confirmed
        self.confirm_all_outlines_requested.emit(new_state)
        self.confirm_all_btn.setText("取消全部" if new_state else "全部确认")

    def _on_write_single_chapter(self):
        """请求生成当前章节正文。"""
        if not self._current_chapter_id:
            return
        self.write_single_chapter_requested.emit(self._current_chapter_id)

    def open_revise_dialog(self):
        """对外暴露：打开局部修订对话框（供菜单「AI 修订当前章节」调用）。"""
        self._on_revise()

    def _on_revise(self):
        """位置精确修订：取当前选中片段（或光标附近窗口），弹窗收集意见。

        关键修复：点击「修订」按钮时编辑器已失焦、选区被清空，live cursor 的
        hasSelection() 会是 False。故优先用失焦前缓存的 _cached_sel，确保用户
        精心选中的片段真正被修订，而不是退化为光标前后 ±250 字窗口。
        """
        if not self._current_chapter_id:
            return
        cursor = self.editor.textCursor()
        full = self.editor.toPlainText()
        L = len(full)
        if cursor.hasSelection():
            sel_start = cursor.selectionStart()
            sel_end = cursor.selectionEnd()
        elif self._cached_sel:
            # 失焦前缓存的选区（已 clamp 到当前文本长度，防止章节切换后越界）
            sel_start = max(0, min(self._cached_sel[0], L))
            sel_end = max(0, min(self._cached_sel[1], L))
        else:
            pos = cursor.position()
            sel_start = max(0, pos - 250)
            sel_end = min(L, pos + 250)
        segment = full[sel_start:sel_end]
        if not segment.strip():
            QMessageBox.information(
                self, "提示",
                "请先选中要修订的段落，或将光标放在要修订的位置附近。"
            )
            return
        ctx_before = full[max(0, sel_start - 400):sel_start]
        ctx_after = full[sel_end:sel_end + 400]

        dlg = ReviseDialog(segment, ctx_before, ctx_after, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            instruction = dlg.instruction()
            if not instruction:
                return
            self.revise_segment_requested.emit(
                self._current_chapter_id, sel_start, sel_end,
                segment, ctx_before, ctx_after, instruction
            )

    def _on_validate_chapter(self):
        """请求校验当前章节。"""
        if not self._current_chapter_id:
            return
        self.validate_chapter_requested.emit(self._current_chapter_id)

    def _on_delete_chapter(self):
        """请求删除当前章节（主窗口执行真正的删除与刷新）。"""
        if self._current_chapter_id is None or self._current_chapter_id <= 0:
            # 尝试通过大纲区内容反查章节ID（兼容编辑器未刷新状态）
            logger.warning("删除失败: _current_chapter_id 无效 "
                           f"(value={self._current_chapter_id})")
            return
        self.delete_chapter_requested.emit(self._current_chapter_id)

    def clear_view(self):
        """清空编辑器（章节被删除后调用）。"""
        if self._dirty and self._current_chapter_id:
            self._flush_save()
        self._current_chapter_id = None
        self._current_title = ""
        self._current_outline = None
        self._prev_id = None
        self._next_id = None
        self._next_mode = None
        self._cached_sel = None
        self.editor.setPlainText("")
        self.editor.setPlaceholderText("章节正文将在这里显示...")
        self.clear_outline_detail()
        self.title_label.setText("请选择或新建章节")
        self.prev_btn.setEnabled(False)
        self.next_btn.setText("生成下一章大纲")
        self.next_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
        self.next_btn.setEnabled(True)
        self.confirm_outline_btn.setVisible(False)
        self.revise_btn.setVisible(False)
        self.validate_btn.setVisible(False)
        self.delete_chapter_btn.setVisible(False)
        self._has_outline = False
        self._has_content = False
        self._has_deep = False
        self._update_gen_buttons()
        self._update_word_count()

    def _on_text_changed(self):
        self._update_word_count()
        self._dirty = True
        # 防抖自动保存：800ms 内无新改动则落盘
        self._save_timer.start(800)
        self.text_changed.emit(self.editor.toPlainText())

    def _flush_save(self):
        """立即落盘当前章节（切换/清空前调用，跳过防抖）。"""
        self._save_timer.stop()
        self._on_save()

    def _on_save(self):
        if not self._current_chapter_id or not self._pm:
            return
        text = self.editor.toPlainText()
        self._pm.save_chapter_text(
            self._project_root, self._current_chapter_id, self._current_title, text
        )
        self._dirty = False
        self.chapter_saved.emit(self._current_chapter_id, self._current_title, text)

    def _update_word_count(self):
        text = self.editor.toPlainText()
        count = len([c for c in text if c.strip()])
        self.word_count_label.setText(f"{count} 字")

    def get_current_chapter(self) -> tuple[int, str, str]:
        """返回 (chapter_id, title, text)。"""
        return (self._current_chapter_id, self._current_title, self.editor.toPlainText())

    def splice_text(self, start: int, end: int, replacement: str):
        """将 [start:end] 区间替换为 replacement，并立即保存（用于局部修订）。"""
        self.editor.blockSignals(True)
        full = self.editor.toPlainText()
        new_text = full[:start] + replacement + full[end:]
        self.editor.setPlainText(new_text)
        self.editor.blockSignals(False)
        self._update_word_count()
        self._dirty = True
        self._on_save()

    def set_chapter_text(self, text: str):
        """设置编辑器内容（不触发保存）。"""
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        self._dirty = False
        self._update_word_count()

    def append_text(self, text: str):
        """追加文本到编辑器末尾。"""
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)

    def set_short_outline(self, text: str):
        """设置简大纲内容（项目级指令）。"""
        self.short_outline_detail.setPlainText(text if text else "暂无简大纲")

    def set_deep_outline(self, text: str):
        """设置深大纲内容（故事内容 + 文笔落点 + 事件进展）。"""
        self.deep_outline_detail.setPlainText(text if text else "暂无深大纲")

    def clear_outline_detail(self):
        """清空大纲详情两块。"""
        self.short_outline_detail.clear()
        self.short_outline_detail.setPlaceholderText("选中章节后，这里将显示该章节的简大纲（项目级指令）...")
        self.deep_outline_detail.clear()
        self.deep_outline_detail.setPlaceholderText("选中章节后，这里将显示该章节的深大纲（故事内容 + 写作重心 + 事件进展）...")

    def update_confirm_status(self, confirmed: bool):
        """更新当前章节的大纲确认状态显示（确认操作后调用）。"""
        if self._current_outline is not None:
            self._current_outline["confirmed"] = confirmed
        self._update_outline_buttons(
            self._current_outline is not None,
            bool(self._current_chapter_id and self._pm and
                 self._pm.get_chapter_text(self._project_root, self._current_chapter_id))
        )
