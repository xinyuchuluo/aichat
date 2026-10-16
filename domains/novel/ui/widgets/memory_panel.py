"""
记忆面板（MemoryPanelWidget）。

在 UI 中的位置：
    主窗口右侧 Tab 区的第一个 Tab（标签为「记忆档案」）。

作用：
    以 Tab 页形式展示 NovelForge 三层记忆架构的所有内容，并支持**编辑**：
    - 章节摘要 Tab：选择章节后展示结构化摘要（只读）。
    - RAG检索 Tab：输入关键词进行向量检索（只读）。

设计要点：
    - 人类在此监督并修正「AI 记忆建构」：AI 做记忆建构（权重/关联/重要性），
      人类经 UI 调整确认；本地零 token 关联引擎负责自动索引。
    - 编辑只针对人类可直观理解的字段（标量 + 逗号/多行分隔的列表）；
      relationships 等多值结构以「target: relation」多行文本编辑。

刷新时机：
    - 项目打开后自动刷新（set_memory_engine 调用时）
    - 每次写作完成后自动刷新（MainWindow._refresh_memory 调用）
    - 用户点击菜单「视图 > 刷新记忆面板」时手动刷新
    - 编辑保存后自动 refresh（保持 UI 与 DB 一致）
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QTextEdit, QLineEdit, QPushButton, QSplitter,
    QTreeWidget, QTreeWidgetItem, QComboBox, QSpinBox, QGroupBox, QFormLayout,
    QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from domains.novel.core.memory.engine import MemoryEngine
from domains.novel.core.memory.archive_layer import (
    CharacterProfile, Foreshadow, TimelineEntry, WorldSetting,
)


def _csv_to_list(text: str) -> list[str]:
    """逗号分隔文本 -> 列表（去空）。"""
    return [x.strip() for x in text.split(",") if x.strip()]


def _list_to_csv(items) -> str:
    return ", ".join(items or [])


def _parse_relations(text: str) -> list[dict]:
    """多行「target: relation」文本 -> [{target, relation, description}]。"""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            t, r = line.split(":", 1)
            out.append({"target": t.strip(), "relation": r.strip(),
                        "description": r.strip()})
        else:
            out.append({"target": line, "relation": "", "description": ""})
    return out


def _relations_to_text(rels) -> str:
    return "\n".join(f"{r.get('target', '')}: {r.get('relation', '')}" for r in (rels or []))


class MemoryPanelWidget(QWidget):

    def __init__(self, memory_engine: MemoryEngine | None = None, parent=None):
        super().__init__(parent)
        self._memory = memory_engine
        self._sel_char_id = None
        self._sel_fs_id = None
        self._sel_tl_id = None
        self._sel_ws_id = None
        self._init_ui()

    def set_memory_engine(self, memory: MemoryEngine):
        self._memory = memory
        self.refresh()

    def _init_ui(self):
        _TABLE_STYLE = (
            "QTableWidget {"
            "  background-color: #313244; color: #cdd6f4;"
            "  border: none;"
            "  selection-background-color: #585b70;"
            "}"
            "QTableWidget::item { padding: 3px 6px; }"
            "QHeaderView::section {"
            "  background-color: #1e1e2e; color: #bac2de; border: none;"
            "  padding: 4px 8px; font-size: 11px;"
            "}"
        )
        _DETAIL_STYLE = (
            "QTextEdit, QLineEdit {"
            "  background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #45475a;"
            "  border-radius: 4px; padding: 4px; font-size: 12px;"
            "}"
        )
        # label 样式：min-height:0 + padding:0 + background:transparent 让空 QLabel 不显示为占位小方框
        _LABEL_STYLE = "QLabel { color: #bac2de; font-size: 11px; min-height: 0; padding: 0; background: transparent; border: none; }"
        _BTN_PRI = (
            "QPushButton { background-color: #89b4fa; color: #11111b; border: none;"
            "  border-radius: 4px; padding: 4px 10px; font-size: 12px; }"
        )
        _BTN_SEC = (
            "QPushButton { background-color: #45475a; color: #cdd6f4; border: none;"
            "  border-radius: 4px; padding: 4px 10px; font-size: 12px; }"
        )
        _BTN_DAN = (
            "QPushButton { background-color: #f38ba8; color: #11111b; border: none;"
            "  border-radius: 4px; padding: 4px 10px; font-size: 12px; }"
        )
        self._styles = {
            "table": _TABLE_STYLE, "detail": _DETAIL_STYLE, "label": _LABEL_STYLE,
            "pri": _BTN_PRI, "sec": _BTN_SEC, "dan": _BTN_DAN,
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()

        self._init_char_tab()
        self._init_fs_tab()
        self._init_tl_tab()
        self._init_ws_tab()
        self._init_summary_tab()
        self._init_rag_tab()

        layout.addWidget(self.tabs)

    # ===================== 人物 Tab（可编辑）=====================
    def _init_char_tab(self):
        self.char_tab = QWidget()
        v = QVBoxLayout(self.char_tab)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)
        self.char_search = QLineEdit()
        self.char_search.setPlaceholderText("搜索姓名 / 角色 / 状态…")
        self.char_search.setStyleSheet(self._styles["detail"])
        self.char_search.textChanged.connect(self._char_apply_filter)
        v.addWidget(self.char_search)
        self.char_count = QLabel("")
        self.char_count.setStyleSheet(self._styles["label"])
        v.addWidget(self.char_count)
        self._char_all = []
        self._char_display = []
        self.char_table = QTableWidget()
        self.char_table.setColumnCount(3)
        self.char_table.setHorizontalHeaderLabels(["姓名", "角色", "状态"])
        self.char_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.char_table.verticalHeader().setVisible(False)
        self.char_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.char_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.char_table.setStyleSheet(self._styles["table"])
        self.char_table.setMaximumHeight(150)
        self.char_table.currentCellChanged.connect(self._on_char_selected)
        v.addWidget(self.char_table)

        # 编辑表单
        g = QGroupBox("编辑人物（保存即回写记忆库）")
        g.setStyleSheet("QGroupBox { color: #bac2de; font-size: 11px; }")
        form = QFormLayout(g)
        form.setSpacing(4)
        self.char_name = QLineEdit()
        self.char_role = QLineEdit()
        self.char_aliases = QLineEdit()
        self.char_desc = QTextEdit(); self.char_desc.setMaximumHeight(48)
        self.char_person = QTextEdit(); self.char_person.setMaximumHeight(48)
        self.char_bg = QTextEdit(); self.char_bg.setMaximumHeight(48)
        self.char_state = QLineEdit()
        self.char_abilities = QLineEdit()
        self.char_rels = QTextEdit(); self.char_rels.setMaximumHeight(56)
        self.char_rels.setPlaceholderText("每行一个：目标角色: 关系（如 苏渺: 同伴）")
        for w in (self.char_name, self.char_role, self.char_aliases, self.char_desc,
                  self.char_person, self.char_bg, self.char_state, self.char_abilities,
                  self.char_rels):
            w.setStyleSheet(self._styles["detail"])
        form.addRow("姓名:", self.char_name)
        form.addRow("角色:", self.char_role)
        form.addRow("别名(逗号):", self.char_aliases)
        form.addRow("外貌:", self.char_desc)
        form.addRow("性格:", self.char_person)
        form.addRow("背景:", self.char_bg)
        form.addRow("当前状态:", self.char_state)
        form.addRow("能力(逗号):", self.char_abilities)
        form.addRow("关系:", self.char_rels)
        v.addWidget(g)

        row = QHBoxLayout()
        row.addStretch()
        new_btn = QPushButton("新增")
        save_btn = QPushButton("保存")
        del_btn = QPushButton("删除")
        new_btn.setStyleSheet(self._styles["sec"])
        save_btn.setStyleSheet(self._styles["pri"])
        del_btn.setStyleSheet(self._styles["dan"])
        new_btn.clicked.connect(self._on_char_new)
        save_btn.clicked.connect(self._on_char_save)
        del_btn.clicked.connect(self._on_char_delete)
        row.addWidget(new_btn); row.addWidget(save_btn); row.addWidget(del_btn)
        v.addLayout(row)
        self.tabs.addTab(self.char_tab, "人物")

    def _clear_char_form(self):
        self._sel_char_id = None
        for w in (self.char_name, self.char_role, self.char_aliases, self.char_state,
                  self.char_abilities):
            w.clear()
        for w in (self.char_desc, self.char_person, self.char_bg, self.char_rels):
            w.clear()

    def _on_char_selected(self, row, *_):
        if not self._memory or row < 0:
            return
        chars = self._char_display
        if row >= len(chars):
            return
        c = chars[row]
        self._sel_char_id = c.id
        self.char_name.setText(c.name)
        self.char_role.setText(c.role)
        self.char_aliases.setText(_list_to_csv(c.aliases))
        self.char_desc.setPlainText(c.description or "")
        self.char_person.setPlainText(c.personality or "")
        self.char_bg.setPlainText(c.background or "")
        self.char_state.setText(c.current_state or "")
        self.char_abilities.setText(_list_to_csv(c.abilities))
        self.char_rels.setPlainText(_relations_to_text(c.relationships))

    def _on_char_new(self):
        self._clear_char_form()
        self.char_name.setFocus()

    def _on_char_save(self):
        if not self._memory:
            return
        name = self.char_name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "姓名不能为空")
            return
        c = CharacterProfile(
            id=self._sel_char_id or "",
            name=name,
            role=self.char_role.text().strip(),
            aliases=_csv_to_list(self.char_aliases.text()),
            description=self.char_desc.toPlainText().strip(),
            personality=self.char_person.toPlainText().strip(),
            background=self.char_bg.toPlainText().strip(),
            current_state=self.char_state.text().strip(),
            abilities=_csv_to_list(self.char_abilities.text()),
            relationships=_parse_relations(self.char_rels.toPlainText()),
        )
        try:
            self._memory.archive.save_character(c)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.refresh()
        QMessageBox.information(self, "已保存", f"人物「{name}」已写入记忆库")

    def _on_char_delete(self):
        if not self._memory or not self._sel_char_id:
            QMessageBox.information(self, "提示", "请先选择一个人物")
            return
        name = self.char_name.text().strip() or "该人物"
        if QMessageBox.question(self, "确认删除", f"确定删除人物「{name}」？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self._memory.archive.delete_character(self._sel_char_id)
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))
            return
        self._clear_char_form()
        self.refresh()

    # ===================== 伏笔 Tab（可编辑）=====================
    def _init_fs_tab(self):
        self.fs_tab = QWidget()
        v = QVBoxLayout(self.fs_tab)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)
        self.fs_search = QLineEdit()
        self.fs_search.setPlaceholderText("搜索伏笔描述 / 关联角色…")
        self.fs_search.setStyleSheet(self._styles["detail"])
        self.fs_search.textChanged.connect(self._fs_apply_filter)
        v.addWidget(self.fs_search)
        self.fs_count = QLabel("")
        self.fs_count.setStyleSheet(self._styles["label"])
        v.addWidget(self.fs_count)
        self._fs_all = []
        self._fs_display = []
        self.fs_table = QTableWidget()
        self.fs_table.setColumnCount(2)
        self.fs_table.setHorizontalHeaderLabels(["描述", "状态"])
        self.fs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.fs_table.verticalHeader().setVisible(False)
        self.fs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.fs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.fs_table.setStyleSheet(self._styles["table"])
        # 占满 search 框与编辑区之间的剩余空间（之前 setMaximumHeight(150) 留出大片空白）
        self.fs_table.setMinimumHeight(180)
        self.fs_table.currentCellChanged.connect(self._on_fs_selected)
        v.addWidget(self.fs_table, 1)  # stretch=1 吃满中间

        g = QGroupBox("编辑伏笔")
        g.setStyleSheet("QGroupBox { color: #bac2de; font-size: 11px; }")
        form = QFormLayout(g)
        form.setSpacing(4)
        self.fs_desc = QLineEdit()
        self.fs_status = QComboBox(); self.fs_status.addItems(["planted", "advanced", "resolved", "abandoned"])
        self.fs_priority = QSpinBox(); self.fs_priority.setRange(1, 5)
        self.fs_planted = QSpinBox(); self.fs_planted.setMinimum(0); self.fs_planted.setMaximum(99999)
        self.fs_resolved = QSpinBox(); self.fs_resolved.setMinimum(0); self.fs_resolved.setMaximum(99999)
        self.fs_chars = QLineEdit()
        for w in (self.fs_desc,):
            w.setStyleSheet(self._styles["detail"])
        form.addRow("描述:", self.fs_desc)
        form.addRow("状态:", self.fs_status)
        form.addRow("优先级:", self.fs_priority)
        form.addRow("埋下章:", self.fs_planted)
        form.addRow("回收章:", self.fs_resolved)
        form.addRow("关联角色(逗号):", self.fs_chars)
        v.addWidget(g)

        row = QHBoxLayout(); row.addStretch()
        new_btn = QPushButton("新增"); save_btn = QPushButton("保存"); del_btn = QPushButton("删除")
        new_btn.setStyleSheet(self._styles["sec"]); save_btn.setStyleSheet(self._styles["pri"])
        del_btn.setStyleSheet(self._styles["dan"])
        new_btn.clicked.connect(lambda: (self._on_fs_new()))
        save_btn.clicked.connect(self._on_fs_save)
        del_btn.clicked.connect(self._on_fs_delete)
        row.addWidget(new_btn); row.addWidget(save_btn); row.addWidget(del_btn)
        v.addLayout(row)
        self.tabs.addTab(self.fs_tab, "伏笔")

    def _on_fs_selected(self, row, *_):
        if not self._memory or row < 0:
            return
        fs = self._fs_display
        if row >= len(fs):
            return
        f = fs[row]
        self._sel_fs_id = f.id
        self.fs_desc.setText(f.description)
        self.fs_status.setCurrentText(f.status)
        self.fs_priority.setValue(f.priority)
        self.fs_planted.setValue(f.planted_chapter)
        self.fs_resolved.setValue(f.resolution_chapter or 0)
        self.fs_chars.setText(_list_to_csv(f.related_characters))

    def _on_fs_new(self):
        self._sel_fs_id = None
        self.fs_desc.clear(); self.fs_status.setCurrentText("planted")
        self.fs_priority.setValue(3); self.fs_planted.setValue(0)
        self.fs_resolved.setValue(0); self.fs_chars.clear()
        self.fs_desc.setFocus()

    def _on_fs_save(self):
        if not self._memory:
            return
        desc = self.fs_desc.text().strip()
        if not desc:
            QMessageBox.warning(self, "提示", "描述不能为空")
            return
        f = Foreshadow(
            id=self._sel_fs_id or "",
            description=desc,
            status=self.fs_status.currentText(),
            priority=self.fs_priority.value(),
            planted_chapter=self.fs_planted.value(),
            resolution_chapter=self.fs_resolved.value() or None,
            related_characters=_csv_to_list(self.fs_chars.text()),
        )
        try:
            self._memory.archive.save_foreshadow(f)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.refresh()
        QMessageBox.information(self, "已保存", "伏笔已写入记忆库")

    def _on_fs_delete(self):
        if not self._memory or not self._sel_fs_id:
            QMessageBox.information(self, "提示", "请先选择一个伏笔")
            return
        if QMessageBox.question(self, "确认删除", "确定删除该伏笔？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self._memory.archive.delete_foreshadow(self._sel_fs_id)
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))
            return
        self._on_fs_new(); self.refresh()

    # ===================== 时间线 Tab（可编辑）=====================
    def _init_tl_tab(self):
        self.tl_tab = QWidget()
        v = QVBoxLayout(self.tl_tab)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)
        self.tl_search = QLineEdit()
        self.tl_search.setPlaceholderText("搜索事件 / 章节 / 角色…")
        self.tl_search.setStyleSheet(self._styles["detail"])
        self.tl_search.textChanged.connect(self._tl_apply_filter)
        v.addWidget(self.tl_search)
        self.tl_count = QLabel("")
        self.tl_count.setStyleSheet(self._styles["label"])
        v.addWidget(self.tl_count)
        self._tl_all = []
        self._tl_display = []
        self.tl_table = QTableWidget()
        self.tl_table.setColumnCount(2)
        self.tl_table.setHorizontalHeaderLabels(["章节", "事件"])
        self.tl_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tl_table.verticalHeader().setVisible(False)
        self.tl_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tl_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tl_table.setStyleSheet(self._styles["table"])
        # 占满 search 框与编辑区之间的剩余空间
        self.tl_table.setMinimumHeight(180)
        self.tl_table.currentCellChanged.connect(self._on_tl_selected)
        v.addWidget(self.tl_table, 1)  # stretch=1 吃满中间

        g = QGroupBox("编辑时间线事件")
        g.setStyleSheet("QGroupBox { color: #bac2de; font-size: 11px; }")
        form = QFormLayout(g)
        form.setSpacing(4)
        self.tl_chapter = QSpinBox(); self.tl_chapter.setMinimum(0); self.tl_chapter.setMaximum(99999)
        self.tl_event = QLineEdit()
        self.tl_ts = QLineEdit()
        self.tl_desc = QTextEdit(); self.tl_desc.setMaximumHeight(48)
        self.tl_chars = QLineEdit()
        self.tl_event.setStyleSheet(self._styles["detail"])
        self.tl_ts.setStyleSheet(self._styles["detail"])
        self.tl_desc.setStyleSheet(self._styles["detail"])
        form.addRow("章节:", self.tl_chapter)
        form.addRow("事件:", self.tl_event)
        form.addRow("故事时间:", self.tl_ts)
        form.addRow("描述:", self.tl_desc)
        form.addRow("涉及角色(逗号):", self.tl_chars)
        v.addWidget(g)

        row = QHBoxLayout(); row.addStretch()
        new_btn = QPushButton("新增"); save_btn = QPushButton("保存"); del_btn = QPushButton("删除")
        new_btn.setStyleSheet(self._styles["sec"]); save_btn.setStyleSheet(self._styles["pri"])
        del_btn.setStyleSheet(self._styles["dan"])
        new_btn.clicked.connect(self._on_tl_new)
        save_btn.clicked.connect(self._on_tl_save)
        del_btn.clicked.connect(self._on_tl_delete)
        row.addWidget(new_btn); row.addWidget(save_btn); row.addWidget(del_btn)
        v.addLayout(row)
        self.tabs.addTab(self.tl_tab, "时间线")

    def _on_tl_selected(self, row, *_):
        if not self._memory or row < 0:
            return
        tl = self._tl_display
        if row >= len(tl):
            return
        t = tl[row]
        self._sel_tl_id = t.id
        self.tl_chapter.setValue(t.chapter_id)
        self.tl_event.setText(t.event)
        self.tl_ts.setText(t.timestamp_in_story or "")
        self.tl_desc.setPlainText(t.description or "")
        self.tl_chars.setText(_list_to_csv(t.characters_involved))

    def _on_tl_new(self):
        self._sel_tl_id = None
        self.tl_chapter.setValue(0); self.tl_event.clear(); self.tl_ts.clear()
        self.tl_desc.clear(); self.tl_chars.clear()
        self.tl_event.setFocus()

    def _on_tl_save(self):
        if not self._memory:
            return
        event = self.tl_event.text().strip()
        if not event:
            QMessageBox.warning(self, "提示", "事件不能为空")
            return
        t = TimelineEntry(
            id=self._sel_tl_id or "",
            chapter_id=self.tl_chapter.value(),
            event=event,
            timestamp_in_story=self.tl_ts.text().strip(),
            description=self.tl_desc.toPlainText().strip(),
            characters_involved=_csv_to_list(self.tl_chars.text()),
        )
        try:
            self._memory.archive.save_timeline_entry(t)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.refresh()
        QMessageBox.information(self, "已保存", "时间线事件已写入记忆库")

    def _on_tl_delete(self):
        if not self._memory or not self._sel_tl_id:
            QMessageBox.information(self, "提示", "请先选择一个事件")
            return
        if QMessageBox.question(self, "确认删除", "确定删除该时间线事件？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self._memory.archive.delete_timeline_entry(self._sel_tl_id)
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))
            return
        self._on_tl_new(); self.refresh()

    # ===================== 世界观 Tab（可编辑）=====================
    def _init_ws_tab(self):
        self.ws_tab = QWidget()
        v = QVBoxLayout(self.ws_tab)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)
        self.ws_search = QLineEdit()
        self.ws_search.setPlaceholderText("搜索名称 / 分类 / 描述…")
        self.ws_search.setStyleSheet(self._styles["detail"])
        self.ws_search.textChanged.connect(self._ws_apply_filter)
        v.addWidget(self.ws_search)
        self.ws_count = QLabel("")
        self.ws_count.setStyleSheet(self._styles["label"])
        v.addWidget(self.ws_count)
        self._ws_all = []
        self._ws_display = []
        self.ws_table = QTableWidget()
        self.ws_table.setColumnCount(2)
        self.ws_table.setHorizontalHeaderLabels(["名称", "分类"])
        self.ws_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ws_table.verticalHeader().setVisible(False)
        self.ws_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.ws_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ws_table.setStyleSheet(self._styles["table"])
        # 占满 search 框与编辑区之间的剩余空间
        self.ws_table.setMinimumHeight(180)
        self.ws_table.currentCellChanged.connect(self._on_ws_selected)
        v.addWidget(self.ws_table, 1)  # stretch=1 吃满中间

        g = QGroupBox("编辑世界观设定")
        g.setStyleSheet("QGroupBox { color: #bac2de; font-size: 11px; }")
        form = QFormLayout(g)
        form.setSpacing(4)
        self.ws_name = QLineEdit()
        self.ws_category = QComboBox()
        self.ws_category.addItems(["地理", "势力", "规则", "物品", "其他"])
        self.ws_desc = QTextEdit(); self.ws_desc.setMaximumHeight(60)
        self.ws_name.setStyleSheet(self._styles["detail"])
        self.ws_desc.setStyleSheet(self._styles["detail"])
        form.addRow("名称:", self.ws_name)
        form.addRow("分类:", self.ws_category)
        form.addRow("描述:", self.ws_desc)
        v.addWidget(g)

        row = QHBoxLayout(); row.addStretch()
        new_btn = QPushButton("新增"); save_btn = QPushButton("保存"); del_btn = QPushButton("删除")
        new_btn.setStyleSheet(self._styles["sec"]); save_btn.setStyleSheet(self._styles["pri"])
        del_btn.setStyleSheet(self._styles["dan"])
        new_btn.clicked.connect(self._on_ws_new)
        save_btn.clicked.connect(self._on_ws_save)
        del_btn.clicked.connect(self._on_ws_delete)
        row.addWidget(new_btn); row.addWidget(save_btn); row.addWidget(del_btn)
        v.addLayout(row)
        self.tabs.addTab(self.ws_tab, "世界观")

    def _on_ws_selected(self, row, *_):
        if not self._memory or row < 0:
            return
        ws = self._ws_display
        if row >= len(ws):
            return
        s = ws[row]
        self._sel_ws_id = s.id
        self.ws_name.setText(s.name)
        idx = self.ws_category.findText(s.category)
        if idx >= 0:
            self.ws_category.setCurrentIndex(idx)
        self.ws_desc.setPlainText(s.description or "")

    def _on_ws_new(self):
        self._sel_ws_id = None
        self.ws_name.clear(); self.ws_category.setCurrentIndex(0); self.ws_desc.clear()
        self.ws_name.setFocus()

    def _on_ws_save(self):
        if not self._memory:
            return
        name = self.ws_name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "名称不能为空")
            return
        s = WorldSetting(
            id=self._sel_ws_id or "",
            category=self.ws_category.currentText(),
            name=name,
            description=self.ws_desc.toPlainText().strip(),
        )
        try:
            self._memory.archive.save_world_setting(s)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.refresh()
        QMessageBox.information(self, "已保存", f"设定「{name}」已写入记忆库")

    def _on_ws_delete(self):
        if not self._memory or not self._sel_ws_id:
            QMessageBox.information(self, "提示", "请先选择一个设定")
            return
        if QMessageBox.question(self, "确认删除", "确定删除该设定？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self._memory.archive.delete_world_setting(self._sel_ws_id)
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))
            return
        self._on_ws_new(); self.refresh()

    # ===================== 列表搜索过滤（人物/伏笔/时间线/世界观）=====================
    def _char_apply_filter(self):
        q = self.char_search.text().strip().lower()
        if not q:
            self._char_display = list(self._char_all)
        else:
            self._char_display = [c for c in self._char_all
                                  if q in (c.name or "").lower()
                                  or q in (c.role or "").lower()
                                  or q in (c.current_state or "").lower()]
        self._char_fill()

    def _char_fill(self):
        items = self._char_display
        self.char_table.setRowCount(len(items))
        for i, c in enumerate(items):
            self.char_table.setItem(i, 0, QTableWidgetItem(c.name))
            self.char_table.setItem(i, 1, QTableWidgetItem(c.role))
            self.char_table.setItem(i, 2, QTableWidgetItem((c.current_state or "")[:30]))
        self.char_count.setText(f"匹配 {len(items)} / 共 {len(self._char_all)} 人")

    def _fs_apply_filter(self):
        q = self.fs_search.text().strip().lower()
        if not q:
            self._fs_display = list(self._fs_all)
        else:
            self._fs_display = [f for f in self._fs_all
                                if q in (f.description or "").lower()
                                or q in _list_to_csv(f.related_characters).lower()]
        self._fs_fill()

    def _fs_fill(self):
        status_map = {"planted": "已埋下", "advanced": "推进中",
                      "resolved": "已回收", "abandoned": "已放弃"}
        items = self._fs_display
        self.fs_table.setRowCount(len(items))
        for i, f in enumerate(items):
            self.fs_table.setItem(i, 0, QTableWidgetItem(f.description[:60]))
            self.fs_table.setItem(i, 1, QTableWidgetItem(
                f"{status_map.get(f.status, f.status)} {'★'*f.priority}"))
        self.fs_count.setText(f"匹配 {len(items)} / 共 {len(self._fs_all)} 条")

    def _tl_apply_filter(self):
        q = self.tl_search.text().strip().lower()
        if not q:
            self._tl_display = list(self._tl_all)
        else:
            self._tl_display = [t for t in self._tl_all
                                if q in f"第{t.chapter_id}章".lower()
                                or q in (t.event or "").lower()
                                or q in (t.description or "").lower()
                                or q in _list_to_csv(t.characters_involved).lower()]
        self._tl_fill()

    def _tl_fill(self):
        items = self._tl_display
        self.tl_table.setRowCount(len(items))
        for i, t in enumerate(items):
            self.tl_table.setItem(i, 0, QTableWidgetItem(f"第{t.chapter_id}章"))
            self.tl_table.setItem(i, 1, QTableWidgetItem(t.event[:60]))
        self.tl_count.setText(f"匹配 {len(items)} / 共 {len(self._tl_all)} 条")

    def _ws_apply_filter(self):
        q = self.ws_search.text().strip().lower()
        if not q:
            self._ws_display = list(self._ws_all)
        else:
            self._ws_display = [s for s in self._ws_all
                                if q in (s.name or "").lower()
                                or q in (s.category or "").lower()
                                or q in (s.description or "").lower()]
        self._ws_fill()

    def _ws_fill(self):
        items = self._ws_display
        self.ws_table.setRowCount(len(items))
        for i, s in enumerate(items):
            self.ws_table.setItem(i, 0, QTableWidgetItem(s.name))
            self.ws_table.setItem(i, 1, QTableWidgetItem(s.category))
        self.ws_count.setText(f"匹配 {len(items)} / 共 {len(self._ws_all)} 条")

    # ===================== 摘要 Tab（只读）=====================
    def _init_summary_tab(self):
        self.sum_tab = QWidget()
        v = QVBoxLayout(self.sum_tab)
        v.setContentsMargins(4, 4, 4, 4)
        self.sum_combo = QComboBox()
        self.sum_combo.currentIndexChanged.connect(self._on_summary_selected)
        lab = QLabel("选择章节:")
        lab.setStyleSheet(self._styles["label"])
        v.addWidget(lab)
        v.addWidget(self.sum_combo)
        self.sum_detail = QTextEdit()
        self.sum_detail.setReadOnly(True)
        self.sum_detail.setStyleSheet(self._styles["detail"])
        v.addWidget(self.sum_detail)
        self.tabs.addTab(self.sum_tab, "章节摘要")

    # ===================== RAG Tab（只读）=====================
    def _init_rag_tab(self):
        self.rag_tab = QWidget()
        v = QVBoxLayout(self.rag_tab)
        v.setContentsMargins(4, 4, 4, 4)
        search = QHBoxLayout()
        self.rag_input = QLineEdit()
        self.rag_input.setPlaceholderText("输入检索关键词...")
        self.rag_input.returnPressed.connect(self._on_rag_search)
        search_btn = QPushButton("检索")
        search_btn.setStyleSheet(self._styles["sec"])
        search_btn.clicked.connect(self._on_rag_search)
        search.addWidget(self.rag_input); search.addWidget(search_btn)
        v.addLayout(search)
        self.rag_results = QTextEdit()
        self.rag_results.setReadOnly(True)
        self.rag_results.setStyleSheet(self._styles["detail"])
        v.addWidget(self.rag_results)
        self.tabs.addTab(self.rag_tab, "RAG检索")

    # ===================== 刷新 =====================
    def refresh(self):
        if not self._memory:
            return
        # 人物
        self._char_all = self._memory.archive.get_all_characters()
        self._char_apply_filter()
        # 伏笔
        self._fs_all = self._memory.archive.get_foreshadows()
        self._fs_apply_filter()
        # 时间线
        self._tl_all = self._memory.archive.get_timeline()
        self._tl_apply_filter()
        # 世界观
        self._ws_all = self._memory.archive.get_world_settings()
        self._ws_apply_filter()
        # 摘要
        self.sum_combo.blockSignals(True)
        self.sum_combo.clear()
        for s in self._memory.summary.get_all_summaries():
            self.sum_combo.addItem(f"第{s.chapter_id}章 - {s.title}", s.chapter_id)
        self.sum_combo.blockSignals(False)
        # RAG 统计
        try:
            stats = self._memory.rag.get_stats()
            self.rag_results.setPlainText(
                f"向量索引统计:\n  总 chunk 数: {stats['total_chunks']}\n"
                f"  覆盖章节数: {stats['total_chapters']}\n"
                f"  sqlite-vec: {'✓' if stats['has_vec_ext'] else '✗'}\n"
                f"  Embedding API: {'✓' if stats['has_embed_api'] else '✗ (使用BM25)'}\n"
                f"  向量维度: {stats['embedding_dim'] or '未确定'}\n\n在上方输入关键词进行检索。"
            )
        except Exception:
            pass

    def _on_summary_selected(self, index: int):
        if index < 0 or not self._memory:
            return
        cid = self.sum_combo.itemData(index)
        if cid is None:
            return
        summary = self._memory.summary.get_summary(cid)
        if not summary:
            return
        text = f"【第{summary.chapter_id}章《{summary.title}》】\n\n"
        text += f"一句话摘要: {summary.one_line_summary}\n\n"
        text += f"情节进展: {summary.plot_progress}\n\n"
        if summary.character_state_changes:
            text += "人物状态变化:\n"
            for csc in summary.character_state_changes:
                text += f"  {csc.get('character', '?')}: {csc.get('before', '')} → {csc.get('after', '')}\n"
            text += "\n"
        if summary.foreshadowing_progress:
            text += "伏笔推进:\n"
            for fp in summary.foreshadowing_progress:
                text += f"  [{fp.get('action', '?')}] {fp.get('detail', '')}\n"
            text += "\n"
        if summary.key_facts:
            text += "关键事实:\n"
            for fact in summary.key_facts:
                text += f"  - {fact}\n"
        self.sum_detail.setPlainText(text)

    def _on_rag_search(self):
        if not self._memory:
            return
        query = self.rag_input.text().strip()
        if not query:
            return
        results = self._memory.search_text(query, top_k=8)
        if not results:
            self.rag_results.setPlainText("未找到相关内容。")
            return
        text = f"检索: \"{query}\" → {len(results)} 个结果\n{'='*50}\n\n"
        for i, r in enumerate(results):
            text += (f"--- 结果 {i+1} (第{r.get('chapter_id', '?')}章, "
                     f"相似度: {r.get('score', 0):.3f}) ---\n")
            text += r.get("text", "")[:500] + "\n\n"
        self.rag_results.setPlainText(text)
