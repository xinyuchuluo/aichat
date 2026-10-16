"""
灵感碎片管理面板（InspirationPanelWidget）。

在 UI 中的位置：
    主窗口右侧 Tab 区（与「记忆档案」并列），
    通过主窗口右侧 QTabWidget 切换显示。

作用：
    - 快速记录灵感（选择类型 + 输入内容，一键保存）
    - 列表查看灵感（支持按状态/类型过滤）
    - 查看灵感详情（类型、状态、内容、标签、关联人物、创建时间）
    - 一键扩展为正文（调用 LLM 将灵感扩展为完整段落，注入编辑器）
    - 融入大纲/设定（AI 分析灵感应融入的位置，一键写入）
    - 标记状态（草稿 → 已安排 → 已使用 → 已归档）
    - 安排到指定章节 / 删除灵感

信号说明：
    expand_requested      (insp_id, expanded_text)
        -> 灵感扩展为正文后发出，主窗口接收后将正文插入章节编辑器
    integrate_to_outline  (modified_content, target)
        -> 灵感融入大纲后发出，主窗口接收后写入 outline/master.md
    integrate_to_setting  (setting_name, modified_content)
        -> 灵感融入设定集后发出，主窗口接收后写入对应设定文件
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget, QListWidgetItem,
    QTextEdit, QPlainTextEdit, QLabel, QPushButton, QComboBox, QLineEdit,
    QGroupBox, QMessageBox, QInputDialog, QFrame, QScrollArea, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from typing import Optional

from domains.novel.core.inspiration.manager import (
    InspirationManager, Inspiration,
    TYPE_SCENE, TYPE_SETTING, TYPE_PLOT, TYPE_DIALOGUE, TYPE_TRAIT, TYPE_FORESHADOW,
    TYPE_LABELS, STATUS_DRAFT, STATUS_SCHEDULED, STATUS_USED, STATUS_ARCHIVED,
    STATUS_LABELS,
)
from domains.novel.ui.styles import (
    BTN_PRIMARY, BTN_DANGER, BTN_SECONDARY,
    HINT_STYLE, LABEL_STYLE,
)


class InspirationPanelWidget(QWidget):
    """灵感碎片管理面板。

    在 UI 中的位置：
        主窗口右侧 Tab Widget 的第二个 Tab（标签为「💡 灵感碎片」），
        位于记忆面板旁边，写作控制台上方。

    布局结构（垂直排列）：
        +--------------------------------------+
        | 💡 快速记录灵感（GroupBox）           |
        |   类型: [下拉]  标题: [输入框]         |
        |   [内容输入区]                        |
        |   标签: [输入框]  关联人物: [输入框]  |
        |   [保存灵感]                          |
        +--------------------------------------+
        | 中部：QSplitter 水平分割               |
        |   +------------------+---------------+|
        |   | 左侧：灵感列表    | 右侧：灵感详情  ||
        |   |（按状态/类型过滤）| + 操作按钮     ||
        |   +------------------+---------------+|
        +--------------------------------------+

    信号：
        expand_requested (insp_id: str, expanded_text: str)
            -> 灵感扩展为正文后发出，由 MainWindow 接收并插入编辑器
        integrate_to_outline (modified_content: str, target: str)
            -> 灵感融入大纲后发出，由 MainWindow 接收并写入大纲文件
        integrate_to_setting (setting_name: str, modified_content: str)
            -> 灵感融入设定集后发出，由 MainWindow 接收并写入设定文件
    """

    # 信号：灵感扩展为正文后发出，携带灵感 ID 和扩展后的正文文本
    expand_requested = pyqtSignal(str, str)  # insp_id, expanded_text
    # 信号：灵感融入大纲后发出，携带修改后的大纲内容和目标位置
    integrate_to_outline = pyqtSignal(str, str)  # modified_content, target
    # 信号：灵感融入设定集后发出，携带设定文件名和修改后的设定内容
    integrate_to_setting = pyqtSignal(str, str)  # setting_name, modified_content

    def __init__(self, manager: Optional[InspirationManager] = None, parent=None):
        """初始化灵感面板。

        参数：
            manager: InspirationManager 实例（由 MemoryEngine 持有），
                     若未提供，需在项目打开后通过 set_manager() 注入。
            parent:  父组件（主窗口）

        初始化内容：
            - 保存 InspirationManager 引用（用于 CRUD 和 AI 扩展操作）
            - 初始化当前选中的灵感对象为 None
            - 调用 _init_ui() 构建界面
        """
        super().__init__(parent)
        self._manager = manager
        self._current_insp: Optional[Inspiration] = None
        self._init_ui()

    def set_manager(self, manager: InspirationManager):
        """注入 InspirationManager 实例并刷新列表。

        在项目打开后由 MainWindow 调用，将 MemoryEngine.inspiration
        （即 InspirationManager 实例）注入到面板中，随后自动刷新列表。

        参数：
            manager: InspirationManager 实例，提供灵感的 CRUD 和 AI 扩展能力
        """
        self._manager = manager
        self._refresh()

    def _init_ui(self):
        """构建灵感面板的 UI 布局。

        布局说明：
            整体为 QVBoxLayout，由以下部分组成：

            1. 顶部：快速记录区（QGroupBox）
               - 第一行：类型下拉框 + 标题输入框
               - 第二行：内容输入区（QPlainTextEdit，最多60px高）
               - 第三行：标签输入框 + 关联人物输入框 + 保存按钮

            2. 中部：QSplitter 水平分割
               - 左侧：过滤条件（状态 + 类型）+ 灵感列表 + 统计标签
               - 右侧：灵感详情展示（富文本）+ 操作按钮区 + 状态操作区

            过滤说明：
                状态过滤：全部 / 草稿 / 已安排 / 已使用 / 已归档
                类型过滤：全部类型 / 场景 / 设定 / 情节 / 对话 / 人物特质 / 伏笔
                两个过滤可组合使用，切换时自动触发 _refresh()。
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ===== 顶部：快速记录区 =====
        record_group = QGroupBox("💡 快速记录灵感")
        record_layout = QVBoxLayout(record_group)

        # 第一行：类型 + 标题
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("类型:"))
        self.type_combo = QComboBox()
        for type_id, label in TYPE_LABELS.items():
            self.type_combo.addItem(f"{label}", type_id)
        row1.addWidget(self.type_combo)

        row1.addWidget(QLabel("标题:"))
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("可选，留空自动取内容前30字")
        row1.addWidget(self.title_input)
        record_layout.addLayout(row1)

        # 第二行：内容输入
        self.content_input = QPlainTextEdit()
        self.content_input.setMaximumHeight(60)
        self.content_input.setPlaceholderText("输入灵感内容...  Ctrl+Enter 快速保存")
        record_layout.addWidget(self.content_input)

        # 第三行：标签 + 保存
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("标签:"))
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText("逗号分隔，如: 雨夜, 追杀, 萧炎")
        row3.addWidget(self.tags_input)

        row3.addWidget(QLabel("关联人物:"))
        self.chars_input = QLineEdit()
        self.chars_input.setPlaceholderText("逗号分隔")
        row3.addWidget(self.chars_input)

        save_btn = QPushButton("保存灵感")
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.setMinimumHeight(32)
        save_btn.clicked.connect(self._on_quick_save)
        row3.addWidget(save_btn)
        record_layout.addLayout(row3)

        layout.addWidget(record_group)

        # ===== 中部：列表 + 详情 =====
        middle = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("过滤:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("全部", "")
        self.filter_combo.addItem("草稿", STATUS_DRAFT)
        self.filter_combo.addItem("已安排", STATUS_SCHEDULED)
        self.filter_combo.addItem("已使用", STATUS_USED)
        self.filter_combo.addItem("已归档", STATUS_ARCHIVED)
        self.filter_combo.currentIndexChanged.connect(self._refresh)
        filter_layout.addWidget(self.filter_combo)

        self.type_filter = QComboBox()
        self.type_filter.addItem("全部类型", "")
        for type_id, label in TYPE_LABELS.items():
            self.type_filter.addItem(label, type_id)
        self.type_filter.currentIndexChanged.connect(self._refresh)
        filter_layout.addWidget(self.type_filter)
        left_layout.addLayout(filter_layout)

        self.insp_list = QListWidget()
        self.insp_list.currentRowChanged.connect(self._on_selected)
        left_layout.addWidget(self.insp_list)

        # 统计
        self.stats_label = QLabel("0 条灵感")
        left_layout.addWidget(self.stats_label)

        middle.addWidget(left)

        # 右侧：详情 + 操作
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.detail_title = QLabel("选择左侧灵感查看详情")
        self.detail_title.setStyleSheet("font-size: 15px; font-weight: bold; padding: 4px; color: #cdd6f4;")
        right_layout.addWidget(self.detail_title)

        self.detail_view = QTextEdit()
        self.detail_view.setReadOnly(True)
        right_layout.addWidget(self.detail_view)

        # 操作按钮区
        action_layout = QHBoxLayout()

        expand_btn = QPushButton("扩展为正文")
        expand_btn.setStyleSheet(BTN_PRIMARY)
        expand_btn.setMinimumHeight(32)
        expand_btn.clicked.connect(self._on_expand)
        action_layout.addWidget(expand_btn)

        integrate_btn = QPushButton("融入大纲/设定")
        integrate_btn.setStyleSheet(BTN_SECONDARY)
        integrate_btn.setMinimumHeight(32)
        integrate_btn.clicked.connect(self._on_integrate)
        action_layout.addWidget(integrate_btn)

        right_layout.addLayout(action_layout)

        # 状态操作
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("状态:"))
        self.status_combo = QComboBox()
        for status_id, label in STATUS_LABELS.items():
            self.status_combo.addItem(label, status_id)
        status_layout.addWidget(self.status_combo)

        schedule_chapter_btn = QPushButton("安排到章节")
        schedule_chapter_btn.setStyleSheet(BTN_SECONDARY)
        schedule_chapter_btn.setMinimumHeight(30)
        schedule_chapter_btn.clicked.connect(self._on_schedule)
        status_layout.addWidget(schedule_chapter_btn)

        del_btn = QPushButton("删除")
        del_btn.setStyleSheet(BTN_DANGER)
        del_btn.setMinimumHeight(30)
        del_btn.clicked.connect(self._on_delete)
        status_layout.addWidget(del_btn)
        right_layout.addLayout(status_layout)

        middle.addWidget(right)
        middle.setSizes([300, 500])

        layout.addWidget(middle)

    def _refresh(self):
        """刷新灵感列表（核心刷新方法）。

        触发条件：
            - 管理器首次设置（set_manager 调用后）
            - 过滤条件变化（状态过滤 / 类型过滤下拉框切换）
            - 灵感保存/删除/状态变化后

        处理逻辑：
            1. 清空现有列表
            2. 若管理器未设置，直接返回
            3. 读取当前过滤条件（状态 + 类型）
            4. 调用 InspirationManager.list_all() 获取符合条件的灵感列表
            5. 将每条灵感以「状态标签 [类型标签] 标题」格式添加到列表，
               并将 Inspiration 对象存入 QListWidgetItem 的 UserRole 中
            6. 调用 InspirationManager.get_stats() 更新底部统计标签
        """
        self.insp_list.clear()
        if not self._manager:
            return

        status_filter = self.filter_combo.currentData()
        type_filter = self.type_filter.currentData()

        inspirations = self._manager.list_all(
            status=status_filter if status_filter else None,
            type=type_filter if type_filter else None,
        )

        for insp in inspirations:
            type_label = TYPE_LABELS.get(insp.type, insp.type)
            status_label = STATUS_LABELS.get(insp.status, insp.status)
            item_text = f"{status_label} [{type_label}] {insp.title}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, insp)
            self.insp_list.addItem(item)

        # 统计
        stats = self._manager.get_stats()
        self.stats_label.setText(
            f"共 {stats['total']} 条 "
            f"(💭{stats[STATUS_DRAFT]} 📅{stats[STATUS_SCHEDULED]} ✅{stats[STATUS_USED]})"
        )

    def _on_quick_save(self):
        """顶部「保存灵感」按钮的槽方法。

        触发条件：
            - 用户在顶部快速记录区填写内容后点击「保存灵感」按钮

        流程：
            1. 检查管理器是否已设置、内容是否为空
            2. 读取类型、标题、内容、标签、关联人物输入
            3. 标题留空时自动截取内容前 30 字作为标题
            4. 调用 InspirationManager.add() 保存灵感到数据库
            5. 清空所有输入框
            6. 刷新列表并在状态栏显示提示
        """
        if not self._manager:
            return
        content = self.content_input.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "请输入灵感内容")
            return

        type_id = self.type_combo.currentData()
        title = self.title_input.text().strip() or content[:30]
        tags = [t.strip() for t in self.tags_input.text().split(",") if t.strip()]
        chars = [c.strip() for c in self.chars_input.text().split(",") if c.strip()]

        self._manager.add(
            type=type_id,
            content=content,
            title=title,
            tags=tags,
            related_characters=chars,
        )

        # 清空输入
        self.content_input.clear()
        self.title_input.clear()
        self.tags_input.clear()
        self.chars_input.clear()

        self._refresh()
        self.window().statusBar().showMessage("灵感已保存 💡", 2000)

    def _on_selected(self, row: int):
        """灵感列表选中项变化时的槽方法。

        触发条件：
            - 用户在左侧灵感列表中点击选中某条灵感

        参数：
            row: 选中的行号（-1 表示取消选中）

        作用：
            1. 从 QListWidgetItem 的 UserRole 中取出 Inspiration 对象
            2. 将详情以 HTML 格式展示在右侧详情区（类型、状态、优先级、
               内容、标签、关联人物、关联章节、创建时间）
            3. 同步更新状态下拉框的当前选中项
        """
        if row < 0 or row >= self.insp_list.count():
            return
        item = self.insp_list.item(row)
        insp = item.data(Qt.ItemDataRole.UserRole)
        if not insp:
            return
        self._current_insp = insp

        type_label = TYPE_LABELS.get(insp.type, insp.type)
        status_label = STATUS_LABELS.get(insp.status, insp.status)

        html = f"<h3>{insp.title}</h3>"
        html += f"<p><b>类型:</b> {type_label}　<b>状态:</b> {status_label}　<b>优先级:</b> {'★' * insp.priority}</p>"
        html += f"<p><b>内容:</b></p><p>{insp.content}</p>"
        if insp.tags:
            html += f"<p><b>标签:</b> {', '.join(insp.tags)}</p>"
        if insp.related_characters:
            html += f"<p><b>关联人物:</b> {', '.join(insp.related_characters)}</p>"
        if insp.related_chapter:
            html += f"<p><b>关联章节:</b> 第{insp.related_chapter}章</p>"
        html += f"<p style='color: #6c7086;'><b>创建:</b> {insp.created_at[:19]}</p>"

        self.detail_title.setText(f"{type_label} | {insp.title}")
        self.detail_view.setHtml(html)

        # 更新状态下拉框
        for i in range(self.status_combo.count()):
            if self.status_combo.itemData(i) == insp.status:
                self.status_combo.setCurrentIndex(i)
                break

    def _on_expand(self):
        """「📝 一键扩展为正文」按钮的槽方法。

        触发条件：
            - 用户选中一条灵感后点击「📝 一键扩展为正文」按钮

        流程：
            1. 检查是否已选中灵感、API 端点是否已配置
            2. 弹出确认对话框，告知用户扩展后的内容将插入当前章节编辑器
            3. 尝试获取世界观设定作为上下文（可选）
            4. 调用 InspirationManager.expand_to_text() 使用 LLM 将灵感扩展为完整正文
            5. 发出 expand_requested 信号，由 MainWindow 接收并插入编辑器
        """
        """一键扩展为正文。"""
        if not self._current_insp or not self._manager:
            QMessageBox.warning(self, "提示", "请先选择一个灵感碎片")
            return
        if not self._manager._llm.config.endpoints:
            QMessageBox.warning(self, "提示", "请先配置API端点")
            return

        # 确认对话框
        reply = QMessageBox.question(
            self, "扩展灵感",
            f"将「{self._current_insp.title}」扩展为完整正文段落？\n\n扩展后的内容将插入到当前章节编辑器中。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.window().statusBar().showMessage("正在扩展灵感为正文...")

        # 获取相关人物
        char_names = self._current_insp.related_characters
        worldview = ""
        # 尝试获取世界观设定
        try:
            from domains.novel.core.project.manager import ProjectManager
            pm = ProjectManager()
            if self._manager._project_root:
                worldview = pm.get_setting(str(self._manager._project_root), "worldview")
        except Exception:
            pass

        try:
            expanded = self._manager.expand_to_text(
                self._current_insp,
                context_chars=char_names,
                worldview=worldview,
            )
            # 发信号给主窗口插入编辑器
            self.expand_requested.emit(self._current_insp.id, expanded)
            self.window().statusBar().showMessage("灵感已扩展为正文，已插入编辑器", 3000)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"扩展失败: {e}")

    def _on_integrate(self):
        """「🔗 融入大纲/设定」按钮的槽方法。

        触发条件：
            - 用户选中一条灵感后点击「🔗 融入大纲/设定」按钮

        流程：
            1. 检查是否已选中灵感
            2. 读取当前大纲内容（outline/master.md）和设定集内容
            3. 调用 InspirationManager.suggest_integration() 使用 LLM 分析
               灵感应融入的位置（大纲 or 设定集，具体哪个文件，如何修改）
            4. 弹出建议对话框，展示 AI 的分析结果和修改后内容
            5. 用户确认后，根据建议类型发出对应信号：
               - "setting" -> integrate_to_setting 信号
               - "outline" -> integrate_to_outline 信号
               （由 MainWindow 接收并写入对应文件）
        """
        if not self._current_insp or not self._manager:
            QMessageBox.warning(self, "提示", "请先选择一个灵感碎片")
            return

        self.window().statusBar().showMessage("正在分析融入位置...")

        # 获取当前大纲和设定
        outline = ""
        settings = ""
        try:
            from domains.novel.core.project.manager import ProjectManager
            pm = ProjectManager()
            if self._manager._project_root:
                outline = pm.get_outline(str(self._manager._project_root), "master")
                settings_list = pm.list_settings(str(self._manager._project_root))
                settings = "\n\n".join(
                    pm.get_setting(str(self._manager._project_root), s) for s in settings_list[:5]
                )
        except Exception:
            pass

        try:
            suggestion = self._manager.suggest_integration(
                self._current_insp, outline, settings
            )

            # 展示建议
            stype = suggestion.get("suggestion_type", "outline")
            target = suggestion.get("target", "")
            reason = suggestion.get("reason", "")
            modified = suggestion.get("modified_content", "")

            msg = f"💡 融入建议\n\n"
            msg += f"建议类型: {stype}\n"
            msg += f"目标位置: {target}\n"
            msg += f"理由: {reason}\n\n"
            msg += f"修改后内容:\n\n{modified[:500]}"

            reply = QMessageBox.question(
                self, "融入建议", msg + "\n\n是否应用此修改？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes and modified:
                if stype == "setting":
                    self.integrate_to_setting.emit(target, modified)
                else:
                    self.integrate_to_outline.emit(target, modified)
                self.window().statusBar().showMessage("灵感已融入大纲/设定", 3000)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"融入分析失败: {e}")

    def _on_schedule(self):
        """「安排到章节」按钮的槽方法。

        触发条件：
            - 用户选中一条灵感后点击「安排到章节」按钮

        作用：
            弹出整数输入框，用户输入目标章节号后，
            调用 InspirationManager.mark_scheduled() 将灵感状态从「草稿」
            改为「已安排」，并记录目标章节号，方便后续写作时参考。
        """
        if not self._current_insp or not self._manager:
            return
        chapter_id, ok = QInputDialog.getInt(
            self, "安排到章节", "输入章节号:", min=1, max=9999
        )
        if ok:
            self._manager.mark_scheduled(self._current_insp.id, chapter_id)
            self._refresh()
            self.window().statusBar().showMessage(f"已安排到第{chapter_id}章", 2000)

    def _on_delete(self):
        """「删除」按钮的槽方法。

        触发条件：
            - 用户选中一条灵感后点击「删除」按钮

        作用：
            弹出确认对话框，用户确认后调用 InspirationManager.delete()
            从数据库中删除该灵感，并清空右侧详情区的显示，然后刷新列表。
        """
        if not self._current_insp or not self._manager:
            return
        reply = QMessageBox.question(
            self, "确认", f"删除灵感「{self._current_insp.title}」？"
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._manager.delete(self._current_insp.id)
            self._current_insp = None
            self.detail_title.setText("选择左侧灵感查看详情")
            self.detail_view.clear()
            self._refresh()
