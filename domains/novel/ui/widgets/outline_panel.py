"""
大纲编辑面板（OutlinePanelWidget）。

在 UI 中的位置：
    作为独立面板使用，通常通过主窗口的菜单或按钮切换到此面板。
    包含四个 Tab 页，覆盖「故事大纲 → 卷大纲 → 简单章节大纲 → 深大纲」四层。

作用：
    提供大纲的四层编辑能力（统一对接生成流水线的权威数据源
    .novelforge/chapter_outlines.json，而非旧版 outline/*.md）：
    - 总纲：整个故事的核心冲突、分卷规划、重要转折点、结局方向
            （仍读写 outline/master.md，该文件被 MemoryEngine 与 pipeline 共用）
    - 卷纲：每卷的核心事件和范围（id/title/theme/start_chapter/end_chapter/summary），
           直接读写 .novelforge/volumes.json（生成权威源），编辑后**真实影响生成**；
           并同步生成 outline/volumes.md 人读镜像（仅备份，不被消费）
    - 章纲（简单章节大纲）：每章的 core_events / plot_summary / opening / ending，
           直接读写 chapter_outlines.json，编辑后**真实影响生成**（修复旧版脱节）
    - 深大纲（事件进展规格）：每章的 event_progress（beat 级节拍：
           summary/entities/goal/outcome/advances/checkpoint）+ state_deltas +
           continuity_anchors。这是 AI 在规划阶段把薄大纲展开成的「记忆建构」，
           人类可在此浏览、调整、确认，再交给处理器按脚本执行。

    chapter_outlines.json 是生成流水线的权威大纲源（writing.py 的
    save_chapter_outlines/get_chapter_outlines、arc_subplot、continuity_state、
    outline_vs_body、MemoryEngine、lan/server 全部消费它）。本面板直接读写它，
    保证「界面调整 → 生成生效」闭环。

信号说明：
    outline_changed (outline_type: str, content: str)
        -> 大纲内容保存后发出，通知其他组件（如上下文组装器）更新
        outline_type: "master"（总纲）/ "volumes"（卷纲）/ "chapter_deep"（深大纲章）
        content:     保存后的大纲文本内容
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPlainTextEdit,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QSplitter,
    QMessageBox, QLineEdit, QInputDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QSpinBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush

from domains.novel.core.project.manager import ProjectManager
from domains.novel.core.pipeline.volumes import load_volumes, save_volumes


# ---------------------------------------------------------------------------
# 模块级 JSON 读写（与 writing.py 格式一致，便于离线冒烟测试）
# ---------------------------------------------------------------------------
def load_outlines(project_root: str) -> list[dict]:
    """读取 .novelforge/chapter_outlines.json，返回大纲列表（缺失/损坏返回 []）。"""
    p = Path(project_root) / ".novelforge" / "chapter_outlines.json"
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_outlines(project_root: str, outlines: list[dict]) -> None:
    """写回 chapter_outlines.json（按 chapter_id 排序，规整 list 字段为 str）。"""
    d = Path(project_root) / ".novelforge"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "chapter_outlines.json"
    for ol in outlines:
        for _k in ("core_events", "plot_summary"):
            _v = ol.get(_k)
            if isinstance(_v, list):
                ol[_k] = "\n".join(str(x) for x in _v if x)
    outlines = sorted(outlines, key=lambda x: x.get("chapter_id", 0))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(outlines, f, ensure_ascii=False, indent=2)


def _norm_beat(b: dict) -> dict:
    """规整单个 beat，保证下游零 token 守卫稳定。

    统一走 planner.sanitize_beat（剥前导序号 + 修半截 checkpoint），
    与生成侧同一口径——手工编辑同样不允许写出「楚渊携林」这种半截硬事实。
    """
    try:
        from planner import sanitize_beat  # 包根同级模块
        return sanitize_beat(b)
    except Exception:
        pass
    b.setdefault("beat_id", "")
    b.setdefault("summary", "")
    b.setdefault("entities", [])
    b.setdefault("goal", "")
    b.setdefault("outcome", "")
    b.setdefault("advances", [])
    b.setdefault("checkpoint", "")
    b.setdefault("prose_focus", "")
    return b


class OutlinePanelWidget(QWidget):
    """大纲编辑面板（四层：总纲 / 卷纲 / 章纲 / 深大纲）。"""

    outline_changed = pyqtSignal(str, str)  # outline_type, content

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm: ProjectManager | None = None
        self._project_root = ""
        self._current_volume_id = None
        self._current_chapter_id = 0       # 章纲 tab 当前章
        self._current_deep_chapter = 0     # 深大纲 tab 当前章
        self._init_ui()

    # ------------------------------------------------------------------
    # 注入 & 刷新
    # ------------------------------------------------------------------
    def set_project(self, pm: ProjectManager, project_root: str):
        self._pm = pm
        self._project_root = project_root
        self._refresh()

    def _json_outlines(self) -> list[dict]:
        if not self._project_root:
            return []
        return load_outlines(self._project_root)

    def _save_json_outlines(self, outlines: list[dict]):
        if not self._project_root:
            return
        save_outlines(self._project_root, outlines)
        self.outline_changed.emit("chapter_deep", "")

    def _refresh(self):
        """刷新所有大纲内容：总纲(markdown) + 卷纲(markdown) + 章纲/深大纲(JSON)。"""
        # 总纲（markdown，被 MemoryEngine/pipeline 共用）+ 卷纲（volumes.json 权威源）
        if self._pm and self._project_root:
            self.master_editor.setPlainText(self._pm.get_outline(self._project_root, "master"))
            self._refresh_volume_list()

        # 章纲 + 深大纲（统一读 chapter_outlines.json）
        outlines = self._json_outlines()
        self.chapter_outline_list.clear()
        self.deep_list.clear()
        for ol in outlines:
            cid = ol.get("chapter_id", 0)
            title = ol.get("title", f"第{cid}章")
            label = f"第{cid}章 {title}"
            ci = QListWidgetItem(label)
            ci.setData(Qt.ItemDataRole.UserRole, cid)
            self.chapter_outline_list.addItem(ci)
            di = QListWidgetItem(label)
            di.setData(Qt.ItemDataRole.UserRole, cid)
            self.deep_list.addItem(di)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()

        # ===== 总纲 Tab（markdown，被 MemoryEngine/pipeline 共用）=====
        master_tab = QWidget()
        master_layout = QVBoxLayout(master_tab)
        self.master_editor = QPlainTextEdit()
        self.master_editor.setPlaceholderText("在这里编写故事总纲...\n\n包括：核心冲突、故事结构、分卷规划、重要转折点、结局方向")
        master_layout.addWidget(self.master_editor)
        master_btn = QPushButton("保存总纲")
        master_btn.clicked.connect(self._on_save_master)
        master_layout.addWidget(master_btn)
        self.tabs.addTab(master_tab, "总纲")

        # ===== 卷纲 Tab（对接 .novelforge/volumes.json 权威源）=====
        volume_tab = QWidget()
        volume_layout = QHBoxLayout(volume_tab)
        vol_left = QVBoxLayout()
        vol_left.addWidget(QLabel("卷列表"))
        self.volume_list = QListWidget()
        self.volume_list.currentItemChanged.connect(self._on_volume_selected)
        vol_left.addWidget(self.volume_list)
        vol_btn_layout = QHBoxLayout()
        add_vol_btn = QPushButton("新增卷")
        add_vol_btn.clicked.connect(self._on_add_volume)
        del_vol_btn = QPushButton("删除")
        del_vol_btn.clicked.connect(self._on_del_volume)
        vol_btn_layout.addWidget(add_vol_btn)
        vol_btn_layout.addWidget(del_vol_btn)
        vol_left.addLayout(vol_btn_layout)
        volume_layout.addLayout(vol_left, 1)
        vol_right = QVBoxLayout()
        vol_right.addWidget(QLabel("卷纲（编辑后生效于生成）"))
        form = QGroupBox()
        form_layout = QVBoxLayout(form)
        hl1 = QHBoxLayout()
        hl1.addWidget(QLabel("标题"))
        self.vol_title_edit = QLineEdit()
        hl1.addWidget(self.vol_title_edit)
        form_layout.addLayout(hl1)
        hl2 = QHBoxLayout()
        hl2.addWidget(QLabel("主题"))
        self.vol_theme_edit = QLineEdit()
        hl2.addWidget(self.vol_theme_edit)
        form_layout.addLayout(hl2)
        hl3 = QHBoxLayout()
        hl3.addWidget(QLabel("起始章"))
        self.vol_start_spin = QSpinBox()
        self.vol_start_spin.setRange(1, 9999)
        hl3.addWidget(self.vol_start_spin)
        hl3.addWidget(QLabel("结束章"))
        self.vol_end_spin = QSpinBox()
        self.vol_end_spin.setRange(1, 9999)
        hl3.addWidget(self.vol_end_spin)
        form_layout.addLayout(hl3)
        vol_right.addWidget(form)
        vol_right.addWidget(QLabel("卷概要"))
        self.vol_summary_edit = QPlainTextEdit()
        self.vol_summary_edit.setPlaceholderText("选择左侧卷进行编辑...")
        vol_right.addWidget(self.vol_summary_edit)
        save_vol_btn = QPushButton("保存卷纲")
        save_vol_btn.clicked.connect(self._on_save_volume)
        vol_right.addWidget(save_vol_btn)
        volume_layout.addLayout(vol_right, 3)
        self.tabs.addTab(volume_tab, "卷纲")

        # ===== 章纲 Tab（简单章节大纲，对接 chapter_outlines.json）=====
        chapter_tab = QWidget()
        chapter_layout = QHBoxLayout(chapter_tab)
        ch_left = QVBoxLayout()
        ch_left.addWidget(QLabel("章节列表（权威源：chapter_outlines.json）"))
        self.chapter_outline_list = QListWidget()
        self.chapter_outline_list.currentItemChanged.connect(self._on_chapter_outline_selected)
        ch_left.addWidget(self.chapter_outline_list)
        ch_btn_layout = QHBoxLayout()
        add_ch_btn = QPushButton("新增章纲")
        add_ch_btn.clicked.connect(self._on_add_chapter_outline)
        del_ch_btn = QPushButton("删除")
        del_ch_btn.clicked.connect(self._on_del_chapter_outline)
        ch_btn_layout.addWidget(add_ch_btn)
        ch_btn_layout.addWidget(del_ch_btn)
        ch_left.addLayout(ch_btn_layout)
        chapter_layout.addLayout(ch_left, 1)
        ch_right = QVBoxLayout()
        ch_right.addWidget(QLabel("简大纲 / 写作指令（项目级 · core_events，编辑后生效于生成）—— 这是「做什么」的项目清单，含时间线锚点；深大纲才是「写出来什么样」。"))
        self.chapter_outline_editor = QPlainTextEdit()
        self.chapter_outline_editor.setPlaceholderText("编写本章的简大纲：核心事件 + 写作指令 + 时间线要点...\n\n写作时会自动注入此内容作为指导（它是项目级清单，深大纲负责展开为故事与文笔）。")
        ch_right.addWidget(self.chapter_outline_editor)
        # 时间锚点（章级时间轴：距上章过了几天 / 当前剧情日）
        ch_right.addWidget(QLabel("时间锚点（章级时间轴：距上章过了几天 / 当前剧情日，如「+3天·剧情第17天」）"))
        self.time_anchor_edit = QPlainTextEdit()
        self.time_anchor_edit.setPlaceholderText("例如：距上章 +3 天；剧情日 = 第 17 天")
        self.time_anchor_edit.setMaximumHeight(48)
        ch_right.addWidget(self.time_anchor_edit)
        save_ch_btn = QPushButton("保存章纲")
        save_ch_btn.clicked.connect(self._on_save_chapter_outline)
        ch_right.addWidget(save_ch_btn)
        chapter_layout.addLayout(ch_right, 3)
        self.tabs.addTab(chapter_tab, "简大纲")

        # ===== 深大纲 Tab（事件进展规格，对接 chapter_outlines.json）=====
        deep_tab = QWidget()
        deep_layout = QHBoxLayout(deep_tab)
        deep_left = QVBoxLayout()
        deep_left.addWidget(QLabel("章节列表（event_progress）"))
        self.deep_list = QListWidget()
        self.deep_list.currentItemChanged.connect(self._on_deep_selected)
        deep_left.addWidget(self.deep_list)
        deep_layout.addLayout(deep_left, 1)
        deep_right = QVBoxLayout()
        deep_layout.addLayout(deep_right, 3)

        # 薄大纲（只读上下文）
        deep_right.addWidget(QLabel("薄大纲（上下文，只读）:"))
        self.deep_thin = QPlainTextEdit()
        self.deep_thin.setReadOnly(True)
        self.deep_thin.setMaximumHeight(70)
        deep_right.addWidget(self.deep_thin)

        # 本章写作重心（章级）：深大纲回答「这一章要着重写什么」，写章时钉进硬约束区
        deep_right.addWidget(QLabel("本章写作重心（writing_focus，写章时作为硬约束注入）:"))
        self.writing_focus_edit = QLineEdit()
        self.writing_focus_edit.setPlaceholderText(
            "1-2 句：动作场面 / 人物心理 / 悬念铺陈 / 对话交锋 / 情感转折 / 环境渲染 + 具体落点"
        )
        deep_right.addWidget(self.writing_focus_edit)

        # beat 表
        deep_right.addWidget(QLabel("事件进展节拍（event_progress，按行序推进，可编辑）:"))
        self.deep_align_info = QLabel("")
        self.deep_align_info.setStyleSheet("color:#8aadf4;font-size:11px;padding:2px 0;")
        deep_right.addWidget(self.deep_align_info)
        self.beat_table = QTableWidget()
        self.beat_table.setColumnCount(10)
        self.beat_table.setHorizontalHeaderLabels(
            ["#", "beat_id", "summary", "硬事实(checkpoint)", "entities",
             "goal", "outcome", "advances", "来源", "文笔(prose_focus)"]
        )
        hh = self.beat_table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # summary 占宽
        hh.setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)  # 文笔 占宽
        for _c in (0, 1, 3, 4, 5, 6, 7, 8):
            hh.setSectionResizeMode(_c, QHeaderView.ResizeMode.ResizeToContents)
        self.beat_table.verticalHeader().setVisible(False)
        deep_right.addWidget(self.beat_table)
        legend = QLabel(
            "来源：<font color='#7ee787'>账本对齐</font> = 来自事件账本节点"
            "（与写章注入同一份数据，双重铺设·不吃书）｜"
            "<font color='#cba6f7'>规划额外</font> = LLM/规划补充（人物·暗线·细节）"
        )
        legend.setStyleSheet("color:#6e738d;font-size:10px;padding:2px 0;")
        deep_right.addWidget(legend)

        # 衔接锚点
        anchor_box = QGroupBox("衔接锚点（continuity_anchors）")
        anchor_layout = QVBoxLayout(anchor_box)
        anchor_layout.addWidget(QLabel("承接上章（from_prev）:"))
        self.anchor_from = QLineEdit()
        anchor_layout.addWidget(self.anchor_from)
        anchor_layout.addWidget(QLabel("埋下钩子（to_next）:"))
        self.anchor_to = QLineEdit()
        anchor_layout.addWidget(self.anchor_to)
        deep_right.addWidget(anchor_box)

        # 状态增量
        deep_right.addWidget(QLabel("状态增量（state_deltas，JSON 列表）:"))
        self.state_delta_edit = QPlainTextEdit()
        self.state_delta_edit.setPlaceholderText("[{\"character\": \"沈砚\", \"before\": \"\", \"after\": \"\"}]")
        self.state_delta_edit.setMaximumHeight(70)
        deep_right.addWidget(self.state_delta_edit)

        deep_save_btn = QPushButton("保存深大纲")
        deep_save_btn.clicked.connect(self._on_save_deep)
        deep_right.addWidget(deep_save_btn)

        self.tabs.addTab(deep_tab, "深大纲（故事·文笔）")

        layout.addWidget(self.tabs)

    # ------------------------------------------------------------------
    # 总纲（markdown）
    # ------------------------------------------------------------------
    def _on_save_master(self):
        if not self._pm:
            return
        content = self.master_editor.toPlainText()
        self._pm.save_outline(self._project_root, "master", content)
        self.outline_changed.emit("master", content)
        self._show_saved("总纲")

    # ------------------------------------------------------------------
    # 卷纲（对接 .novelforge/volumes.json 权威源）
    # ------------------------------------------------------------------
    def _refresh_volume_list(self):
        """从 volumes.json 载入卷列表到左侧 QListWidget，保留当前选中。"""
        self.volume_list.blockSignals(True)
        self.volume_list.clear()
        for v in load_volumes(self._project_root):
            vid = v.get("id", 0)
            title = v.get("title", f"第{vid}卷")
            s, e = v.get("start_chapter", 1), v.get("end_chapter", 1)
            label = f"第{vid}卷 {title}（第{s}-{e}章）"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, vid)
            self.volume_list.addItem(item)
        self.volume_list.blockSignals(False)
        if self._current_volume_id is not None:
            for i in range(self.volume_list.count()):
                if self.volume_list.item(i).data(Qt.ItemDataRole.UserRole) == self._current_volume_id:
                    self.volume_list.setCurrentRow(i)
                    break

    def _selected_volume(self) -> dict | None:
        if self._current_volume_id is None:
            return None
        for v in load_volumes(self._project_root):
            if v.get("id") == self._current_volume_id:
                return v
        return None

    def _on_volume_selected(self, current: QListWidgetItem, _previous):
        if not current:
            self._current_volume_id = None
            return
        self._current_volume_id = current.data(Qt.ItemDataRole.UserRole)
        v = self._selected_volume()
        if not v:
            return
        self.vol_title_edit.setText(v.get("title", ""))
        self.vol_theme_edit.setText(v.get("theme", ""))
        self.vol_start_spin.setValue(int(v.get("start_chapter", 1) or 1))
        self.vol_end_spin.setValue(int(v.get("end_chapter", 1) or 1))
        self.vol_summary_edit.setPlainText(v.get("summary", ""))

    def _on_add_volume(self):
        if not self._project_root:
            return
        title, ok = QInputDialog.getText(self, "新增卷", "卷标题（如: 第一卷）:")
        if not (ok and title.strip()):
            return
        vols = load_volumes(self._project_root)
        new_id = max([v.get("id", 0) for v in vols], default=0) + 1
        start = 1
        if vols:
            start = max(int(v.get("end_chapter", 1) or 1) for v in vols) + 1
        vols.append({
            "id": new_id,
            "title": title.strip(),
            "theme": "",
            "start_chapter": start,
            "end_chapter": start,
            "summary": "",
        })
        save_volumes(self._project_root, vols)
        self._current_volume_id = new_id
        self._refresh_volume_list()
        self._on_volume_selected(self.volume_list.currentItem(), None)
        self.outline_changed.emit("volumes", json.dumps(vols, ensure_ascii=False))
        self._show_saved(f"第{new_id}卷 已新增")

    def _on_del_volume(self):
        if self._current_volume_id is None:
            return
        reply = QMessageBox.question(
            self, "确认", f"删除「第{self._current_volume_id}卷」？（仅删卷定义，不删章节正文）")
        if reply != QMessageBox.StandardButton.Yes:
            return
        vols = [v for v in load_volumes(self._project_root) if v.get("id") != self._current_volume_id]
        save_volumes(self._project_root, vols)
        self._current_volume_id = None
        self._refresh_volume_list()
        self.outline_changed.emit("volumes", json.dumps(vols, ensure_ascii=False))
        self._show_saved("卷已删除")

    def _on_save_volume(self):
        if self._current_volume_id is None:
            return
        vols = load_volumes(self._project_root)
        found = None
        for v in vols:
            if v.get("id") == self._current_volume_id:
                found = v
                break
        if not found:
            return
        found["title"] = self.vol_title_edit.text().strip()
        found["theme"] = self.vol_theme_edit.text().strip()
        found["start_chapter"] = self.vol_start_spin.value()
        found["end_chapter"] = self.vol_end_spin.value()
        found["summary"] = self.vol_summary_edit.toPlainText()
        save_volumes(self._project_root, vols)
        self._write_volumes_markdown(vols)
        self._refresh_volume_list()
        self.outline_changed.emit("volumes", json.dumps(vols, ensure_ascii=False))
        self._show_saved(f"第{self._current_volume_id}卷 已保存")

    def _write_volumes_markdown(self, vols: list[dict]) -> None:
        """生成 outline/volumes.md 人读镜像（仅备份，不被生成消费）。"""
        if not self._project_root:
            return
        lines = ["# 卷结构总览（由 volumes.json 自动生成，勿手改）", ""]
        for v in vols:
            s, e = v.get("start_chapter", 1), v.get("end_chapter", 1)
            lines.append(f"## 第{v.get('id')}卷 {v.get('title','')}（第{s}-{e}章）")
            if v.get("theme"):
                lines.append(f"- 主题：{v['theme']}")
            if v.get("summary"):
                lines.append("")
                lines.append(v["summary"])
            lines.append("")
        out_dir = Path(self._project_root) / "outline"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "volumes.md").write_text("\n".join(lines), encoding="utf-8")

    # ------------------------------------------------------------------
    # 章纲（对接 chapter_outlines.json）
    # ------------------------------------------------------------------
    def _on_chapter_outline_selected(self, current: QListWidgetItem, _previous):
        if not current:
            return
        cid = current.data(Qt.ItemDataRole.UserRole)
        self._current_chapter_id = cid
        ol = self._find_outline(cid)
        self.chapter_outline_editor.setPlainText(ol.get("core_events", "") if ol else "")
        self.time_anchor_edit.setPlainText(ol.get("time_anchor", "") if ol else "")

    def _on_save_chapter_outline(self):
        cid = self._current_chapter_id
        if not cid:
            return
        content = self.chapter_outline_editor.toPlainText()
        outlines = self._json_outlines()
        ol = self._find_outline(cid, outlines)
        if ol is None:
            ol = {"chapter_id": cid, "title": f"第{cid}章"}
            outlines.append(ol)
        ol["core_events"] = content
        ol["time_anchor"] = self.time_anchor_edit.toPlainText()
        self._save_json_outlines(outlines)
        self._show_saved(f"第{cid}章 章纲")

    def _on_add_chapter_outline(self):
        outlines = self._json_outlines()
        max_id = max([ol.get("chapter_id", 0) for ol in outlines], default=0)
        next_id = max_id + 1
        outlines.append({"chapter_id": next_id, "title": f"第{next_id}章", "core_events": ""})
        self._save_json_outlines(outlines)
        self._refresh()

    def _on_del_chapter_outline(self):
        cid = self._current_chapter_id
        if not cid:
            return
        reply = QMessageBox.question(self, "确认", f"删除「第{cid}章」大纲（含深大纲）？")
        if reply != QMessageBox.StandardButton.Yes:
            return
        outlines = [ol for ol in self._json_outlines() if ol.get("chapter_id") != cid]
        self._save_json_outlines(outlines)
        self._refresh()

    # ------------------------------------------------------------------
    # 深大纲（event_progress）
    # ------------------------------------------------------------------
    def _on_deep_selected(self, current: QListWidgetItem, _previous):
        if not current:
            return
        cid = current.data(Qt.ItemDataRole.UserRole)
        self._current_deep_chapter = cid
        ol = self._find_outline(cid)
        if not ol:
            self.deep_thin.setPlainText("")
            self.beat_table.setRowCount(0)
            self.deep_align_info.setText("")
            self.writing_focus_edit.setText("")
            self.anchor_from.setText("")
            self.anchor_to.setText("")
            self.state_delta_edit.setPlainText("[]")
            return
        # 薄大纲（只读）
        thin = f"plot_summary: {ol.get('plot_summary', '')}\ncore_events: {ol.get('core_events', '')}"
        self.deep_thin.setPlainText(thin)
        # beat 表（含事件层=章节层 对齐标记）
        beats = ol.get("event_progress") or []
        self.beat_table.setRowCount(len(beats))
        ledger_n = extra_n = 0
        for r, b in enumerate(beats):
            # 第 0 列 = 推进顺序（数组下标+1），序号只由渲染层给，数据层不存
            self._set_cell(r, 0, str(r + 1))
            self._set_cell(r, 1, str(b.get("beat_id", "")))
            self._set_cell(r, 2, str(b.get("summary", "")))
            self._set_cell(r, 3, str(b.get("checkpoint", "")))
            self._set_cell(r, 4, "、".join(b.get("entities", []) or []))
            self._set_cell(r, 5, str(b.get("goal", "")))
            self._set_cell(r, 6, str(b.get("outcome", "")))
            self._set_cell(r, 7, "、".join(b.get("advances", []) or []))
            # 来源标记：beat_id 以 ev* 开头 = 来自事件账本（双重铺设对齐节点）
            is_ledger = str(b.get("beat_id", "")).startswith("ev")
            self._set_cell(r, 8, "账本对齐" if is_ledger else "规划额外")
            # 文笔落点（prose_focus）：本拍的文笔落点，服从章级 writing_focus
            self._set_cell(r, 9, str(b.get("prose_focus", "")))
            # 非法 checkpoint（半截词/粘连词）标红，肉眼即可发现
            try:
                from planner import is_bad_checkpoint
                _ck = str(b.get("checkpoint") or "").strip()
                if _ck and is_bad_checkpoint(_ck):
                    _it = self.beat_table.item(r, 3)
                    if _it is not None:
                        _it.setBackground(QBrush(QColor(90, 30, 30)))
                        _it.setForeground(QBrush(QColor(255, 160, 160)))
                        _it.setToolTip("疑似半截词/粘连词，正文将永远匹配不到 → 假报警。请改为完整专名")
            except Exception:
                pass
            # 着色 beat_id 单元格，让对齐状态一眼可见（暗色主题）
            item0 = self.beat_table.item(r, 1)
            if item0 is not None:
                if is_ledger:
                    item0.setBackground(QBrush(QColor(34, 70, 50)))    # 深绿底
                    item0.setForeground(QBrush(QColor(126, 231, 135)))  # 亮绿字
                    ledger_n += 1
                else:
                    item0.setBackground(QBrush(QColor(60, 45, 75)))    # 深紫底
                    item0.setForeground(QBrush(QColor(203, 166, 247)))  # 亮紫字
                    extra_n += 1
        self.deep_align_info.setText(
            f"本张 {len(beats)} 个 beat：账本对齐 {ledger_n} · 规划额外 {extra_n}　"
            f"（事件层=章节层 双重铺设）"
        )
        # 章级写作重心
        self.writing_focus_edit.setText(str(ol.get("writing_focus", "") or ""))
        # 锚点
        anchors = ol.get("continuity_anchors") or {}
        self.anchor_from.setText(str(anchors.get("from_prev", "")))
        self.anchor_to.setText(str(anchors.get("to_next", "")))
        # 状态增量
        self.state_delta_edit.setPlainText(json.dumps(ol.get("state_deltas", []), ensure_ascii=False, indent=2))

    def _on_save_deep(self):
        cid = self._current_deep_chapter
        if not cid:
            return
        outlines = self._json_outlines()
        ol = self._find_outline(cid, outlines)
        if ol is None:
            ol = {"chapter_id": cid, "title": f"第{cid}章"}
            outlines.append(ol)
        # 收集 beat
        beats = []
        for r in range(self.beat_table.rowCount()):
            b = {
                "beat_id": self._cell(r, 1),
                "summary": self._cell(r, 2),
                "checkpoint": self._cell(r, 3),
                "entities": [x for x in self._cell(r, 4).split("、") if x],
                "goal": self._cell(r, 5),
                "outcome": self._cell(r, 6),
                "advances": [x for x in self._cell(r, 7).split("、") if x],
                "prose_focus": self._cell(r, 9),
            }
            _norm_beat(b)
            beats.append(b)
        ol["event_progress"] = beats
        ol["writing_focus"] = self.writing_focus_edit.text().strip()
        ol["continuity_anchors"] = {
            "from_prev": self.anchor_from.text().strip(),
            "to_next": self.anchor_to.text().strip(),
        }
        try:
            ol["state_deltas"] = json.loads(self.state_delta_edit.toPlainText() or "[]")
        except Exception:
            ol["state_deltas"] = []
        self._save_json_outlines(outlines)
        self._show_saved(f"第{cid}章 深大纲")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _find_outline(self, cid: int, outlines: list[dict] | None = None) -> dict | None:
        outlines = self._json_outlines() if outlines is None else outlines
        for ol in outlines:
            if ol.get("chapter_id") == cid:
                return ol
        return None

    def _set_cell(self, row: int, col: int, text: str):
        self.beat_table.setItem(row, col, QTableWidgetItem(text))

    def _cell(self, row: int, col: int) -> str:
        item = self.beat_table.item(row, col)
        return item.text() if item else ""

    def _show_saved(self, name: str):
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(f"{name} 已保存", 2000)
            except Exception:
                pass

    def get_chapter_outline(self, chapter_id: int) -> str:
        """获取指定章节的章纲内容（写作时注入用）。返回 core_events。"""
        ol = self._find_outline(chapter_id)
        return ol.get("core_events", "") if ol else ""
