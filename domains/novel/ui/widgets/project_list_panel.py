"""项目列表面板：左侧导航栏，显示所有项目，可切换/打开/新建。

作为章节的上级目录，类似文件浏览器的侧边栏。
支持「项目 → 卷 → 章」三层可折叠结构，并承载原本属于编辑器内部章节列表的
全部功能（打开/插入/确认大纲/生成正文/删除/批量重写），避免两份列表重复。
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QMenu, QMessageBox, QHeaderView
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QColor

from domains.novel.core.pipeline import volumes as vol_mod
from domains.novel.ui.styles import (
    BTN_PRIMARY, BTN_SECONDARY, TITLE_STYLE, CURRENT_LABEL,
    BTN_SM_DANGER, HINT_STYLE, apply_tree_branch_style,
)


def _read_outlines(project_root: str) -> list[dict]:
    """直接读取项目的章节大纲文件（轻量，避免引入重型 pipeline 依赖）。"""
    p = Path(project_root) / ".novelforge" / "chapter_outlines.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


class ProjectListPanel(QWidget):
    """项目列表面板 — 左侧导航，项目→卷→章三层折叠树（兼作章节列表）。

    信号：
        project_opened(str): 请求打开项目（project_root）
        chapter_opened(str, int): 请求打开某章（project_root, chapter_id）
        new_project_requested(): 请求新建项目
        chapter_insert_requested(str, int): 在某章前插入新章（project_root, anchor_id）
        chapter_confirm_requested(str, int, bool): 确认/取消确认大纲
        chapter_write_requested(str, int): 生成单章正文
        chapter_delete_content_requested(str, int): 删除正文（保留大纲）
        chapter_delete_requested(str, list[int]): 删除整章（正文+大纲+记忆）
        chapter_batch_delete_content_requested(str, list[int]): 批量删除正文（保留大纲）
        chapter_batch_rewrite_requested(str, list[int]): 批量重写选中章
    """

    project_opened = pyqtSignal(str)
    chapter_opened = pyqtSignal(str, int)
    new_project_requested = pyqtSignal()
    chapter_insert_requested = pyqtSignal(str, int)
    chapter_confirm_requested = pyqtSignal(str, int, bool)
    chapter_write_requested = pyqtSignal(str, int)
    chapter_delete_content_requested = pyqtSignal(str, int)
    chapter_delete_requested = pyqtSignal(str, list)
    chapter_batch_delete_content_requested = pyqtSignal(str, list)
    volume_delete_requested = pyqtSignal(str, int)  # 删除卷及全部章节（root, volume_id）
    chapter_batch_rewrite_requested = pyqtSignal(str, list)
    chapter_validate_requested = pyqtSignal(str, int)

    def __init__(self, project_manager=None, parent=None):
        super().__init__(parent)
        self._pm = project_manager
        self._pipeline = None
        self._current_root = ""
        self._init_ui()
        self.refresh()

    def set_project_manager(self, pm):
        self._pm = pm
        self.refresh()

    def set_pipeline(self, pipeline):
        """设置当前打开项目的 pipeline（用于读取大纲以合并章节列表）。"""
        self._pipeline = pipeline

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # 标题栏
        header = QHBoxLayout()
        title = QLabel("项目 / 卷 / 章节")
        title.setStyleSheet(TITLE_STYLE)
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # 新建按钮
        new_btn = QPushButton("+ 新建项目")
        new_btn.setStyleSheet(BTN_PRIMARY)
        new_btn.setMinimumHeight(32)
        new_btn.clicked.connect(self.new_project_requested.emit)
        layout.addWidget(new_btn)

        # 项目树（项目 → 卷 → 章）—— 单列，全部宽度用于章节名
        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        # 高对比折叠三角（macOS 原生三角在暗色下太暗）
        apply_tree_branch_style(self.tree)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        # macOS 上 Cmd/Ctrl+点击会被 Qt 当作右键，导致无法多选；用事件过滤器修正
        self.tree.installEventFilter(self)
        self.tree.itemSelectionChanged.connect(self._update_batch_buttons)
        layout.addWidget(self.tree)

        # 批量操作工具栏（点按钮时选择稳定，不依赖右键菜单时序）
        batch_bar = QHBoxLayout()
        batch_bar.setSpacing(6)
        self.batch_del_btn = QPushButton("批量删除选中章")
        self.batch_del_btn.setStyleSheet(BTN_SM_DANGER)
        self.batch_del_btn.setMinimumHeight(30)
        self.batch_del_btn.clicked.connect(self._on_batch_delete)
        self.batch_content_btn = QPushButton("批量删正文(留大纲)")
        self.batch_content_btn.setStyleSheet(BTN_SECONDARY)
        self.batch_content_btn.setMinimumHeight(30)
        self.batch_content_btn.clicked.connect(self._on_batch_delete_content)
        batch_bar.addWidget(self.batch_del_btn)
        batch_bar.addWidget(self.batch_content_btn)
        layout.addLayout(batch_bar)

        # 多选提示
        self.batch_hint = QLabel("多选：按住 Cmd/Win 键逐个点选，或 Shift 连选；再点上方按钮批量操作")
        self.batch_hint.setStyleSheet(HINT_STYLE)
        self.batch_hint.setWordWrap(True)
        layout.addWidget(self.batch_hint)

        # 刷新按钮
        refresh_btn = QPushButton("刷新列表")
        refresh_btn.setStyleSheet(BTN_SECONDARY)
        refresh_btn.setMinimumHeight(28)
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

        # 当前项目提示
        self.current_label = QLabel("当前: 未打开")
        self.current_label.setStyleSheet(CURRENT_LABEL)
        self.current_label.setWordWrap(True)
        layout.addWidget(self.current_label)

    # ------------------------------------------------------------------
    # 数据构建
    # ------------------------------------------------------------------

    def _expanded_keys(self) -> set:
        """收集当前已展开节点的标识，用于刷新后恢复。"""
        keys = set()
        for i in range(self.tree.topLevelItemCount()):
            proj = self.tree.topLevelItem(i)
            proj_data = proj.data(0, Qt.ItemDataRole.UserRole)
            if not proj_data:
                continue
            pkey = f"P:{proj_data.get('project_root','')}"
            if proj.isExpanded():
                keys.add(pkey)
            for j in range(proj.childCount()):
                vol = proj.child(j)
                vdata = vol.data(0, Qt.ItemDataRole.UserRole)
                if not vdata:
                    continue
                vkey = f"V:{vdata.get('project_root','')}:{vdata.get('volume_title','')}"
                if vol.isExpanded():
                    keys.add(vkey)
        return keys

    def refresh(self):
        """刷新项目→卷→章折叠树（合并大纲与已有正文，含大纲独有章节）。"""
        expanded = self._expanded_keys()
        self.tree.clear()
        self._update_batch_buttons()  # 清空后无选中，同步禁用批量按钮
        if not self._pm:
            return

        projects = self._pm.list_projects()
        for p in projects:
            chapters = self._pm.get_chapter_list(p.project_root)
            total = len(chapters)
            proj_item = QTreeWidgetItem([
                f"{p.name or '未命名'}  ({total}章)"
            ])
            proj_item.setData(0, Qt.ItemDataRole.UserRole, {
                "kind": "project",
                "project_root": p.project_root,
                "project": p,
            })
            if p.project_root == self._current_root:
                proj_item.setBackground(0, QColor("#313244"))
                font = proj_item.font(0)
                font.setBold(True)
                proj_item.setFont(0, font)

            # 合并文件章节 + 大纲章节（含仅大纲、尚无正文的章节）
            file_map = {ch["chapter_id"]: ch for ch in chapters}
            if p.project_root == self._current_root and self._pipeline is not None:
                outlines = self._pipeline.get_chapter_outlines()
            else:
                outlines = _read_outlines(p.project_root)
            outline_map = {ol.get("chapter_id"): ol for ol in outlines}

            combined: dict[int, dict] = {}
            for cid, ch in file_map.items():
                combined[cid] = {
                    "chapter_id": cid,
                    "file_title": ch.get("title", ""),
                    "word_count": ch.get("word_count", 0),
                    "has_content": ch.get("word_count", 0) > 0,
                    "outline": None,
                    "has_outline": False,
                    "confirmed": False,
                }
            for cid, ol in outline_map.items():
                if cid is None:
                    continue
                if cid not in combined:
                    combined[cid] = {
                        "chapter_id": cid,
                        "file_title": "",
                        "word_count": 0,
                        "has_content": False,
                        "outline": None,
                        "has_outline": False,
                        "confirmed": False,
                    }
                combined[cid]["outline"] = ol
                combined[cid]["has_outline"] = True
                combined[cid]["confirmed"] = bool(ol.get("confirmed", False))

            # 计算显示标题：优先大纲标题（修复插入移位后残留「新章节N」）
            items = []
            for cid in sorted(combined.keys()):
                d = combined[cid]
                ol = d["outline"]
                if ol and ol.get("title"):
                    title = ol["title"]
                elif d["file_title"]:
                    title = d["file_title"]
                else:
                    title = f"第{cid}章"
                d["title"] = title
                items.append(d)

            # 卷 → 章
            volumes = self._pm.get_volumes(p.project_root, use_default=True)
            vol_title_of = {}
            try:
                vols_assigned = vol_mod.assign_chapters_to_volumes(volumes, items)
                buckets, unassigned = vols_assigned
            except Exception:
                buckets, unassigned = {}, items

            for vi, vol in enumerate(volumes):
                vchapters = buckets.get(vi, [])
                vol_item = QTreeWidgetItem([
                    f"卷：{vol.get('title', '未命名卷')}（第{vol.get('start_chapter')}–{vol.get('end_chapter')}章）  ·{len(vchapters)}章",
                ])
                vol_item.setData(0, Qt.ItemDataRole.UserRole, {
                    "kind": "volume",
                    "project_root": p.project_root,
                    "volume_title": vol.get("title", ""),
                    "volume": vol,
                })
                self._add_chapter_items(vol_item, vchapters, p.project_root)
                proj_item.addChild(vol_item)
                for c in vchapters:
                    vol_title_of[c["chapter_id"]] = vol.get("title", "")

            # 未归入任何卷的章节（如删除留下空洞 / 超出卷范围）
            if unassigned:
                other = QTreeWidgetItem([f"未分卷  ·{len(unassigned)}章"])
                other.setData(0, Qt.ItemDataRole.UserRole, {
                    "kind": "volume",
                    "project_root": p.project_root,
                    "volume_title": "未分卷",
                    "volume": None,
                })
                self._add_chapter_items(other, unassigned, p.project_root)
                proj_item.addChild(other)
                for c in unassigned:
                    vol_title_of[c["chapter_id"]] = ""

            self.tree.addTopLevelItem(proj_item)

            # 恢复展开状态
            pkey = f"P:{p.project_root}"
            if pkey in expanded or p.project_root == self._current_root:
                self.tree.expandItem(proj_item)
            for j in range(proj_item.childCount()):
                vol = proj_item.child(j)
                vdata = vol.data(0, Qt.ItemDataRole.UserRole)
                vkey = f"V:{vdata.get('project_root','')}:{vdata.get('volume_title','')}"
                if vkey in expanded:
                    self.tree.expandItem(vol)

    def _add_chapter_items(self, parent_item, chapters, project_root):
        for ch in chapters:
            cid = ch.get("chapter_id", 0)
            wc = ch.get("word_count", 0)
            has_content = ch.get("has_content", False)
            has_outline = ch.get("has_outline", False)
            confirmed = ch.get("confirmed", False)
            title = ch.get("title", f"第{cid}章")

            # 状态前导符号
            if has_content and has_outline:
                glyph = "✓"
                suffix = f" ·{wc}字"
            elif has_outline and confirmed:
                glyph = "✓"
                suffix = " ·大纲"
            elif has_outline:
                glyph = "◷"
                suffix = " ·大纲"
            else:
                glyph = "•"
                suffix = f" ·{wc}字" if has_content else ""

            label = f"{glyph} 第{cid}章 {title}{suffix}"
            vt = ch.get("volume_title", "") or ""
            if vt:
                label += f" 「{vt}」"

            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, {
                "kind": "chapter",
                "project_root": project_root,
                "chapter_id": cid,
                "title": title,
                "has_content": has_content,
                "has_outline": has_outline,
                "confirmed": confirmed,
            })

            # 颜色区分状态
            if has_content and has_outline:
                item.setForeground(0, QColor("#cdd6f4"))
            elif has_outline and confirmed:
                item.setForeground(0, QColor("#a6e3a1"))
            elif has_outline:
                item.setForeground(0, QColor("#89dceb"))
            else:
                item.setForeground(0, QColor("#6c7086"))

            tip = f"第{cid}章：{title}\n"
            tip += f"大纲：{'已确认' if (has_outline and confirmed) else ('待确认' if has_outline else '无')}\n"
            tip += f"正文：{'已生成（' + str(wc) + '字）' if has_content else '未生成'}\n"
            tip += f"所属卷：{vt if vt else '未分卷'}"
            item.setToolTip(0, tip)

            parent_item.addChild(item)

    def get_selected_chapter_ids(self) -> list[int]:
        """返回当前多选的章节 id 列表（按章节号排序）。"""
        ids = []
        for item in self.tree.selectedItems():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("kind") == "chapter":
                ids.append(data["chapter_id"])
        return sorted(set(ids))

    # ------------------------------------------------------------------
    # 多选增强：macOS 上 Cmd/Ctrl+点击会被 Qt 当作右键，导致无法多选
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.tree and event.type() == QEvent.Type.MouseButtonPress:
            # 左键 + Cmd/Win 修饰：视为多选点选，手动切换选中态并吞掉事件，
            # 避免被 Qt 解释成右键而弹出菜单（多选意图丢失）。
            if event.button() == Qt.MouseButton.LeftButton and (
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                     | Qt.KeyboardModifier.MetaModifier)
            ):
                item = self.tree.itemAt(event.pos())
                if item is not None:
                    item.setSelected(not item.isSelected())
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 批量操作（可见按钮，点按时选择已稳定）
    # ------------------------------------------------------------------

    def _update_batch_buttons(self):
        """根据当前选中章数更新批量按钮可用状态与提示。"""
        n = len(self.get_selected_chapter_ids())
        self.batch_del_btn.setEnabled(n >= 1)
        self.batch_content_btn.setEnabled(n >= 1)
        if n >= 1:
            self.batch_hint.setText(f"已选中 {n} 章：点上方按钮批量删除，或右键查看更多操作")
        else:
            self.batch_hint.setText(
                "多选：按住 Cmd/Win 键逐个点选，或 Shift 连选；再点上方按钮批量操作"
            )

    def _on_batch_delete(self):
        """批量删除整章（正文+大纲+记忆）。"""
        ids = self.get_selected_chapter_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先在左侧树中选中一个或多个章节（Cmd/Win+点击 或 Shift 连选）。")
            return
        if not self._current_root:
            QMessageBox.information(self, "提示", "请先打开一个项目。")
            return
        self.chapter_delete_requested.emit(self._current_root, ids)

    def _on_batch_delete_content(self):
        """批量删除正文（保留大纲）。"""
        ids = self.get_selected_chapter_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先在左侧树中选中一个或多个章节（Cmd/Win+点击 或 Shift 连选）。")
            return
        if not self._current_root:
            QMessageBox.information(self, "提示", "请先打开一个项目。")
            return
        self.chapter_batch_delete_content_requested.emit(self._current_root, ids)

    def set_current_project(self, project_root: str, name: str = ""):
        """设置当前打开的项目。"""
        self._current_root = project_root
        if not project_root:
            self._pipeline = None
        self.current_label.setText(f"当前: {name}" if name else f"当前: {project_root}")
        self.refresh()

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind = data.get("kind")
        if kind == "chapter":
            self.chapter_opened.emit(data["project_root"], data["chapter_id"])
        elif kind == "project":
            # 展开/折叠 + 在编辑器中打开
            self.tree.expandItem(item) if not item.isExpanded() else self.tree.collapseItem(item)
            self.project_opened.emit(data["project_root"])
        elif kind == "volume":
            if item.isExpanded():
                self.tree.collapseItem(item)
            else:
                self.tree.expandItem(item)

    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        menu = QMenu(self)

        # ---- 章节右键：完整操作 ----
        if data["kind"] == "chapter":
            cid = data["chapter_id"]
            root = data["project_root"]
            has_outline = data.get("has_outline", False)
            has_content = data.get("has_content", False)
            confirmed = data.get("confirmed", False)

            open_action = menu.addAction("打开章节")
            menu.addAction(
                "校验此章",
                lambda: self.chapter_validate_requested.emit(root, cid)
            )
            menu.addSeparator()
            menu.addAction(
                f"在第{cid}章前插入新章",
                lambda: self.chapter_insert_requested.emit(root, cid)
            )
            menu.addSeparator()

            if has_outline:
                if confirmed:
                    menu.addAction(
                        "取消确认大纲",
                        lambda: self.chapter_confirm_requested.emit(root, cid, False)
                    )
                else:
                    menu.addAction(
                        "确认大纲",
                        lambda: self.chapter_confirm_requested.emit(root, cid, True)
                    )
                menu.addSeparator()

            if has_outline:
                if has_content:
                    menu.addAction(
                        "重新生成此章正文",
                        lambda: self.chapter_write_requested.emit(root, cid)
                    )
                else:
                    menu.addAction(
                        "生成此章正文",
                        lambda: self.chapter_write_requested.emit(root, cid)
                    )
                menu.addSeparator()

            if has_content:
                menu.addAction(
                    "删除正文（保留大纲）",
                    lambda: self.chapter_delete_content_requested.emit(root, cid)
                )
                menu.addSeparator()

            menu.addAction(
                "删除章节（正文+大纲）",
                lambda: self.chapter_delete_requested.emit(root, [cid])
            )

            # 多选批量操作
            selected_ids = self.get_selected_chapter_ids()
            if len(selected_ids) > 1:
                menu.addSeparator()
                menu.addAction(
                    f"批量删除正文（保留大纲）- {len(selected_ids)} 章",
                    lambda: self.chapter_batch_delete_content_requested.emit(root, selected_ids)
                )
                menu.addAction(
                    f"批量重写选中的 {len(selected_ids)} 章",
                    lambda: self.chapter_batch_rewrite_requested.emit(root, selected_ids)
                )
                menu.addAction(
                    f"批量删除选中的 {len(selected_ids)} 章",
                    lambda: self.chapter_delete_requested.emit(root, selected_ids)
                )

            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action == open_action:
                self.chapter_opened.emit(root, cid)
            return

        # ---- 卷右键：展开 / 折叠 + 删除（连章节） ----
        if data["kind"] == "volume":
            vol = data.get("volume")
            if vol and vol.get("id") is not None:
                root = data["project_root"]
                vid = vol.get("id")
                expand_action = menu.addAction("展开 / 折叠")
                menu.addSeparator()
                del_action = menu.addAction("删除卷及全部章节")
                action = menu.exec(self.tree.viewport().mapToGlobal(pos))
                if action == expand_action:
                    if item.isExpanded():
                        self.tree.collapseItem(item)
                    else:
                        self.tree.expandItem(item)
                elif action == del_action:
                    self.volume_delete_requested.emit(root, vid)
            return

        # ---- 项目右键 ----
        project = data.get("project")
        if not project:
            return
        open_action = menu.addAction("打开项目")
        menu.addSeparator()
        remove_action = menu.addAction("从列表移除")
        action = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if action == open_action:
            self.project_opened.emit(project.project_root)
        elif action == remove_action:
            reply = QMessageBox.question(
                self, "确认移除",
                f"从项目列表中移除「{project.name}」？\n（不会删除项目文件，仅从列表中取消注册）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes and self._pm:
                self._pm.remove_project(project.project_root)
                self.refresh()
