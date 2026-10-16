"""项目管理面板：项目概览、章节管理、设定管理、总纲管理。

UI 设计：
- 统一按钮风格，避免颜色冲突
- 适当间距，减少密集感
- 章节大纲与章节列表融合为统一视图
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget, QLabel, QTextEdit,
    QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QFormLayout,
    QLineEdit, QComboBox, QMessageBox, QGroupBox, QProgressBar,
    QSpinBox, QInputDialog, QAbstractItemView, QSplitter, QDialog,
    QStyleOptionButton, QStyle, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QPoint, QItemSelectionModel, QThread
from PyQt6.QtGui import QColor, QMouseEvent, QTextOption
from pathlib import Path

from domains.novel.ui.styles import (
    BTN_PRIMARY, BTN_SECONDARY, BTN_DANGER,
    BTN_SM, BTN_SM_ACCENT, BTN_SM_DANGER,
    HINT_STYLE, LABEL_STYLE, SUCCESS_TEXT, ERROR_TEXT, LIST_STYLE,
)
from domains.novel.ui.widgets.flow_layout import FlowLayout


def _multiline_dialog(parent, title: str, label: str, prefill: str = "",
                      readonly_text: str = "") -> tuple[str, bool]:
    """自定义多行输入对话框 — 限制大小，防止弹窗过长。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedWidth(500)
    dlg.setMinimumHeight(200)
    dlg.setMaximumHeight(450)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

    layout.addWidget(QLabel(label))

    # 只读展示区（如当前大纲预览）
    if readonly_text:
        ro = QTextEdit()
        ro.setReadOnly(True)
        ro.setPlainText(readonly_text)
        ro.setMaximumHeight(120)
        ro.setStyleSheet("background-color: #1e1e2e; color: #a6adc8; border: 1px solid #313244;")
        layout.addWidget(ro)

    # 输入区
    edit = QTextEdit()
    edit.setPlainText(prefill)
    edit.setMinimumHeight(60)
    edit.setMaximumHeight(150)
    layout.addWidget(edit, stretch=1)

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    ok_btn = QPushButton("确定")
    ok_btn.setStyleSheet(BTN_PRIMARY)
    ok_btn.setMinimumWidth(80)
    cancel_btn = QPushButton("取消")
    cancel_btn.setStyleSheet(BTN_SECONDARY)
    cancel_btn.setMinimumWidth(80)
    btn_row.addWidget(ok_btn)
    btn_row.addWidget(cancel_btn)
    layout.addLayout(btn_row)

    ok_btn.clicked.connect(dlg.accept)
    cancel_btn.clicked.connect(dlg.reject)

    result = dlg.exec()
    return edit.toPlainText(), result == QDialog.DialogCode.Accepted


class _DragSelectTable(QTableWidget):
    """支持鼠标拖拽多选行的表格。

    用法：像普通 QTableWidget 一样使用，
    按住鼠标左键拖过时自动选中经过的行。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_selecting = False
        self._drag_start_row = -1
        self._drag_current_row = -1
        self.setMouseTracking(True)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            row = self.rowAt(pos.y())
            if row >= 0:
                self._drag_selecting = True
                self._drag_start_row = row
                self._drag_current_row = row
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_selecting:
            pos = event.position().toPoint()
            row = self.rowAt(pos.y())
            if row >= 0 and row != self._drag_current_row:
                self._drag_current_row = row
                # 选中从起始行到当前行的所有行
                lo = min(self._drag_start_row, row)
                hi = max(self._drag_start_row, row)
                # 先清空选择
                self.clearSelection()
                # 选中范围
                sel_model = self.selectionModel()
                for r in range(lo, hi + 1):
                    sel_model.select(
                        self.model().index(r, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
                    )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_selecting = False
        super().mouseReleaseEvent(event)


class VolumeGenWorker(QThread):
    """从总纲解析卷结构的工作线程（避免阻塞 UI）。"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, pipeline, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline

    def run(self):
        try:
            vols = self._pipeline.regenerate_volumes_from_master()
            self.finished.emit(vols)
        except Exception as e:
            self.error.emit(str(e))


class EventPlanWorker(QThread):
    """AI 规划事件账本的工作线程（避免阻塞 UI）。"""
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, memory, use_ai: bool = True, parent=None):
        super().__init__(parent)
        self._memory = memory
        self._use_ai = use_ai

    def run(self):
        try:
            n = self._memory.plan_story_events(use_ai=self._use_ai)
            self.finished.emit(n)
        except Exception as e:
            self.error.emit(str(e))


class ProjectDashboardWidget(QWidget):
    """项目管理面板 — 项目概览 + 章节 + 设定 + 总纲。"""

    setting_saved = pyqtSignal(str, str)
    outline_saved = pyqtSignal(str, str)
    generate_outlines_requested = pyqtSignal(int, int, str)
    generate_chapter_requested = pyqtSignal(int)
    batch_generate_requested = pyqtSignal(list)
    confirm_outline_requested = pyqtSignal(int, bool)
    regenerate_outline_requested = pyqtSignal(int, str)
    batch_regenerate_outlines_requested = pyqtSignal(int, int, str)  # start, end, feedback
    reinit_foundation_requested = pyqtSignal()
    # 单项重新生成信号
    regenerate_setting_requested = pyqtSignal(str, str)  # setting_type, user_request
    regenerate_master_outline_requested = pyqtSignal(str, str)  # outline_type, user_request
    regenerate_synopsis_requested = pyqtSignal(str)  # user_request
    volumes_changed = pyqtSignal()  # 卷结构变更（通知左侧项目树刷新）
    deep_outline_requested = pyqtSignal()  # 生成深大纲（按卷批量）
    framework_requested = pyqtSignal()  # 生成整本书框架（阶段0）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_root = ""
        self._project_manager = None
        self._pipeline = None
        self._memory = None
        self._init_ui()

    def set_data(self, project_manager, project_root: str, pipeline=None, memory=None):
        self._project_manager = project_manager
        self._project_root = project_root
        self._pipeline = pipeline
        self._memory = memory
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.tabs = QTabWidget()
        # 二级 Tab 标题（项目概览/设定管理/总纲管理/卷管理/故事脊柱/事件账本）
        # 必须完整可读，窄面板下用滚动箭头而非「…」省略。
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setMovable(False)
        self.tabs.tabBar().setExpanding(False)  # 不要强行均分宽度，窄了走滚动
        self.tabs.setMinimumSize(0, 0)  # 不锁死高度
        self.tabs.setStyleSheet(
            "QTabBar { font-size: 13px; }"
            "QTabBar::tab { font-size: 13px; padding: 6px 12px; min-width: 84px;"
            "  color: #a6adc8; background: #1e1e2e; border: 1px solid #313244;"
            "  border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px;"
            "  margin-right: 1px; }"
            "QTabBar::tab:selected { color: #cdd6f4; background: #313244; border-color: #45475a; }"
            "QTabBar::tab:hover:!selected { background: #262637; }"
            "QTabBar::scroller { width: 22px; }"
        )
        self.tabs.currentChanged.connect(self._on_dash_tab_changed)

        self.overview_tab = QWidget()
        self._init_overview_tab()
        self.tabs.addTab(self.overview_tab, "项目概览")

        self.chapters_tab = QWidget()
        # self._init_chapters_tab()  # 章节管理已集成到中间编辑器区域
        # self.tabs.addTab(self.chapters_tab, "章节")

        self.settings_tab = QWidget()
        self._init_settings_tab()
        self.tabs.addTab(self.settings_tab, "设定管理")

        self.outline_tab = QWidget()
        self._init_outline_tab()
        self.tabs.addTab(self.outline_tab, "总纲管理")

        self.volumes_tab = QWidget()
        self._init_volumes_tab()
        self.tabs.addTab(self.volumes_tab, "卷管理")

        self.spine_tab = QWidget()
        self._init_spine_tab()
        self.tabs.addTab(self.spine_tab, "故事脊柱")

        self.event_tab = QWidget()
        self._init_event_tab()
        self.tabs.addTab(self.event_tab, "事件账本")

        layout.addWidget(self.tabs)

    # ============================================================
    # 项目概览
    # ============================================================

    def _init_overview_tab(self):
        layout = QVBoxLayout(self.overview_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        hint = QLabel("本页是项目总入口：查看基本信息与简介、跟踪写作进度、"
                      "初始化 AI 基础设定，并用底部「生成控制」批量生成大纲 / 深大纲 / 正文。")
        hint.setStyleSheet(HINT_STYLE)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # --- 基本信息：简介独占大块 ---
        info_group = QGroupBox("基本信息")
        info_vlayout = QVBoxLayout(info_group)
        info_vlayout.setSpacing(4)
        info_vlayout.setContentsMargins(6, 4, 6, 4)

        # 上排：书名/题材/作者/创建 用 2x2 网格，只占很小高度
        info_grid = QGridLayout()
        info_grid.setSpacing(4)
        info_grid.setVerticalSpacing(2)
        info_grid.setColumnStretch(1, 1)
        info_grid.setColumnStretch(3, 1)

        self.name_label = QLabel("-")
        self.genre_label = QLabel("-")
        self.author_label = QLabel("-")
        self.created_label = QLabel("-")

        info_grid.addWidget(QLabel("书名:"), 0, 0)
        info_grid.addWidget(self.name_label, 0, 1)
        info_grid.addWidget(QLabel("题材:"), 0, 2)
        info_grid.addWidget(self.genre_label, 0, 3)
        info_grid.addWidget(QLabel("作者:"), 1, 0)
        info_grid.addWidget(self.author_label, 1, 1)
        info_grid.addWidget(QLabel("创建:"), 1, 2)
        info_grid.addWidget(self.created_label, 1, 3)
        info_vlayout.addLayout(info_grid)

        # 简介：独占大框，真正可以拉伸
        synopsis_row = FlowLayout(margin=0, spacing=6)
        synopsis_label = QLabel("简介:")
        synopsis_row.addWidget(synopsis_label)

        self.synopsis_editable = QTextEdit()
        self.synopsis_editable.setReadOnly(True)
        self.synopsis_editable.setVisible(False)
        self.synopsis_editable.setMaximumHeight(120)

        regen_synopsis_btn = QPushButton("AI 重新生成简介")
        regen_synopsis_btn.setStyleSheet(BTN_SECONDARY)
        regen_synopsis_btn.setToolTip("AI 重新生成小说简介")
        regen_synopsis_btn.clicked.connect(self._on_regen_synopsis)
        synopsis_row.addWidget(regen_synopsis_btn)

        save_synopsis_btn = QPushButton("保存简介")
        save_synopsis_btn.setStyleSheet(BTN_PRIMARY)
        save_synopsis_btn.setToolTip("保存修改后的简介")
        save_synopsis_btn.clicked.connect(self._on_save_synopsis)
        save_synopsis_btn.setVisible(False)
        self._save_synopsis_btn = save_synopsis_btn
        synopsis_row.addWidget(save_synopsis_btn)

        edit_synopsis_btn = QPushButton("编辑简介")
        edit_synopsis_btn.setStyleSheet(BTN_SECONDARY)
        edit_synopsis_btn.setToolTip("手动编辑简介")
        edit_synopsis_btn.clicked.connect(self._on_toggle_edit_synopsis)
        self._edit_synopsis_btn = edit_synopsis_btn
        synopsis_row.addWidget(edit_synopsis_btn)

        info_vlayout.addLayout(synopsis_row)

        self.desc_edit = QTextEdit()
        self.desc_edit.setReadOnly(True)
        self.desc_edit.setMinimumHeight(200)
        info_vlayout.addWidget(self.desc_edit, stretch=1)

        splitter.addWidget(info_group)

        # --- 写作进度：横向紧凑 ---
        progress_group = QGroupBox("写作进度")
        prog_hlayout = QHBoxLayout(progress_group)
        prog_hlayout.setSpacing(12)
        prog_hlayout.setContentsMargins(6, 4, 6, 4)

        self.chapter_count_label = QLabel("0")
        self.total_words_label = QLabel("0 字")
        self.target_words_label = QLabel("-")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)

        prog_form = QFormLayout()
        prog_form.setSpacing(2)
        prog_form.addRow("章节:", self.chapter_count_label)
        prog_form.addRow("总字数:", self.total_words_label)
        prog_form.addRow("目标:", self.target_words_label)
        prog_hlayout.addLayout(prog_form)
        prog_hlayout.addStretch()
        prog_hlayout.addWidget(self.progress_bar)
        splitter.addWidget(progress_group)

        # --- AI 基础设定 ---
        foundation_group = QGroupBox("AI 基础设定")
        foundation_layout = QVBoxLayout(foundation_group)
        foundation_layout.setSpacing(4)
        foundation_layout.setContentsMargins(6, 4, 6, 4)

        self.foundation_status = QLabel("未检测")
        foundation_layout.addWidget(self.foundation_status)

        fbtn_row = QHBoxLayout()
        fbtn_row.setSpacing(8)
        check_btn = QPushButton("检查基础设定")
        check_btn.setStyleSheet(BTN_SECONDARY)
        check_btn.clicked.connect(self._check_foundation)
        fbtn_row.addWidget(check_btn)

        reinit_btn = QPushButton("重新 AI 初始化")
        reinit_btn.setStyleSheet(BTN_SECONDARY)
        reinit_btn.clicked.connect(self.reinit_foundation_requested.emit)
        fbtn_row.addWidget(reinit_btn)
        foundation_layout.addLayout(fbtn_row)
        splitter.addWidget(foundation_group)

        splitter.setSizes([420, 70, 70])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter)

        # --- 生成控制（批量范围 + 深大纲 + 批量写正文）---
        gen_group = QGroupBox("生成控制（批量选项）")
        gen_layout = QVBoxLayout(gen_group)
        gen_layout.setContentsMargins(6, 4, 6, 4)
        gen_layout.setSpacing(6)

        gen_row1 = QHBoxLayout()
        gen_row1.setSpacing(6)
        gen_row1.addWidget(QLabel("范围:"))
        self.gen_scope_combo = QComboBox()
        self.gen_scope_combo.setMinimumWidth(160)
        gen_row1.addWidget(self.gen_scope_combo, stretch=1)
        gen_layout.addLayout(gen_row1)

        gen_row2 = QHBoxLayout()
        gen_row2.setSpacing(8)
        deep_btn = QPushButton("生成深大纲（按卷批量）")
        deep_btn.setStyleSheet(BTN_PRIMARY)
        deep_btn.setToolTip("AI 一次性把薄大纲展开为深大纲（事件进展规格），按卷批量规划")
        deep_btn.clicked.connect(self.deep_outline_requested.emit)
        gen_row2.addWidget(deep_btn)

        batch_btn = QPushButton("批量生产正文")
        batch_btn.setStyleSheet(BTN_SM_ACCENT)
        batch_btn.setToolTip("按所选范围批量生成已确认大纲的正文")
        batch_btn.clicked.connect(self._on_batch_generate_scoped)
        gen_row2.addWidget(batch_btn)
        gen_layout.addLayout(gen_row2)

        gen_row3 = QHBoxLayout()
        gen_row3.setSpacing(8)
        fw_btn = QPushButton("生成整本书框架（阶段0）")
        fw_btn.setStyleSheet(BTN_SM_ACCENT)
        fw_btn.setToolTip("一次性生成整本书框架：粗大纲→背景→主线大事件→粗设定→卷（扁鹊大哥：灭矛盾于未发）")
        fw_btn.clicked.connect(self.framework_requested.emit)
        gen_row3.addWidget(fw_btn)
        gen_layout.addLayout(gen_row3)

        layout.addWidget(gen_group)

    def _check_foundation(self):
        if not self._pipeline:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        has_foundation = self._pipeline.has_foundation()
        if has_foundation:
            self.foundation_status.setText("✅ 世界观 + 总纲已生成，可以开始生成章节大纲")
            self.foundation_status.setStyleSheet(SUCCESS_TEXT)
            QMessageBox.information(self, "检查完成",
                "AI 基础设定已完整!\n\n"
                "切换到「章节」页，点击「生成大纲」让 AI 批量生成章节大纲。")
        else:
            self.foundation_status.setText("❌ 基础设定不完整")
            self.foundation_status.setStyleSheet(ERROR_TEXT)
            QMessageBox.warning(self, "基础设定不完整",
                "世界观或总纲尚未生成。\n\n"
                "请点击「重新 AI 初始化」让 AI 自动生成。")

    # ============================================================
    # 章节管理（大纲 + 已写章节 统一视图）
    # ============================================================

    def _init_chapters_tab(self):
        layout = QVBoxLayout(self.chapters_tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # --- 单行工具栏（紧凑按钮，减小 padding 避免文字被截断） ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        # 紧凑按钮样式（统一暗色，避免彩虹按钮）
        _BTN_SM_PRI = BTN_SM_ACCENT
        _BTN_SM_SEC = BTN_SM
        _BTN_SM_SUC = BTN_SM
        _BTN_SM_DAN = BTN_SM_DANGER
        _BTN_SM_ACC = BTN_SM_ACCENT

        toolbar.addWidget(QLabel("起:"))
        self.start_chapter_spin = QSpinBox()
        self.start_chapter_spin.setMinimum(1)
        self.start_chapter_spin.setMaximum(2000)
        self.start_chapter_spin.setFixedWidth(50)
        toolbar.addWidget(self.start_chapter_spin)

        toolbar.addWidget(QLabel("数:"))
        self.chapter_count_spin = QSpinBox()
        self.chapter_count_spin.setMinimum(1)
        self.chapter_count_spin.setMaximum(2000)
        self.chapter_count_spin.setValue(5)
        self.chapter_count_spin.setFixedWidth(45)
        toolbar.addWidget(self.chapter_count_spin)

        gen_btn = QPushButton("生成")
        gen_btn.setStyleSheet(_BTN_SM_PRI)
        gen_btn.setFixedWidth(50)
        gen_btn.clicked.connect(self._on_generate_outlines)
        toolbar.addWidget(gen_btn)

        toolbar.addSpacing(6)

        self.stats_total_chapters = QLabel("0")
        self.stats_total_words = QLabel("0")
        toolbar.addWidget(QLabel("共:"))
        toolbar.addWidget(self.stats_total_chapters)
        toolbar.addWidget(QLabel("字:"))
        toolbar.addWidget(self.stats_total_words)

        toolbar.addStretch()

        confirm_btn = QPushButton("确认")
        confirm_btn.setStyleSheet(_BTN_SM_SUC)
        confirm_btn.setFixedWidth(50)
        confirm_btn.clicked.connect(self._on_confirm_outline)
        toolbar.addWidget(confirm_btn)

        regen_btn = QPushButton("重写")
        regen_btn.setStyleSheet(_BTN_SM_SEC)
        regen_btn.setFixedWidth(50)
        regen_btn.setToolTip("重新生成选中行的大纲")
        regen_btn.clicked.connect(self._on_regenerate_outline)
        toolbar.addWidget(regen_btn)

        select_all_btn = QPushButton("全选")
        select_all_btn.setStyleSheet(_BTN_SM_SEC)
        select_all_btn.setFixedWidth(50)
        select_all_btn.setToolTip("勾选/取消所有")
        select_all_btn.clicked.connect(self._on_select_all)
        toolbar.addWidget(select_all_btn)

        del_btn = QPushButton("删除")
        del_btn.setStyleSheet(_BTN_SM_DAN)
        del_btn.setFixedWidth(50)
        del_btn.clicked.connect(self._on_delete_outline)
        toolbar.addWidget(del_btn)

        batch_btn = QPushButton("批量写")
        batch_btn.setStyleSheet(_BTN_SM_PRI)
        batch_btn.setFixedWidth(60)
        batch_btn.clicked.connect(self._on_batch_generate)
        toolbar.addWidget(batch_btn)

        layout.addLayout(toolbar)

        # --- 章节表格 ---
        self.chapter_table = _DragSelectTable()
        self.chapter_table.setColumnCount(6)
        self.chapter_table.setHorizontalHeaderLabels(["", "章节", "标题", "类型", "字数", "状态"])
        self.chapter_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.chapter_table.setColumnWidth(0, 44)
        self.chapter_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.chapter_table.setColumnWidth(1, 50)
        self.chapter_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.chapter_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.chapter_table.setColumnWidth(3, 85)
        self.chapter_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.chapter_table.setColumnWidth(4, 55)
        self.chapter_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.chapter_table.setColumnWidth(5, 90)
        self.chapter_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.chapter_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.chapter_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.chapter_table.setAlternatingRowColors(True)
        self.chapter_table.verticalHeader().setDefaultSectionSize(36)
        self.chapter_table.verticalHeader().setVisible(False)
        self.chapter_table.setStyleSheet("""
            QTableWidget {
                background-color: #1e1e2e;
                alternate-background-color: #252536;
                color: #cdd6f4;
                gridline-color: #313244;
                border: 1px solid #313244;
            }
            QTableWidget::item:selected {
                background-color: #45475a;
                color: #cdd6f4;
            }
            QHeaderView::section {
                background-color: #313244;
                color: #cdd6f4;
                padding: 4px;
                border: 1px solid #45475a;
            }
            QTableWidget::indicator {
                width: 20px;
                height: 20px;
            }
            QTableWidget::indicator:unchecked {
                background-color: #45475a;
                border: 2px solid #6c7086;
                border-radius: 4px;
            }
            QTableWidget::indicator:checked {
                background-color: #585b70;
                border: 2px solid #585b70;
                border-radius: 4px;
            }
            QTableWidget::indicator:checked::after {
                color: #1e1e2e;
            }
        """)
        self.chapter_table.itemClicked.connect(self._on_chapter_clicked)
        layout.addWidget(self.chapter_table, stretch=1)

        # --- 详情面板（紧凑，固定高度） ---
        detail_group = QGroupBox("详情")
        detail_layout = QVBoxLayout(detail_group)
        detail_layout.setContentsMargins(6, 2, 6, 4)

        # 简大纲 / 深大纲 分离显示（与编辑器一致）
        short_lbl = QLabel("📋 简大纲（项目级指令）")
        short_lbl.setStyleSheet("QLabel { color: #cba6f7; font-weight: 500; font-size: 11px; }")
        detail_layout.addWidget(short_lbl)
        self.short_detail = QTextEdit()
        self.short_detail.setReadOnly(True)
        self.short_detail.setMinimumHeight(60)
        self.short_detail.setPlaceholderText("选中上方章节查看简大纲...")
        self.short_detail.setStyleSheet("QTextEdit { background-color: #252536; color: #a6adc8; border: none; font-size: 11px; }")
        detail_layout.addWidget(self.short_detail)

        deep_lbl = QLabel("📖 深大纲（故事内容 · 文笔落点）")
        deep_lbl.setStyleSheet("QLabel { color: #7ee787; font-weight: 500; font-size: 11px; }")
        detail_layout.addWidget(deep_lbl)
        self.deep_detail = QTextEdit()
        self.deep_detail.setReadOnly(True)
        self.deep_detail.setMinimumHeight(80)
        self.deep_detail.setPlaceholderText("选中上方章节查看深大纲（事件进展）...")
        self.deep_detail.setStyleSheet("QTextEdit { background-color: #252536; color: #a6adc8; border: none; font-size: 11px; }")
        detail_layout.addWidget(self.deep_detail)
        layout.addWidget(detail_group, stretch=1)

    def _refresh_chapters_table(self):
        """刷新统一章节表 — 合并已写章节和大纲。"""
        self.chapter_table.setRowCount(0)

        # 收集所有章节号
        written_chapters = {}
        if self._project_manager:
            try:
                chapters = self._project_manager.get_chapter_list(self._project_root)
                for ch in chapters:
                    written_chapters[ch["chapter_id"]] = ch
            except Exception:
                pass

        outlines = {}
        if self._pipeline:
            try:
                for ol in self._pipeline.get_chapter_outlines():
                    outlines[ol.get("chapter_id", 0)] = ol
            except Exception:
                pass

        all_ids = sorted(set(list(written_chapters.keys()) + list(outlines.keys())))
        if not all_ids:
            return

        self.chapter_table.setRowCount(len(all_ids))

        for i, cid in enumerate(all_ids):
            written = written_chapters.get(cid)
            outline = outlines.get(cid)

            # 复选框列（列0）
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.chapter_table.setItem(i, 0, check_item)

            # 章节号（列1）
            ch_item = QTableWidgetItem(f"第{cid}章")
            ch_item.setData(Qt.ItemDataRole.UserRole, {"cid": cid, "outline": outline, "written": written})
            self.chapter_table.setItem(i, 1, ch_item)

            # 标题（列2）
            title = ""
            if outline:
                title = outline.get("title", "")
            if not title and written:
                title = written.get("title", "")
            self.chapter_table.setItem(i, 2, QTableWidgetItem(title))

            # 类型（列3）
            ct_label = ""
            if outline:
                ct_label = outline.get("chapter_type_label", "")
            self.chapter_table.setItem(i, 3, QTableWidgetItem(ct_label))

            # 字数（列4）
            word_count = ""
            if written:
                word_count = f"{written.get('word_count', 0):,}"
            self.chapter_table.setItem(i, 4, QTableWidgetItem(word_count))

            # 状态（列5）
            if written and outline:
                status_text = "✅ 已写正文"
                status_color = QColor("#a6e3a1")
            elif written:
                status_text = "✅ 已写正文"
                status_color = QColor("#a6e3a1")
            elif outline and outline.get("confirmed"):
                status_text = "🔵 已确认"
                status_color = QColor("#89b4fa")
            elif outline:
                status_text = "🟡 待确认"
                status_color = QColor("#f9e2af")
            else:
                status_text = "-"
                status_color = QColor("#6c7086")

            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(status_color)
            self.chapter_table.setItem(i, 5, status_item)

        # 更新统计
        total_ch = len(written_chapters)
        total_wc = sum(ch.get("word_count", 0) for ch in written_chapters.values())
        self.stats_total_chapters.setText(str(total_ch))
        self.stats_total_words.setText(f"{total_wc:,}")

    def _on_chapter_clicked(self, item):
        """点击章节行 → 显示详情。"""
        # data 存在第1列（章节号列）
        row = item.row()
        data_item = self.chapter_table.item(row, 1)
        data = data_item.data(Qt.ItemDataRole.UserRole) if data_item else None
        if not data:
            return

        cid = data["cid"]
        outline = data.get("outline")
        written = data.get("written")

        short_parts = [f"━━━ 第{cid}章 ━━━"]
        deep_parts = []

        if outline:
            short_parts.append(f"标题: {outline.get('title', '')}")
            short_parts.append(f"类型: {outline.get('chapter_type_label', '')}")
            short_parts.append(f"")
            short_parts.append(f"【核心事件】")
            short_parts.append(outline.get("core_events", "（无）"))
            short_parts.append(f"")
            short_parts.append(f"【开篇钩子】")
            short_parts.append(outline.get("opening_hook", "（无）"))
            short_parts.append(f"")
            short_parts.append(f"【结尾钩子】")
            short_parts.append(outline.get("ending_hook", "（无）"))
            short_parts.append(f"")
            short_parts.append(f"【伏笔策略】")
            short_parts.append(outline.get("foreshadowing", "none"))
            short_parts.append(f"")
            short_parts.append(f"【情节概要】")
            short_parts.append(outline.get("plot_summary", "（无）"))
            short_parts.append(f"")
            short_parts.append(f"【时间锚点】")
            short_parts.append(outline.get("time_anchor", "（无）"))
            # 深大纲（本章写作重心 + 事件进展 + 每拍文笔落点）
            wf = str(outline.get("writing_focus") or "").strip()
            if wf:
                deep_parts.append("【本章写作重心】")
                deep_parts.append(f"  {wf}")
                deep_parts.append("")
            beats = outline.get("event_progress") or []
            if beats:
                # 覆盖率/可验收性是价值锚点，"厚度"不是（2026-08-01 已废弃）
                deep_parts.append(f"事件进展（共 {len(beats)} 拍，按序推进）：")
                for i, b in enumerate(beats, 1):
                    if not isinstance(b, dict):
                        continue
                    s = b.get("summary") or ""
                    ck = b.get("checkpoint") or ""
                    pf = b.get("prose_focus") or ""
                    line = f"  {i}. {s}"
                    if ck:
                        line += f" ｜硬事实：{ck}"
                    if pf:
                        line += f" ｜文笔：{pf}"
                    deep_parts.append(line)
            else:
                deep_parts.append("（本章暂无深大纲 / 事件进展规格）")

        if written:
            short_parts.append(f"")
            short_parts.append(f"━━━ 已写正文 ━━━")
            short_parts.append(f"字数: {written.get('word_count', 0):,}")
            short_parts.append(f"")
            # 显示正文前300字预览
            try:
                text = self._project_manager.get_chapter_text(self._project_root, cid)
                if text:
                    preview = text[:500]
                    if len(text) > 500:
                        preview += "\n\n......（完整内容请在编辑器中查看）"
                    short_parts.append(preview)
            except Exception:
                pass

        if not outline and not written:
            short_parts.append("（无大纲信息）")
            deep_parts.append("（无大纲信息）")

        self.short_detail.setPlainText("\n".join(short_parts))
        self.deep_detail.setPlainText("\n".join(deep_parts))

    def _on_cell_clicked(self, row: int, col: int):
        """点击单元格 — 处理复选框列。"""
        if col == 0:  # 复选框列：切换勾选状态
            item = self.chapter_table.item(row, 0)
            if item is not None:
                new_state = Qt.CheckState.Checked if item.checkState() != Qt.CheckState.Checked else Qt.CheckState.Unchecked
                item.setCheckState(new_state)

    def _on_select_all(self):
        """全选/取消全选：切换所有复选框。"""
        if self.chapter_table.rowCount() == 0:
            return
        # 判断当前是否全部已勾选
        all_checked = all(
            self.chapter_table.item(r, 0).checkState() == Qt.CheckState.Checked
            for r in range(self.chapter_table.rowCount())
            if self.chapter_table.item(r, 0) is not None
        )
        new_state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        for r in range(self.chapter_table.rowCount()):
            item = self.chapter_table.item(r, 0)
            if item is not None:
                item.setCheckState(new_state)

    # ============================================================
    # 章节大纲操作
    # ============================================================

    def _on_generate_outlines(self):
        if not self._pipeline:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        if not self._pipeline.has_foundation():
            QMessageBox.warning(self, "基础设定不完整",
                "请先在「项目概览」页完成 AI 初始化。\n"
                "AI 需要先完善基础信息后才能生成章节大纲。")
            return

        start = self.start_chapter_spin.value()
        count = self.chapter_count_spin.value()
        end = start + count - 1

        user_request, ok = _multiline_dialog(
            self, "生成章节大纲",
            f"将生成第{start}章到第{end}章的章节大纲（共{count}章）。\n"
            "可选：输入对这批章节的总体要求（留空则AI自主规划）",
        )
        if not ok:
            return

        self.generate_outlines_requested.emit(start, end, user_request.strip())

    def _on_confirm_outline(self):
        row = self.chapter_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一个章节")
            return
        item = self.chapter_table.item(row, 1)
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data or not data.get("outline"):
            QMessageBox.warning(self, "提示", "该章节没有大纲，无法确认")
            return

        cid = data["cid"]
        outline = data["outline"]
        confirmed = not outline.get("confirmed", False)
        self.confirm_outline_requested.emit(cid, confirmed)

    def _on_regenerate_outline(self):
        row = self.chapter_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一个章节")
            return
        item = self.chapter_table.item(row, 1)
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data or not data.get("outline"):
            QMessageBox.warning(self, "提示", "该章节没有大纲，无法重新生成")
            return

        cid = data["cid"]
        outline = data["outline"]

        # 显示完整当前大纲（只读），让用户能看到全文
        current_summary = outline.get('plot_summary', '')
        feedback, ok = _multiline_dialog(
            self, f"重新生成第{cid}章大纲",
            "修改意见（留空则完全重新生成）:",
            readonly_text=current_summary,
        )
        if ok:
            self.regenerate_outline_requested.emit(cid, feedback.strip())

    def _on_batch_regenerate_outlines(self):
        """批量重新生成选中的章节大纲（按复选框）。"""
        # 读取复选框列，收集勾选的章节
        cids = []
        for row in range(self.chapter_table.rowCount()):
            check_item = self.chapter_table.item(row, 0)
            if check_item is not None and check_item.checkState() == Qt.CheckState.Checked:
                data_item = self.chapter_table.item(row, 1)
                data = data_item.data(Qt.ItemDataRole.UserRole) if data_item else None
                if data and data.get("outline"):
                    cids.append(data["cid"])

        if not cids:
            QMessageBox.warning(self, "提示", "请先勾选要重新生成的章节（点击最左侧复选框）")
            return

        start = min(cids)
        end = max(cids)

        feedback, ok = _multiline_dialog(
            self, f"批量重新生成大纲",
            f"将重新生成以下章节的大纲：\n"
            + ", ".join([f"第{c}章" for c in cids]) +
            f"\n\n可选：输入修改意见（留空则AI自主规划，会参考相邻章节大纲保持衔接）",
        )
        if not ok:
            return

        self.batch_regenerate_outlines_requested.emit(start, end, feedback.strip())

    def _on_delete_outline(self):
        row = self.chapter_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一个章节")
            return
        item = self.chapter_table.item(row, 1)
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data:
            return

        cid = data["cid"]
        has_written = data.get("written") is not None

        if has_written:
            reply = QMessageBox.question(
                self, "确认删除",
                f"第{cid}章已有正文，确定删除大纲吗？\n（正文不会被删除）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
        else:
            reply = QMessageBox.question(
                self, "确认删除",
                f"确定删除第{cid}章的大纲吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
        if reply == QMessageBox.StandardButton.Yes and self._pipeline:
            self._pipeline.delete_chapter_outline(cid)
            self._refresh_chapters_table()

    def _on_batch_generate(self):
        if not self._pipeline:
            return
        outlines = self._pipeline.get_chapter_outlines()
        confirmed = [ol for ol in outlines if ol.get("confirmed", False)]

        if not confirmed:
            QMessageBox.warning(self, "提示", "没有已确认的大纲。请先确认至少一条大纲。")
            return

        cids = [ol["chapter_id"] for ol in confirmed]
        reply = QMessageBox.question(
            self, "批量生成章节正文",
            f"将为 {len(cids)} 个已确认大纲生成章节正文:\n"
            + ", ".join([f"第{cid}章" for cid in cids]) +
            f"\n\nAI 将逐章自动写作，确认开始？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.batch_generate_requested.emit(cids)

    def _refresh_gen_scope(self):
        """填充批量范围下拉：全书 + 各卷。"""
        self.gen_scope_combo.blockSignals(True)
        self.gen_scope_combo.clear()
        self.gen_scope_combo.addItem("全书（所有已确认大纲）", "all")
        if self._pipeline:
            try:
                for v in self._pipeline.get_volumes():
                    self.gen_scope_combo.addItem(
                        f"第{v.get('id')}卷 {v.get('title', '')}（第{v.get('start_chapter')}–{v.get('end_chapter')}章）",
                        v.get("id"),
                    )
            except Exception:
                pass
        self.gen_scope_combo.blockSignals(False)

    def _scope_cids(self):
        """按当前范围取应生成的章节 id（已确认大纲）。"""
        if not self._pipeline:
            return []
        outlines = self._pipeline.get_chapter_outlines()
        confirmed = {ol["chapter_id"]: ol for ol in outlines if ol.get("confirmed", False)}
        scope = self.gen_scope_combo.currentData()
        if scope == "all" or scope is None:
            return [cid for cid in confirmed]
        # 按卷：取该卷范围内的已确认大纲
        vol = next((v for v in self._pipeline.get_volumes() if v.get("id") == scope), None)
        if not vol:
            return list(confirmed.keys())
        lo, hi = int(vol.get("start_chapter", 1)), int(vol.get("end_chapter", 1))
        return [cid for cid in confirmed if lo <= cid <= hi]

    def _on_batch_generate_scoped(self):
        if not self._pipeline:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        cids = self._scope_cids()
        if not cids:
            QMessageBox.information(self, "提示",
                "所选范围内没有已确认的大纲。请先确认大纲（在章节表格勾选后点「确认」）。")
            return
        reply = QMessageBox.question(
            self, "批量生成章节正文",
            f"范围为「{self.gen_scope_combo.currentText()}」，将生成 {len(cids)} 章正文:\n"
            + ", ".join([f"第{cid}章" for cid in cids]) +
            "\n\nAI 将逐章自动写作，确认开始？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.batch_generate_requested.emit(cids)

    # ============================================================
    # 设定管理
    # ============================================================

    def _init_settings_tab(self):
        layout = QVBoxLayout(self.settings_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        hint = QLabel("世界观、角色等设定由 AI 自动生成，用户可查看和微调")
        hint.setStyleSheet(HINT_STYLE)
        layout.addWidget(hint)

        select_row = FlowLayout(margin=0, spacing=6)
        select_row.addWidget(QLabel("设定文件:"))
        self.settings_combo = QComboBox()
        self.settings_combo.currentIndexChanged.connect(self._on_setting_selected)
        self.settings_combo.setMinimumWidth(120)
        select_row.addWidget(self.settings_combo)

        new_btn = QPushButton("新建")
        new_btn.setStyleSheet(BTN_SECONDARY)
        new_btn.clicked.connect(self._on_new_setting)
        select_row.addWidget(new_btn)

        regen_setting_btn = QPushButton("AI 重新生成")
        regen_setting_btn.setStyleSheet(BTN_SECONDARY)
        regen_setting_btn.setToolTip("AI 重新生成当前设定（世界观/主角设定等）")
        regen_setting_btn.clicked.connect(self._on_regen_setting)
        select_row.addWidget(regen_setting_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setStyleSheet(BTN_SECONDARY)
        refresh_btn.clicked.connect(self._refresh_settings_list)
        select_row.addWidget(refresh_btn)
        layout.addLayout(select_row)

        self.setting_name_input = QLineEdit()
        self.setting_name_input.setPlaceholderText("设定文件名（如 worldview, power_system）")
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(self.setting_name_input)

        self.setting_edit = QTextEdit()
        layout.addWidget(QLabel("内容:"))
        layout.addWidget(self.setting_edit, stretch=1)

        save_btn = QPushButton("保存设定")
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.clicked.connect(self._on_save_setting)
        layout.addWidget(save_btn)

    # ============================================================
    # 总纲管理
    # ============================================================

    def _init_outline_tab(self):
        layout = QVBoxLayout(self.outline_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        hint = QLabel("故事总纲由 AI 自动生成，用户可查看和微调")
        hint.setStyleSheet(HINT_STYLE)
        layout.addWidget(hint)

        select_row = FlowLayout(margin=0, spacing=6)
        select_row.addWidget(QLabel("大纲文件:"))
        self.outline_combo = QComboBox()
        self.outline_combo.currentIndexChanged.connect(self._on_outline_selected)
        self.outline_combo.setMinimumWidth(120)
        select_row.addWidget(self.outline_combo)

        new_btn = QPushButton("新建")
        new_btn.setStyleSheet(BTN_SECONDARY)
        new_btn.clicked.connect(self._on_new_outline)
        select_row.addWidget(new_btn)

        regen_outline_btn = QPushButton("AI 重新生成")
        regen_outline_btn.setStyleSheet(BTN_SECONDARY)
        regen_outline_btn.setToolTip("AI 重新生成当前大纲（总纲等）")
        regen_outline_btn.clicked.connect(self._on_regen_outline)
        select_row.addWidget(regen_outline_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setStyleSheet(BTN_SECONDARY)
        refresh_btn.clicked.connect(self._refresh_outlines_list)
        select_row.addWidget(refresh_btn)
        layout.addLayout(select_row)

        self.outline_name_input = QLineEdit()
        self.outline_name_input.setPlaceholderText("大纲文件名（如 master, volume_1）")
        layout.addWidget(QLabel("名称:"))
        layout.addWidget(self.outline_name_input)

        self.outline_edit = QTextEdit()
        layout.addWidget(QLabel("内容:"))
        layout.addWidget(self.outline_edit, stretch=1)

        save_btn = QPushButton("保存大纲")
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.clicked.connect(self._on_save_outline)
        layout.addWidget(save_btn)

        # 查看全部章节大纲（汇总展示，方便一口气读完）
        view_all_btn = QPushButton("查看全部章节大纲")
        view_all_btn.setStyleSheet(BTN_SECONDARY)
        view_all_btn.setToolTip("一口气阅读所有章节的大纲汇总")
        view_all_btn.clicked.connect(self._on_view_all_outlines)
        layout.addWidget(view_all_btn)

        # 查看全部正文（连续阅读，不用一章章翻）
        view_body_btn = QPushButton("查看全部正文")
        view_body_btn.setStyleSheet(BTN_SECONDARY)
        view_body_btn.setToolTip("把全书正文拼接成一篇，连续滚动阅读")
        view_body_btn.clicked.connect(self._on_view_all_bodies)
        layout.addWidget(view_body_btn)

        # 修复全部章节标题（消除「第N章」「新章节2」等占位符）
        fix_title_btn = QPushButton("修复全部标题")
        fix_title_btn.setStyleSheet(BTN_SECONDARY)
        fix_title_btn.setToolTip("把文件头同步为大纲标题；无标题章节从正文生成标题")
        fix_title_btn.clicked.connect(self._on_fix_all_titles)
        layout.addWidget(fix_title_btn)

    # ============================================================
    # 卷管理
    # ============================================================

    def _init_volumes_tab(self):
        layout = QVBoxLayout(self.volumes_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        hint = QLabel("将章节划分为若干「卷」，每卷有主题与节奏定位；"
                      "左侧项目树会按卷折叠展示章节，写作时也会注入当前卷上下文以保持一致性。")
        hint.setStyleSheet(HINT_STYLE)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # 上：卷列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self.volumes_list = QListWidget()
        self.volumes_list.setStyleSheet(LIST_STYLE)
        self.volumes_list.setAlternatingRowColors(True)
        self.volumes_list.currentItemChanged.connect(self._on_volume_selected)
        self.volumes_list.setMinimumHeight(100)
        left_layout.addWidget(self.volumes_list, 1)
        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ 新增卷")
        add_btn.setStyleSheet(BTN_PRIMARY)
        add_btn.clicked.connect(self._on_add_volume)
        gen_btn = QPushButton("从总纲生成卷")
        gen_btn.setStyleSheet(BTN_SECONDARY)
        gen_btn.clicked.connect(self._on_generate_volumes)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(gen_btn)
        left_layout.addLayout(btn_row)
        splitter.addWidget(left)

        # 下：编辑区
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        form = QFormLayout()
        form.setSpacing(8)
        self.vol_title_edit = QLineEdit()
        self.vol_theme_edit = QLineEdit()
        self.vol_start_spin = QSpinBox()
        self.vol_start_spin.setMinimum(1)
        self.vol_start_spin.setMaximum(99999)
        self.vol_end_spin = QSpinBox()
        self.vol_end_spin.setMinimum(1)
        self.vol_end_spin.setMaximum(99999)
        form.addRow("卷标题:", self.vol_title_edit)
        form.addRow("主题/基调:", self.vol_theme_edit)
        form.addRow("起始章:", self.vol_start_spin)
        form.addRow("结束章:", self.vol_end_spin)
        right_layout.addLayout(form)

        right_layout.addWidget(QLabel("卷概要:"))
        self.vol_summary_edit = QTextEdit()
        self.vol_summary_edit.setMinimumHeight(80)
        # 不设 maxHeight：全屏时让 QTextEdit 随 splitter 自然伸缩，吃掉中间空隙；
        # 最小 80 保证窗口极小时仍有可读区域。
        right_layout.addWidget(self.vol_summary_edit, 1)

        save_btn = QPushButton("保存修改")
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.clicked.connect(self._on_save_volume)
        right_layout.addWidget(save_btn)

        del_btn = QPushButton("删除选中卷")
        del_btn.setStyleSheet(BTN_DANGER)
        del_btn.clicked.connect(self._on_delete_volume)
        right_layout.addWidget(del_btn)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)   # 上：卷列表（小）
        splitter.setStretchFactor(1, 3)   # 下：编辑区（大）
        layout.addWidget(splitter, 1)

        self._current_volume_id = None

    def _refresh_volumes_list(self):
        self.volumes_list.blockSignals(True)
        self.volumes_list.clear()
        if not self._pipeline or not self._project_root:
            self.volumes_list.blockSignals(False)
            return
        vols = self._pipeline.get_volumes()
        if not vols:
            item = QListWidgetItem("（未定义卷，可新增或从总纲生成）")
            item.setData(Qt.ItemDataRole.UserRole, None)
            self.volumes_list.addItem(item)
            self.volumes_list.blockSignals(False)
            return
        for v in vols:
            item = QListWidgetItem(
                f"{v.get('title', '')}（第{v.get('start_chapter')}–{v.get('end_chapter')}章）"
            )
            item.setData(Qt.ItemDataRole.UserRole, v)
            self.volumes_list.addItem(item)
        self.volumes_list.blockSignals(False)

    def _on_volume_selected(self, current, _previous):
        if not current:
            return
        v = current.data(Qt.ItemDataRole.UserRole)
        if not v:
            self._current_volume_id = None
            return
        self._current_volume_id = v.get("id")
        self.vol_title_edit.setText(v.get("title", ""))
        self.vol_theme_edit.setText(v.get("theme", ""))
        self.vol_start_spin.setValue(int(v.get("start_chapter", 1)))
        self.vol_end_spin.setValue(int(v.get("end_chapter", 1)))
        self.vol_summary_edit.setPlainText(v.get("summary", ""))

    def _on_add_volume(self):
        if not self._pipeline or not self._project_root:
            return
        title, ok = QInputDialog.getText(self, "新增卷", "卷标题：")
        if not ok or not title.strip():
            return
        # 默认范围：接在已有卷之后
        vols = self._pipeline.get_volumes()
        max_end = max([v.get("end_chapter", 0) for v in vols], default=0)
        chapters = self._project_manager.get_chapter_list(self._project_root) if self._project_manager else []
        max_ch = max([c.get("chapter_id", 0) for c in chapters], default=0)
        start = max_end + 1 if max_end > 0 else 1
        end = max(max_ch, start)
        self._pipeline.add_volume(title.strip(), "", start, end, "")
        self._refresh_volumes_list()
        self.volumes_changed.emit()

    def _on_save_volume(self):
        if not self._pipeline or self._current_volume_id is None:
            QMessageBox.information(self, "提示", "请先在左侧选择要编辑的卷")
            return
        self._pipeline.update_volume(
            self._current_volume_id,
            title=self.vol_title_edit.text().strip() or f"第{self._current_volume_id}卷",
            theme=self.vol_theme_edit.text().strip(),
            start_chapter=self.vol_start_spin.value(),
            end_chapter=self.vol_end_spin.value(),
            summary=self.vol_summary_edit.toPlainText().strip(),
        )
        self._refresh_volumes_list()
        self.volumes_changed.emit()
        QMessageBox.information(self, "已保存", "卷信息已更新")

    def _on_delete_volume(self):
        if not self._pipeline or self._current_volume_id is None:
            QMessageBox.information(self, "提示", "请先在左侧选择要删除的卷")
            return
        reply = QMessageBox.question(
            self, "确认删除",
            "确定删除选中的卷及其范围内的全部章节吗？\n"
            "卷内所有章节的正文、大纲及关联记忆数据将一并删除，不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self._pipeline.delete_volume_with_chapters(self._current_volume_id)
        except Exception as e:
            QMessageBox.critical(self, "删除失败", f"删除卷及章节失败: {e}")
            return
        self._current_volume_id = None
        self._refresh_volumes_list()
        self.volumes_changed.emit()
        QMessageBox.information(self, "已删除", f"已删除该卷及范围内的 {len(deleted)} 章")

    def _on_generate_volumes(self):
        if not self._pipeline or not self._project_root:
            return
        reply = QMessageBox.question(
            self, "从总纲生成卷",
            "将依据总纲（master.md）的「分卷规划」重新解析卷结构并覆盖现有卷。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._vol_gen_worker = VolumeGenWorker(self._pipeline)
        self._vol_gen_worker.finished.connect(self._on_volumes_generated)
        self._vol_gen_worker.error.connect(lambda e: QMessageBox.warning(self, "生成失败", e))
        self._vol_gen_worker.start()

    def _on_volumes_generated(self, vols):
        self._refresh_volumes_list()
        self.volumes_changed.emit()
        QMessageBox.information(self, "完成", f"已从总纲解析出 {len(vols)} 卷")

    # ============================================================
    # 故事脊柱（AI 主线账本的可视化 + 手动纠正漂移）
    # ============================================================

    def _init_spine_tab(self):
        layout = QVBoxLayout(self.spine_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        hint = QLabel(
            "故事脊柱账本是 AI 脑子里那条「贯穿全书的主线」——主角 / 核心设定 / 终局方向 / "
            "当前走到哪 / 关键节点。它字数再多也不膨胀，是治「几百万字主线崩溃、吃书」的核心。\n"
            "你可以查看并纠正 AI 的偏差：若发现主线跑偏或终局方向不对，直接在此改，后续章节会严格沿此推进。"
        )
        hint.setStyleSheet(HINT_STYLE)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 输入框统一样式：13px 清晰字号 + 明亮文字色 + 多行可滚动 + 自动换行
        # 单行 QLineEdit 在窄面板会被截断看不清；改 QPlainTextEdit 多行 + 固定高度
        # 即可换行看全，又能限制不让布局无限拉长。
        _INPUT_STYLE = (
            "QPlainTextEdit { background-color: #1e1e2e; color: #cdd6f4;"
            "  border: 1px solid #45475a; border-radius: 4px;"
            "  font-size: 13px; padding: 6px 8px; selection-background-color: #585b70; }"
            "QPlainTextEdit:focus { border-color: #89b4fa; }"
        )

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        # label 加大字号，避免「主角:」看着也偏小
        _LABEL_STYLE_S = "color: #cdd6f4; font-size: 13px; font-weight: 600;"

        def _make_field(min_h: int = 60, max_h: int = 120) -> QPlainTextEdit:
            edit = QPlainTextEdit()
            edit.setStyleSheet(_INPUT_STYLE)
            edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            # 允许垂直滚动，限制最大高度避免撑爆
            edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            edit.setMinimumHeight(min_h)
            edit.setMaximumHeight(max_h)
            edit.setSizeAdjustPolicy(QPlainTextEdit.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
            edit.setTabChangesFocus(True)
            return edit

        self.spine_protagonist_edit = _make_field(min_h=44, max_h=80)
        self.spine_premise_edit = _make_field(min_h=60, max_h=120)
        self.spine_endgame_edit = _make_field(min_h=60, max_h=120)
        self.spine_state_edit = _make_field(min_h=60, max_h=120)
        self.spine_prot_state_edit = _make_field(min_h=60, max_h=120)

        for label, field in [
            ("主角:", self.spine_protagonist_edit),
            ("核心设定:", self.spine_premise_edit),
            ("终局方向:", self.spine_endgame_edit),
            ("主线现状:", self.spine_state_edit),
            ("主角处境:", self.spine_prot_state_edit),
        ]:
            lab = QLabel(label)
            lab.setStyleSheet(_LABEL_STYLE_S)
            form.addRow(lab, field)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        save_btn = QPushButton("保存锚点")
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.setToolTip("保存手动修改的主线锚点")
        save_btn.clicked.connect(self._on_save_spine_anchors)
        rescan_btn = QPushButton("从总纲重扫终局")
        rescan_btn.setStyleSheet(BTN_SECONDARY)
        rescan_btn.setToolTip("重新从总纲 master.md 扫描终局方向并补齐主角/设定")
        rescan_btn.clicked.connect(self._on_rescan_spine)
        clear_btn = QPushButton("清空动态(留锚点)")
        clear_btn.setStyleSheet(BTN_DANGER)
        clear_btn.setToolTip("清空当前阶段/主角处境/关键节点，保留固定锚点")
        clear_btn.clicked.connect(self._on_clear_spine)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(rescan_btn)
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

        layout.addWidget(QLabel("已发生的主线关键节点（稀疏采样，AI 逐章自动累积）："))
        self.spine_milestones = QTextEdit()
        self.spine_milestones.setReadOnly(True)
        self.spine_milestones.setMinimumHeight(120)
        # 关键节点框：去 stretch=1 + 设最大高度 240（够 12-15 行），避免全屏时框被撑到屏幕底部、内容顶部对齐留大片空白
        self.spine_milestones.setMaximumHeight(240)
        layout.addWidget(self.spine_milestones)

    def _refresh_spine(self):
        if self._memory is None or self._memory.spine is None:
            return
        st = self._memory.spine.state
        self.spine_protagonist_edit.setPlainText(st.protagonist)
        self.spine_premise_edit.setPlainText(st.premise)
        self.spine_endgame_edit.setPlainText(st.endgame)
        self.spine_state_edit.setPlainText(st.current_stage)
        self.spine_prot_state_edit.setPlainText(st.protagonist_state)
        if st.milestones:
            def _fmt(m):
                # 防御渲染：milestones 理论上必为 [{chapter_id, beat}, ...]，
                # 但旧 framework_planner 会写字符串列表（已被故事脊柱归一化层兜成 chapter_id=-1），
                # 此层再做一次保险——任意类型都不会崩。
                if isinstance(m, dict):
                    return f"第{m.get('chapter_id')}章：{m.get('beat')}"
                if isinstance(m, str):
                    return f"· {m}"
                return ""
            lines = [_fmt(m) for m in st.milestones if _fmt(m)]
            if st.milestone_total > len(st.milestones):
                lines.append(
                    f"（更早还有 {st.milestone_total - len(st.milestones)} 个节点，已并入前情摘要）"
                )
            self.spine_milestones.setPlainText("\n".join(lines))
        else:
            self.spine_milestones.setPlainText("（尚无关键节点，写作若干章后自动累积）")

    def _on_save_spine_anchors(self):
        if self._memory is None or self._memory.spine is None:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        self._memory.spine.set_fields(
            protagonist=self.spine_protagonist_edit.toPlainText().strip(),
            premise=self.spine_premise_edit.toPlainText().strip(),
            endgame=self.spine_endgame_edit.toPlainText().strip(),
            current_stage=self.spine_state_edit.toPlainText().strip(),
            protagonist_state=self.spine_prot_state_edit.toPlainText().strip(),
        )
        QMessageBox.information(self, "已保存", "故事脊柱锚点已更新，后续章节将严格沿此推进。")

    def _on_rescan_spine(self):
        if self._memory is None or self._memory.spine is None:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        self._memory.rescan_spine_anchors()
        self._refresh_spine()
        QMessageBox.information(self, "已重扫", "已重新从总纲扫描终局方向，并补齐主角/设定锚点。")

    def _on_clear_spine(self):
        if self._memory is None or self._memory.spine is None:
            return
        reply = QMessageBox.question(
            self, "确认",
            "清空主线动态记忆（当前阶段 / 主角处境 / 关键节点），保留固定锚点（主角 / 核心设定 / 终局方向）？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._memory.spine.clear(keep_anchors=True)
        self._refresh_spine()

    # ============================================================
    # 事件账本（前向规划：这一章应该发生什么）
    # ============================================================

    def _init_event_tab(self):
        layout = QVBoxLayout(self.event_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        hint = QLabel(
            "故事事件账本是「这一章应该发生什么」的共享计划（main 主线 / major 大事件）。\n"
            "每章写作时只读取【窗口内、且涵盖到本章】的事件作为硬约束，并附「已铺垫→即将节点」的逐章进展，\n"
            "保证分开生成的章节对齐总计划、主线不崩、不吃书。选中某条事件可见其在各章的具体进展。"
        )
        hint.setStyleSheet(HINT_STYLE)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        plan_btn = QPushButton("规划事件账本")
        plan_btn.setStyleSheet(BTN_PRIMARY)
        plan_btn.setToolTip("基于卷规划+总纲生成完整事件账本（每书一次，少量 token）")
        plan_btn.clicked.connect(self._on_plan_events)
        toolbar.addWidget(plan_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setStyleSheet(BTN_SECONDARY)
        refresh_btn.clicked.connect(self._refresh_event_ledger)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()

        save_btn = QPushButton("保存修改")
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.clicked.connect(self._on_save_event)
        toolbar.addWidget(save_btn)

        del_btn = QPushButton("删除选中")
        del_btn.setStyleSheet(BTN_DANGER)
        del_btn.clicked.connect(self._on_delete_event)
        toolbar.addWidget(del_btn)
        layout.addLayout(toolbar)

        # 表格
        self.event_table = QTableWidget()
        self.event_table.setColumnCount(6)
        self.event_table.setHorizontalHeaderLabels(["类型", "标题", "窗口", "状态", "进展", "描述"])
        # 描述列 Fixed 320（之前 Stretch 会随窗口无限拉宽）+ 单元格 setToolTip 显示完整描述
        self.event_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.event_table.setColumnWidth(0, 70)
        self.event_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.event_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.event_table.setColumnWidth(2, 90)
        self.event_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.event_table.setColumnWidth(3, 70)
        self.event_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.event_table.setColumnWidth(4, 56)
        self.event_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.event_table.setColumnWidth(5, 320)
        # 不换行，长描述行高不膨胀；省略由 ToolTip 显示全文
        self.event_table.setWordWrap(False)
        self.event_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.event_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.event_table.setAlternatingRowColors(True)
        self.event_table.setStyleSheet("""
            QTableWidget { background-color: #1e1e2e; alternate-background-color: #252536;
                color: #cdd6f4; gridline-color: #313244; border: 1px solid #313244; }
            QTableWidget::item:selected { background-color: #45475a; color: #cdd6f4; }
            QHeaderView::section { background-color: #313244; color: #cdd6f4;
                padding: 4px; border: 1px solid #45475a; }
        """)
        self.event_table.itemClicked.connect(self._on_event_selected)
        layout.addWidget(self.event_table, stretch=1)

        self.event_empty_hint = QLabel(
            "事件账本为空。点击上方「AI 规划事件」基于卷规划 + 总纲生成；"
            "或先写若干章，系统会随章节自动累积关键事件。生成后可在此手动调整状态与窗口。"
        )
        self.event_empty_hint.setStyleSheet(HINT_STYLE)
        self.event_empty_hint.setWordWrap(True)
        self.event_empty_hint.setVisible(False)
        layout.addWidget(self.event_empty_hint)

        # 编辑区
        editor = QGroupBox("编辑选中事件")
        ev = QVBoxLayout(editor)
        ev.setContentsMargins(6, 4, 6, 4)
        ev.setSpacing(4)
        self.ev_type_edit = QLineEdit()
        self.ev_type_edit.setReadOnly(True)
        self.ev_title_edit = QLineEdit()
        self.ev_desc_edit = QTextEdit()
        self.ev_desc_edit.setMaximumHeight(60)
        self.ev_status_combo = QComboBox()
        self.ev_status_combo.addItems(["planned", "active", "done", "skipped"])
        self.ev_start_spin = QSpinBox()
        self.ev_start_spin.setMinimum(1)
        self.ev_start_spin.setMaximum(99999)
        self.ev_end_spin = QSpinBox()
        self.ev_end_spin.setMinimum(1)
        self.ev_end_spin.setMaximum(99999)
        form = QFormLayout()
        form.setSpacing(4)
        form.addRow("类型:", self.ev_type_edit)
        form.addRow("标题:", self.ev_title_edit)
        form.addRow("状态:", self.ev_status_combo)
        form.addRow("窗口起:", self.ev_start_spin)
        form.addRow("窗口止:", self.ev_end_spin)
        ev.addLayout(form)
        ev.addWidget(QLabel("描述:"))
        ev.addWidget(self.ev_desc_edit)
        ev.addWidget(QLabel("各章进展（只读，来自共享计划）："))
        self.ev_progress_view = QTextEdit()
        self.ev_progress_view.setReadOnly(True)
        self.ev_progress_view.setMaximumHeight(120)
        self.ev_progress_view.setStyleSheet("""
            QTextEdit { background-color: #181825; color: #bac2de; border: 1px solid #45475a;
                padding: 4px 6px; font-size: 12px; }
        """)
        ev.addWidget(self.ev_progress_view)
        layout.addWidget(editor)
        self._current_event_id = None

    def _refresh_event_ledger(self):
        if self._memory is None:
            return
        try:
            events = self._memory.get_story_events()
        except Exception:
            events = []
        self.event_table.setRowCount(0)
        if not events:
            self.event_empty_hint.setVisible(True)
            return
        self.event_empty_hint.setVisible(False)
        self.event_table.setRowCount(len(events))
        label = {"main": "主线", "major": "大事件", "minor": "小事件"}
        status_color = {
            "planned": QColor("#f9e2af"), "active": QColor("#89b4fa"),
            "done": QColor("#a6e3a1"), "skipped": QColor("#6c7086"),
        }
        for i, ev in enumerate(events):
            t_item = QTableWidgetItem(label.get(ev.type, ev.type))
            title_item = QTableWidgetItem(ev.title)
            win_item = QTableWidgetItem(f"{ev.target_start}~{ev.target_end}")
            st_item = QTableWidgetItem(ev.status)
            st_item.setForeground(status_color.get(ev.status, QColor("#cdd6f4")))
            prog_item = QTableWidgetItem(str(len(ev.progress or [])))
            desc_item = QTableWidgetItem(ev.description)
            # 描述列固定 320 像素不滚动，悬停 ToolTip 显示完整描述
            desc_item.setToolTip(ev.description or "")
            self.event_table.setItem(i, 0, t_item)
            self.event_table.setItem(i, 1, title_item)
            self.event_table.setItem(i, 2, win_item)
            self.event_table.setItem(i, 3, st_item)
            self.event_table.setItem(i, 4, prog_item)
            self.event_table.setItem(i, 5, desc_item)
            t_item.setData(Qt.ItemDataRole.UserRole, ev.id)

    def _on_event_selected(self, item):
        row = item.row()
        id_item = self.event_table.item(row, 0)
        ev_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        if not ev_id:
            return
        self._current_event_id = ev_id
        try:
            events = self._memory.get_story_events()
        except Exception:
            events = []
        ev = next((e for e in events if e.id == ev_id), None)
        if not ev:
            return
        self.ev_type_edit.setText(ev.type)
        self.ev_title_edit.setText(ev.title)
        self.ev_desc_edit.setPlainText(ev.description)
        self.ev_status_combo.setCurrentText(ev.status)
        self.ev_start_spin.setValue(ev.target_start or 1)
        self.ev_end_spin.setValue(ev.target_end or 1)
        # 渲染各章进展
        prog = ev.progress or []
        if prog:
            lines = [f"共 {len(prog)} 个检查点（第{prog[0].get('chapter')}→第{prog[-1].get('chapter')}章）："]
            for p in prog:
                lines.append(f"· 第{p.get('chapter')}章 · {p.get('title','')}：{p.get('summary','')}")
            self.ev_progress_view.setPlainText("\n".join(lines))
        else:
            self.ev_progress_view.setPlainText("（该事件暂无逐章进展记录）")

    def _on_save_event(self):
        if not getattr(self, "_current_event_id", None):
            QMessageBox.information(self, "提示", "请先选择一条事件")
            return
        from domains.novel.core.memory.archive_layer import StoryEvent
        # 保留原有 per-chapter 进展，避免保存时清空
        old_prog = []
        try:
            for e in (self._memory.get_story_events() or []):
                if e.id == self._current_event_id:
                    old_prog = e.progress or []
                    break
        except Exception:
            pass
        ev = StoryEvent(
            id=self._current_event_id,
            type=self.ev_type_edit.text().strip() or "minor",
            title=self.ev_title_edit.text().strip(),
            description=self.ev_desc_edit.toPlainText().strip(),
            target_start=self.ev_start_spin.value(),
            target_end=self.ev_end_spin.value(),
            status=self.ev_status_combo.currentText(),
            progress=old_prog,
        )
        try:
            self._memory._archive.upsert_event(ev)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self._refresh_event_ledger()

    def _on_delete_event(self):
        if not getattr(self, "_current_event_id", None):
            return
        reply = QMessageBox.question(
            self, "确认删除",
            "确定删除选中的计划事件吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._memory._archive.delete_event(self._current_event_id)
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))
            return
        self._current_event_id = None
        self._refresh_event_ledger()

    def _on_plan_events(self):
        if self._memory is None:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        reply = QMessageBox.question(
            self, "AI 规划事件账本",
            "将基于卷规划 + 总纲生成完整事件账本（每书一次，少量 token）。\n"
            "会清空并重建现有事件账本，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._event_plan_worker = EventPlanWorker(self._memory, use_ai=True)
        self._event_plan_worker.finished.connect(
            lambda n: (self._refresh_event_ledger(),
                       QMessageBox.information(self, "完成", f"事件账本已规划：共 {n} 条"))
        )
        self._event_plan_worker.error.connect(
            lambda e: QMessageBox.warning(self, "规划失败", e)
        )
        self._event_plan_worker.start()

    # ============================================================
    # 刷新
    # ============================================================

    def _on_dash_tab_changed(self, idx: int):
        """切到 故事脊柱 / 事件账本 时实时刷新，确保显示最新数据。"""
        try:
            title = self.tabs.tabText(idx)
        except Exception:
            return
        if title == "故事脊柱":
            self._refresh_spine()
        elif title == "事件账本":
            self._refresh_event_ledger()

    def refresh(self):
        if not self._project_root:
            return
        self._refresh_overview()
        self._refresh_settings_list()
        self._refresh_outlines_list()
        self._refresh_volumes_list()
        self._refresh_gen_scope()
        self._refresh_spine()
        self._refresh_event_ledger()

    def _refresh_overview(self):
        if not self._project_manager:
            return
        try:
            import json
            meta_path = Path(self._project_root) / "project.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                self.name_label.setText(meta.get("name", "-"))
                self.genre_label.setText(meta.get("genre", "-"))
                self.author_label.setText(meta.get("author", "-"))
                self.created_label.setText(meta.get("created_at", "-"))
                self.desc_edit.setPlainText(meta.get("description", "-"))
                target = meta.get("target_words", 1000000)
                self.target_words_label.setText(f"{target:,} 字")
        except Exception:
            pass

        chapters = self._project_manager.get_chapter_list(self._project_root) if self._project_manager else []
        total_words = sum(ch.get("word_count", 0) for ch in chapters)
        self.chapter_count_label.setText(str(len(chapters)))
        self.total_words_label.setText(f"{total_words:,} 字")

        max_id = max([c["chapter_id"] for c in chapters], default=0)
        if hasattr(self, 'start_chapter_spin'):
            self.start_chapter_spin.setValue(max_id + 1)

        target = 1000000
        try:
            import json
            meta_path = Path(self._project_root) / "project.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    target = json.load(f).get("target_words", 1000000)
        except Exception:
            pass
        if target > 0:
            progress = min(100, int(total_words / target * 100))
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(progress)

        # 由目标字数推导默认生成章数（让「设定字数」真正成立）
        try:
            from domains.novel.core.novel_categories import planned_chapter_count_for
            cat = ""
            try:
                with open(Path(self._project_root) / "project.json", encoding="utf-8") as _f:
                    cat = json.load(_f).get("novel_category", "")
            except Exception:
                pass
            planned = planned_chapter_count_for(cat, target)
            if hasattr(self, "chapter_count_spin"):
                self.chapter_count_spin.setValue(min(max(planned, 5), 2000))
        except Exception:
            pass

        if self._pipeline:
            has_f = self._pipeline.has_foundation()
            if has_f:
                self.foundation_status.setText("✅ 世界观 + 总纲已生成")
                self.foundation_status.setStyleSheet(SUCCESS_TEXT)
            else:
                self.foundation_status.setText("❌ 基础设定不完整，请先 AI 初始化")
                self.foundation_status.setStyleSheet(ERROR_TEXT)

    def _refresh_settings_list(self):
        self.settings_combo.blockSignals(True)
        self.settings_combo.clear()
        if self._project_manager:
            settings = self._project_manager.list_settings(self._project_root)
            for s in settings:
                self.settings_combo.addItem(s, s)
        self.settings_combo.blockSignals(False)
        if self.settings_combo.count() > 0:
            self._on_setting_selected(0)

    def _refresh_outlines_list(self):
        self.outline_combo.blockSignals(True)
        self.outline_combo.clear()
        outline_dir = Path(self._project_root) / "outline"
        if outline_dir.exists():
            for f in sorted(outline_dir.glob("*.md")):
                self.outline_combo.addItem(f.stem, f.stem)
        self.outline_combo.blockSignals(False)
        if self.outline_combo.count() > 0:
            self._on_outline_selected(0)

    # ============================================================
    # 设定/大纲 读写
    # ============================================================

    def _on_setting_selected(self, index: int):
        if index < 0 or not self._project_manager:
            return
        name = self.settings_combo.itemData(index)
        if not name:
            return
        content = self._project_manager.get_setting(self._project_root, name)
        self.setting_name_input.setText(name)
        self.setting_edit.setPlainText(content)

    def _on_new_setting(self):
        self.setting_name_input.clear()
        self.setting_name_input.setFocus()
        self.setting_edit.clear()

    def _on_save_setting(self):
        name = self.setting_name_input.text().strip()
        content = self.setting_edit.toPlainText()
        if not name:
            QMessageBox.warning(self, "提示", "请输入设定名称")
            return
        if self._project_manager:
            self._project_manager.save_setting(self._project_root, name, content)
            self.setting_saved.emit(name, content)
            self._refresh_settings_list()

    def _on_outline_selected(self, index: int):
        if index < 0 or not self._project_manager:
            return
        name = self.outline_combo.itemData(index)
        if not name:
            return
        content = self._project_manager.get_outline(self._project_root, name)
        self.outline_name_input.setText(name)
        self.outline_edit.setPlainText(content)

    def _on_new_outline(self):
        self.outline_name_input.clear()
        self.outline_name_input.setFocus()
        self.outline_edit.clear()

    def _on_save_outline(self):
        name = self.outline_name_input.text().strip()
        content = self.outline_edit.toPlainText()
        if not name:
            QMessageBox.warning(self, "提示", "请输入大纲名称")
            return
        if self._project_manager:
            self._project_manager.save_outline(self._project_root, name, content)
            self.outline_saved.emit(name, content)
            self._refresh_outlines_list()

    def _on_view_all_outlines(self):
        """汇总展示所有章节大纲，方便一口气读完。"""
        if not self._project_manager or not self._project_root:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        # 获取所有章节大纲
        outlines = []
        try:
            from domains.novel.core.pipeline.writing import WritingPipeline
            # 用 pipeline 读取大纲
            outlines = self._pipeline.get_chapter_outlines() if self._pipeline else []
        except Exception:
            pass

        if not outlines:
            QMessageBox.information(self, "提示", "暂无章节大纲，请先生成大纲")
            return

        # 构建全部大纲文本
        lines = []
        for ol in sorted(outlines, key=lambda x: x.get("chapter_id", 0)):
            cid = ol.get("chapter_id", "?")
            title = ol.get("title", "")
            ctype = ol.get("chapter_type_label", "")
            confirmed = "✓" if ol.get("confirmed") else "○"
            vol = ol.get("volume", "")
            summary = (ol.get("plot_summary") or ol.get("core_events") or "")

            # 拼合一章概要
            lines.append(f"{'='*50}")
            lines.append(f"【第{cid}章《{title}》】{confirmed}  {ctype}")
            if vol:
                lines.append(f"  所属卷: {vol}")
            if summary:
                lines.append(f"  概要: {summary[:300]}")

        full_text = "\n".join(lines)

        # 弹窗显示
        dlg = QDialog(self)
        dlg.setWindowTitle("全部章节大纲汇总")
        dlg.resize(600, 500)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)
        lay.addWidget(QLabel("以下是所有章节大纲的完整汇总（已确认/未确认）："))
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(full_text)
        text_edit.setStyleSheet(
            "QTextEdit { background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #313244; font-size: 12px; }"
        )
        lay.addWidget(text_edit, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)
        dlg.exec()

    def _on_view_all_bodies(self):
        """把全书正文拼接成一篇连续文本，方便一口气读完。"""
        if not self._project_manager or not self._project_root:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        chapters = self._project_manager.get_chapter_list(self._project_root)
        chapters = [c for c in chapters if c.get("word_count", 0) > 0]
        if not chapters:
            QMessageBox.information(self, "提示", "暂无正文，请先生成章节正文")
            return

        # 拼接待阅读正文
        blocks = []
        for ch in sorted(chapters, key=lambda x: x.get("chapter_id", 0)):
            cid = ch.get("chapter_id", "?")
            title = ch.get("title") or f"第{cid}章"
            body = self._project_manager.get_chapter_text(self._project_root, cid) or ""
            body = body.strip()
            if not body:
                continue
            blocks.append(f"# 第{cid}章 {title}\n\n{body}")

        full_text = "\n\n".join(blocks)
        if not full_text.strip():
            QMessageBox.information(self, "提示", "暂无正文内容")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("全书正文（连续阅读）")
        dlg.resize(820, 680)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)
        lay.addWidget(QLabel(f"共 {len(blocks)} 章正文，可滚动连续阅读 / Ctrl+F 搜索："))
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(full_text)
        text_edit.setStyleSheet(
            "QTextEdit { background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #313244; "
            "font-size: 14px; line-height: 1.7; }"
        )
        lay.addWidget(text_edit, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)
        dlg.exec()

    def _on_fix_all_titles(self):
        """全量修复章节标题（同步大纲标题到文件头 / 从无标题正文生成标题）。"""
        if not self._pipeline or not self._project_root:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        reply = QMessageBox.question(
            self, "确认",
            "将把所有章节的标题统一为大纲标题（文件头与大纲不一致时自动修正）。\n"
            "若某些章节既无大纲标题又无文件标题、但有正文，会用 AI 从正文生成标题（消耗 API）。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            report = self._pipeline.fix_all_titles()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"修复标题时出错：{e}")
            return

        fixed = report.get("fixed", [])
        generated = report.get("generated", [])
        skipped = report.get("skipped", [])
        parts = []
        if fixed:
            parts.append(f"已修正 {len(fixed)} 章标题：{fixed}")
        if generated:
            parts.append(f"用 AI 从正文生成 {len(generated)} 章标题：{generated}")
        if skipped:
            parts.append(f"无可用标题、已跳过 {len(skipped)} 章：{skipped}")
        if not parts:
            parts.append("全部章节标题均正常，无需修复。")
        QMessageBox.information(self, "修复完成", "\n".join(parts))
        # 刷新左侧项目树（volumes_changed 已连到 project_list.refresh）
        if fixed or generated:
            try:
                self.volumes_changed.emit()
            except Exception:
                pass

    # ============================================================
    # AI 重新生成 — 设定/总纲/简介
    # ============================================================

    def _on_regen_setting(self):
        """AI 重新生成当前选中的设定文件。"""
        name = self.settings_combo.currentData()
        if not name:
            QMessageBox.warning(self, "提示", "请先选择一个设定文件")
            return

        # 已知设定类型映射
        known_types = {
            "worldview": "世界观设定",
            "character_main": "主角设定",
        }
        type_label = known_types.get(name, name)

        # 显示当前内容预览 + 输入修改意见
        current_content = self.setting_edit.toPlainText()
        current_preview = current_content[:500] + ("..." if len(current_content) > 500 else "") if current_content else "（空）"

        feedback, ok = _multiline_dialog(
            self, f"AI 重新生成{type_label}",
            f"将AI重新生成「{type_label}」（{name}）。\n"
            f"可选：输入修改意见（留空则完全重新生成）\n\n"
            f"依赖说明：\n"
            f"  • worldview（世界观）：独立生成\n"
            f"  • character_main（主角设定）：基于已有世界观生成\n"
            f"  • 其他设定：需手动编辑",
            readonly_text=current_preview,
        )
        if not ok:
            return

        self.regenerate_setting_requested.emit(name, feedback.strip())

    def _on_regen_outline(self):
        """AI 重新生成当前选中的大纲文件。"""
        name = self.outline_combo.currentData()
        if not name:
            QMessageBox.warning(self, "提示", "请先选择一个大纲文件")
            return

        type_label = "故事总纲" if name == "master" else name

        current_content = self.outline_edit.toPlainText()
        current_preview = current_content[:500] + ("..." if len(current_content) > 500 else "") if current_content else "（空）"

        feedback, ok = _multiline_dialog(
            self, f"AI 重新生成{type_label}",
            f"将AI重新生成「{type_label}」（{name}）。\n"
            f"可选：输入修改意见（留空则完全重新生成）\n\n"
            f"依赖说明：\n"
            f"  • master（总纲）：基于已有主角设定生成\n"
            f"  • 其他大纲：需手动编辑",
            readonly_text=current_preview,
        )
        if not ok:
            return

        self.regenerate_master_outline_requested.emit(name, feedback.strip())

    def _on_regen_synopsis(self):
        """AI 重新生成小说简介。"""
        current = self.desc_edit.toPlainText()
        current_preview = current[:300] + ("..." if len(current) > 300 else "") if current else "（空）"

        feedback, ok = _multiline_dialog(
            self, "AI 重新生成简介",
            "将AI重新生成小说简介。\n"
            "可选：输入修改意见（留空则完全重新生成）",
            readonly_text=current_preview,
        )
        if not ok:
            return

        self.regenerate_synopsis_requested.emit(feedback.strip())

    def _on_toggle_edit_synopsis(self):
        """切换简介的编辑/只读模式。"""
        if self.desc_edit.isReadOnly():
            self.desc_edit.setReadOnly(False)
            self._edit_synopsis_btn.setText("取消编辑")
            self._save_synopsis_btn.setVisible(True)
        else:
            self.desc_edit.setReadOnly(True)
            self._edit_synopsis_btn.setText("编辑简介")
            self._save_synopsis_btn.setVisible(False)

    def _on_save_synopsis(self):
        """保存手动编辑的简介。"""
        content = self.desc_edit.toPlainText()
        if self._pipeline:
            self._pipeline.save_synopsis(content)
            self._edit_synopsis_btn.setText("编辑简介")
            self._save_synopsis_btn.setVisible(False)
            self.desc_edit.setReadOnly(True)

    def update_synopsis(self, text: str):
        """更新简介显示（AI 重新生成后调用）。"""
        self.desc_edit.setPlainText(text)
        self.desc_edit.setReadOnly(True)
        self._edit_synopsis_btn.setText("编辑简介")
        self._save_synopsis_btn.setVisible(False)
