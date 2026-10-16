"""
NovelForge 主窗口（MainWindow）。

设计理念（AI主导 + 钩子驱动 + 大纲确认模式）：
- 新建项目时，用户只需提供书名、题材、核心灵感和故事基调
- 世界观、设定、简介、大纲由AI自动生成（不可跳过）
- AI完成基础设定后，用户可批量生成章节大纲
- 用户确认满意的大纲后，AI批量生成章节正文
- 对已生成的章节可给出修改意见，AI自动修订
- 每章必须有钩子（开篇+结尾优先，最少保证结尾钩子）
- 主线可暂停/放缓，允许缓冲章/氛围章/独白章/世界观特写章
- 伏笔可埋可不埋，无强制要求

布局结构（2026-07-06 重构后）：
+----------------------------------------------------------+
| 菜单栏: 文件 | 写作 | 项目 | 视图 | 帮助                   |
+----------------------------------------------------------+
| 工具栏: [新建] [打开] | [一键写下一章] [写作] | [设置]     |
+--------+---------------------------------------+-----------+
|        |              中央工作区                  |           |
| 项目   |  +----------------------------------+  |           |
| 列表   |  |         章节编辑器               |  |  管理面板  |
| (左)   |  |  左:章节树  右:编辑器+大纲详情   |  |  (右，可折叠)│
|        |  +----------------------------------+  |           |
|        |  +----------------------------------+  |           |
|        |  |    写作控制台（紧凑栏）           |  |           |
|        |  |  模式/类型 | 走向输入 | 写作/修订 |  |           |
|        |  +----------------------------------+  |           |
+--------+---------------------------------------+-----------+
| 状态栏: 当前项目 | 章节数 | 总字数 | 上次保存时间           |
+----------------------------------------------------------+

中央工作区：
- ChapterEditorWidget 占满（水平分割：左侧章节树 + 右侧编辑器区）
- 写作/修订控制台已集成在右侧编辑器的正文下方，不再有独立浮层
  注：AI 流式输出直接写入正文编辑器

右侧面板（水平可折叠）：
- 管理 Tab：项目概览、设定管理、总纲管理
- 记忆 Tab：记忆档案
- 灵感 Tab：灵感碎片
（注：章节管理已从管理面板移除，集成到中央编辑器）
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QLabel, QPushButton,
    QToolBar, QStatusBar, QMenu, QMenuBar, QMessageBox, QFileDialog,
    QDialog, QFormLayout, QLineEdit, QTextEdit, QComboBox, QInputDialog,
    QProgressDialog, QTabWidget, QSizePolicy, QFrame, QSpinBox,
    QCheckBox, QDialogButtonBox, QListWidget, QListWidgetItem, QGroupBox,
)
from PyQt6.QtGui import QPainter, QColor, QAction
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize

import sys
import traceback as _tb_mod


_DIALOG_CLOSE_LOG = os.path.expanduser("~/.novelforge_dialog_close.log")


def _write_dialog_log(line: str):
    """写讨论框生命周期日志（独立函数：C++ 对象销毁后也能写）。"""
    try:
        with open(_DIALOG_CLOSE_LOG, "a", encoding="utf-8") as _f:
            _f.write(line + "\n" + "=" * 60 + "\n")
    except Exception:
        pass


class _TracedDialog(QWidget):
    """讨论窗口（用 QWidget 而非 QDialog，从根源消灭 accept/reject/done 整类关框 bug）。

    QDialog 有 C++ 内部机制（autoDefault 按钮、ESC→reject、Return→accept、done()）
    可以绕过 Python override 直接关掉/销毁对话框。QWidget 没有这些机制，
    只有 close() 和窗口管理器（点 X）才能关框，且都走 closeEvent——可完全控制。

    全生命周期留痕：created/shown/close/hide/destroyed 全部写日志；
    主窗口侧另有守护定时器，发现窗口异常消失（未走 closeEvent）立即恢复/重建。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 设为独立窗口（有标题栏），而非嵌入父窗口的子 widget
        self.setWindowFlag(Qt.WindowType.Window, True)
        # 绝不在关闭时自动删除 C++ 对象（防止 Python 引用还在但 widget 已销毁）
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._user_closed = False  # 用户主动关闭标记（区分「正常关」vs「异常消失」）
        _dlg_id = id(self)
        _log_path = _DIALOG_CLOSE_LOG

        def _on_destroyed(*_a):
            # C++ 对象被销毁（任何原因）。destroyed 后不能调 self 方法，只写文件。
            try:
                with open(_log_path, "a", encoding="utf-8") as _f:
                    _f.write(
                        f"[DIALOG destroyed] id={_dlg_id} — 窗口被 C++ 层销毁，"
                        f"未走 closeEvent/hideEvent！\n" + "=" * 60 + "\n"
                    )
            except Exception:
                pass

        try:
            self.destroyed.connect(_on_destroyed)
        except Exception:
            pass
        self._trace(f"created id={_dlg_id}")

    def _trace(self, tag: str):
        stack = "".join(_tb_mod.format_stack())
        line = f"[DIALOG {tag}] caller stack:\n{stack}"
        try:
            print(line, file=sys.stderr, flush=True)
        except Exception:
            pass
        _write_dialog_log(line)

    def showEvent(self, ev):
        # 每次显示都重置「用户已关闭」标记，并留痕
        self._user_closed = False
        self._trace("shown")
        super().showEvent(ev)

    def closeEvent(self, ev):
        self._trace("closeEvent")
        panel = getattr(self, "_panel", None)
        if panel is not None and getattr(panel, "_busy", False):
            # AI 生成中拦截关框，避免 dangling 线程崩溃
            self._trace("closeEvent(blocked: busy)")
            ev.ignore()
            try:
                panel._append_history("system", "⏳ 讨论仍在生成中，请稍候再关闭。")
                panel._render()
            except Exception:
                pass
            return
        # 用户主动关闭（点 X / close()）——守护定时器据此不干预
        self._user_closed = True
        super().closeEvent(ev)

    def hideEvent(self, ev):
        self._trace("hideEvent")
        super().hideEvent(ev)

    def keyPressEvent(self, ev):
        # QWidget 没有 ESC→reject 机制；保留回车拦截以防万一
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            ev.ignore()
            return
        # ESC：走 close()（→closeEvent）——空闲时正常关、AI 生成中被 closeEvent 拦截，
        # 与提示文案「点 X / 按 ESC 可正常关闭」保持一致。
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(ev)

from ..config import Config, LLMEndpoint
from ..core.llm_client import LLMClient
from ..core.memory.engine import MemoryEngine
from ..core.context.assembler import ContextAssembler
from ..core.pipeline.writing import WritingPipeline, PipelineStage, PipelineResult, InitProjectResult
from ..core.project.manager import ProjectManager, NovelProject
from ..core.lan.server import LanViewerServer
from .widgets.lan_share_dialog import LanShareDialog
from .widgets.chapter_editor import ChapterEditorWidget
from .styles import (
    BTN_PRIMARY, BTN_SECONDARY, BTN_SM, BTN_SM_ACCENT,
    TOOLBAR_BTN, TOOLBAR_BTN_ACCENT,
)
from .widgets.memory_panel import MemoryPanelWidget
from .widgets.context_guard_preview import ContextGuardPreviewWidget
from .widgets.settings_dialog import SettingsDialog
from .widgets.inspiration_panel import InspirationPanelWidget
from .widgets.discussion_panel import DiscussionPanelWidget
# 临时开关：关闭灵感碎片 Tab（用户反馈右侧栏标签拥挤/显示不全）。
# 置 True 即恢复（注意同步恢复下方 collapsed 标签、头部按钮、tab_map 三项）。
SHOW_INSPIRATION_TAB = False
from .widgets.project_dashboard import ProjectDashboardWidget
from .widgets.project_list_panel import ProjectListPanel
from .widgets.plausibility_dialog import PlausibilityReportDialog
from .workers.llm_worker import WritingWorker
from ..utils.logger import logger


class _VerticalLabel(QWidget):
    """垂直文字标签，用于面板收起时显示面板名称（可点击）。"""
    clicked = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._text = text
        self.setFixedWidth(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: transparent;")
        self.setToolTip(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.translate(18, 0)
        painter.rotate(90)
        painter.setPen(QColor("#cdd6f4"))
        fm = self.fontMetrics()
        text = self._text
        avail = self.height()
        if fm.horizontalAdvance(text) > avail:
            while fm.horizontalAdvance(text + "…") > avail and len(text) > 1:
                text = text[:-1]
            text += "…"
        painter.drawText(
            0, 0, avail, fm.height(),
            Qt.AlignmentFlag.AlignCenter,
            text
        )
        painter.end()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


# ============================================================
# Worker 线程类
# ============================================================

class OutlineGenWorker(QThread):
    """批量生成章节大纲的工作线程。"""

    progress = pyqtSignal(str, str)  # stage_name, content
    batch_progress = pyqtSignal(int, int)  # current_batch, total_batches
    finished = pyqtSignal(bool, str)  # success, error

    def __init__(
        self,
        pipeline: WritingPipeline,
        start: int,
        end: int,
        request: str = "",
        book_name: str = "",
        genre: str = "",
        tone: str = "",
        novel_category: str = "",
        prior_window: int = 15,
        parent=None,
    ):
        super().__init__(parent)
        self._pipeline = pipeline
        self._start = start
        self._end = end
        self._request = request
        self._book_name = book_name
        self._genre = genre
        self._tone = tone
        self._novel_category = novel_category
        self._prior_window = prior_window

    def run(self):
        try:
            chapter_count = self._end - self._start + 1

            self.progress.emit(
                f"逐章顺序生成 {chapter_count} 章大纲（每章基于前文真实内容）...", ""
            )

            def _on_chapter(cid: int, end: int, ol: dict):
                title = ol.get("title", f"第{cid}章")
                self.batch_progress.emit(cid, end)
                self.progress.emit(f"已生成第{cid}/{end}章《{title}》", "")

            outlines = self._pipeline.generate_chapter_outlines(
                start_chapter=self._start,
                end_chapter=self._end,
                book_name=self._book_name,
                genre=self._genre,
                tone=self._tone,
                novel_category=self._novel_category,
                user_request=self._request,
                prior_window=self._prior_window,
                on_chapter=_on_chapter,
            )
            self._pipeline.save_chapter_outlines(outlines)
            self.finished.emit(True, "")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"大纲生成失败: {e}\n{tb}")
            self.finished.emit(False, str(e))


class BatchWriteWorker(QThread):
    """批量写入多个章节正文的工作线程。"""

    progress = pyqtSignal(int, int, str)  # current, total, stage_name
    stream = pyqtSignal(str)
    chapter_done = pyqtSignal(int, str)  # chapter_id, text
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        pipeline: WritingPipeline,
        chapter_ids: list[int],
        outlines: list[dict],
        config: dict,
        parent=None,
    ):
        super().__init__(parent)
        self._pipeline = pipeline
        self._cids = chapter_ids
        self._outlines = outlines
        self._config = config

    def run(self):
        outline_map = {ol.get("chapter_id", 0): ol for ol in self._outlines}
        total = len(self._cids)
        success_count = 0
        errors = []
        for i, cid in enumerate(self._cids):
            ol = outline_map.get(cid, {})
            title = ol.get("title", f"第{cid}章")

            # 从大纲提取完整写作指引（包含钩子、伏笔等）
            instruction_parts = []
            if ol.get("chapter_type_label"):
                instruction_parts.append(f"章节类型: {ol['chapter_type_label']}")
            if ol.get("core_events"):
                instruction_parts.append(f"核心事件: {ol['core_events']}")
            if ol.get("plot_summary"):
                instruction_parts.append(f"情节概要: {ol['plot_summary']}")
            if ol.get("opening_hook"):
                instruction_parts.append(f"开篇钩子: {ol['opening_hook']}")
            if ol.get("ending_hook"):
                instruction_parts.append(f"结尾钩子: {ol['ending_hook']}")
            if ol.get("foreshadowing") and ol["foreshadowing"] != "none":
                instruction_parts.append(f"伏笔: {ol['foreshadowing']}")
            instruction = "\n".join(instruction_parts) if instruction_parts else ""

            self.progress.emit(i + 1, total, f"正在写第{cid}章...")
            try:
                result = self._pipeline.write_chapter(
                    chapter_id=cid,
                    chapter_title=title,
                    writing_instruction=instruction,
                    config=self._config,
                    on_progress=lambda stage: self.progress.emit(i + 1, total, stage.value),
                    on_stream=lambda chunk: self.stream.emit(chunk),
                    outline=ol if ol else None,
                )
                if result.success:
                    success_count += 1
                    self.chapter_done.emit(cid, result.chapter_text)
                else:
                    err_msg = result.error or "未知错误"
                    errors.append(f"第{cid}章: {err_msg}")
                    logger.warning(f"第{cid}章写入失败: {err_msg}")
            except Exception as e:
                errors.append(f"第{cid}章: {e}")
                logger.error(f"第{cid}章写入异常: {e}")

        if errors:
            self.finished.emit(False, f"成功{success_count}/{total}章，失败: {'; '.join(errors[:3])}")
        else:
            self.finished.emit(True, "")


class ReviseWorker(QThread):
    """修订章节的工作线程。"""

    stream = pyqtSignal(str)
    finished = pyqtSignal(bool, str, str)  # success, revised_text, error

    def __init__(
        self,
        pipeline: WritingPipeline,
        chapter_id: int,
        chapter_title: str,
        chapter_text: str,
        revision_request: str,
        parent=None,
    ):
        super().__init__(parent)
        self._pipeline = pipeline
        self._chapter_id = chapter_id
        self._chapter_title = chapter_title
        self._chapter_text = chapter_text
        self._revision_request = revision_request

    def run(self):
        try:
            revised = self._pipeline.revise_chapter(
                chapter_id=self._chapter_id,
                chapter_title=self._chapter_title,
                chapter_text=self._chapter_text,
                revision_request=self._revision_request,
                on_stream=lambda chunk: self.stream.emit(chunk),
            )
            self.finished.emit(True, revised, "")
        except Exception as e:
            self.finished.emit(False, "", str(e))


class ReviseSegmentWorker(QThread):
    """位置精确局部修订的工作线程：只返回修订后的片段本身。"""

    finished = pyqtSignal(bool, str, str)  # success, revised_segment, error

    def __init__(
        self,
        pipeline: WritingPipeline,
        chapter_title: str,
        segment: str,
        context_before: str,
        context_after: str,
        instruction: str,
        parent=None,
    ):
        super().__init__(parent)
        self._pipeline = pipeline
        self._chapter_title = chapter_title
        self._segment = segment
        self._context_before = context_before
        self._context_after = context_after
        self._instruction = instruction

    def run(self):
        try:
            revised = self._pipeline.revise_segment(
                chapter_title=self._chapter_title,
                segment=self._segment,
                context_before=self._context_before,
                context_after=self._context_after,
                revision_request=self._instruction,
            )
            self.finished.emit(True, revised, "")
        except Exception as e:
            self.finished.emit(False, "", str(e))


class PlausibilityWorker(QThread):
    """合理性校验工作线程。"""

    finished_report = pyqtSignal(dict, int)  # report, chapter_id

    def __init__(self, pipeline: WritingPipeline, chapter_id: int, config: dict, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._chapter_id = chapter_id
        self._config = config

    def run(self):
        try:
            report = self._pipeline.validate_chapter(self._chapter_id, self._config)
            self.finished_report.emit(report, self._chapter_id)
        except Exception as e:
            self.finished_report.emit(
                {"passed": True, "score": 0, "issues": [], "real_world_concerns": [],
                 "overall_suggestion": "", "error": str(e)},
                self._chapter_id,
            )


class FullBookValidationWorker(QThread):
    """全书最终校验：写完所有章后，逐章跑一次合理性校验（替代「每章自动审」）。

    仅在用户显式触发时运行；逐章报告聚合为一份总览，避免 500 个弹窗。
    """

    progress = pyqtSignal(int, int, str)          # current, total, status_text
    finished_summary = pyqtSignal(dict)            # 聚合报告

    def __init__(self, pipeline: WritingPipeline, chapter_ids: list[int], config: dict, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._chapter_ids = chapter_ids
        self._config = config

    def run(self):
        total = len(self._chapter_ids)
        passed, failed, errored = 0, 0, 0
        failed_chapters: list[dict] = []   # {cid, title, score, issues, concerns}
        try:
            for i, cid in enumerate(self._chapter_ids, 1):
                self.progress.emit(i, total, f"正在校验第{cid}章 ({i}/{total})...")
                try:
                    rep = self._pipeline.validate_chapter(cid, self._config)
                except Exception as e:
                    errored += 1
                    failed_chapters.append({"cid": cid, "title": f"第{cid}章",
                                            "score": 0, "issues": [], "concerns": [],
                                            "error": str(e)})
                    continue
                err = rep.get("error")
                if err:
                    errored += 1
                    failed_chapters.append({"cid": cid, "title": f"第{cid}章",
                                            "score": 0, "issues": [], "concerns": [],
                                            "error": err})
                    continue
                if rep.get("passed"):
                    passed += 1
                else:
                    failed += 1
                    failed_chapters.append({
                        "cid": cid,
                        "title": rep.get("chapter_title") or f"第{cid}章",
                        "score": rep.get("score", 0),
                        "issues": rep.get("serious_issues") or rep.get("issues") or [],
                        "concerns": rep.get("real_world_concerns") or [],
                        "error": "",
                    })
            summary = {
                "total": total, "passed": passed, "failed": failed, "errored": errored,
                "failed_chapters": failed_chapters,
            }
            self.finished_summary.emit(summary)
        except Exception as e:
            self.finished_summary.emit({"total": total, "passed": passed,
                                         "failed": failed, "errored": errored,
                                         "failed_chapters": failed_chapters, "error": str(e)})


class FullNovelReviseWorker(QThread):
    """全书一致性重写工作线程。"""

    progress = pyqtSignal(int, int, int, str)      # chapter_id, index, total, stage
    chapter_done = pyqtSignal(int, int, int, dict)  # chapter_id, index, total, result
    finished = pyqtSignal(dict, str)                # summary, error

    def __init__(self, pipeline: WritingPipeline, config: dict,
                 stop_check=None, verify: bool = True, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._config = config
        self._stop_check = stop_check
        self._verify = verify

    def run(self):
        try:
            summary = self._pipeline.revise_novel_full(
                config=self._config,
                on_progress=lambda cid, idx, total, stage: self.progress.emit(cid, idx, total, stage),
                on_chapter=lambda cid, idx, total, res: self.chapter_done.emit(cid, idx, total, res),
                stop_check=self._stop_check,
                verify=self._verify,
            )
            self.finished.emit(summary, "")
        except Exception as e:
            import traceback
            logger.error(f"全书一致性重写失败: {e}\n{traceback.format_exc()}")
            self.finished.emit({}, str(e))


class AutoWriteWorker(QThread):
    """自动规划并写下一章的工作线程。"""

    progress = pyqtSignal(str)
    stream = pyqtSignal(str)
    finished = pyqtSignal(bool, int, str, str)  # success, chapter_id, title, text

    def __init__(
        self,
        pipeline: WritingPipeline,
        next_chapter_id: int,
        book_name: str,
        genre: str,
        outline: str,
        config: dict,
        parent=None,
    ):
        super().__init__(parent)
        self._pipeline = pipeline
        self._chapter_id = next_chapter_id
        self._book_name = book_name
        self._genre = genre
        self._outline = outline
        self._config = config

    def run(self):
        try:
            # 1. 自动生成章节方向
            self.progress.emit("正在规划章节方向...")
            direction = self._pipeline.auto_chapter_direction(
                chapter_id=self._chapter_id,
                book_name=self._book_name,
                genre=self._genre,
                outline=self._outline,
                recent_summaries="",
                user_direction="（自动规划下一章内容）",
                foreshadows="",
                volume_context=self._pipeline._volume_context_for_range(
                    self._chapter_id, self._chapter_id
                ),
            )
            instruction = direction.get("direction_detail", "根据总纲自主写作")

            # 2. 写正文
            self.progress.emit("正在生成正文...")
            title = direction.get("title", f"第{self._chapter_id}章")
            result = self._pipeline.write_chapter(
                chapter_id=self._chapter_id,
                chapter_title=title,
                writing_instruction=instruction,
                config=self._config,
                on_progress=lambda stage: self.progress.emit(stage.value),
                on_stream=lambda chunk: self.stream.emit(chunk),
            )
            if result.success:
                self.finished.emit(True, self._chapter_id, title, result.chapter_text)
            else:
                self.finished.emit(False, self._chapter_id, title, result.error)
        except Exception as e:
            self.finished.emit(False, self._chapter_id, "", str(e))


class RegenSettingWorker(QThread):
    """单项重新生成（世界观/主角设定/总纲/简介）的工作线程。"""

    progress = pyqtSignal(str)
    finished = pyqtSignal(str, bool, str, str)  # item_type, success, content, error

    def __init__(
        self,
        pipeline: WritingPipeline,
        item_type: str,  # "worldview" | "character_main" | "master" | "synopsis"
        book_name: str,
        genre: str,
        inspiration: str,
        tone: str = "",
        user_request: str = "",
        novel_category: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._pipeline = pipeline
        self._item_type = item_type
        self._book_name = book_name
        self._genre = genre
        self._inspiration = inspiration
        self._tone = tone
        self._user_request = user_request
        self._novel_category = novel_category

    def run(self):
        try:
            it = self._item_type
            self.progress.emit(f"正在重新生成{self._item_label()}...")

            if it == "worldview":
                content = self._pipeline.regenerate_worldview(
                    self._book_name, self._genre, self._inspiration,
                    self._tone, self._user_request,
                )
            elif it == "character_main":
                content = self._pipeline.regenerate_character(
                    self._book_name, self._genre, self._inspiration,
                    self._tone, self._user_request,
                )
            elif it == "master":
                content = self._pipeline.regenerate_outline(
                    self._book_name, self._genre, self._inspiration,
                    self._tone, self._user_request, self._novel_category,
                )
            elif it == "synopsis":
                content = self._pipeline.regenerate_synopsis(
                    self._book_name, self._genre, self._inspiration,
                    self._tone, self._user_request,
                )
                # 简介额外保存到 project.json
                self._pipeline.save_synopsis(content)
            else:
                self.finished.emit(it, False, "", f"未知设定类型: {it}")
                return

            self.finished.emit(it, True, content, "")
        except Exception as e:
            import traceback
            logger.error(f"重新生成失败: {e}\n{traceback.format_exc()}")
            self.finished.emit(self._item_type, False, "", str(e))

    def _item_label(self) -> str:
        labels = {
            "worldview": "世界观设定",
            "character_main": "主角设定",
            "master": "故事总纲",
            "synopsis": "小说简介",
        }
        return labels.get(self._item_type, self._item_type)


class InsertChapterWorker(QThread):
    """插入章节的工作线程（机械移位 + 可选 AI 钩子调整）。"""

    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)  # insert_chapter 返回的 result dict

    def __init__(
        self,
        pipeline: WritingPipeline,
        insert_pos: int,
        book_name: str,
        genre: str,
        tone: str = "",
        user_request: str = "",
        adjust_adjacent: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._pipeline = pipeline
        self._insert_pos = insert_pos
        self._book_name = book_name
        self._genre = genre
        self._tone = tone
        self._user_request = user_request
        self._adjust_adjacent = adjust_adjacent

    def run(self):
        try:
            self.progress.emit(f"正在插入第{self._insert_pos}章并调整前后章...")
            result = self._pipeline.insert_chapter(
                insert_pos=self._insert_pos,
                book_name=self._book_name,
                genre=self._genre,
                tone=self._tone,
                user_request=self._user_request,
                adjust_adjacent=self._adjust_adjacent,
            )
            self.finished.emit(result)
        except Exception as e:
            import traceback
            logger.error(f"插入章节线程异常: {e}\n{traceback.format_exc()}")
            self.finished.emit({
                "new_chapter_id": self._insert_pos,
                "shifted_count": 0,
                "adjusted": False,
                "new_outline": None,
                "error": str(e),
            })


class DeepOutlineWorker(QThread):
    """生成深大纲（按卷批量）的工作线程，避免阻塞 UI。"""

    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, llm_client, pipeline, parent=None):
        super().__init__(parent)
        self._llm = llm_client
        self._pipeline = pipeline

    def run(self):
        try:
            from planner import plan_book_full
            outlines = self._pipeline.get_chapter_outlines()
            # 阶段1→阶段2：先简大纲（含时间线·顺序·前后关系），再深大纲（接事件账本 + 前后±1章）；
            # 从 pipeline 的 memory.archive 取，缺则 None 退化为纯大纲规划。
            arch = None
            mem = getattr(self._pipeline, "_memory", None)
            if mem is not None:
                arch = getattr(mem, "_archive", None)
            plan_book_full(self._llm, outlines, archive=arch)
            self._pipeline.save_chapter_outlines(outlines)
            self.finished.emit(len(outlines))
        except Exception as e:
            import traceback
            logger.error(f"深大纲生成线程异常: {e}\n{traceback.format_exc()}")
            self.error.emit(f"{e}\n{traceback.format_exc()[:600]}")


class DeepOutlineSingleWorker(QThread):
    """生成单章深大纲（按当前章）的工作线程，避免阻塞 UI。"""

    finished = pyqtSignal(int)  # chapter_id
    error = pyqtSignal(str)

    def __init__(self, llm_client, pipeline, chapter_id, parent=None):
        super().__init__(parent)
        self._llm = llm_client
        self._pipeline = pipeline
        self._cid = chapter_id

    def run(self):
        try:
            from planner import plan_chapter_single
            outlines = self._pipeline.get_chapter_outlines()
            target = next(
                (o for o in outlines if o.get("chapter_id") == self._cid), None
            )
            if target is None:
                self.error.emit(f"第{self._cid}章没有简大纲，无法生成深大纲")
                return
            arch = None
            mem = getattr(self._pipeline, "_memory", None)
            if mem is not None:
                arch = getattr(mem, "_archive", None)
            plan_chapter_single(self._llm, target, outlines, archive=arch)
            self._pipeline.save_chapter_outlines(outlines)
            self.finished.emit(self._cid)
        except Exception as e:
            import traceback
            logger.error(f"单章深大纲生成线程异常: {e}\n{traceback.format_exc()}")
            self.error.emit(f"{e}\n{traceback.format_exc()[:600]}")


class FrameworkWorker(QThread):
    """阶段0：一次性生成整本书框架（扁鹊大哥：灭矛盾于未发）。"""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, llm_client, pipeline, parent=None):
        super().__init__(parent)
        self._llm = llm_client
        self._pipeline = pipeline

    def run(self):
        try:
            from framework_planner import generate_framework
            root = str(getattr(self._pipeline, "_project_root", "") or "")
            res = generate_framework(self._llm, root, use_ai=True, overwrite=False)
            self.finished.emit(res)
        except Exception as e:
            import traceback
            logger.error(f"整本书框架生成线程异常: {e}\n{traceback.format_exc()}")
            self.error.emit(f"{e}\n{traceback.format_exc()[:600]}")



class MainWindow(QMainWindow):
    """NovelForge 主窗口。"""

    # 信号
    project_opened = pyqtSignal(str)
    project_closed = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()

        # 注入全局样式表 + 调色板（根据用户选择的皮肤）
        from PyQt6.QtWidgets import QApplication
        from .styles import apply_skin
        _app = QApplication.instance()
        if _app is not None:
            apply_skin(_app, getattr(config, "skin", "mocha"))

        self._config = config
        self._llm_client = LLMClient(config)
        self._project_manager = ProjectManager(str(config.config_dir))
        self._lan_server = LanViewerServer(self._project_manager, port=self._config.lan_port)
        self._current_project: Optional[NovelProject] = None
        self._memory: Optional[MemoryEngine] = None
        self._pipeline: Optional[WritingPipeline] = None
        self._context_assembler: Optional[ContextAssembler] = None
        self._workers: list[QThread] = []

        # 面板折叠状态
        self._left_panel_visible = True
        self._right_panel_visible = True
        self._left_panel_saved_size = 360
        self._right_panel_saved_size = 300

        self.setWindowTitle("NovelForge - AI 驱动的小说写作工具")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        self._init_ui()
        self._init_menus()
        self._init_toolbar()
        self._init_statusbar()

    # ============================================================
    # UI 初始化
    # ============================================================

    def _init_ui(self):
        """构建完整布局。"""
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QHBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # --- 外层分割器 ---
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._outer_splitter.setHandleWidth(8)

        # --- 左侧面板（QStackedWidget 切换折叠/展开）---
        self._left_container = QWidget()
        left_layout = QVBoxLayout(self._left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self._left_stack = QStackedWidget()
        left_layout.addWidget(self._left_stack, 1)

        # 折叠态页面：顶部展开按钮 + 垂直标签
        _left_collapsed_page = QWidget()
        _left_collapsed_page.setStyleSheet("background-color: #181825; border-right: 2px solid #313244;")
        _lc_layout = QVBoxLayout(_left_collapsed_page)
        _lc_layout.setContentsMargins(0, 0, 0, 0)
        _lc_layout.setSpacing(0)
        _lc_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 顶部明显展开按钮
        _lc_expand_btn = QPushButton("▶")
        _lc_expand_btn.setFixedSize(32, 32)
        _lc_expand_btn.setToolTip("展开项目列表")
        _lc_expand_btn.setStyleSheet(TOOLBAR_BTN)
        _lc_expand_btn.clicked.connect(self._toggle_left_panel)
        _lc_layout.addWidget(_lc_expand_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        _lc_layout.addStretch()

        # 垂直标签
        self._left_collapsed_label = _VerticalLabel("项目列表")
        self._left_collapsed_label.clicked.connect(self._toggle_left_panel)
        _lc_layout.addWidget(self._left_collapsed_label, alignment=Qt.AlignmentFlag.AlignCenter)

        _lc_layout.addStretch()
        self._left_stack.addWidget(_left_collapsed_page)

        # 展开态页面：头部 + 项目列表
        _left_expanded_page = QWidget()
        _le_layout = QVBoxLayout(_left_expanded_page)
        _le_layout.setContentsMargins(0, 0, 0, 0)
        _le_layout.setSpacing(0)

        # 头部：标题 + 收起按钮
        self._left_header = QFrame()
        self._left_header.setFixedHeight(32)
        self._left_header.setStyleSheet(
            "QFrame { background-color: #181825; border-bottom: 1px solid #313244; }"
        )
        lh_layout = QHBoxLayout(self._left_header)
        lh_layout.setContentsMargins(8, 0, 4, 0)
        lh_layout.setSpacing(4)
        self._left_title = QLabel("项目列表")
        self._left_title.setStyleSheet("color: #cdd6f4; font-size: 13px; font-weight: bold;")
        lh_layout.addWidget(self._left_title)
        lh_layout.addStretch()
        self._left_collapse_btn = QPushButton("◀")
        self._left_collapse_btn.setFixedSize(22, 22)
        self._left_collapse_btn.setToolTip("收起项目列表")
        self._left_collapse_btn.setStyleSheet(TOOLBAR_BTN)
        self._left_collapse_btn.clicked.connect(self._toggle_left_panel)
        lh_layout.addWidget(self._left_collapse_btn)
        _le_layout.addWidget(self._left_header)

        # 项目列表
        self.project_list = ProjectListPanel(self._project_manager)
        self.project_list.project_opened.connect(self._on_project_list_open)
        self.project_list.chapter_opened.connect(self._on_project_list_chapter_opened)
        self.project_list.new_project_requested.connect(self._on_new_project)
        # 章节操作（信号携带 project_root，必要时先确保为当前项目）
        self.project_list.chapter_insert_requested.connect(
            lambda root, cid: (self._ensure_current_project(root), self._on_insert_chapter(cid))
        )
        self.project_list.chapter_confirm_requested.connect(
            lambda root, cid, conf: (self._ensure_current_project(root), self._on_confirm_outline(cid, conf))
        )
        self.project_list.chapter_write_requested.connect(
            lambda root, cid: (self._ensure_current_project(root), self._on_write_single_chapter(cid))
        )
        self.project_list.chapter_delete_content_requested.connect(
            lambda root, cid: (self._ensure_current_project(root), self._on_delete_chapter_content(cid))
        )
        self.project_list.chapter_batch_delete_content_requested.connect(
            lambda root, cids: (self._ensure_current_project(root), self._on_batch_delete_chapter_content(cids))
        )
        self.project_list.chapter_delete_requested.connect(
            lambda root, cids: self._on_delete_chapter(root, cids)
        )
        self.project_list.volume_delete_requested.connect(
            lambda root, vid: self._on_delete_volume(root, vid)
        )
        self.project_list.chapter_batch_rewrite_requested.connect(
            lambda root, cids: (self._ensure_current_project(root), self._on_batch_write_chapters(cids))
        )
        self.project_list.chapter_validate_requested.connect(
            lambda root, cid: (self._ensure_current_project(root), self._run_validation(cid, auto_mode=False))
        )
        _le_layout.addWidget(self.project_list, 1)
        self._left_stack.addWidget(_left_expanded_page)

        self._left_container.setMinimumWidth(48)

        # --- 主任工作区 ---
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setHandleWidth(8)

        # 中央：编辑器 + 写作控制台（垂直分割）
        self._center_splitter = QSplitter(Qt.Orientation.Vertical)
        self._center_splitter.setHandleWidth(6)

        # 章节编辑器：占主要空间（内部已集成写作/修订控制台于正文下方）
        self.editor = ChapterEditorWidget(self._project_manager)
        self.editor.setMinimumWidth(400)
        self.editor.chapter_selected.connect(self._on_editor_chapter_selected)
        # 章节列表已统一到左侧项目树：编辑器每次切换/刷新章节都同步刷新项目树
        self.editor.chapter_selected.connect(lambda cid, t: self.project_list.refresh())
        self.editor.generate_outlines_requested.connect(self._on_menu_generate_outlines)
        self.editor.batch_write_requested.connect(self._on_menu_batch_write)
        self.editor.confirm_outline_requested.connect(self._on_confirm_outline)
        self.editor.confirm_all_outlines_requested.connect(self._on_confirm_all_outlines)
        self.editor.write_single_chapter_requested.connect(self._on_write_single_chapter)
        self.editor.delete_chapter_requested.connect(
            lambda cid: self._on_delete_chapter(self._current_project.project_root, [cid])
            if self._current_project else None
        )
        self.editor.regenerate_outlines_requested.connect(self._on_menu_generate_outlines)
        # 中间三区块各自的「生成 / 重新生成」按钮
        # 注：_on_regenerate_outline(cid, feedback) 签名是 2 个参数
        # feedback 是给 LLM 的「修改意见」；新加的中间编辑区按钮不弹意见框，直接传空串。
        self.editor.gen_short_outline_requested.connect(
            lambda cid: self._on_regenerate_outline(cid, ""))
        self.editor.regen_short_outline_requested.connect(
            lambda cid: self._on_regenerate_outline(cid, ""))
        self.editor.gen_deep_outline_requested.connect(self._on_gen_deep_outline_single)
        self.editor.regen_deep_outline_requested.connect(self._on_gen_deep_outline_single)
        # 批量重写：编辑器按钮无自身选择，读取左侧项目树的多选
        self.editor.batch_rewrite_requested.connect(self._on_batch_rewrite_from_editor)
        # 顶部工具栏「批量生产深大纲」按钮：复用菜单栏同款 handler
        self.editor.batch_deep_outline_requested.connect(self._on_menu_batch_deep_outline)
        # 新建章节：由编辑器触发，主窗口在末尾创建并定位
        self.editor.new_chapter_requested.connect(self._on_new_chapter)
        # 保存后刷新左侧项目树（章节列表已统一到项目树）
        self.editor.chapter_saved.connect(lambda cid, t, x: self.project_list.refresh())
        # 校验本章：手动触发合理性校验
        self.editor.validate_chapter_requested.connect(
            lambda cid: self._run_validation(cid, auto_mode=False)
        )
        self._center_splitter.addWidget(self.editor)

        # 章节导航（上一章/下一章）
        self.editor.navigate_requested.connect(self._on_navigate)
        # 无下一章时：生成下一章大纲
        self.editor.generate_next_outline_requested.connect(self._on_generate_next_outline)
        # 位置精确局部修订
        self.editor.revise_segment_requested.connect(self._on_revise_segment)

        self._main_splitter.addWidget(self._center_splitter)

        # --- 右侧面板（QStackedWidget 切换折叠/展开）---
        self._right_container = QWidget()
        right_layout = QVBoxLayout(self._right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._right_stack = QStackedWidget()
        right_layout.addWidget(self._right_stack, 1)

        # 折叠态页面：顶部展开按钮 + 三个垂直标签
        _right_collapsed_page = QWidget()
        _right_collapsed_page.setStyleSheet("background-color: #181825; border-left: 2px solid #313244;")
        _rc_layout = QVBoxLayout(_right_collapsed_page)
        _rc_layout.setContentsMargins(0, 0, 0, 0)
        _rc_layout.setSpacing(0)
        _rc_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 顶部明显展开按钮
        _rc_expand_btn = QPushButton("◀")
        _rc_expand_btn.setFixedSize(32, 32)
        _rc_expand_btn.setToolTip("展开面板")
        _rc_expand_btn.setStyleSheet(TOOLBAR_BTN)
        _rc_expand_btn.clicked.connect(lambda: self._toggle_right_panel())
        _rc_layout.addWidget(_rc_expand_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        _rc_layout.addStretch()

        self._right_collapsed_labels = []
        _collapsed_tips = {
            "管理": "项目管理（基础设定/总纲/卷/脊柱/事件）",
            "记忆": "记忆档案（人物/伏笔/时间线/世界观）",
            "预览": "上下文预览（生成前监督 AI 将消费的上下文与守卫）",
        }
        for tab_text in ["管理", "记忆", "预览"]:
            vlabel = _VerticalLabel(tab_text)
            vlabel.setToolTip(_collapsed_tips.get(tab_text, tab_text))
            vlabel.clicked.connect(
                lambda checked=False, text=tab_text: self._toggle_right_panel(tab_name=text)
            )
            self._right_collapsed_labels.append(vlabel)
            _rc_layout.addWidget(vlabel, alignment=Qt.AlignmentFlag.AlignCenter)

        _rc_layout.addStretch()
        self._right_stack.addWidget(_right_collapsed_page)

        # 展开态页面：头部 + 内容
        _right_expanded_page = QWidget()
        _re_layout = QVBoxLayout(_right_expanded_page)
        _re_layout.setContentsMargins(0, 0, 0, 0)
        _re_layout.setSpacing(0)

        # 头部：收起按钮 + 外层 Tab 切换 + 标题
        self._right_header = QFrame()
        self._right_header.setFixedHeight(36)
        self._right_header.setStyleSheet(
            "QFrame { background-color: #181825; border-bottom: 1px solid #313244; }"
        )
        rh_layout = QHBoxLayout(self._right_header)
        rh_layout.setContentsMargins(4, 0, 8, 0)
        rh_layout.setSpacing(4)
        self._right_collapse_btn = QPushButton("▶")
        self._right_collapse_btn.setFixedSize(22, 22)
        self._right_collapse_btn.setToolTip("收起面板")
        self._right_collapse_btn.setStyleSheet(TOOLBAR_BTN)
        self._right_collapse_btn.clicked.connect(self._toggle_right_panel)
        rh_layout.addWidget(self._right_collapse_btn)

        # 外层 Tab 切换按钮（代替 right_tabs 的 TabBar）
        self._right_tab_btns = []
        tab_btn_style = (
            "QPushButton { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a;"
            " border-radius: 3px; font-size: 13px; padding: 2px 10px; }"
            "QPushButton:hover { background-color: #45475a; }"
            "QPushButton:checked { background-color: #585b70; color: #ffffff; border-color: #89b4fa; }"
        )
        for idx, text in enumerate(["管理", "记忆", "预览"]):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setFixedSize(60, 26)
            btn.setStyleSheet(tab_btn_style)
            btn.setToolTip(["项目管理（基础设定/总纲/卷/脊柱/事件）",
                            "记忆档案（人物/伏笔/时间线/世界观）",
                            "上下文预览（生成前监督 AI 将消费的上下文与守卫）"][idx])
            btn.clicked.connect(lambda checked=False, i=idx: self._on_right_tab_changed(i))
            self._right_tab_btns.append(btn)
            rh_layout.addWidget(btn)
        self._right_tab_btns[0].setChecked(True)

        rh_layout.addStretch()
        self._right_title = QLabel("面板")
        self._right_title.setStyleSheet("color: #cdd6f4; font-size: 13px; font-weight: bold;")
        rh_layout.addWidget(self._right_title)
        _re_layout.addWidget(self._right_header)

        # 内容区域：直接放 right_tabs（控制台已移到中间区域）
        self.right_tabs = QTabWidget()
        self.right_tabs.tabBar().setVisible(False)  # 隐藏外层 TabBar，用头部按钮切换

        # 项目管理 Tab
        self.dashboard = ProjectDashboardWidget()
        self.dashboard.generate_outlines_requested.connect(self._on_generate_outlines)
        self.dashboard.batch_generate_requested.connect(self._on_batch_write_chapters)
        self.dashboard.confirm_outline_requested.connect(self._on_confirm_outline)
        self.dashboard.volumes_changed.connect(lambda: self.project_list.refresh())
        self.dashboard.regenerate_outline_requested.connect(self._on_regenerate_outline)
        self.dashboard.regenerate_master_outline_requested.connect(self._on_dashboard_regenerate_outline)
        self.dashboard.batch_regenerate_outlines_requested.connect(self._on_batch_regenerate_outlines)
        self.dashboard.reinit_foundation_requested.connect(self._on_reinit_foundation)
        self.dashboard.regenerate_setting_requested.connect(self._on_dashboard_regenerate_setting)
        self.dashboard.regenerate_synopsis_requested.connect(self._on_dashboard_regenerate_synopsis)
        # 深大纲（按卷批量）：仅在此连一次，避免每次开项目重复连接导致多 worker 并发
        self.dashboard.deep_outline_requested.connect(self._on_deep_outline)
        self.dashboard.framework_requested.connect(self._on_framework)
        self.right_tabs.addTab(self.dashboard, "项目管理")

        # 记忆档案 Tab
        self.memory_panel = MemoryPanelWidget()
        self.right_tabs.addTab(self.memory_panel, "记忆档案")

        # 上下文 & 守卫预览 Tab（生成前监督 AI 将消费的上下文）
        self.context_preview = ContextGuardPreviewWidget()
        self.right_tabs.addTab(self.context_preview, "上下文预览")

        # 灵感碎片 Tab（默认隐藏，由 SHOW_INSPIRATION_TAB 控制）
        if SHOW_INSPIRATION_TAB:
            self.inspiration_panel = InspirationPanelWidget()
            self.inspiration_panel.expand_requested.connect(self._on_inspiration_expanded)
            self.inspiration_panel.integrate_to_outline.connect(self._on_inspire_integrate_outline)
            self.inspiration_panel.integrate_to_setting.connect(self._on_inspire_integrate_setting)
            self.right_tabs.addTab(self.inspiration_panel, "灵感碎片")

        _re_layout.addWidget(self.right_tabs, 1)
        self._right_stack.addWidget(_right_expanded_page)

        self._right_container.setMinimumWidth(48)

        # --- 组装主分割器 ---
        # editor 已在 _center_splitter 中，只需添加 _center_splitter 和 _right_container
        self._main_splitter.addWidget(self._center_splitter)
        self._main_splitter.addWidget(self._right_container)
        self._main_splitter.setSizes([900, 500])

        # --- 组装外层分割器 ---
        self._outer_splitter.addWidget(self._left_container)
        self._outer_splitter.addWidget(self._main_splitter)
        self._outer_splitter.setSizes([360, 1110])

        # 初始默认显示展开态（索引 1）
        self._left_stack.setCurrentIndex(1)
        self._right_stack.setCurrentIndex(1)

        outer_layout.addWidget(self._outer_splitter)

    # ============================================================
    # 菜单栏
    # ============================================================

    def _init_menus(self):
        """构建菜单栏。"""
        menubar = self.menuBar()

        # --- 文件 ---
        file_menu = menubar.addMenu("文件(&F)")

        new_action = QAction("新建项目(&N)", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._on_new_project)
        file_menu.addAction(new_action)

        open_action = QAction("打开项目(&O)", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open_project)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        save_action = QAction("保存项目(&S)", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._on_save_project)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        close_action = QAction("关闭项目(&C)", self)
        close_action.setShortcut("Ctrl+W")
        close_action.triggered.connect(self._on_close_project)
        file_menu.addAction(close_action)

        file_menu.addSeparator()

        exit_action = QAction("退出(&Q)", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # --- 写作 ---
        writing_menu = menubar.addMenu("写作(&W)")

        gen_outline_action = QAction("批量生产简大纲(&O)", self)
        gen_outline_action.triggered.connect(self._on_menu_generate_outlines)
        writing_menu.addAction(gen_outline_action)

        batch_deep_outline_action = QAction("批量生产深大纲(&D)", self)
        batch_deep_outline_action.triggered.connect(self._on_menu_batch_deep_outline)
        writing_menu.addAction(batch_deep_outline_action)

        batch_write_action = QAction("批量生产正文(&B)", self)
        batch_write_action.triggered.connect(self._on_menu_batch_write)
        writing_menu.addAction(batch_write_action)

        writing_menu.addSeparator()

        revise_action = QAction("AI 修订当前章节(&R)", self)
        revise_action.triggered.connect(lambda: self.editor.open_revise_dialog())
        writing_menu.addAction(revise_action)

        # --- 项目 ---
        project_menu = menubar.addMenu("项目(&P)")

        info_action = QAction("项目信息(&I)", self)
        info_action.triggered.connect(self._on_project_info)
        project_menu.addAction(info_action)

        settings_action = QAction("项目设置(&S)", self)
        settings_action.triggered.connect(self._on_project_settings)
        project_menu.addAction(settings_action)

        project_menu.addSeparator()

        full_revise_action = QAction("全书一致性重写(&C)...", self)
        full_revise_action.triggered.connect(self._on_menu_full_novel_revise)
        project_menu.addAction(full_revise_action)

        full_validate_action = QAction("全书最终校验(&V)...", self)
        full_validate_action.triggered.connect(self._on_menu_full_book_validate)
        self._full_validate_action = full_validate_action
        project_menu.addAction(full_validate_action)

        project_menu.addSeparator()

        lan_action = QAction("📱 局域网分享阅读(&L)...", self)
        lan_action.triggered.connect(self._on_lan_share)
        project_menu.addAction(lan_action)

        # --- 视图 ---
        view_menu = menubar.addMenu("视图(&V)")

        toggle_left_action = QAction("切换左侧面板", self)
        toggle_left_action.setShortcut("Ctrl+B")
        toggle_left_action.triggered.connect(self._toggle_left_panel)
        view_menu.addAction(toggle_left_action)

        toggle_right_action = QAction("切换右侧面板", self)
        toggle_right_action.setShortcut("Ctrl+E")
        toggle_right_action.triggered.connect(self._toggle_right_panel)
        view_menu.addAction(toggle_right_action)

        view_menu.addSeparator()

        refresh_memory_action = QAction("刷新记忆面板", self)
        refresh_memory_action.triggered.connect(self._refresh_memory)
        view_menu.addAction(refresh_memory_action)

        # --- 帮助 ---
        help_menu = menubar.addMenu("帮助(&H)")

        about_action = QAction("关于(&A)", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    # ============================================================
    # 工具栏
    # ============================================================

    def _init_toolbar(self):
        """构建工具栏。"""
        toolbar = self.addToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))

        new_btn = QPushButton("新建项目")
        new_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
        new_btn.clicked.connect(self._on_new_project)
        toolbar.addWidget(new_btn)

        open_btn = QPushButton("打开项目")
        open_btn.setStyleSheet(TOOLBAR_BTN)
        open_btn.clicked.connect(self._on_open_project)
        toolbar.addWidget(open_btn)

        toolbar.addSeparator()

        auto_next_btn = QPushButton("一键写下一章")
        auto_next_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
        auto_next_btn.clicked.connect(self._on_auto_next_chapter)
        toolbar.addWidget(auto_next_btn)

        toolbar.addSeparator()

        settings_btn = QPushButton("设置")
        settings_btn.setStyleSheet(TOOLBAR_BTN)
        settings_btn.clicked.connect(self._on_settings)
        toolbar.addWidget(settings_btn)

        toolbar.addSeparator()

        lan_btn = QPushButton("📱 手机阅读")
        lan_btn.setStyleSheet(TOOLBAR_BTN_ACCENT)
        lan_btn.clicked.connect(self._on_lan_share)
        toolbar.addWidget(lan_btn)

        # 讨论：点击弹大弹窗（与 AI agent 互动讨论）
        discuss_btn = QPushButton("💬 讨论")
        discuss_btn.setStyleSheet(TOOLBAR_BTN)
        discuss_btn.setToolTip("与 AI agent 互动讨论剧情/人物/伏笔，调整先讨论再写入（大弹窗）")
        discuss_btn.clicked.connect(self._on_open_discussion)
        toolbar.addWidget(discuss_btn)

    # ============================================================
    # 状态栏
    # ============================================================

    def _init_statusbar(self):
        """构建状态栏。"""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_project_label = QLabel("当前项目: 未打开")
        self._status_chapter_label = QLabel("章节: 0")
        self._status_word_label = QLabel("总字数: 0")
        self._status_save_label = QLabel("上次保存: -")

        self._statusbar.addWidget(self._status_project_label)
        self._statusbar.addWidget(self._status_chapter_label)
        self._statusbar.addWidget(self._status_word_label)
        self._statusbar.addPermanentWidget(self._status_save_label)

    # ============================================================
    # 面板折叠/展开
    # ============================================================

    def _toggle_left_panel(self):
        """折叠/展开左侧项目列表。"""
        if self._left_panel_visible:
            # 折叠：切到折叠页，调整宽度
            sizes = self._outer_splitter.sizes()
            self._left_panel_saved_size = sizes[0]
            self._left_stack.setCurrentIndex(0)
            self._outer_splitter.setSizes([48, sum(sizes) - 48])
        else:
            # 展开：切到展开页，恢复宽度
            sizes = self._outer_splitter.sizes()
            total = sum(sizes)
            self._left_stack.setCurrentIndex(1)
            self._outer_splitter.setSizes([self._left_panel_saved_size, total - self._left_panel_saved_size])
        self._left_panel_visible = not self._left_panel_visible

    def _toggle_right_panel(self, tab_name: str = ""):
        """折叠/展开右侧面板。若传入 tab_name，展开时切换到对应 Tab。"""
        if self._right_panel_visible:
            # 折叠：切到折叠页，调整宽度
            sizes = self._main_splitter.sizes()
            self._right_panel_saved_size = sizes[1]
            total = sum(sizes)
            self._right_stack.setCurrentIndex(0)
            self._main_splitter.setSizes([total - 48, 48])
        else:
            # 展开：切到展开页，恢复宽度
            sizes = self._main_splitter.sizes()
            total = sum(sizes)
            self._right_stack.setCurrentIndex(1)
            self._main_splitter.setSizes([total - self._right_panel_saved_size, self._right_panel_saved_size])
            # 如果指定了 tab_name，切换到对应 Tab
            if tab_name:
                tab_map = {"管理": 0, "记忆": 1, "预览": 2}
                idx = tab_map.get(tab_name)
                if idx is not None:
                    self._on_right_tab_changed(idx)
        self._right_panel_visible = not self._right_panel_visible

    def _on_right_tab_changed(self, index: int):
        """切换外层 Tab（项目管理/记忆档案/上下文预览）。"""
        self.right_tabs.setCurrentIndex(index)
        for i, btn in enumerate(self._right_tab_btns):
            btn.setChecked(i == index)
        _right_titles = ["项目管理", "记忆档案", "上下文预览"]
        self._right_title.setText(_right_titles[index] if 0 <= index < len(_right_titles) else "面板")

    # ============================================================
    # 项目操作
    # ============================================================

    def _on_new_project(self, on_created=None):
        """新建项目对话框（AI 自动初始化）。

        on_created(root): 可选回调，项目创建成功后以 project_root 调用
        （供讨论面板等外部入口在新建完成后刷新并切换到新书）。
        """
        if self._current_project:
            reply = QMessageBox.question(
                self, "确认", "当前有打开的项目，是否新建项目？\n（当前项目不会丢失）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        dlg = QDialog(self)
        dlg.setWindowTitle("新建项目")
        dlg.setMinimumWidth(500)
        layout = QVBoxLayout(dlg)

        from ..core.novel_categories import (
            category_labels, get_category, key_by_label, DEFAULT_CATEGORY_KEY,
        )

        form = QFormLayout()

        name_input = QLineEdit()
        name_input.setPlaceholderText("如: 星辰陨落")
        form.addRow("书名:", name_input)

        genre_input = QComboBox()
        genre_input.addItems(["玄幻", "修仙", "都市", "科幻", "历史", "悬疑", "言情", "武侠", "末日", "游戏", "其他"])
        genre_input.setEditable(True)
        form.addRow("题材:", genre_input)

        inspiration_input = QTextEdit()
        inspiration_input.setPlaceholderText("输入你的核心灵感/故事构想...\n如: 一个被流放的皇子带着前世记忆重生了")
        inspiration_input.setMaximumHeight(100)
        form.addRow("核心灵感:", inspiration_input)

        tone_input = QComboBox()
        tone_input.addItems(["根据题材自动选择", "暗黑", "轻松", "史诗", "日常", "热血", "悲壮"])
        tone_input.setEditable(True)
        form.addRow("故事基调:", tone_input)

        # 文风/作者预设（每本小说统一风格；文风是固定写法指令，不是长段原文）
        from ..core.pipeline.style_presets import (
            preset_keys, UNSPECIFIED, CUSTOM, resolve_style,
        )
        style_combo = QComboBox()
        style_combo.addItems(preset_keys())
        style_combo.setCurrentText(UNSPECIFIED)
        form.addRow("文风/作者:", style_combo)

        style_custom = QLineEdit()
        style_custom.setPlaceholderText("自定义文风指令（如：冷峻白描，对话精炼，少用修饰语）")
        style_custom.setVisible(False)
        form.addRow("自定义文风:", style_custom)

        def _on_style_pick(text: str):
            style_custom.setVisible(text == CUSTOM)
        style_combo.currentTextChanged.connect(_on_style_pick)

        # 小说分类：决定主流文字量规划与叙事节奏（网文/科幻/短篇等）
        category_combo = QComboBox()
        category_combo.addItems(category_labels())
        category_combo.setCurrentText(get_category(DEFAULT_CATEGORY_KEY)["label"])
        form.addRow("小说分类:", category_combo)

        target_words_spin = QSpinBox()
        target_words_spin.setRange(10000, 5000000)
        target_words_spin.setSingleStep(50000)
        target_words_spin.setSuffix(" 字")
        target_words_spin.setValue(get_category(DEFAULT_CATEGORY_KEY)["target_words"])
        form.addRow("目标字数:", target_words_spin)

        chapter_est_label = QLabel("")
        form.addRow("章节估算:", chapter_est_label)

        def _update_cat():
            cat = get_category(key_by_label(category_combo.currentText()))
            target_words_spin.setValue(cat["target_words"])
            est = max(1, round(cat["target_words"] / cat["chapter_words"]))
            chapter_est_label.setText(
                f"约 {est} 章（每章约 {cat['chapter_words']} 字，节奏：{cat['pacing']}）"
            )

        category_combo.currentTextChanged.connect(_update_cat)
        _update_cat()

        layout.addLayout(form)

        # 系统标签包预览（按题材自动套用，可勾选增删；新增从系统选项里挑）
        from ..core.pipeline.tag_system import (
            suggest_tags_for_genre, TagSpec, KIND_LABELS,
        )
        tag_preview_specs: list = []  # 与列表项一一对应的 (类别, dict)
        tag_group = QGroupBox("系统标签包（按题材自动套用，可勾选增删）")
        _tg_layout = QVBoxLayout(tag_group)
        tag_preview_list = QListWidget()
        tag_preview_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        _tg_layout.addWidget(tag_preview_list)
        tag_count_label = QLabel("")
        _tg_layout.addWidget(tag_count_label)
        add_from_lib_btn = QPushButton("＋ 从选项新增标签")
        _tg_layout.addWidget(add_from_lib_btn)
        layout.addWidget(tag_group)

        def _update_tag_count():
            tag_count_label.setText(
                f"共 {tag_preview_list.count()} 个标签；取消勾选则不套用，"
                f"点「＋ 从选项新增标签」从系统库补充。"
            )

        def _refresh_tag_preview(g: str):
            tag_preview_list.clear()
            tag_preview_specs.clear()
            content, process = suggest_tags_for_genre(g)
            n_content = len(content)
            n_process = len(process)
            for cat, specs in (("内容", content), ("流程", process)):
                for raw in specs:
                    spec = TagSpec.from_any(raw)
                    if not spec.name:
                        continue
                    kind_label = KIND_LABELS.get(spec.kind, spec.kind)
                    item = QListWidgetItem(f"[{cat}·{kind_label}] {spec.name}")
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
                    item.setToolTip(spec.doctrine or spec.name)
                    tag_preview_list.addItem(item)
                    tag_preview_specs.append((cat, spec.to_dict()))
            _update_tag_count()

        def _on_add_from_lib():
            """从系统标签库选项中勾选新增（而非自由手填）。"""
            existing = {tag_preview_specs[i][1]["name"] for i in range(len(tag_preview_specs))}
            from .widgets.tag_picker import pick_tags
            picked = pick_tags(dlg, existing_names=existing)
            if not picked:
                return
            added = 0
            for spec in picked:
                if spec.name in existing:
                    continue
                cat = "流程" if spec.kind == "流程" else "内容"
                prefix = f"[{cat}·{spec.kind}]"
                item = QListWidgetItem(f"{prefix} {spec.name}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                item.setToolTip(spec.doctrine or spec.name)
                tag_preview_list.addItem(item)
                tag_preview_specs.append((cat, spec.to_dict()))
                existing.add(spec.name)
                added += 1
            if added:
                _update_tag_count()

        add_from_lib_btn.clicked.connect(_on_add_from_lib)
        genre_input.currentTextChanged.connect(_refresh_tag_preview)
        _refresh_tag_preview(genre_input.currentText())

        # 进度
        progress_label = QLabel("")
        progress_label.setVisible(False)
        layout.addWidget(progress_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(BTN_SECONDARY)
        create_btn = QPushButton("创建项目（AI 自动初始化）")
        create_btn.setStyleSheet(BTN_PRIMARY)
        create_btn.setDefault(True)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(create_btn)
        layout.addLayout(btn_layout)

        cancel_btn.clicked.connect(dlg.reject)

        def on_create():
            name = name_input.text().strip()
            genre = genre_input.currentText().strip()
            # 根据用户在预览区的勾选，套用系统级标签包（内容标签 + 通用流程约束）
            init_tags: list = []
            init_process: list = []
            for i in range(tag_preview_list.count()):
                item = tag_preview_list.item(i)
                if item.checkState() != Qt.CheckState.Checked:
                    continue
                cat, spec_dict = tag_preview_specs[i]
                if cat == "内容":
                    init_tags.append(spec_dict)
                else:
                    init_process.append(spec_dict)
            inspiration = inspiration_input.toPlainText().strip()
            tone = tone_input.currentText().strip()
            writing_style = resolve_style(style_combo.currentText(), style_custom.text().strip())

            if not name:
                QMessageBox.warning(dlg, "提示", "请输入书名")
                return
            if not inspiration:
                QMessageBox.warning(dlg, "提示", "请输入核心灵感")
                return

            # 选择项目目录
            default_dir = str(Path.home() / "NovelForgeProjects" / name)
            project_root = QFileDialog.getSaveFileName(
                dlg, "选择项目保存位置", default_dir
            )[0]
            if not project_root:
                return

            # 安全检查：目标目录已有旧项目（project.json 存在），询问确认
            from pathlib import Path as _Path
            old_meta = _Path(project_root) / "project.json"
            if old_meta.exists():
                reply = QMessageBox.question(
                    self, "确认覆盖",
                    f"目标目录已有旧项目文件。\n\n{project_root}\n\n"
                    "若继续，旧章节/大纲/记忆文件将被清除，重建为全新项目。\n"
                    "是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    create_btn.setEnabled(True)
                    return

            create_btn.setEnabled(False)
            progress_label.setVisible(True)
            progress_label.setText("正在初始化项目...")

            try:
                # 1. 创建项目结构
                project = self._project_manager.create_project(
                    name=name,
                    genre=genre,
                    project_root=project_root,
                    target_words=target_words_spin.value(),
                    novel_category=key_by_label(category_combo.currentText()),
                    writing_style=writing_style,
                    tags=init_tags,
                    process_tags=init_process,
                )
                project.writing_style = writing_style  # 确保返回对象已带风格

                # 2. 初始化 MemoryEngine 和 WritingPipeline
                mem = MemoryEngine(project_root, self._llm_client)
                context = ContextAssembler(self._llm_client, {
                    "genre": genre,
                    "writing_style": writing_style,
                })
                pipeline = WritingPipeline(
                    memory_engine=mem,
                    context_assembler=context,
                    llm_client=self._llm_client,
                    project_root=project_root,
                )

                # 3. AI 自动初始化
                def on_progress(stage_name: str, content: str):
                    progress_label.setText(stage_name)

                init_result = pipeline.auto_init_project(
                    book_name=name,
                    genre=genre,
                    inspiration=inspiration,
                    tone=tone,
                    novel_category=key_by_label(category_combo.currentText()),
                    on_progress=on_progress,
                )

                if not init_result.success:
                    QMessageBox.warning(dlg, "初始化失败", init_result.error)
                    create_btn.setEnabled(True)
                    progress_label.setVisible(False)
                    return

                # 4. 保存项目描述
                project.description = init_result.synopsis
                self._project_manager.update_project(project)

                dlg.accept()

                # 5. 打开项目
                self._open_project_internal(project_root)
                if on_created:
                    on_created(project_root)

                QMessageBox.information(
                    self, "项目创建完成",
                    f"项目「{name}」创建成功！\n\n"
                    "AI 已完成基础设定生成：\n"
                    "  ✓ 世界观设定\n"
                    "  ✓ 主角设定\n"
                    "  ✓ 故事总纲\n"
                    "  ✓ 小说简介\n\n"
                    "你可以在「项目管理」面板查看和修改这些内容。\n"
                    "接下来，在左侧章节列表点击「生成大纲」开始规划章节。"
                )

            except Exception as e:
                QMessageBox.critical(dlg, "错误", f"创建项目失败: {e}")
                create_btn.setEnabled(True)
                progress_label.setVisible(False)

        create_btn.clicked.connect(on_create)
        dlg.exec()

    def _on_open_project(self):
        """打开已有项目。"""
        start_dir = str(Path.home() / "NovelForgeProjects")
        project_root = QFileDialog.getExistingDirectory(
            self, "选择项目目录", start_dir
        )
        if not project_root:
            return
        self._open_project_internal(project_root)

    def _on_project_list_open(self, project_root: str):
        """从项目列表打开项目。"""
        self._open_project_internal(project_root)

    def _on_project_list_chapter_opened(self, project_root: str, chapter_id: int):
        """从左侧卷/章树双击章节：若项目未打开则先打开，再在编辑器中定位该章。"""
        if not self._current_project or self._current_project.project_root != project_root:
            self._open_project_internal(project_root)
            # 打开项目是同步的，若失败则直接返回
            if not self._current_project or self._current_project.project_root != project_root:
                return
        try:
            self.editor.select_chapter(chapter_id)
            self._statusbar.showMessage(f"已定位到第{chapter_id}章", 2000)
        except Exception as e:
            logger.warning(f"定位章节失败: {e}")

    def _ensure_current_project(self, project_root: str):
        """确保 project_root 为当前已打开项目（否则先同步打开）。"""
        if not self._current_project or self._current_project.project_root != project_root:
            self._open_project_internal(project_root)

    def _on_new_chapter(self):
        """在末尾新建空白章节，并定位到编辑器。"""
        if not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        root = self._current_project.project_root
        chapters = self._project_manager.get_chapter_list(root)
        next_id = max([c["chapter_id"] for c in chapters], default=0) + 1
        title = f"新章节{next_id}"
        try:
            self._project_manager.save_chapter_text(root, next_id, title, "")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"新建章节失败: {e}")
            return
        self.project_list.refresh()
        self.editor.select_chapter(next_id)
        self._statusbar.showMessage(f"已新建第{next_id}章", 2000)

    def _on_batch_rewrite_from_editor(self):
        """批量重写：读取左侧项目树的多选章节。"""
        ids = self.project_list.get_selected_chapter_ids()
        if not ids:
            QMessageBox.information(
                self, "提示",
                "请先在左侧项目列表中选中要重写的章节。\n"
                "（按住 Ctrl / ⌘ 可多选，按住 Shift 可选连续范围）"
            )
            return
        self._on_batch_write_chapters(ids)

    def _on_delete_chapter(self, project_root: str, cids: list[int]):
        """删除整章（正文文件 + 关联记忆 + 大纲）。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        self._ensure_current_project(project_root)
        if not self._pipeline:
            return

        cids = sorted(set(cids))
        if len(cids) == 1:
            msg = f"确定删除第{cids[0]}章吗？此操作将同时删除正文、大纲及关联记忆数据，不可撤销。"
        else:
            msg = (f"确定批量删除第 {cids[0]}–{cids[-1]} 章（共 {len(cids)} 章）吗？"
                   f"此操作将同时删除正文、大纲及关联记忆数据，不可撤销。")
        reply = QMessageBox.question(
            self, "确认删除", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        errors = []
        from pathlib import Path as _Path
        for cid in cids:
            # 诊断：检查实际路径
            chap_path = _Path(self._current_project.project_root) / "chapters" / f"chapter_{cid:04d}.md"
            if not chap_path.exists():
                errors.append(f"第{cid}章：正文文件不存在于 {chap_path}")
            try:
                self._project_manager.delete_chapter(self._current_project.project_root, cid)
            except Exception as e:
                errors.append(f"第{cid}章正文文件删除失败: {e}")
                logger.warning(f"删除第{cid}章正文文件失败: {e}")
            # 验证：删除后文件是否真的消失了
            if chap_path.exists():
                errors.append(f"第{cid}章：delete_chapter 执行后文件仍存在！{chap_path}")
                try:
                    chap_path.unlink()
                    errors.append(f"第{cid}章：已强制 unlink 删除")
                except Exception as e2:
                    errors.append(f"第{cid}章：强制 unlink 也失败: {e2}")
            try:
                self._pipeline.delete_chapter_content(cid)
            except Exception as e:
                errors.append(f"第{cid}章记忆数据删除失败: {e}")
                logger.warning(f"删除第{cid}章记忆数据失败: {e}")
            try:
                self._pipeline.delete_chapter_outline(cid)
            except Exception as e:
                errors.append(f"第{cid}章大纲删除失败: {e}")
                logger.warning(f"删除第{cid}章大纲失败: {e}")

        if errors:
            QMessageBox.warning(self, "删除异常",
                f"删除过程中遇到 {len(errors)} 个错误：\n\n" + "\n".join(errors[:5]) +
                "\n\n部分数据可能未完全清除，请刷新后重试。"
            )

        self.project_list.refresh()
        self.editor.refresh_chapters()
        self._refresh_memory()
        self._update_wordcount()
        self._statusbar.showMessage(f"已删除 {len(cids)} 章", 3000)

    def _on_delete_volume(self, project_root: str, volume_id: int):
        """删除整卷及其范围内的全部章节（正文 + 大纲 + 关联记忆）。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        self._ensure_current_project(project_root)
        if not self._pipeline:
            return

        reply = QMessageBox.question(
            self, "确认删除卷",
            "确定删除该卷及其范围内的全部章节吗？\n"
            "卷内所有章节的正文、大纲及关联记忆数据将一并删除，不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            deleted = self._pipeline.delete_volume_with_chapters(volume_id)
        except Exception as e:
            QMessageBox.critical(self, "删除失败", f"删除卷及章节失败: {e}")
            return

        # 若当前正打开被删章节，清空编辑器
        if self.editor._current_chapter_id in deleted:
            self.editor.clear_view()

        self.project_list.refresh()
        self.editor.refresh_chapters()
        self._refresh_memory()
        self._update_wordcount()
        self._statusbar.showMessage(f"已删除卷及范围内 {len(deleted)} 章", 3000)

    def _open_project_internal(self, project_root: str):
        """内部：打开项目并初始化所有组件。"""
        try:
            project = self._project_manager.open_project(project_root)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"无法打开项目: {e}")
            return

        self._current_project = project

        try:
            # 初始化记忆引擎
            self._memory = MemoryEngine(project_root, self._llm_client)

            # 初始化上下文组装器和写作流水线
            self._context_assembler = ContextAssembler(self._llm_client, {
                "genre": project.genre,
                "quality_tier": self._config.quality_tier,
                "system_prompt_budget": self._config.system_prompt_budget,
                "writing_style": project.writing_style,
            })
            self._pipeline = WritingPipeline(
                memory_engine=self._memory,
                context_assembler=self._context_assembler,
                llm_client=self._llm_client,
                project_root=project_root,
            )

            # 注入到各面板
            self.editor.set_project_manager(self._project_manager, project_root)
            self.editor.set_pipeline(self._pipeline)
            self.project_list.set_pipeline(self._pipeline)
            self.project_list.set_current_project(project_root, project.name)
            self.dashboard.set_data(
                self._project_manager, project_root,
                pipeline=self._pipeline, memory=self._memory
            )
            self.memory_panel.set_memory_engine(self._memory)
            if SHOW_INSPIRATION_TAB and hasattr(self, "inspiration_panel"):
                self.inspiration_panel.set_manager(self._memory.inspiration)
            self.context_preview.set_pipeline(self._pipeline)

            self._update_wordcount()
            self.project_opened.emit(project_root)
            self._statusbar.showMessage(f"已打开项目: {project.name}", 3000)
            logger.info(f"项目已打开: {project.name} ({project_root})")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"打开项目时出错: {e}\n{tb}")
            QMessageBox.critical(self, "打开项目出错", f"初始化项目组件时出错:\n{e}\n\n{tb[:1000]}")

    def _on_save_project(self):
        """保存当前项目。"""
        if not self._current_project:
            return
        self.editor._on_save()
        cid, _, _ = self.editor.get_current_chapter()
        if cid:
            self._current_project.current_chapter = cid
        self._project_manager.update_project(self._current_project)
        self._update_wordcount()
        self._status_save_label.setText(f"上次保存: {datetime.now().strftime('%H:%M:%S')}")
        self._statusbar.showMessage("项目已保存", 2000)

    def _on_close_project(self):
        """关闭当前项目。"""
        if not self._current_project:
            return
        self._on_save_project()
        self._current_project = None
        self._memory = None
        self._pipeline = None
        self._context_assembler = None
        self.project_list.set_current_project("", "")
        self._status_project_label.setText("当前项目: 未打开")
        self._status_chapter_label.setText("章节: 0")
        self._status_word_label.setText("总字数: 0")
        self._status_save_label.setText("上次保存: -")
        self.project_closed.emit()

    def _on_auto_next_chapter(self):
        """AI 自动规划并写下一章。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        # 计算下一章号
        chapters = self._project_manager.get_chapter_list(self._current_project.project_root)
        next_id = max([c["chapter_id"] for c in chapters], default=0) + 1

        # 读取大纲
        outline = self._pipeline._read_outline()
        if not outline:
            QMessageBox.warning(self, "提示", "未找到故事总纲，请先生成总纲")
            return

        self._statusbar.showMessage(f"正在自动写作第{next_id}章...")

        config = self._build_writing_config()
        worker = AutoWriteWorker(
            pipeline=self._pipeline,
            next_chapter_id=next_id,
            book_name=self._current_project.name,
            genre=self._current_project.genre,
            outline=outline,
            config=config,
        )
        worker.progress.connect(
            lambda msg: self._statusbar.showMessage(msg)
        )
        worker.stream.connect(lambda chunk: self.editor.append_text(chunk))
        worker.finished.connect(self._on_auto_write_finished)
        worker.start()
        self._workers.append(worker)

    def _on_settings(self):
        """打开设置对话框。"""
        dlg = SettingsDialog(
            self._config, self,
            project=self._current_project,
            project_manager=self._project_manager,
        )
        dlg.config_saved.connect(self._on_config_updated)
        dlg.exec()

    def _on_config_updated(self, config: Config):
        """设置保存后更新配置。"""
        self._config = config
        self._llm_client = LLMClient(config)
        if self._pipeline:
            self._pipeline._llm = self._llm_client
        # 同步质量档位 / 本书风格到已在运行的上下文组装器，避免重开项目才生效
        if self._context_assembler is not None:
            self._context_assembler._config["quality_tier"] = config.quality_tier
            self._context_assembler._config["system_prompt_budget"] = config.system_prompt_budget
            if self._current_project is not None:
                self._context_assembler._config["writing_style"] = self._current_project.writing_style
        # 局域网端口变更：若服务未运行则按新端口重建实例（运行中需先停止旧服务再重启）
        if config.lan_port != self._lan_server.port:
            was_running = self._lan_server.is_running
            self._lan_server.stop()
            self._lan_server = LanViewerServer(self._project_manager, port=config.lan_port)
            if was_running:
                try:
                    self._lan_server.start()
                except OSError:
                    self._statusbar.showMessage(
                        f"局域网端口已更新为 {config.lan_port}，但新端口启动失败（可能被占用），请重试", 4000)
        self._statusbar.showMessage("设置已保存", 2000)

    def _on_project_info(self):
        """显示项目信息。"""
        if not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        p = self._current_project
        info = (
            f"书名: {p.name}\n"
            f"作者: {p.author or '-'}\n"
            f"题材: {p.genre or '-'}\n"
            f"创建时间: {p.created_at[:19] if p.created_at else '-'}\n"
            f"项目路径: {p.project_root}\n"
            f"目标字数: {p.target_words:,}"
        )
        QMessageBox.information(self, "项目信息", info)

    def _on_project_settings(self):
        """项目设置（简化版）。"""
        QMessageBox.information(self, "项目设置", "请在「项目管理」面板中修改项目设定和总纲。")

    def _on_about(self):
        """显示关于对话框。"""
        QMessageBox.about(
            self, "关于 NovelForge",
            "NovelForge - AI 驱动的小说写作工具\n\n"
            "版本: 0.1.0\n"
            "基于 PyQt6 + OpenAI 兼容 API\n\n"
            "特性:\n"
            "- AI 自动初始化项目（世界观/主角/总纲/简介）\n"
            "- 批量生成章节大纲（可调整确认）\n"
            "- AI 批量写作正文（流式输出）\n"
            "- 三层记忆架构（档案库/RAG/摘要）\n"
            "- 灵感碎片管理\n"
            "- 钩子驱动的章节衔接"
        )

    # ============================================================
    # 局域网分享阅读
    # ============================================================

    def _on_open_discussion(self):
        """点击工具栏「💬 讨论」旁的「讨论」按钮：弹出大弹窗，与 AI agent 互动讨论。"""
        if not getattr(self, "_discussion_dialog", None):
            # parent=None：独立顶层窗口，杜绝 macOS 上「带父窗口的独立窗口」
            # 被父窗口状态（最小化/隐藏/重绘/模态）连带关闭/销毁的机制
            dlg = _TracedDialog(None)
            dlg.setWindowTitle("讨论 · 与 AI agent 互动 [PATCH-v7]")
            import sys as _sys
            print("[DISCUSSION_PATCH_V7_ACTIVE]", file=_sys.stderr, flush=True)
            dlg.resize(1000, 720)
            dlg.setMinimumSize(760, 540)
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(0, 0, 0, 0)
            panel = DiscussionPanelWidget()
            panel.data_changed.connect(self._refresh_memory)
            panel.gen_short_outline_requested.connect(
                lambda cid: self._on_regenerate_outline(cid, ""))
            panel.gen_deep_outline_requested.connect(self._on_gen_deep_outline_single)
            panel.write_chapter_requested.connect(self._on_write_single_chapter)
            panel.new_project_requested.connect(
                lambda: self._on_new_project(
                    on_created=lambda root: panel.refresh_projects(root)
                )
            )
            layout.addWidget(panel, 1)
            self._discussion_dialog = dlg
            self._discussion_dialog_panel = panel
            dlg._panel = panel  # 供 closeEvent 判断是否在生成中
            # 守护定时器：检测讨论框「异常消失」（未走 closeEvent）并自动恢复；
            # 若 C++ 对象被销毁则自动重建。任何消失都会留下日志。
            self._dlg_was_visible = False
            self._dlg_guard_timer = QTimer(self)
            self._dlg_guard_timer.timeout.connect(self._guard_discussion_dialog)
            self._dlg_guard_timer.start(400)
            # 加载确认：首次创建时写一条提示，用户一眼可辨新代码是否生效
            # （看到这条 = 新代码已加载；看不到 = 仍跑旧进程，需彻底退出 app 重启）
            # role="system"：渲染为居中灰色系统提示，不进讨论历史、不当 AI 消息
            try:
                panel._append_history(
                    "system",
                    "✅ 已加载 PATCH-v7：回车发送消息、AI 执行中不会误关框；"
                    "点 X / 按 ESC 可正常关闭。若此框仍会自动消失，说明你跑的是旧进程。",
                )
                panel._render()
            except Exception:
                pass
        # 每次打开都刷新 context（项目/记忆可能已切换，保证讨论基于最新正典）
        # 取当前章 id 作为讨论锚点（让 agent 读到"当前章节正文"）
        cid = None
        try:
            c, _, _ = self.editor.get_current_chapter()
            if isinstance(c, int) and c > 0:
                cid = c
        except Exception:
            cid = None
        self._discussion_dialog_panel.set_context(
            self._llm_client, self._config, self._current_project, self._memory,
            current_chapter_id=cid,
            project_manager=self._project_manager,
            pipeline=self._pipeline,
        )
        self._discussion_dialog.show()
        self._discussion_dialog.raise_()
        self._discussion_dialog.activateWindow()

    def _guard_discussion_dialog(self):
        """讨论框守护：发现「异常消失」（未走 closeEvent）立即恢复；
        若 C++ 对象已被销毁，自动重建。任何异常都在日志留痕。"""
        dlg = getattr(self, "_discussion_dialog", None)
        if dlg is None:
            return
        try:
            visible = dlg.isVisible()
        except RuntimeError:
            # C++ 对象已被销毁（未走 closeEvent/hideEvent）——重建窗口
            _write_dialog_log("[GUARD] 讨论框 C++ 对象被销毁 → 自动重建")
            self._discussion_dialog = None
            self._discussion_dialog_panel = None
            self._on_open_discussion()
            self._dlg_was_visible = True
            return
        was = getattr(self, "_dlg_was_visible", False)
        if was and not visible:
            if getattr(dlg, "_user_closed", False):
                # 用户主动关闭（点 X / close()）：尊重，不干预
                self._dlg_was_visible = False
                return
            # 异常消失：未走 closeEvent 就不可见了——记录并恢复
            _write_dialog_log(
                "[GUARD] 讨论框异常消失（未走 closeEvent）→ 自动恢复显示"
            )
            try:
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()
            except Exception as _e:
                _write_dialog_log(f"[GUARD] 恢复失败：{_e}")
            return
        self._dlg_was_visible = visible

    def _on_lan_share(self):
        """菜单/工具栏：在手机浏览器中查看正文与大纲（只读）。"""
        dlg = LanShareDialog(self._lan_server, self)
        dlg.exec()

    # ============================================================
    # 全书最终校验（写完后再校验，替代逐章自动审）
    # ============================================================

    def _on_menu_full_book_validate(self):
        """菜单：全书最终校验 — 写完所有章后逐章跑一次合理性校验（替代每章自动审）。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        if getattr(self, "_full_validate_running", False):
            QMessageBox.information(self, "提示", "全书校验正在进行中，请稍候。")
            return

        outlines = self._pipeline.get_chapter_outlines()
        if not outlines:
            QMessageBox.information(self, "提示", "本书还没有章节大纲。")
            return

        cids = [o["chapter_id"] for o in outlines if o.get("chapter_id")]
        cids_with_text = [cid for cid in cids if self._pipeline._read_chapter_text(cid).strip()]
        if not cids_with_text:
            QMessageBox.information(self, "提示", "还没有任何章节正文，无法校验。")
            return

        total = len(cids_with_text)
        reply = QMessageBox.question(
            self, "全书最终校验",
            f"将对全部 {total} 章已写正文进行一次性合理性校验（设定 / 动机 / 逻辑一致性）。\n\n"
            f"这是「写完后再校验」的模式：不会逐章自动审查，也不会自动改写正文。\n"
            f"预计消耗约 {total} 次 LLM 调用，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        config = self._build_writing_config()
        self._full_validate_running = True
        if hasattr(self, "_full_validate_action"):
            self._full_validate_action.setEnabled(False)
        worker = FullBookValidationWorker(self._pipeline, cids_with_text, config)
        worker.progress.connect(
            lambda i, t, msg: self._statusbar.showMessage(f"全书校验中 ({i}/{t})...")
        )
        worker.finished_summary.connect(self._on_full_validate_finished)
        worker.start()
        self._workers.append(worker)

    def _on_full_validate_finished(self, summary: dict):
        """全书最终校验完成：展示聚合报告（不弹 500 个窗口）。"""
        self._full_validate_running = False
        if hasattr(self, "_full_validate_action"):
            self._full_validate_action.setEnabled(True)

        total = summary.get("total", 0)
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        errored = summary.get("errored", 0)
        failed_chapters = summary.get("failed_chapters", [])

        header = [
            f"全书最终校验完成：共 {total} 章",
            f"通过 {passed} 章 ｜ 未通过 {failed} 章 ｜ 出错 {errored} 章",
        ]
        detail = list(header)
        if failed_chapters:
            detail.append("")
            detail.append("未通过 / 出错章节：")
            for fc in failed_chapters[:80]:
                cid = fc.get("cid", "?")
                title = fc.get("title", f"第{cid}章")
                score = fc.get("score", 0)
                n_issues = len(fc.get("issues") or [])
                n_concerns = len(fc.get("concerns") or [])
                err = fc.get("error", "")
                if err:
                    detail.append(f"  • 第{cid}章《{title}》 出错: {err}")
                else:
                    detail.append(f"  • 第{cid}章《{title}》 评分{score} 问题{ n_issues + n_concerns }条")
            if len(failed_chapters) > 80:
                detail.append(f"  …（其余 {len(failed_chapters) - 80} 章略）")
        else:
            detail.append("")
            detail.append("全部通过，未发现需修复的设定 / 逻辑问题。")

        msg = QMessageBox(self)
        msg.setWindowTitle("全书最终校验报告")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("\n".join(header))
        msg.setDetailedText("\n".join(detail))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    # ============================================================
    # 全书一致性重写
    # ============================================================

    def _on_menu_full_novel_revise(self):
        """菜单：全书一致性重写 — 逐章校验并基于已确立事实重写，保证不吃书。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        stats = self._pipeline.get_project_stats()
        total = stats.get("total_chapters", 0)
        if total == 0:
            QMessageBox.warning(self, "提示", "当前项目还没有任何章节正文，无法重写。")
            return

        approx_calls = total * (3 if True else 1)  # 校验 + 重写 + 复校
        reply = QMessageBox.question(
            self, "全书一致性重写",
            f"将对全部 {total} 章进行「逐章校验 → 依据已确立事实重写 → 覆盖 → 提交记忆」。\n\n"
            f"注意：\n"
            f"• 开始前会重置派生记忆（人物/伏笔/时间线/世界观/RAG向量/摘要），"
            f"重写后随章节重新干净建立，避免百万字级重复提交导致记忆膨胀；"
            f"作者源文件（正文/设定/大纲）不受影响。\n"
            f"• 每章提交时会自动备份（.novelforge/backups）。\n"
            f"• 该操作会消耗较多 API 额度（约 {approx_calls} 次调用，含可选复校）。\n"
            f"• 可在进度对话框中随时取消（已完成的章节会保留）。\n\n"
            f"是否开始？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        config = self._build_writing_config()
        self._full_revise_stop = False

        dlg = QProgressDialog("全书一致性重写中...", "取消", 0, total, self)
        dlg.setWindowTitle("全书一致性重写")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(lambda: setattr(self, "_full_revise_stop", True))

        worker = FullNovelReviseWorker(
            self._pipeline, config,
            stop_check=lambda: getattr(self, "_full_revise_stop", False),
            verify=True,
        )
        self._full_revise_dlg = dlg
        self._full_revise_worker = worker
        worker.progress.connect(self._on_full_revise_progress)
        worker.chapter_done.connect(self._on_full_revise_chapter_done)
        worker.finished.connect(self._on_full_revise_finished)
        worker.start()
        self._workers.append(worker)
        dlg.exec()

    def _on_full_revise_progress(self, cid: int, idx: int, total: int, stage: str):
        dlg = getattr(self, "_full_revise_dlg", None)
        if dlg:
            dlg.setValue(idx)
            dlg.setLabelText(f"第{idx}/{total}章（第{cid}章）：{stage}")
        self._statusbar.showMessage(f"全书重写：第{cid}章 {stage} ({idx}/{total})")

    def _on_full_revise_chapter_done(self, cid: int, idx: int, total: int, res: dict):
        dlg = getattr(self, "_full_revise_dlg", None)
        if dlg:
            dlg.setValue(idx)
            passed = "通过" if res.get("passed_before") else "未通过"
            dlg.setLabelText(
                f"第{cid}章完成（重写前：{passed}，{res.get('issues_before', 0)}问题，"
                f"{res.get('word_count', 0)}字）"
            )

    def _on_full_revise_finished(self, summary: dict, error: str):
        dlg = getattr(self, "_full_revise_dlg", None)
        if dlg and dlg.isVisible():
            dlg.close()
        if error:
            QMessageBox.critical(self, "全书重写失败", f"发生错误：\n{error}")
            return
        if not summary.get("success"):
            QMessageBox.warning(self, "全书重写", summary.get("error", "未执行"))
            return

        stopped = summary.get("stopped")
        bp = summary.get("before_pass")
        bt = summary.get("before_total")
        ap = summary.get("after_pass")
        at = summary.get("after_total")
        msg = f"全书一致性重写完成（共 {summary.get('total')} 章）。\n\n"
        msg += f"重写前校验通过：{bp}/{bt}\n" if bp is not None else ""
        if ap is not None:
            msg += f"重写后校验通过：{ap}/{at}\n"
        if stopped:
            msg += "\n（已中途取消，已完成的章节已保留。）"

        # 刷新编辑器与记忆面板
        try:
            self.editor.refresh_chapters()
        except Exception:
            pass
        self._refresh_memory()
        QMessageBox.information(self, "全书一致性重写", msg)

    # ============================================================
    # 编辑器事件
    # ============================================================

    def _on_editor_chapter_selected(self, chapter_id: int, title: str):
        """当编辑器中选中章节时，分别加载简大纲与深大纲到两个独立区块。"""
        if not self._pipeline:
            self.editor.clear_outline_detail()
            return

        try:
            outline = self._pipeline.get_chapter_outline(chapter_id)
            if outline:
                # —— 简大纲（项目级指令 + 时间线）——
                parts = []
                if outline.get("title"):
                    parts.append(f"标题: {outline['title']}")
                if outline.get("chapter_type_label"):
                    parts.append(f"类型: {outline['chapter_type_label']}")
                elif outline.get("chapter_type"):
                    parts.append(f"类型: {outline['chapter_type']}")
                if outline.get("core_events"):
                    parts.append(f"核心事件: {outline['core_events']}")
                if outline.get("plot_summary"):
                    parts.append(f"情节概要: {outline['plot_summary']}")
                if outline.get("opening_hook"):
                    parts.append(f"开篇钩子: {outline['opening_hook']}")
                if outline.get("ending_hook"):
                    parts.append(f"结尾钩子: {outline['ending_hook']}")
                if outline.get("foreshadowing"):
                    parts.append(f"伏笔: {outline['foreshadowing']}")
                if outline.get("time_anchor"):
                    parts.append(f"时间锚点: {outline['time_anchor']}")
                if outline.get("confirmed"):
                    parts.append("状态: ✅ 已确认")
                else:
                    parts.append("状态: ⏳ 待确认")
                self.editor.set_short_outline("\n".join(parts))

                # —— 深大纲（本章写作重心 + 事件进展）—— 价值锚点是覆盖率与可验收性，非厚度
                beats = outline.get("event_progress") or []
                deep_parts = []
                _wf = str(outline.get("writing_focus") or "").strip()
                if _wf:
                    deep_parts.append(f"【本章写作重心】{_wf}")
                    deep_parts.append("")
                if beats:
                    deep_parts.append(f"事件进展（共 {len(beats)} 个节拍，按序推进）：")
                    for i, b in enumerate(beats, 1):
                        if not isinstance(b, dict):
                            continue
                        summary = b.get("summary") or ""
                        goal = b.get("goal") or ""
                        outcome = b.get("outcome") or ""
                        ck = b.get("checkpoint") or ""
                        prose = b.get("prose_focus") or ""
                        line = f"  {i}. {summary}"
                        if goal:
                            line += f"  ｜目标：{goal}"
                        if outcome:
                            line += f" ｜结果：{outcome}"
                        if ck:
                            line += f"  ｜硬事实：{ck}"
                        if prose:
                            line += f"  ｜文笔：{prose}"
                        deep_parts.append(line)
                else:
                    deep_parts.append("（本章暂无深大纲 / 事件进展规格）")
                self.editor.set_deep_outline("\n".join(deep_parts))
            else:
                self.editor.clear_outline_detail()
        except Exception:
            self.editor.clear_outline_detail()

    # ============================================================
    # 写作操作 / 导航 / 局部修订
    # ============================================================

    def _on_navigate(self, chapter_id: int):
        """章节导航：跳转到上一章/下一章。"""
        if not self._current_project:
            return
        self.editor.select_chapter(chapter_id)
        self.project_list.refresh()

    def _on_generate_next_outline(self):
        """无下一章时：生成下一章大纲（单次顺序生成，保证衔接）。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        if not self._pipeline.has_foundation():
            QMessageBox.warning(
                self, "提示",
                "请先完成AI基础设定（世界观/主角/总纲）\n在右侧「管理」面板中点击「重新初始化」"
            )
            return

        chapters = self._project_manager.get_chapter_list(self._current_project.project_root)
        next_ch = max([c["chapter_id"] for c in chapters], default=0) + 1
        config = self._build_writing_config()

        worker = OutlineGenWorker(
            pipeline=self._pipeline,
            start=next_ch,
            end=next_ch,
            book_name=self._current_project.name,
            genre=self._current_project.genre,
            tone=getattr(self._current_project, "tone", "") or "",
        )
        worker.progress.connect(
            lambda stage, content: self._statusbar.showMessage(f"生成第{next_ch}章大纲：{stage}")
        )
        worker.finished.connect(
            lambda success, error: self._on_generate_next_outline_finished(success, error, next_ch)
        )
        worker.start()
        self._workers.append(worker)
        self._statusbar.showMessage(f"正在生成第{next_ch}章大纲...")

    def _on_generate_next_outline_finished(self, success: bool, error: str, next_ch: int):
        if success:
            self.editor.refresh_chapters()
            self.dashboard.refresh()
            self.project_list.refresh()
            # 生成后自动跳到该章，下一章按钮随之变为正常导航
            self.editor.select_chapter(next_ch)
            self._statusbar.showMessage(f"已生成第{next_ch}章大纲，可点「生成此章正文」写作", 4000)
        else:
            QMessageBox.critical(self, "大纲生成失败", error)

    def _on_revise_segment(self, chapter_id, sel_start, sel_end, segment, ctx_before, ctx_after, instruction):
        """位置精确局部修订：AI 只改写指定片段，splice 回原文。"""
        if not self._pipeline:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        cid, ctitle, _ = self.editor.get_current_chapter()
        if cid != chapter_id:
            cid, ctitle = chapter_id, self._current_title or f"第{chapter_id}章"

        self._statusbar.showMessage(f"正在修订第{cid}章指定片段...")
        worker = ReviseSegmentWorker(
            pipeline=self._pipeline,
            chapter_title=ctitle,
            segment=segment,
            context_before=ctx_before,
            context_after=ctx_after,
            instruction=instruction,
        )
        worker.finished.connect(
            lambda success, revised, error: self._on_revise_segment_finished(
                success, revised, error, cid, ctitle, sel_start, sel_end
            )
        )
        worker.start()
        self._workers.append(worker)

    def _on_revise_segment_finished(self, success, revised, error, chapter_id, title, sel_start, sel_end):
        if success and revised:
            self.editor.splice_text(sel_start, sel_end, revised)
            # 修订后同步档案库，确保人物/设定/伏笔状态更新
            if self._pipeline:
                self._pipeline.refresh_chapter_archive(chapter_id)
            self._refresh_memory()
            self._statusbar.showMessage(f"第{chapter_id}章片段修订完成，档案已同步", 3000)
        else:
            QMessageBox.warning(self, "修订失败", error or "未知错误")

    def _on_menu_generate_outlines(self):
        """菜单：生成章节大纲 — 弹出对话框收集参数。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        if not self._pipeline.has_foundation():
            QMessageBox.warning(self, "提示", "请先完成AI基础设定（世界观/主角/总纲）\n在右侧「管理」面板中点击「重新初始化」")
            return

        # 计算下一章号
        chapters = self._project_manager.get_chapter_list(self._current_project.project_root)
        next_ch = max([c["chapter_id"] for c in chapters], default=0) + 1

        # 由目标字数推导应规划的章节总数（让「设定字数」真正成立）
        from ..core.novel_categories import planned_chapter_count_for, get_category
        target_words = int(getattr(self._current_project, "target_words", 1_000_000) or 1_000_000)
        category_key = getattr(self._current_project, "novel_category", "") or ""
        planned_total = planned_chapter_count_for(category_key, target_words)
        # 默认生成到「规划总数」，但不超过 2000，且至少覆盖当前批次
        default_end = min(max(planned_total, next_ch), 2000)
        if default_end < next_ch + 4:
            default_end = next_ch + 4

        dlg = QDialog(self)
        dlg.setWindowTitle("生成章节大纲")
        dlg.setMinimumWidth(360)
        dlg_layout = QVBoxLayout(dlg)

        form = QFormLayout()
        start_spin = QSpinBox()
        start_spin.setMinimum(1)
        start_spin.setMaximum(2000)
        start_spin.setValue(next_ch)
        form.addRow("起始章:", start_spin)

        end_spin = QSpinBox()
        end_spin.setMinimum(1)
        end_spin.setMaximum(2000)
        end_spin.setValue(default_end)
        form.addRow("结束章:", end_spin)
        plan_hint = QLabel(f"本书目标 {target_words:,} 字，建议共约 {planned_total} 章（单章~{get_category(category_key)['chapter_words']}字）")
        plan_hint.setStyleSheet("color:#9399b2; font-size:11px;")
        form.addRow("", plan_hint)

        request_edit = QTextEdit()
        request_edit.setMaximumHeight(60)
        request_edit.setPlaceholderText("可选：对大纲的特殊要求...")
        form.addRow("要求:", request_edit)

        dlg_layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("生成")
        ok_btn.setStyleSheet(BTN_PRIMARY)
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(BTN_SECONDARY)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        dlg_layout.addLayout(btn_row)

        cancel_btn.clicked.connect(dlg.reject)
        ok_btn.clicked.connect(dlg.accept)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            start = start_spin.value()
            end = end_spin.value()
            if end < start:
                QMessageBox.warning(self, "提示", "结束章不能小于起始章")
                return
            request = request_edit.toPlainText().strip()
            self._on_generate_outlines(start, end, request)

    def _on_menu_batch_deep_outline(self):
        """菜单：批量生产深大纲 — 弹范围对话框，串行调 DeepOutlineSingleWorker（单章 worker 复用）。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        outlines = self._pipeline.get_chapter_outlines()
        if not outlines:
            QMessageBox.warning(self, "提示", "没有章节大纲，请先生成简大纲")
            return

        pool_ids = sorted(ol.get("chapter_id", 0) for ol in outlines if ol.get("chapter_id", 0) > 0)
        if not pool_ids:
            QMessageBox.information(self, "提示", "没有可选的大纲章节")
            return

        default_start = pool_ids[0]
        default_end = pool_ids[-1]

        dlg = QDialog(self)
        dlg.setWindowTitle("批量生产深大纲 - 选择范围")
        dlg.setMinimumWidth(420)
        form = QFormLayout(dlg)
        start_spin = QSpinBox(); start_spin.setRange(1, 5000); start_spin.setValue(default_start)
        end_spin = QSpinBox(); end_spin.setRange(1, 5000); end_spin.setValue(default_end)
        skip_existing = QCheckBox("跳过已有深大纲的章节（不覆盖，按 event_progress 判定）")
        skip_existing.setChecked(True)
        form.addRow("起始章:", start_spin)
        form.addRow("结束章:", end_spin)
        form.addRow(skip_existing)
        hint = QLabel(
            f"共 {len(pool_ids)} 章有简大纲可选。\n"
            f"深大纲串行生成（一次一章，避免上下文过载）。"
        )
        hint.setStyleSheet("color:#9399b2; font-size:11px;")
        hint.setWordWrap(True)
        form.addRow("", hint)
        btn_row = QHBoxLayout(); btn_row.addStretch()
        ok_btn = QPushButton("开始生成"); ok_btn.setStyleSheet(BTN_PRIMARY)
        cancel_btn = QPushButton("取消"); cancel_btn.setStyleSheet(BTN_SECONDARY)
        btn_row.addWidget(cancel_btn); btn_row.addWidget(ok_btn)
        form.addRow(btn_row)
        cancel_btn.clicked.connect(dlg.reject); ok_btn.clicked.connect(dlg.accept)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        start = start_spin.value()
        end = end_spin.value()
        if end < start:
            QMessageBox.warning(self, "提示", "结束章不能小于起始章")
            return

        # 范围内所有简大纲已存在的章
        range_ids = [cid for cid in pool_ids if start <= cid <= end]
        # 跳过已有深大纲（event_progress 非空即视为已有）
        existing_ids = {
            ol.get("chapter_id") for ol in outlines
            if (ol.get("event_progress") or [])
        }
        if skip_existing.isChecked():
            target_ids = [cid for cid in range_ids if cid not in existing_ids]
            skipped = [cid for cid in range_ids if cid in existing_ids]
        else:
            target_ids = range_ids
            skipped = []

        if not target_ids:
            QMessageBox.information(
                self, "提示",
                f"所选范围（第 {start}-{end} 章）内没有可生成深大纲的章节。"
                f"{(f'已有深大纲 {len(skipped)} 章（已跳过）。' if skipped else '')}"
                f"可取消「跳过已有深大纲」来覆盖重写。"
            )
            return

        # 串行启动：复用 DeepOutlineSingleWorker
        self._deep_outline_queue = list(target_ids)
        self._deep_outline_total = len(target_ids)
        self._deep_outline_done = 0
        self._deep_outline_skipped = len(skipped)
        self._statusbar.showMessage(
            f"开始批量生产深大纲：共 {self._deep_outline_total} 章"
            f"{('（跳过已有 ' + str(self._deep_outline_skipped) + ' 章）') if skipped else ''}",
            4000,
        )
        self._launch_next_deep_outline()

    def _launch_next_deep_outline(self):
        """串行队列：取下一章 cid，起单章 worker；队列空时收尾。"""
        queue = getattr(self, "_deep_outline_queue", None)
        if not queue:
            return
        if not queue:
            return
        cid = queue.pop(0)
        self._statusbar.showMessage(
            f"正在生成第 {cid} 章深大纲（{self._deep_outline_done + 1}/{self._deep_outline_total}）..."
        )
        worker = DeepOutlineSingleWorker(
            self._llm_client, self._pipeline, cid, parent=self,
        )
        worker.finished.connect(self._on_deep_outline_batch_one_done)
        worker.error.connect(
            lambda err, _cid=cid: self._on_deep_outline_batch_one_error(_cid, err)
        )
        self._workers.append(worker)
        worker.start()

    def _on_deep_outline_batch_one_done(self, cid):
        self._deep_outline_done += 1
        # 刷新 UI
        try:
            self.editor.refresh_chapters()
        except Exception:
            pass
        try:
            self.dashboard.refresh()
        except Exception:
            pass
        # 续跑下一章
        self._launch_next_deep_outline()
        # 全部完成
        if not self._deep_outline_queue and self._deep_outline_done >= self._deep_outline_total:
            self._statusbar.showMessage(
                f"批量生产深大纲完成：共生成 {self._deep_outline_done} 章"
                + (f"（另有 {self._deep_outline_skipped} 章跳过）" if self._deep_outline_skipped else ""),
                5000,
            )

    def _on_deep_outline_batch_one_error(self, cid, err):
        QMessageBox.warning(self, "深大纲生成失败", f"第 {cid} 章：{err}\n\n继续下一章…")
        # 出错也继续下一章（不让一批卡住）
        self._launch_next_deep_outline()

    def _on_menu_batch_write(self):
        """菜单：批量生成正文 — 弹范围选择对话框，写入范围内的已确认大纲。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        outlines = self._pipeline.get_chapter_outlines()
        if not outlines:
            QMessageBox.warning(self, "提示", "没有章节大纲，请先生成大纲")
            return

        confirmed = [ol for ol in outlines if ol.get("confirmed")]
        # 始终使用全部大纲；是否仅写已确认由 checkbox 控制
        pool = outlines
        pool_ids = sorted(ol.get("chapter_id", 0) for ol in pool if ol.get("chapter_id", 0) > 0)
        if not pool_ids:
            QMessageBox.information(self, "提示", "没有可选的大纲章节")
            return

        default_start = pool_ids[0]
        default_end = pool_ids[-1]

        # 已存在正文的章节（仅含实质内容的文件；纯标题行空壳不计）
        existing_ids: set[int] = set()
        try:
            all_chapters = self._project_manager.get_chapter_list(self._current_project.project_root)
            # 字数阈值：标题行+少量标点通常 < 50 字；有正文的一般 > 200 字
            existing_ids = {
                ch["chapter_id"] for ch in all_chapters
                if (ch.get("word_count") or 0) > 50
            }
        except Exception:
            pass

        # --- 范围选择对话框 ---
        dlg = QDialog(self)
        dlg.setWindowTitle("批量写作 - 选择范围")
        dlg.setMinimumWidth(400)
        dlg_layout = QVBoxLayout(dlg)

        form = QFormLayout()
        start_spin = QSpinBox()
        start_spin.setRange(1, 5000)
        start_spin.setValue(default_start)
        form.addRow("起始章:", start_spin)

        end_spin = QSpinBox()
        end_spin.setRange(1, 5000)
        end_spin.setValue(default_end)
        form.addRow("结束章:", end_spin)

        only_confirmed = QCheckBox("仅写已确认的大纲（未确认的大纲跳过）")
        # 仅当有已确认大纲时默认勾选，防止用户误写未确认的草稿
        only_confirmed.setChecked(bool(confirmed))
        form.addRow(only_confirmed)

        skip_existing = QCheckBox("跳过已有正文的章节（不覆盖）")
        skip_existing.setChecked(True)
        form.addRow(skip_existing)

        hint = QLabel(
            f"共 {len(pool_ids)} 条大纲可选"
            f"（其中 {len(confirmed)} 条已确认）。"
            f"\n范围内含大纲的章节才会写入。"
        )
        hint.setStyleSheet("color:#9399b2; font-size:11px;")
        form.addRow("", hint)
        dlg_layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("开始写作")
        ok_btn.setStyleSheet(BTN_PRIMARY)
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(BTN_SECONDARY)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        dlg_layout.addLayout(btn_row)

        cancel_btn.clicked.connect(dlg.reject)
        ok_btn.clicked.connect(dlg.accept)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        start = start_spin.value()
        end = end_spin.value()
        if end < start:
            QMessageBox.warning(self, "提示", "结束章不能小于起始章")
            return

        target_ids = [cid for cid in pool_ids if start <= cid <= end]
        if only_confirmed.isChecked():
            confirmed_ids = {ol.get("chapter_id") for ol in confirmed}
            target_ids = [cid for cid in target_ids if cid in confirmed_ids]
        if skip_existing.isChecked():
            target_ids = [cid for cid in target_ids if cid not in existing_ids]
        if not target_ids:
            # 诊断信息：告诉用户为什么没有可写章节
            range_all = [cid for cid in pool_ids if start <= cid <= end]
            range_existing = [cid for cid in range_all if cid in existing_ids]
            range_unconfirmed = (
                [cid for cid in range_all if cid not in {ol.get("chapter_id") for ol in confirmed}]
                if only_confirmed.isChecked() else []
            )
            reasons = []
            if range_existing:
                reasons.append(f"已有正文（{len(range_existing)} 章）")
            if range_unconfirmed:
                reasons.append(f"大纲未确认（{len(range_unconfirmed)} 章）")
            detail = "、".join(reasons) if reasons else "范围内无大纲"
            QMessageBox.information(
                self, "提示",
                f"所选范围（第 {start}-{end} 章）内没有可写的章节。\n\n"
                f"原因：{detail}。\n\n"
                f"提示：可取消「跳过已有正文」来覆盖重写，"
                f"或取消「仅写已确认的大纲」来包含草稿大纲。"
            )
            return

        self._on_batch_write_chapters(target_ids)

    def _on_generate_outlines(self, start: int, end: int, request: str):
        """生成章节大纲。"""
        if not self._pipeline:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        # 检测目标范围内已有正文的章节
        chapters_with_content = []
        if self._current_project:
            all_chapters = self._project_manager.get_chapter_list(self._current_project.project_root)
            existing_ids = {ch["chapter_id"] for ch in all_chapters}
            for cid in range(start, end + 1):
                if cid in existing_ids:
                    chapters_with_content.append(cid)

        # 如果有正文冲突，弹出确认对话框
        if chapters_with_content:
            conflict_str = ", ".join(f"第{cid}章" for cid in chapters_with_content)
            total = len(chapters_with_content)

            msg = QMessageBox(self)
            msg.setWindowTitle("检测到已有正文")
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setText(
                f"以下 {total} 个章节已有正文内容：\n"
                f"{conflict_str}\n\n"
                f"重新生成大纲后，这些章节的正文与记忆数据将与新大纲不匹配。\n\n"
                f"建议先删除旧正文再生成大纲。"
            )

            delete_btn = msg.addButton("删除正文并生成大纲", QMessageBox.ButtonRole.AcceptRole)
            keep_btn = msg.addButton("保留正文，仅生成大纲", QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg.exec()

            clicked = msg.clickedButton()

            if clicked == cancel_btn:
                return
            elif clicked == delete_btn:
                # 删除正文文件和记忆数据
                for cid in chapters_with_content:
                    try:
                        self._pipeline.delete_chapter_content(cid)
                    except Exception as e:
                        logger.warning(f"删除第{cid}章正文失败: {e}")
                self.editor.refresh_chapters()
                self._refresh_memory()
                self._statusbar.showMessage(
                    f"已删除 {total} 章正文及关联记忆数据", 3000
                )
            # else: keep_btn — 保留正文，继续生成大纲

        self._statusbar.showMessage(f"正在生成第{start}-{end}章大纲...")
        worker = OutlineGenWorker(
            self._pipeline, start, end, request,
            book_name=self._current_project.name if self._current_project else "",
            genre=self._current_project.genre if self._current_project else "",
            tone=getattr(self._current_project, 'tone', '') if self._current_project else "",
            novel_category=getattr(self._current_project, 'novel_category', '') if self._current_project else "",
        )
        worker.progress.connect(
            lambda stage, content: self._statusbar.showMessage(stage)
        )
        worker.finished.connect(
            lambda success, error: self._on_generate_outlines_finished(success, error)
        )
        worker.start()
        self._workers.append(worker)

    def _on_regenerate_outline(self, chapter_id: int, feedback: str):
        """重新生成单个章节大纲。"""
        if not self._pipeline:
            return

        # 检测该章节是否已有正文
        has_content = False
        if self._current_project:
            chapter_path = Path(self._current_project.project_root) / "chapters" / f"chapter_{chapter_id:04d}.md"
            has_content = chapter_path.exists()

        if has_content:
            msg = QMessageBox(self)
            msg.setWindowTitle("检测到已有正文")
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setText(
                f"第{chapter_id}章已有正文内容。\n\n"
                f"重新生成大纲后，旧正文与记忆数据可能与新大纲不匹配。\n\n"
                f"建议先删除旧正文再生成大纲。"
            )
            delete_btn = msg.addButton("删除正文并生成大纲", QMessageBox.ButtonRole.AcceptRole)
            keep_btn = msg.addButton("保留正文，仅生成大纲", QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg.exec()

            clicked = msg.clickedButton()
            if clicked == cancel_btn:
                return
            elif clicked == delete_btn:
                try:
                    self._pipeline.delete_chapter_content(chapter_id)
                    self.editor.refresh_chapters()
                    self._refresh_memory()
                except Exception as e:
                    logger.warning(f"删除第{chapter_id}章正文失败: {e}")

        self._statusbar.showMessage(f"正在重新生成第{chapter_id}章大纲...")
        try:
            adjacent = self._pipeline._get_adjacent_outlines_str(
                chapter_id, chapter_id,
                exclude_ids={chapter_id},
            )
            outlines = self._pipeline.generate_chapter_outlines(
                start_chapter=chapter_id,
                end_chapter=chapter_id,
                book_name=self._current_project.name if self._current_project else "",
                genre=self._current_project.genre if self._current_project else "",
                tone=getattr(self._current_project, 'tone', '') if self._current_project else "",
                novel_category=getattr(self._current_project, 'novel_category', '') if self._current_project else "",
                user_request=feedback,
                adjacent_outlines=adjacent,
            )
            if outlines:
                self._pipeline.save_chapter_outlines(outlines)
                self.editor.refresh_chapters()
                self.dashboard.refresh()
                self._statusbar.showMessage(f"第{chapter_id}章大纲已重新生成", 2000)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"重新生成大纲失败: {e}")

    def _on_batch_regenerate_outlines(self, start: int, end: int, feedback: str):
        """批量重新生成章节大纲。"""
        if not self._pipeline:
            return

        # 检测目标范围内已有正文的章节
        chapters_with_content = []
        if self._current_project:
            all_chapters = self._project_manager.get_chapter_list(self._current_project.project_root)
            existing_ids = {ch["chapter_id"] for ch in all_chapters}
            for cid in range(start, end + 1):
                if cid in existing_ids:
                    chapters_with_content.append(cid)

        # 如果有正文冲突，弹出确认对话框
        if chapters_with_content:
            conflict_str = ", ".join(f"第{cid}章" for cid in chapters_with_content)
            total = len(chapters_with_content)

            msg = QMessageBox(self)
            msg.setWindowTitle("检测到已有正文")
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setText(
                f"以下 {total} 个章节已有正文内容：\n"
                f"{conflict_str}\n\n"
                f"重新生成大纲后，这些章节的正文与记忆数据将与新大纲不匹配。\n\n"
                f"建议先删除旧正文再生成大纲。"
            )

            delete_btn = msg.addButton("删除正文并生成大纲", QMessageBox.ButtonRole.AcceptRole)
            keep_btn = msg.addButton("保留正文，仅生成大纲", QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg.exec()

            clicked = msg.clickedButton()

            if clicked == cancel_btn:
                return
            elif clicked == delete_btn:
                for cid in chapters_with_content:
                    try:
                        self._pipeline.delete_chapter_content(cid)
                    except Exception as e:
                        logger.warning(f"删除第{cid}章正文失败: {e}")
                self.editor.refresh_chapters()
                self._refresh_memory()
                self._statusbar.showMessage(
                    f"已删除 {total} 章正文及关联记忆数据", 3000
                )

        self._statusbar.showMessage(f"正在批量重新生成第{start}-{end}章大纲...")
        worker = OutlineGenWorker(
            self._pipeline, start, end, feedback,
            book_name=self._current_project.name if self._current_project else "",
            genre=self._current_project.genre if self._current_project else "",
            tone=getattr(self._current_project, 'tone', '') if self._current_project else "",
            novel_category=getattr(self._current_project, 'novel_category', '') if self._current_project else "",
        )
        worker.progress.connect(
            lambda stage, content: self._statusbar.showMessage(stage)
        )
        worker.finished.connect(
            lambda success, error: self._on_generate_outlines_finished(success, error)
        )
        worker.start()
        self._workers.append(worker)

    def _on_confirm_outline(self, chapter_id: int, confirmed: bool):
        """确认/取消确认章节大纲。"""
        if not self._pipeline:
            return
        self._pipeline.confirm_chapter_outline(chapter_id, confirmed)
        self.editor.update_confirm_status(confirmed)
        self.editor.refresh_chapters()
        self.dashboard.refresh()
        self._statusbar.showMessage(
            f"第{chapter_id}章大纲已{'确认' if confirmed else '取消确认'}", 2000
        )

    def _on_confirm_all_outlines(self, confirmed: bool):
        """全部确认/取消确认大纲。"""
        if not self._pipeline:
            return
        outlines = self._pipeline.get_chapter_outlines()
        if not outlines:
            return
        for ol in outlines:
            cid = ol.get("chapter_id")
            if cid:
                self._pipeline.confirm_chapter_outline(cid, confirmed)
        self.editor.refresh_chapters()
        self.dashboard.refresh()
        self._statusbar.showMessage(
            f"已{'确认' if confirmed else '取消确认'}全部 {len(outlines)} 章大纲", 3000
        )

    def _on_delete_chapter_content(self, chapter_id: int):
        """删除章节正文（保留大纲）— 清理正文文件和关联记忆数据。"""
        if not self._pipeline or not self._current_project:
            return

        reply = QMessageBox.question(
            self, "确认删除正文",
            f"确定删除第{chapter_id}章的正文吗？\n\n"
            f"将删除：\n"
            f"  • 章节正文文件\n"
            f"  • RAG 向量数据\n"
            f"  • 章节摘要\n"
            f"  • 写作 checkpoint\n\n"
            f"大纲将保留，可重新生成正文。\n"
            f"此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._pipeline.delete_chapter_content(chapter_id)
            self.editor.refresh_chapters()
            self.project_list.refresh()
            self._refresh_memory()
            self._update_wordcount()
            self._statusbar.showMessage(f"第{chapter_id}章正文已删除（大纲保留）", 3000)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"删除正文失败: {e}")

    def _on_batch_delete_chapter_content(self, cids: list):
        """批量删除章节正文（保留大纲）— 逐章清正文文件与关联记忆，大纲不动。"""
        if not self._pipeline or not self._current_project:
            return
        cids = sorted(cids)
        reply = QMessageBox.question(
            self, "确认批量删除正文",
            f"确定批量删除以下 {len(cids)} 章的正文吗？\n\n"
            f"  第 {cids[0]}–{cids[-1]} 章（共 {len(cids)} 章）\n\n"
            f"每章将删除：\n"
            f"  • 章节正文文件\n"
            f"  • RAG 向量数据\n"
            f"  • 章节摘要\n"
            f"  • 写作 checkpoint\n\n"
            f"全部大纲保留，可重新生成正文。\n"
            f"此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok, fail = 0, []
        for cid in cids:
            try:
                self._pipeline.delete_chapter_content(cid)
                ok += 1
            except Exception as e:
                fail.append((cid, str(e)))
                logger.warning(f"批量删除第{cid}章正文失败: {e}")

        self.editor.refresh_chapters()
        self.project_list.refresh()
        self._refresh_memory()
        self._update_wordcount()

        if fail:
            detail = "\n".join(f"第{c}章: {e}" for c, e in fail[:10])
            more = f" 等 {len(fail)} 章" if len(fail) > 10 else ""
            QMessageBox.warning(
                self, "部分失败",
                f"已删除 {ok} 章正文，{len(fail)} 章失败{more}：\n{detail}"
            )
        else:
            self._statusbar.showMessage(f"已批量删除 {ok} 章正文（大纲保留）", 4000)

    def _on_insert_chapter(self, anchor_id: int):
        """在 anchor_id 章节前插入新章：弹对话框收集标题与是否 AI 调整前后章。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return

        # 插入位置 = anchor_id（新章成为第 anchor_id 章，原 anchor_id 及之后顺延）
        insert_pos = anchor_id

        dialog = QDialog(self)
        dialog.setWindowTitle(f"在第{anchor_id}章前插入新章")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)

        form = QFormLayout()
        title_edit = QLineEdit()
        title_edit.setPlaceholderText(f"新章节标题（留空则默认「新章节{insert_pos}」）")
        form.addRow("章节标题:", title_edit)
        layout.addLayout(form)

        # 用户要求（可选）
        req_edit = QTextEdit()
        req_edit.setFixedHeight(70)
        req_edit.setPlaceholderText("对插入章节的内容要求（可选，如：补充一段回忆杀 / 插入一场战斗 / 过渡一下情绪……）")
        layout.addWidget(QLabel("插入内容要求（可选）:"))
        layout.addWidget(req_edit)

        # 是否 AI 调整前后章钩子
        adjust_chk = QCheckBox("AI 自动调整前后章钩子，保持上下文衔接")
        adjust_chk.setChecked(True)
        layout.addWidget(adjust_chk)

        # 提示信息
        note = QLabel(
            "插入后，原第%d章及之后所有章节编号将自动顺延 +1，\n"
            "对应正文、摘要、大纲与记忆数据会一并平移。" % anchor_id
        )
        note.setWordWrap(True)
        note.setStyleSheet("QLabel { color: #9399b2; font-size: 11px; }")
        layout.addWidget(note)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        title = title_edit.text().strip()
        user_request = req_edit.toPlainText().strip()
        adjust_adjacent = adjust_chk.isChecked()

        # 若用户填写了标题，先写入到占位章（insert_chapter 内部也建占位章）
        # 这里把标题通过 user_request 之外的机制处理：占位章用默认标题，
        # 待 worker 完成后再用用户标题覆盖保存。
        self._pending_insert_title = title

        self._statusbar.showMessage(f"正在插入第{insert_pos}章...")
        worker = InsertChapterWorker(
            self._pipeline,
            insert_pos,
            book_name=self._current_project.name,
            genre=self._current_project.genre,
            tone=getattr(self._current_project, 'tone', '') or "",
            user_request=user_request,
            adjust_adjacent=adjust_adjacent,
        )
        worker.progress.connect(lambda msg: self._statusbar.showMessage(msg))
        worker.finished.connect(
            lambda result: self._on_insert_chapter_finished(insert_pos, result)
        )
        worker.start()
        self._workers.append(worker)

    def _on_insert_chapter_finished(self, insert_pos: int, result: dict):
        """插入章节完成后的处理。"""
        if result.get("error"):
            QMessageBox.critical(self, "插入失败", f"插入章节时发生错误:\n{result['error']}")
            return

        # 若用户指定了标题，覆盖占位章标题
        title = getattr(self, "_pending_insert_title", "")
        if title:
            try:
                self._project_manager.save_chapter_text(
                    self._current_project.project_root, insert_pos, title, ""
                )
            except Exception as e:
                logger.warning(f"覆盖插入章标题失败: {e}")
            self._pending_insert_title = ""

        self.editor.refresh_chapters()
        self.project_list.refresh()
        self._refresh_memory()
        self._update_wordcount()

        shifted = result.get("shifted_count", 0)
        adjusted = result.get("adjusted", False)
        parts = [f"已在第{insert_pos}章位置插入新章。"]
        if shifted:
            parts.append(f"原第{insert_pos}章及之后共 {shifted} 章已顺延。")
        if adjusted:
            parts.append("AI 已重新规划新章大纲并调整前后章钩子，上下文衔接已保持。")
        else:
            parts.append("（未启用 AI 钩子调整：机械移位已完成，可稍后手动生成新章大纲）")
        self._statusbar.showMessage(" ".join(parts), 5000)

        # 选中新插入的章节
        self.editor.select_chapter(insert_pos)

    def _on_deep_outline(self):
        """Dashboard「生成深大纲（按卷批量）」→ 调 planner.plan_book 并写回。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        reply = QMessageBox.question(
            self, "生成深大纲",
            "将用 AI 把当前所有章节的薄大纲一次性展开为深大纲（事件进展规格），"
            "按卷批量规划。\n这会覆盖现有深大纲（event_progress）。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._deep_worker = DeepOutlineWorker(self._llm_client, self._pipeline)
        self._deep_worker.finished.connect(self._on_deep_outline_done)
        self._deep_worker.error.connect(
            lambda e: QMessageBox.warning(self, "深大纲生成失败", e))
        self._statusbar.showMessage("正在生成深大纲（按卷批量）…")
        self._deep_worker.start()

    def _on_deep_outline_done(self, n: int):
        self._statusbar.showMessage(f"深大纲已生成：{n} 章", 3000)
        self.context_preview._refresh_chapter_list()
        QMessageBox.information(
            self, "完成",
            f"深大纲已生成并写回，共 {n} 章。\n"
            "可在「上下文预览」 Tab 查看钉顶的章节脚本块与守卫检查项。"
        )

    def _on_gen_deep_outline_single(self, chapter_id: int):
        """中间编辑区「生成 / 重新生成深大纲」按钮 → 单章深大纲。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        self._deep_single_worker = DeepOutlineSingleWorker(
            self._llm_client, self._pipeline, chapter_id
        )
        self._deep_single_worker.finished.connect(
            lambda cid: self._on_deep_outline_single_done(cid)
        )
        self._deep_single_worker.error.connect(
            lambda e: QMessageBox.warning(self, "深大纲生成失败", e)
        )
        self._statusbar.showMessage(f"正在生成第{chapter_id}章深大纲…")
        self._deep_single_worker.start()
        self._workers.append(self._deep_single_worker)

    def _on_deep_outline_single_done(self, chapter_id: int):
        self._statusbar.showMessage(f"第{chapter_id}章深大纲已生成", 3000)
        self.editor.refresh_chapters()
        self.dashboard.refresh()
        self.context_preview._refresh_chapter_list()

    def _on_framework(self):
        """Dashboard「生成整本书框架（阶段0）」→ 一次性生成整本书框架。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        root = str(getattr(self._pipeline, "_project_root", "") or "")
        if not root:
            QMessageBox.warning(self, "提示", "无法定位项目根目录")
            return
        reply = QMessageBox.question(
            self, "生成整本书框架",
            "将一次性生成整本书框架（粗大纲→背景→主线大事件→粗设定→卷）。\n"
            "已有总纲/卷结构默认保留，仅补全缺失部分与每卷主线事件。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._fw_worker = FrameworkWorker(self._llm_client, self._pipeline)
        self._fw_worker.finished.connect(self._on_framework_done)
        self._fw_worker.error.connect(
            lambda e: QMessageBox.warning(self, "框架生成失败", e))
        self._statusbar.showMessage("正在生成整本书框架（阶段0）…")
        self._fw_worker.start()

    def _on_framework_done(self, res: dict):
        added = res.get("main_events_added", 0)
        self._statusbar.showMessage("整本书框架已生成（阶段0）", 3000)
        QMessageBox.information(
            self, "完成",
            f"整本书框架已生成：\n"
            f"- 总纲 / 卷结构 / 事件弧 / 脊柱 已就绪\n"
            f"- 每卷主线事件新增 {added} 条\n"
            f"下一步可点「生成深大纲」展开章节级规划。"
        )

    def _on_write_single_chapter(self, chapter_id: int):
        """根据大纲生成单章正文。"""
        if not self._pipeline or not self._current_project:
            return

        outline = self._pipeline.get_chapter_outline(chapter_id)
        if not outline:
            QMessageBox.warning(self, "提示", f"第{chapter_id}章没有大纲")
            return

        title = outline.get("title", f"第{chapter_id}章")
        config = self._build_writing_config()

        # 从大纲提取写作指引
        instruction_parts = []
        if outline.get("core_events"):
            instruction_parts.append(f"核心事件: {outline['core_events']}")
        if outline.get("plot_summary"):
            instruction_parts.append(f"情节概要: {outline['plot_summary']}")
        if outline.get("opening_hook"):
            instruction_parts.append(f"开篇钩子: {outline['opening_hook']}")
        if outline.get("ending_hook"):
            instruction_parts.append(f"结尾钩子: {outline['ending_hook']}")
        if outline.get("foreshadowing"):
            instruction_parts.append(f"伏笔: {outline['foreshadowing']}")
        writing_instruction = "\n".join(instruction_parts) if instruction_parts else ""

        self._statusbar.showMessage(f"正在生成第{chapter_id}章正文...")

        # 若本章已有正文，则以重写方式基于现有正文优化，保证与已有内容连贯
        existing_text = ""
        try:
            existing_text = self._project_manager.get_chapter_text(
                self._current_project.project_root, chapter_id
            )
        except Exception:
            existing_text = ""
        mode = "rewrite" if (existing_text and existing_text.strip()) else "write"

        # 清空编辑器，流式输出直接写入
        self.editor.set_chapter_text("")

        worker = WritingWorker(
            pipeline=self._pipeline,
            chapter_id=chapter_id,
            chapter_title=title,
            writing_instruction=writing_instruction,
            config=config,
            mode=mode,
            existing_text=existing_text,
        )
        worker.progress.connect(
            lambda stage: self._statusbar.showMessage(f"写作中：{stage}")
        )
        worker.stream.connect(lambda chunk: self.editor.append_text(chunk))
        worker.finished_signal.connect(
            lambda result: self._on_write_finished(result, chapter_id, title)
        )
        worker.start()
        self._workers.append(worker)

    def _on_batch_write_chapters(self, cids: list[int]):
        """批量写入章节正文。"""
        if not self._pipeline or not cids:
            return

        # 读取大纲
        outlines = self._pipeline.get_chapter_outlines()
        target_outlines = [ol for ol in outlines if ol.get("chapter_id") in cids]

        config = self._build_writing_config()
        self._statusbar.showMessage(f"正在批量生成 {len(cids)} 章正文...")

        self._batch_write_worker = BatchWriteWorker(
            pipeline=self._pipeline,
            chapter_ids=cids,
            outlines=target_outlines,
            config=config,
        )
        self._batch_write_worker.progress.connect(
            lambda cur, total, stage: self._statusbar.showMessage(f"[{cur}/{total}] {stage}")
        )
        self._batch_write_worker.chapter_done.connect(self._on_batch_chapter_done)
        self._batch_write_worker.finished.connect(self._on_batch_write_finished)
        self._batch_write_worker.start()
        self._workers.append(self._batch_write_worker)

    def _on_reinit_foundation(self):
        """重新 AI 初始化项目基础设定。"""
        if not self._pipeline or not self._current_project:
            return

        reply = QMessageBox.question(
            self, "确认重新初始化",
            "重新 AI 初始化将覆盖现有的世界观、主角设定和总纲。\n"
            "章节正文不会被影响，但章节大纲需要重新生成。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._statusbar.showMessage("正在重新 AI 初始化...")

        def on_progress(stage_name, content):
            self._statusbar.showMessage(stage_name)

        init_result = self._pipeline.auto_init_project(
            book_name=self._current_project.name,
            genre=self._current_project.genre,
            inspiration="",
            tone="",
            on_progress=on_progress,
        )
        if init_result.success:
            self.dashboard.refresh()
            self._refresh_memory()
            self._statusbar.showMessage("AI 重新初始化完成", 3000)
        else:
            QMessageBox.critical(self, "初始化失败", init_result.error)

    def _on_dashboard_regenerate_setting(self, setting_type: str, user_request: str):
        """单项重新生成设定文件（世界观/主角设定等）。"""
        if not self._pipeline or not self._current_project:
            return

        # 获取灵感
        inspiration = self._pipeline.get_project_inspiration()
        tone = getattr(self._current_project, 'tone', '') or ""

        known_types = {"worldview", "character_main"}
        if setting_type not in known_types:
            QMessageBox.information(
                self, "提示",
                f"AI 重新生成仅支持已知设定类型（worldview/character_main）。\n"
                f"当前类型「{setting_type}」请手动编辑。"
            )
            return

        worker = RegenSettingWorker(
            pipeline=self._pipeline,
            item_type=setting_type,
            book_name=self._current_project.name,
            genre=self._current_project.genre,
            inspiration=inspiration,
            tone=tone,
            user_request=user_request,
        )
        worker.progress.connect(lambda msg: self._statusbar.showMessage(msg))
        worker.finished.connect(self._on_regen_setting_finished)
        worker.start()
        self._workers.append(worker)
        self._statusbar.showMessage(f"正在重新生成设定: {setting_type}...")

    def _on_dashboard_regenerate_outline(self, outline_type: str, user_request: str):
        """单项重新生成大纲文件（总纲等）。"""
        if not self._pipeline or not self._current_project:
            return

        inspiration = self._pipeline.get_project_inspiration()
        tone = getattr(self._current_project, 'tone', '') or ""

        if outline_type != "master":
            QMessageBox.information(
                self, "提示",
                f"AI 重新生成仅支持总纲（master）。\n"
                f"当前类型「{outline_type}」请手动编辑。"
            )
            return

        worker = RegenSettingWorker(
            pipeline=self._pipeline,
            item_type="master",
            book_name=self._current_project.name,
            genre=self._current_project.genre,
            inspiration=inspiration,
            tone=tone,
            user_request=user_request,
            novel_category=self._current_project.novel_category,
        )
        worker.progress.connect(lambda msg: self._statusbar.showMessage(msg))
        worker.finished.connect(self._on_regen_setting_finished)
        worker.start()
        self._workers.append(worker)
        self._statusbar.showMessage("正在重新生成故事总纲...")

    def _on_dashboard_regenerate_synopsis(self, user_request: str):
        """单项重新生成小说简介。"""
        if not self._pipeline or not self._current_project:
            return

        inspiration = self._pipeline.get_project_inspiration()
        tone = getattr(self._current_project, 'tone', '') or ""

        worker = RegenSettingWorker(
            pipeline=self._pipeline,
            item_type="synopsis",
            book_name=self._current_project.name,
            genre=self._current_project.genre,
            inspiration=inspiration,
            tone=tone,
            user_request=user_request,
        )
        worker.progress.connect(lambda msg: self._statusbar.showMessage(msg))
        worker.finished.connect(self._on_regen_setting_finished)
        worker.start()
        self._workers.append(worker)
        self._statusbar.showMessage("正在重新生成小说简介...")

    def _on_regen_setting_finished(self, item_type: str, success: bool, content: str, error: str):
        """单项重新生成完成回调。"""
        labels = {
            "worldview": "世界观设定",
            "character_main": "主角设定",
            "master": "故事总纲",
            "synopsis": "小说简介",
        }
        label = labels.get(item_type, item_type)

        if success:
            self.dashboard.refresh()
            self._refresh_memory()

            # 如果是简介，额外更新 dashboard 的显示
            if item_type == "synopsis" and self._current_project:
                self._current_project.description = content
                self._project_manager.update_project(self._current_project)
                self.dashboard.update_synopsis(content)

            self._statusbar.showMessage(f"{label}已重新生成", 3000)
            QMessageBox.information(self, "重新生成完成", f"{label}已重新生成并保存。")
        else:
            QMessageBox.critical(self, "重新生成失败", f"{label}重新生成失败:\n\n{error}")

    def _on_inspiration_expanded(self, insp_id: str, text: str):
        """灵感扩展为正文后插入编辑器。"""
        self.editor.append_text("\n\n" + text)
        self._statusbar.showMessage("灵感已扩展为正文，已插入编辑器", 3000)

    def _on_inspire_integrate_outline(self, target: str, modified: str):
        """灵感融入大纲。"""
        if not self._pipeline:
            return
        try:
            self._project_manager.save_outline(self._current_project.project_root, "master", modified)
            self.dashboard.refresh()
            self._statusbar.showMessage("灵感已融入大纲", 3000)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"融入大纲失败: {e}")

    def _on_inspire_integrate_setting(self, name: str, modified: str):
        """灵感融入设定集。"""
        if not self._pipeline:
            return
        try:
            self._project_manager.save_setting(self._current_project.project_root, name, modified)
            self.dashboard.refresh()
            self._statusbar.showMessage(f"灵感已融入设定集「{name}」", 3000)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"融入设定集失败: {e}")

    # ============================================================
    # Worker 回调
    # ============================================================

    def _on_write_finished(self, result: PipelineResult, chapter_id: int, title: str):
        """写作完成回调。正文已通过流式输出写入编辑器，这里只做保存和刷新。"""
        if result.success:
            # 保存到文件
            self._project_manager.save_chapter_text(
                self._current_project.project_root, chapter_id, title, result.chapter_text
            )
            self.editor.refresh_chapters()
            self.project_list.refresh()
            self._refresh_memory()
            self._update_wordcount()

            # 检查记忆提交状态
            if result.commit_report and not result.commit_report.success:
                QMessageBox.warning(
                    self, "记忆提交警告",
                    f"第{chapter_id}章正文已保存，但记忆更新存在问题：\n\n"
                    f"{result.commit_report.error or '未知错误'}\n\n"
                    f"人物档案/摘要/RAG可能未完全更新。\n"
                    f"可在「记忆档案」面板中手动刷新或重新提交。"
                )
            elif result.commit_report and result.commit_report.success:
                cr = result.commit_report
                self._statusbar.showMessage(
                    f"第{chapter_id}章完成 | "
                    f"摘要: {'✓' if cr.summary_created else '✗'} | "
                    f"RAG: {cr.rag_chunks_added}块 | "
                    f"备份: {'✓' if cr.backup_created else '✗'}",
                    5000
                )

            self._statusbar.showMessage(f"第{chapter_id}章写作完成", 3000)

            # 写完自动校验：情节合理性与设定一致性（自动模式，含现实知识疑点弹框标红）
            # 受 enable_quality_review 开关控制，避免违背「前置约束 + 按需校验」原则、
            # 在批量写作时每章都自动烧 token 校验（表现为「一直在校验」）。
            # 需要自动校验请在「设置 → 校验等级」勾选「每章写后自动审查」，
            # 或手动点「校验本章」/「项目 → 全书最终校验」。
            if self._config.enable_quality_review:
                self._run_validation(chapter_id, auto_mode=True)
            else:
                self._statusbar.showMessage(
                    f"第{chapter_id}章写作完成（自动校验已关闭，可手动「校验本章」或「全书最终校验」）",
                    3000
                )

        if result.success:
            self._statusbar.showMessage(f"第{chapter_id}章写作完成", 3000)
        else:
            self._statusbar.showMessage(f"第{chapter_id}章写作失败：{result.error}", 5000)

    # ============================================================
    # 情节 / 设定合理性校验
    # ============================================================

    def _run_validation(self, chapter_id: int, auto_mode: bool = False, from_fix: bool = False):
        """触发单章合理性校验。auto_mode=True 表示写完自动校验；from_fix=True 表示自动修复后的复校。"""
        if not self._pipeline or not self._current_project:
            QMessageBox.warning(self, "提示", "请先打开一个项目")
            return
        config = self._build_writing_config()
        self._statusbar.showMessage(f"正在校验第{chapter_id}章的情节与设定合理性...")
        worker = PlausibilityWorker(self._pipeline, chapter_id, config)
        worker.finished_report.connect(
            lambda report, cid: self._on_validate_finished(report, cid, auto_mode, from_fix)
        )
        worker.start()
        self._workers.append(worker)

    def _on_validate_finished(self, report: dict, chapter_id: int, auto_mode: bool = False, from_fix: bool = False):
        """校验完成回调。

        设计铁律（2026-07-10）：写后不靠事后 LLM 自校验保证质量，校验是「按需」行为。
        本回调**绝不形成自动修订循环**：
        - 复校（from_fix）：只告知结果，不弹窗、不再次自动修订；
        - 自动模式（写后自动校验）：只状态栏汇报，不弹模态框（避免批量写作疯狂弹窗）、
          不自动循环改写；除非用户显式开启 quality_auto_fix，才做「单轮」自动修订，修订后仅告知；
        - 手动模式：弹报告框，由用户决定是否「应用 AI 修订」（一次性，修订后仅告知）。
        """
        err = report.get("error")
        if err:
            if auto_mode:
                self._statusbar.showMessage(f"第{chapter_id}章自动校验跳过（{err}）", 3000)
            else:
                QMessageBox.information(self, "校验未完成", f"校验未能完成：\n{err}")
            return

        title = self._chapter_title_for(chapter_id)
        passed = report.get("passed")
        issues = report.get("issues") or []
        concerns = report.get("real_world_concerns") or []
        has_blocking = any(c.get("needs_user_confirmation") for c in concerns)

        # ---- 复校（自动/手动修订后的再校验）：只告知，绝不弹窗/再改 → 打破「改了又校验」死循环 ----
        if from_fix:
            if passed:
                self._statusbar.showMessage(f"✓ 第{chapter_id}章修订后复校通过", 3000)
            else:
                n = len(report.get("serious_issues") or issues)
                self._statusbar.showMessage(
                    f"第{chapter_id}章修订后复校仍有 {n} 项问题（如需再次修订请手动点「校验本章」→「应用 AI 修订」）", 5000)
            return

        # ---- 通过 ----
        if passed:
            if auto_mode:
                self._statusbar.showMessage(f"✓ 第{chapter_id}章校验通过", 3000)
            else:
                QMessageBox.information(
                    self, "校验通过",
                    f"第{chapter_id}章《{title}》情节与设定一致性校验通过。"
                )
            return

        # ---- 未通过：区分自动 / 手动 ----
        if auto_mode:
            # 自动模式：严禁弹模态框（批量写作否则疯狂弹窗）、严禁自动循环改写
            if self._config.quality_auto_fix and not has_blocking:
                self._statusbar.showMessage(f"第{chapter_id}章校验未通过，正在自动修订（单轮）...", 3000)
                self._autofix_chapter(chapter_id, report)  # 修订完走 from_fix 复校，仅告知
            elif has_blocking:
                self._statusbar.showMessage(
                    f"⚠ 第{chapter_id}章校验发现现实知识疑点，请手动点「校验本章」确认", 5000)
            else:
                n = len(report.get("serious_issues") or issues)
                self._statusbar.showMessage(
                    f"第{chapter_id}章校验发现 {n} 项问题（如需修订请手动点「校验本章」→「应用 AI 修订」）", 5000)
            return

        # ---- 手动模式：弹报告框，由用户决定 ----
        self._show_report(report, chapter_id)

    def _show_report(self, report: dict, chapter_id: int):
        """展示校验报告弹窗；若用户点「应用 AI 修订」则触发自动修复。"""
        title = self._chapter_title_for(chapter_id)
        dlg = PlausibilityReportDialog(report, chapter_id, title, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.apply_requested:
            self._autofix_chapter(chapter_id, report)

    def _autofix_chapter(self, chapter_id: int, report: dict):
        """根据校验报告自动修订本章；仅针对达到校验等级门槛的问题，避免无限改写循环。

        若含衔接/时间线类问题，顺带调整前后章。
        """
        if not self._pipeline or not self._current_project:
            return
        # 只重写达到门槛的问题（serious_issues），低严重度软建议不触发改写
        issues = report.get("serious_issues") or report.get("issues") or []
        if not issues:
            self._statusbar.showMessage(f"第{chapter_id}章无达到门槛的问题，跳过自动重写", 3000)
            return

        issues_text = "\n".join(
            f"- [{i.get('severity', '')}] {i.get('type', '')}: {i.get('description', '')}"
            f"（位置：{i.get('location', '')}；建议：{i.get('fix_suggestion', '')}）"
            for i in issues
        )
        revision_request = (
            "请根据以下【合理性校验问题】修订本章，修复情节逻辑 / 设定矛盾 / 衔接问题，"
            "保持文风与前文连贯，不要偏离已有内容：\n" + issues_text
        )

        # 是否需要顺带调整前后章（衔接 / 时间线类问题）
        continuity_types = {"continuity", "timeline"}
        neighbor_ids = []
        if any(i.get("type") in continuity_types for i in issues):
            for nid in (chapter_id - 1, chapter_id + 1):
                if nid >= 1 and self._chapter_file_exists(nid):
                    neighbor_ids.append(nid)

        requests = [(chapter_id, revision_request)]
        for nid in neighbor_ids:
            nreq = (
                f"第{nid}章与第{chapter_id}章的衔接出现了合理性问题。请微调本章与第{chapter_id}章相邻的"
                f"开头或结尾钩子与过渡，使两章自然衔接，不要大改已有情节与人物设定。"
            )
            requests.append((nid, nreq))

        self._statusbar.showMessage(f"正在自动修订第{chapter_id}章及其衔接章...", 3000)
        self._revise_chain(requests, on_done=lambda: self._on_autofix_all_done(chapter_id))

    def _revise_chain(self, requests: list, on_done):
        """依次修订 requests 中的章节（顺序执行，每章保存后再下一章）。"""
        idx = [0]

        def run_next():
            if idx[0] >= len(requests):
                on_done()
                return
            cid, req = requests[idx[0]]
            idx[0] += 1
            title = self._chapter_title_for(cid)
            try:
                text = self._project_manager.get_chapter_text(
                    self._current_project.project_root, cid
                )
            except Exception:
                text = ""
            worker = ReviseWorker(self._pipeline, cid, title, text, req)
            worker.finished.connect(
                lambda ok, revised, err: self._on_chain_step_done(ok, revised, err, cid, title, run_next)
            )
            worker.start()
            self._workers.append(worker)

        run_next()

    def _on_chain_step_done(self, ok: bool, revised: str, err: str, cid: int, title: str, next_cb):
        """修订链单步完成：保存并刷新，然后进入下一步。"""
        if ok and revised:
            try:
                self._project_manager.save_chapter_text(
                    self._current_project.project_root, cid, title, revised
                )
            except Exception as e:
                logger.warning(f"自动修订保存失败（第{cid}章）: {e}")
        else:
            logger.warning(f"第{cid}章自动修订失败: {err}")

        if cid == self.editor._current_chapter_id:
            self.editor.refresh_chapters()
        self._refresh_memory()
        next_cb()

    def _on_autofix_all_done(self, chapter_id: int):
        """自动修复全部完成后：刷新并复校一次。"""
        self.editor.refresh_chapters()
        self.project_list.refresh()
        self._refresh_memory()
        self._statusbar.showMessage(f"第{chapter_id}章自动修订完成，正在复校...", 3000)
        self._run_validation(chapter_id, auto_mode=True, from_fix=True)

    def _chapter_file_exists(self, cid: int) -> bool:
        """判断某章正文文件是否存在。"""
        if not self._current_project:
            return False
        p = Path(self._current_project.project_root) / "chapters" / f"chapter_{cid:04d}.md"
        return p.exists()

    def _chapter_title_for(self, cid: int) -> str:
        """取得章节标题（优先大纲标题）。"""
        if self._pipeline:
            try:
                outlines = self._pipeline.get_chapter_outlines()
                ol = next((o for o in outlines if o.get("chapter_id") == cid), None)
                if ol and ol.get("title"):
                    return ol["title"]
            except Exception:
                pass
        return f"第{cid}章"

    def _on_revise_finished(self, success: bool, text: str, error: str, chapter_id: int, title: str):
        """修订完成回调。正文已通过流式输出写入编辑器，这里只做保存和刷新。"""
        if success and text:
            self._project_manager.save_chapter_text(
                self._current_project.project_root, chapter_id, title, text
            )
            self.editor.refresh_chapters()
            self._refresh_memory()
            self._statusbar.showMessage(f"第{chapter_id}章修订完成", 3000)
        else:
            self._statusbar.showMessage(f"第{chapter_id}章修订失败：{error}", 5000)

    def _on_auto_write_finished(self, success: bool, chapter_id: int, title: str, text_or_error: str):
        """自动写下一章完成回调。正文已通过流式输出写入编辑器。"""
        if success:
            self._project_manager.save_chapter_text(
                self._current_project.project_root, chapter_id, title, text_or_error
            )
            self.editor.refresh_chapters()
            self._refresh_memory()
            self._update_wordcount()
            self._statusbar.showMessage(f"第{chapter_id}章自动写作完成", 3000)
        else:
            self._statusbar.showMessage(f"第{chapter_id}章自动写作失败：{text_or_error}", 5000)

    def _on_generate_outlines_finished(self, success: bool, error: str):
        """大纲生成完成回调。"""
        if success:
            self.editor.refresh_chapters()
            self.dashboard.refresh()
            outlines = self._pipeline.get_chapter_outlines() if self._pipeline else []
            self._statusbar.showMessage(f"章节大纲生成完成（共 {len(outlines)} 条）", 5000)
        else:
            QMessageBox.critical(self, "大纲生成失败", error)

    def _on_batch_chapter_done(self, chapter_id: int, text: str):
        """批量写入中单章完成。"""
        self.editor.refresh_chapters()
        self._refresh_memory()
        self._update_wordcount()

    def _on_batch_write_finished(self, success: bool, error: str):
        """批量写入完成。"""
        if success:
            self.dashboard.refresh()
            self._statusbar.showMessage("批量写入完成", 3000)
        else:
            self._statusbar.showMessage(f"批量写入失败: {error}", 5000)

    # ============================================================
    # 辅助方法
    # ============================================================

    def _build_writing_config(self) -> dict:
        """构建写作配置字典。"""
        return {
            "genre": self._current_project.genre if self._current_project else "",
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "enable_llm_review": False,
            "validation_level": self._config.validation_level,
            "adjacent_outline_window": self._config.adjacent_outline_window,
            # 质量审查通道（写后独立运行，不进写作提示词）
            "enable_quality_review": self._config.enable_quality_review,
            "quality_auto_fix": self._config.quality_auto_fix,
            "quality_tier": self._config.quality_tier,
            "system_prompt_budget": self._config.system_prompt_budget,
        }

    def _update_wordcount(self):
        """更新状态栏字数统计。"""
        if not self._current_project:
            return
        try:
            chapters = self._project_manager.get_chapter_list(self._current_project.project_root)
            total_words = sum(ch.get("word_count", 0) for ch in chapters)
            self._status_project_label.setText(f"当前项目: {self._current_project.name}")
            self._status_chapter_label.setText(f"章节: {len(chapters)}")
            self._status_word_label.setText(f"总字数: {total_words:,}")
            self._status_save_label.setText(f"上次保存: {datetime.now().strftime('%H:%M:%S')}")
        except Exception:
            pass

    def _refresh_memory(self):
        """刷新记忆面板。"""
        try:
            if self._memory:
                self.memory_panel.refresh()
        except Exception as _e:
            # 讨论回调触发的记忆刷新若失败，绝不该连累讨论窗口崩溃
            import traceback as _tb
            _tb.print_exc()

    def closeEvent(self, event):
        """窗口关闭时清理。"""
        _write_dialog_log("[MAIN_WINDOW] 主窗口 closeEvent（若讨论框消失与此同时，即 app 退出连带）")
        self._on_save_project()
        for w in self._workers:
            if w.isRunning():
                w.quit()
                w.wait(1000)
        event.accept()
