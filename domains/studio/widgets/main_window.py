"""短片制片工作台 — 主面板（4 Tab）。

PRD §四 Tab 规划：
  Tab1 项目列表：新建项目 + 列表
  Tab2 资产池：资产 CRUD + 素材管理
  Tab3 分镜管理：分镜 CRUD + @资产解析 + 卡片连线
  Tab4 导出中心：批量勾选分镜 + 5 套模板导出

当前阶段：Tab1 功能完整，Tab2/3/4 占位（后续迭代）。
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QWidget, QMainWindow, QTabWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QListWidget, QListWidgetItem,
    QFormLayout, QDialog, QDialogButtonBox, QMessageBox, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from ..models import (
    StudioDB, get_default_db, Project, Asset, Storyboard,
)
from ..at_syntax import parse_asset_refs, resolve_refs

logger = logging.getLogger(__name__)


class ProjectDialog(QDialog):
    """新建/编辑项目弹窗"""

    def __init__(self, parent=None, project: Project = None):
        super().__init__(parent)
        self.setWindowTitle("编辑项目" if project else "新建项目")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        self.name_edit = QLineEdit(project.project_name if project else "")
        self.brief_edit = QTextEdit(project.project_brief if project else "")
        self.brief_edit.setMaximumHeight(80)
        self.duration_edit = QLineEdit(project.total_estimate_duration if project else "")

        form.addRow("项目名称", self.name_edit)
        form.addRow("项目简介", self.brief_edit)
        form.addRow("总预估时长", self.duration_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self._project = project

    def _on_ok(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "校验", "项目名称不能为空")
            return
        self.accept()

    def to_project(self) -> Project:
        if self._project:
            self._project.project_name = self.name_edit.text().strip()
            self._project.project_brief = self.brief_edit.toPlainText().strip()
            self._project.total_estimate_duration = self.duration_edit.text().strip()
            return self._project
        return Project(
            project_name=self.name_edit.text().strip(),
            project_brief=self.brief_edit.toPlainText().strip(),
            total_estimate_duration=self.duration_edit.text().strip(),
        )


class ProjectsTab(QWidget):
    """Tab1: 项目列表"""

    project_opened = pyqtSignal(int)  # 双击打开项目

    def __init__(self, db: StudioDB):
        super().__init__()
        self._db = db
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 顶部工具栏
        bar = QHBoxLayout()
        title = QLabel("🎬 项目列表")
        title.setStyleSheet("font-size: 16px; font-weight: 500;")
        bar.addWidget(title)
        bar.addStretch()
        btn_new = QPushButton("新建项目")
        btn_new.clicked.connect(self._on_new)
        bar.addWidget(btn_new)
        layout.addLayout(bar)

        # 项目列表
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list)

    def refresh(self):
        self.list.clear()
        for p in self._db.list_projects():
            item = QListWidgetItem(f"{p.project_name}  (时长 {p.total_estimate_duration})")
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            self.list.addItem(item)

    def _on_new(self):
        dlg = ProjectDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            p = dlg.to_project()
            try:
                self._db.insert_project(p)
                self.refresh()
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "错误", f"新建失败: {e}")

    def _on_double_click(self, item: QListWidgetItem):
        pid = item.data(Qt.ItemDataRole.UserRole)
        self.project_opened.emit(pid)


class AssetsTab(QWidget):
    """Tab2: 项目资产池（占位，后续补全）"""

    def __init__(self, db: StudioDB):
        super().__init__()
        self._db = db
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("📦 资产池（待开发）"))
        layout.addWidget(QLabel("选择项目后，这里展示该项目所有资产 + 素材管理"))
        layout.addStretch()


class StoryboardsTab(QWidget):
    """Tab3: 分镜管理（占位，后续补全）"""

    def __init__(self, db: StudioDB):
        super().__init__()
        self._db = db
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("🎥 分镜管理（待开发）"))
        layout.addWidget(QLabel("分镜 CRUD + @资产解析 + 卡片连线 + 项目统计面板"))
        layout.addStretch()


class ExportTab(QWidget):
    """Tab4: 导出中心（占位，后续补全）"""

    def __init__(self, db: StudioDB):
        super().__init__()
        self._db = db
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("📤 导出中心（待开发）"))
        layout.addWidget(QLabel("批量勾选分镜 → 5 套模板导出（Markdown/DrawThings/Blender/Seedance/TTS）"))
        layout.addStretch()


class MainWindow(QMainWindow):
    """短片域主面板：4 Tab"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("短片制片工作台")
        self.setMinimumSize(900, 640)

        self._db = get_default_db()

        # 顶部：当前项目选择
        top = QHBoxLayout()
        top.addWidget(QLabel("当前项目:"))
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._on_project_change)
        top.addWidget(self.project_combo, 1)
        top.addStretch()
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #475569;")
        top.addWidget(self.stats_label)
        top.setContentsMargins(8, 4, 8, 4)

        # 4 Tab
        self.tabs = QTabWidget()
        self.tab_projects = ProjectsTab(self._db)
        self.tab_assets = AssetsTab(self._db)
        self.tab_storyboards = StoryboardsTab(self._db)
        self.tab_export = ExportTab(self._db)

        self.tabs.addTab(self.tab_projects, "🎬 项目列表")
        self.tabs.addTab(self.tab_assets, "📦 资产池")
        self.tabs.addTab(self.tab_storyboards, "🎥 分镜管理")
        self.tabs.addTab(self.tab_export, "📤 导出中心")

        # 双击项目 → 切到分镜 Tab（先保留项目列表，后续扩展）
        self.tab_projects.project_opened.connect(self._on_project_opened)

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addLayout(top)
        v.addWidget(self.tabs, 1)
        self.setCentralWidget(container)

        self._refresh_projects()

    def _refresh_projects(self):
        self.project_combo.clear()
        for p in self._db.list_projects():
            self.project_combo.addItem(f"{p.project_name} (#{p.id})", p.id)

    def _on_project_change(self, idx: int):
        if idx < 0:
            self.stats_label.setText("")
            return
        pid = self.project_combo.itemData(idx)
        stats = self._db.project_stats(pid)
        self.stats_label.setText(
            f"分镜 {stats['shots_total']}(完成 {stats['shots_done']})  "
            f"资产 {stats['assets_total']}  旁白 {stats['narration_chars']}字  "
            f"素材 {stats['materials_done']}/{stats['materials_total']}"
        )

    def _on_project_opened(self, pid: int):
        # 切到分镜管理 Tab
        self.tabs.setCurrentIndex(2)
        self._on_project_change(self.project_combo.currentIndex())
