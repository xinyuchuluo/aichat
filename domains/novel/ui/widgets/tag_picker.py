"""系统标签选项选择器。

设计意图：标签的「新增」必须从**系统标签库的选项**里挑（带完整定义/规则/锚点），
而不是让用户自由手填一个名字。本对话框列出全部系统标签（按题材 / 流程分组，可搜索），
勾选后返回对应的 TagSpec，由调用方追加到项目。

用法：
    from domains.novel.ui.widgets.tag_picker import pick_tags
    picked = pick_tags(parent=self, existing_names={"金手指"}, kind_filter="content")
    # picked: list[TagSpec] 或 None（取消）
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QDialogButtonBox,
)
from PyQt6.QtCore import Qt

from domains.novel.core.pipeline.tag_system import (
    TagSpec, list_genres, get_genre_tags, get_universal_process_tags,
)


class TagPickerDialog(QDialog):
    """从系统标签库勾选标签。"""

    def __init__(self, parent=None, existing_names: Optional[set] = None,
                 kind_filter: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("从系统标签库选择")
        self.setMinimumWidth(540)
        self.setMinimumHeight(440)
        self._existing = set(existing_names or set())
        # kind_filter: None=全部; "content"=排除流程; "process"=仅流程
        self._kind_filter = kind_filter
        self._all_options: list = []  # [(分组名, TagSpec)]
        self._build_options()
        self._build_ui()
        self._apply_filter()

    def _build_options(self):
        for g in list_genres():
            for s in get_genre_tags(g):
                self._all_options.append((g, s))
        for s in get_universal_process_tags():
            self._all_options.append(("通用流程", s))

    def _build_ui(self):
        layout = QVBoxLayout(self)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("分组:"))
        self.cat_combo = QComboBox()
        self.cat_combo.addItems(["全部", "通用流程"] + list_genres())
        self.cat_combo.currentTextChanged.connect(self._apply_filter)
        frow.addWidget(self.cat_combo)
        frow.addWidget(QLabel("搜索:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("按名称过滤…")
        self.search.textChanged.connect(self._apply_filter)
        frow.addWidget(self.search)
        layout.addLayout(frow)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self.list_widget)

        note = QLabel(
            "勾选要新增的标签（已在本书的会自动隐藏）；确定后带完整定义与锚点加入，"
            "而非空白手填。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _passes_kind(self, spec: TagSpec) -> bool:
        if self._kind_filter == "process":
            return spec.kind == "流程"
        if self._kind_filter == "content":
            return spec.kind != "流程"
        return True

    def _apply_filter(self):
        cat = self.cat_combo.currentText().strip()
        q = self.search.text().strip().lower()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for group, spec in self._all_options:
            if cat != "全部" and group != cat:
                continue
            if not self._passes_kind(spec):
                continue
            if q and q not in spec.name.lower():
                continue
            if spec.name in self._existing:
                continue
            item = QListWidgetItem(f"[{group}] {spec.name}（{spec.kind}）")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip(spec.doctrine or spec.name)
            item.setData(Qt.ItemDataRole.UserRole, (group, spec.to_dict()))
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

    def get_selected(self) -> list:
        out = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                _, d = item.data(Qt.ItemDataRole.UserRole)
                out.append(TagSpec.from_any(d))
        return out


def pick_tags(parent=None, existing_names: Optional[set] = None,
              kind_filter: Optional[str] = None) -> Optional[list]:
    """弹出选择器，返回勾选的 TagSpec 列表；取消返回 None。"""
    dlg = TagPickerDialog(parent, existing_names=existing_names, kind_filter=kind_filter)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.get_selected()
    return None
