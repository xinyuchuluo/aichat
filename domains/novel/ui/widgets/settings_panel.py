"""设定集编辑面板：世界观 / 力量体系 / 人物设定 / 其他。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPlainTextEdit, QLabel, QPushButton, QSplitter, QMessageBox,
    QInputDialog, QLineEdit
)
from PyQt6.QtCore import Qt, pyqtSignal

from domains.novel.core.project.manager import ProjectManager
from domains.novel.ui.styles import apply_tree_branch_style


class SettingsPanelWidget(QWidget):
    """设定集编辑面板。

    左侧分类树 + 右侧编辑器。
    分类：世界观 / 力量体系 / 人物设定 / 其他
    """

    setting_changed = pyqtSignal(str, str)  # setting_type, content

    CATEGORY_MAP = {
        "世界观": "worldview",
        "力量体系": "power_system",
        "人物设定": "character",
        "其他": "misc",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm: ProjectManager | None = None
        self._project_root = ""
        self._current_setting = ""
        self._init_ui()

    def set_project(self, pm: ProjectManager, project_root: str):
        self._pm = pm
        self._project_root = project_root
        self._refresh()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：分类树
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel("设定集"))
        add_btn = QPushButton("新增")
        add_btn.clicked.connect(self._on_add)
        header.addWidget(add_btn)
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(self._on_del)
        header.addWidget(del_btn)
        left_layout.addLayout(header)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._on_item_selected)
        apply_tree_branch_style(self.tree)
        left_layout.addWidget(self.tree)
        splitter.addWidget(left)

        # 右侧：编辑器
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel("请选择左侧设定项")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        right_layout.addWidget(self.title_label)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("选择左侧设定项进行编辑，或点击「新增」创建新设定...")
        right_layout.addWidget(self.editor)

        save_layout = QHBoxLayout()
        save_layout.addStretch()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._on_save)
        save_layout.addWidget(save_btn)
        right_layout.addLayout(save_layout)

        splitter.addWidget(right)
        splitter.setSizes([250, 550])
        layout.addWidget(splitter)

    def _refresh(self):
        """刷新设定集树。"""
        self.tree.clear()
        if not self._pm or not self._project_root:
            return

        # 按分类组织
        categories: dict[str, QTreeWidgetItem] = {}
        for cat_name in self.CATEGORY_MAP:
            cat_item = QTreeWidgetItem([cat_name])
            cat_item.setFont(0, self.font())
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(cat_item)
            categories[cat_name] = cat_item

        # 列出所有设定文件
        settings = self._pm.list_settings(self._project_root)
        for setting_name in settings:
            # 判断分类
            cat = "其他"
            if setting_name.startswith("worldview") or setting_name == "worldview":
                cat = "世界观"
            elif setting_name.startswith("power_system"):
                cat = "力量体系"
            elif setting_name.startswith("character"):
                cat = "人物设定"

            item = QTreeWidgetItem([setting_name])
            item.setData(0, Qt.ItemDataRole.UserRole, setting_name)
            categories[cat].addChild(item)

        # 展开所有分类
        for cat_item in categories.values():
            self.tree.expandItem(cat_item)

    def _on_item_selected(self, item: QTreeWidgetItem, _column: int):
        setting_name = item.data(0, Qt.ItemDataRole.UserRole)
        if not setting_name or not self._pm:
            return
        self._current_setting = setting_name
        content = self._pm.get_setting(self._project_root, setting_name)
        self.editor.setPlainText(content)
        self.title_label.setText(setting_name)

    def _on_add(self):
        if not self._pm:
            return
        name, ok = QInputDialog.getText(self, "新增设定", "设定名称（如: worldview, power_system, character_001）:")
        if ok and name.strip():
            name = name.strip()
            self._pm.save_setting(self._project_root, name, "")
            self._refresh()
            # 选中新项
            self._select_by_name(name)

    def _on_del(self):
        if not self._pm or not self._current_setting:
            return
        reply = QMessageBox.question(self, "确认", f"删除设定「{self._current_setting}」？")
        if reply == QMessageBox.StandardButton.Yes:
            path = Path(self._project_root) / "settings" / f"{self._current_setting}.md"
            if path.exists():
                path.unlink()
            self._current_setting = ""
            self.editor.clear()
            self.title_label.setText("请选择左侧设定项")
            self._refresh()

    def _on_save(self):
        if not self._pm or not self._current_setting:
            return
        content = self.editor.toPlainText()
        self._pm.save_setting(self._project_root, self._current_setting, content)
        self.setting_changed.emit(self._current_setting, content)
        self.window().statusBar().showMessage(f"{self._current_setting} 已保存", 2000)

    def _select_by_name(self, name: str):
        """按名称选中树中的项。"""
        for i in range(self.tree.topLevelItemCount()):
            cat_item = self.tree.topLevelItem(i)
            for j in range(cat_item.childCount()):
                child = cat_item.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == name:
                    self.tree.setCurrentItem(child)
                    self._on_item_selected(child, 0)
                    return

    def get_all_settings_text(self) -> str:
        """获取所有设定集内容（注入上下文用）。"""
        if not self._pm:
            return ""
        parts = []
        for name in self._pm.list_settings(self._project_root):
            content = self._pm.get_setting(self._project_root, name)
            if content.strip():
                parts.append(f"=== {name} ===\n{content}")
        return "\n\n".join(parts)
