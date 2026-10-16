"""讨论功能 UI：聊天式 agent 讨论面板（Tab「讨论」）。

布局：
- 顶部上下文信息条（显示已加载的人物/伏笔/事件数）
- 中间聊天流（用户/agent 气泡 + 流式）
- 提案操作条（agent 提出调整建议后出现，采纳/驳回）
- 底部输入框（Enter 发送 / Shift+Enter 换行）+ 唯一「发送」按钮（智能体模式：可讨论、读章、搜全本、自检；需改正文时生成提案卡待确认写入）
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
import datetime as _dt
from typing import Optional

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal, qInstallMessageHandler, QtMsgType
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QTextEdit, QVBoxLayout, QWidget, QMessageBox,
)

# ---- 全局异常兜底：任何漏到主线程槽里的未捕获异常都写日志、弹提示，
#      绝不让 PyQt6 直接 abort 闪退（讨论窗口崩溃的根因治理）。 ----
_EXC_LOG = os.path.join(os.path.expanduser("~"), ".novelforge_discussion_exc.log")
_orig_excepthook = sys.excepthook

# ---- 流程日志：记录回车发送后的关键节点，用于定位「对话框在流程哪一步消失」。 ----
_PANEL_FLOW_LOG = os.path.expanduser("~/.novelforge_panel_flow.log")
def _flow_log(tag: str):
    try:
        with open(_PANEL_FLOW_LOG, "a", encoding="utf-8") as _f:
            _f.write(f"{_dt.datetime.now().isoformat()} {tag}\n")
    except Exception:
        pass


def _global_excepthook(etype, evalue, tb):
    msg = "".join(traceback.format_exception(etype, evalue, tb))
    try:
        with open(_EXC_LOG, "a", encoding="utf-8") as _f:
            _f.write(msg + "\n" + "=" * 60 + "\n")
    except Exception:
        pass
    # 注意：绝不调用 _orig_excepthook —— 那就是 PyQt6 的致命处理器(qFatal)，
    # 调用它会让程序直接 abort（窗口退出）。这里只记日志 + 弹提示，然后正常返回，
    # 让 Python 认为异常已被处理、进程继续运行（不再闪退）。
    try:
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                None, "程序异常（已捕获，未崩溃）",
                f"{getattr(etype, '__name__', 'Error')}: {evalue}\n\n"
                f"详细已写入：\n{_EXC_LOG}",
            ))
    except Exception:
        pass


sys.excepthook = _global_excepthook


# ---- 答案级「退化复读」确定性去重（零 token，纯程序）----
# 模型在「长上下文 + 高重复设定注入」下会退化成复述模式（最后一句话/列表项重复）。
# 这是采样副作用，但凡能确定性推导的量绝不交给模型自觉——相邻完全相同的句子/
# 段落一律去重，保留第一段，绝不破坏语义（非相邻、非完全相同的真内容不动）。
def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _dedup_repeat(text: str) -> str:
    """消除 LLM 退化复读：丢弃与上一行/上一句完全相同的相邻片段。"""
    if not text:
        return text
    paras = text.split("\n")
    out_paras: list[str] = []
    for p in paras:
        if p.strip() and out_paras and _norm_text(p) == _norm_text(out_paras[-1]):
            continue  # 与上一个保留的段落完全相同 → 复读，丢弃
        out_paras.append(p)
    result: list[str] = []
    for p in out_paras:
        if not p.strip():
            result.append(p)
            continue
        sents = re.split(r"(?<=[。！？!?])", p)
        out_s: list[str] = []
        for s in sents:
            if s.strip() and out_s and _norm_text(s) == _norm_text(out_s[-1]):
                continue  # 段内相邻重复句 → 复读，丢弃
            out_s.append(s)
        result.append("".join(out_s))
    return "\n".join(result)


# ---- Qt/C++ 层致命消息兜底 ----
# PyQt6 的 C++ 级 abort（如 qFatal("...ASSERT...")）不经过 Python try/except，
# 也走不到 sys.excepthook，但会在 abort 前先经过 Qt 消息系统。这里把它打到
# 终端(stderr) + 日志文件，让用户从终端启动时能直接看到崩溃原因。
def _qt_message_handler(msg_type: QtMsgType, context, msg: str):
    level = {
        QtMsgType.QtDebugMsg: "DEBUG",
        QtMsgType.QtInfoMsg: "INFO",
        QtMsgType.QtWarningMsg: "WARN",
        QtMsgType.QtCriticalMsg: "CRIT",
        QtMsgType.QtFatalMsg: "FATAL",
    }.get(msg_type, "??")
    line = f"[QT {level}] {msg} ({getattr(context, 'file', '')}:{getattr(context, 'line', '')})"
    try:
        with open(_EXC_LOG, "a", encoding="utf-8") as _f:
            _f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass


try:
    qInstallMessageHandler(_qt_message_handler)
except Exception:
    pass

from domains.novel.config import Config
from domains.novel.core.discussion.agent import DiscussionAgent
from domains.novel.core.discussion.context_builder import build_discussion_context
from domains.novel.core.discussion.proposal import build_proposal_view, build_task_cards
from domains.novel.core.llm_client import LLMClient
from domains.novel.core.memory.engine import MemoryEngine
from domains.novel.ui.workers.discussion_worker import DiscussionWorker

_USER_BG = "#89b4fa"
_AGENT_BG = "#313244"
_BORDER = "#45475a"
_TEXT = "#cdd6f4"
_BTN_SEC = "#45475a"
_ACCENT = "#89b4fa"
_DONE = "#a6e3a1"
_WARN = "#f9e2af"
_SUBTEXT = "#6c7086"
_SUBTEXT2 = "#a6adc8"
_ACCENT_HOVER = "#b4befe"


class _TaskCard(QFrame):
    """右侧任务卡：一张卡 = 一类可落地的改动。

    展示：状态徽章 + 项目徽章 + 标题 + 元信息 / 影响范围 / 改动对比；操作：确认执行 / 忽略。
    支持整卡独立收起（点击标题栏 chevron 切换）。
    """

    apply_clicked = pyqtSignal(str)
    reject_clicked = pyqtSignal(str)

    def __init__(self, card: dict, parent=None):
        super().__init__(parent)
        self._card = card
        self._expanded = False       # 改动对比子折叠
        self._card_collapsed = False # 整卡折叠
        self._build()

    def _build(self):
        c = self._card
        status = c.get("status", "pending")
        border = _DONE if status == "done" else (_BTN_SEC if status == "rejected" else "#585b70")
        self.setFrameShape(QFrame.Shape.NoFrame)
        # 卡片本体：圆角 + 左侧色条 + 悬浮微亮
        self.setStyleSheet(
            f"_TaskCard {{"
            f"  background: #1e1e2e;"
            f"  border: 1px solid {border};"
            f"  border-left: 3px solid {border};"
            f"  border-radius: 8px;"
            f"}}"
            f"_TaskCard:hover {{ border-color: {_ACCENT}; }}"
        )
        self.setMinimumWidth(200)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(5)

        # ---- 标题栏（始终可见，含收起按钮）----
        badge_map = {"pending": ("待确认", _WARN), "done": ("已执行", _DONE), "rejected": ("已忽略", "#6c7086")}
        bt, bc = badge_map.get(status, ("待确认", _WARN))
        head = QHBoxLayout()
        head.setSpacing(5)

        # 收起/展开 chevron
        self._chevron = QPushButton("▾")
        self._chevron.setFixedSize(18, 18)
        self._chevron.setStyleSheet(
            f"QPushButton{{background:transparent; border:none; color:{_SUBTEXT}; font-size:14px;}}"
            f"QPushButton:hover{{color:{_ACCENT};}}"
        )
        self._chevron.setAutoDefault(False)
        self._chevron.setDefault(False)
        self._chevron.clicked.connect(self._on_collapse_toggle)
        head.addWidget(self._chevron)

        lab_badge = QLabel(bt)
        lab_badge.setStyleSheet(
            f"color:#11111b; background:{bc}; border-radius:8px; padding:1px 7px; font-size:10px; font-weight:bold;"
        )
        lab_badge.setFixedHeight(16)
        head.addWidget(lab_badge)

        # 项目标识徽章
        proj_name = str(c.get("project_name") or "")
        if proj_name:
            lab_proj = QLabel(proj_name[:10])
            lab_proj.setStyleSheet(
                f"color:{_TEXT}; background:#2a2a3e; border:1px solid {_BORDER}; "
                f"border-radius:8px; padding:1px 7px; font-size:10px;"
            )
            lab_proj.setFixedHeight(16)
            head.addWidget(lab_proj)

        lab_title = QLabel(self._esc(str(c.get("title") or "未命名改动")))
        lab_title.setStyleSheet(f"color:{_TEXT}; font-size:12px; font-weight:600;")
        lab_title.setWordWrap(True)
        lab_title.mousePressEvent = lambda e: self._on_collapse_toggle()
        head.addWidget(lab_title, 1)
        lay.addLayout(head)

        # ---- 卡片主体（可整体收起）----
        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;")
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 4, 0, 0)
        body_lay.setSpacing(5)

        # ---- 元信息 ----
        meta_rows = [
            ("创建", str(c.get("created_at") or "")),
            ("剧情时间", str(c.get("story_time") or "—")),
            ("范围", str(c.get("chapter_range") or "全局")),
        ]
        for k, v in meta_rows:
            if not v:
                continue
            row = QHBoxLayout()
            row.setSpacing(4)
            lk = QLabel(k)
            lk.setStyleSheet(f"color:{_SUBTEXT}; font-size:10px;")
            lk.setFixedWidth(48)
            lv = QLabel(self._esc(v))
            lv.setStyleSheet(f"color:{_SUBTEXT2}; font-size:11px;")
            lv.setWordWrap(True)
            row.addWidget(lk)
            row.addWidget(lv, 1)
            body_lay.addLayout(row)

        # ---- 涉及实体 chips ----
        scope = c.get("scope") or []
        if scope:
            chips = QLabel(
                "、".join(self._esc(str(s.get("label", ""))) for s in scope[:6])
                + (f" 等 {len(scope)} 项" if len(scope) > 6 else "")
            )
            chips.setStyleSheet(f"color:{_ACCENT}; font-size:10px;")
            chips.setWordWrap(True)
            body_lay.addWidget(chips)

        # ---- 被过滤的无效操作 ----
        filtered = c.get("filtered") or []
        if filtered:
            note = QLabel(
                "以下操作已被软件过滤（目标在库里不存在，属幻觉）：\n"
                + "\n".join("• " + self._esc(str(x)) for x in filtered[:4])
                + (f"\n…另有 {len(filtered) - 4} 条" if len(filtered) > 4 else "")
            )
            note.setStyleSheet("color:#f9a23b; font-size:10px;")
            note.setWordWrap(True)
            body_lay.addWidget(note)

        # ---- 改动对比（子折叠）----
        changes = c.get("changes") or []
        if changes:
            self._toggle_btn = QPushButton(f"▸ 改动对比（{len(changes)} 处）")
            self._toggle_btn.setStyleSheet(
                f"QPushButton{{background:transparent; color:{_ACCENT}; border:none; "
                f"text-align:left; padding:0px; font-size:11px;}}"
                f"QPushButton:hover{{color:{_ACCENT_HOVER};}}"
            )
            self._toggle_btn.setAutoDefault(False)
            self._toggle_btn.setDefault(False)
            self._toggle_btn.clicked.connect(self._on_toggle)
            body_lay.addWidget(self._toggle_btn)

            self._diff = QWidget()
            self._diff.setStyleSheet("background:transparent;")
            dl = QVBoxLayout(self._diff)
            dl.setContentsMargins(8, 2, 8, 2)
            dl.setSpacing(3)
            self._diff.setStyleSheet(
                f"QWidget#_diff_bg{{background:#181825; border:1px solid {_BORDER}; border-radius:4px;}}"
            )
            self._diff.setObjectName("_diff_bg")
            for ch in changes[:12]:
                blk = QVBoxLayout()
                fld = QLabel(
                    f"{self._esc(str(ch.get('scope_label','')))} · {self._esc(str(ch.get('field','')))}"
                )
                fld.setStyleSheet(f"color:{_SUBTEXT2}; font-size:10px;")
                blk.addWidget(fld)
                bef = QLabel(f"改前：{self._esc(str(ch.get('before',''))[:120])}")
                bef.setStyleSheet(f"color:{_SUBTEXT}; font-size:10px;")
                bef.setWordWrap(True)
                aft = QLabel(f"改后：{self._esc(str(ch.get('after',''))[:120])}")
                aft.setStyleSheet(f"color:{_DONE}; font-size:10px;")
                aft.setWordWrap(True)
                blk.addWidget(bef)
                blk.addWidget(aft)
                dl.addLayout(blk)
            if len(changes) > 12:
                more = QLabel(f"…另有 {len(changes) - 12} 处")
                more.setStyleSheet(f"color:{_SUBTEXT}; font-size:10px;")
                dl.addWidget(more)
            self._diff.setVisible(False)
            body_lay.addWidget(self._diff)
        else:
            self._toggle_btn = None
            self._diff = None

        # ---- 执行结果 ----
        if c.get("result"):
            res = QLabel(self._esc(str(c.get("result"))[:200]))
            res.setStyleSheet(f"color:{_DONE}; font-size:10px;")
            res.setWordWrap(True)
            body_lay.addWidget(res)

        # ---- 操作按钮 ----
        if status == "pending":
            brow = QHBoxLayout()
            brow.setSpacing(6)
            b_rej = QPushButton("忽略")
            b_rej.setStyleSheet(
                f"QPushButton{{background:transparent; color:{_SUBTEXT2}; border:1px solid {_BORDER}; "
                f"border-radius:5px; padding:4px 12px; font-size:11px;}}"
                f"QPushButton:hover{{background:{_BTN_SEC}; color:{_TEXT};}}"
            )
            b_app = QPushButton("确认执行")
            b_app.setStyleSheet(
                f"QPushButton{{background:{_DONE}; color:#11111b; border:none; "
                f"border-radius:5px; padding:4px 12px; font-size:11px; font-weight:bold;}}"
                f"QPushButton:hover{{background:#b5e8b0;}}"
            )
            for b in (b_rej, b_app):
                b.setAutoDefault(False)
                b.setDefault(False)
            cid = str(c.get("id") or "")
            b_rej.clicked.connect(lambda _=False, i=cid: self.reject_clicked.emit(i))
            b_app.clicked.connect(lambda _=False, i=cid: self.apply_clicked.emit(i))
            brow.addStretch(1)
            brow.addWidget(b_rej)
            brow.addWidget(b_app)
            body_lay.addLayout(brow)

        lay.addWidget(self._body)

    def _on_collapse_toggle(self):
        """整卡收起/展开。"""
        self._card_collapsed = not self._card_collapsed
        self._body.setVisible(not self._card_collapsed)
        self._chevron.setText("▸" if self._card_collapsed else "▾")

    def _on_toggle(self):
        """改动对比子折叠。"""
        try:
            self._expanded = not self._expanded
            if self._diff is not None:
                self._diff.setVisible(self._expanded)
            if self._toggle_btn is not None:
                n = len(self._card.get("changes") or [])
                self._toggle_btn.setText(
                    f"{'▾' if self._expanded else '▸'} 改动对比（{n} 处）"
                )
        except Exception:
            pass

    @staticmethod
    def _esc(s: str) -> str:
        try:
            from html import escape
            return escape(str(s))
        except Exception:
            return str(s)


class DiscussionPanelWidget(QWidget):
    # 写入数据后通知主窗口刷新记忆面板
    data_changed = pyqtSignal()
    # 用户在下拉里点了「新建小说」，由主窗口弹新建流程（AI 自动初始化新书）
    new_project_requested = pyqtSignal()
    gen_short_outline_requested = pyqtSignal(int)
    gen_deep_outline_requested = pyqtSignal(int)
    write_chapter_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._llm: Optional[LLMClient] = None
        self._config: Optional[Config] = None
        self._project = None
        self._project_root = ""
        self._project_name = ""
        self._pm = None
        self._current_chapter_id = None
        self._memory: Optional[MemoryEngine] = None
        self._agent: Optional[DiscussionAgent] = None
        self._history: list[dict] = []
        self._expanded_indices: set = set()  # 已展开（用户点开）的消息索引；未展开过则默认折叠
        self._context_break_idx: int = 0  # 上下文断点：仅回灌此索引之后的历史；采纳提案写入后前移
        self._cur_assistant = ""
        self._pending_proposal: Optional[dict] = None
        self._task_cards: list[dict] = []   # 右侧任务卡（待确认 / 已执行 / 已忽略）
        self._busy_card_id: str = ""
        self._worker: Optional[DiscussionWorker] = None
        self._busy = False
        self._has_short = False
        self._has_deep = False
        self._has_body = False
        self._pipeline = None
        self._init_ui()

    # ------------------------------------------------------------------
    def set_context(self, llm, config, project, memory, current_chapter_id=None, project_manager=None, pipeline=None):
        self._llm = llm
        self._config = config
        self._project = project
        self._memory = memory
        self._pm = project_manager
        self._pipeline = pipeline
        self._current_chapter_id = current_chapter_id
        self._project_root = getattr(project, "project_root", "") or ""
        self._project_name = (getattr(project, "name", "") or "") if project else ""
        self._agent = DiscussionAgent(llm, config, memory, project_root=self._project_root)
        self._rebuild_project_combo()
        # 打开弹窗：载入当前小说的历史讨论记录（若此前已 _load_project 则幂等重建）
        self._load_history()
        self._load_task_cards()
        self._refresh_gen_state()
        self._render()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 顶部小说选择器（标题区）
        header = QHBoxLayout()
        header.setSpacing(6)
        header_label = QLabel("小说:")
        header_label.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
        self._project_combo = QComboBox()
        self._project_combo.setStyleSheet(
            f"QComboBox{{background:#1e1e2e; color:{_TEXT}; border:1px solid {_BORDER}; "
            f"border-radius:4px; padding:3px 8px; font-size:12px;}}"
        )
        self._project_combo.currentIndexChanged.connect(self._on_project_switched)
        header.addWidget(header_label)
        header.addWidget(self._project_combo, 1)
        root.addLayout(header)

        self._ctx_label = QLabel("（未加载项目）")
        self._ctx_label.setStyleSheet(
            f"color:#a6adc8; font-size:11px; background:transparent; padding:2px 4px;"
        )
        self._ctx_label.setWordWrap(True)
        root.addWidget(self._ctx_label)

        # 聊天流：用可滚动的 QWidget 容器（而非单个 QTextEdit），
        # 每条消息渲染为独立卡片——这样回答内的【标题】/正文/明细才能有真正的
        # 字体层级与可收起交互（代码层强制，不靠 LLM/提示词自觉）。
        # ---- 主体：左（聊天+输入） | 右（任务卡栏）----
        body = QHBoxLayout()
        body.setSpacing(8)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        root.addLayout(body, 1)
        body.addWidget(left, 1)
        body.addWidget(self._build_task_panel())

        self._chat_scroll = QScrollArea()
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setStyleSheet(
            f"background:#181825; border:none;"
        )
        self._chat_content = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_content)
        self._chat_layout.setContentsMargins(8, 8, 8, 8)
        self._chat_layout.setSpacing(8)
        self._chat_scroll.setWidget(self._chat_content)
        lv.addWidget(self._chat_scroll, 1)

        # 提案操作条（改动范围 + 对比图 + 三动作）
        self._prop_bar = QFrame()
        self._prop_bar.setFrameShape(QFrame.Shape.NoFrame)
        self._prop_bar.setStyleSheet(
            f"background:#1e1e2e; border:1px solid #585b70; border-radius:6px;"
        )
        pvb = QVBoxLayout(self._prop_bar)
        pvb.setContentsMargins(10, 8, 10, 8)
        pvb.setSpacing(8)
        self._prop_body = QTextEdit()
        self._prop_body.setReadOnly(True)
        self._prop_body.setStyleSheet(
            f"background:#181825; color:{_TEXT}; border:1px solid {_BORDER}; "
            f"border-radius:6px; font-size:12px;"
        )
        self._prop_body.setFixedHeight(160)
        pvb.addWidget(self._prop_body)
        prop_btn_row = QHBoxLayout()
        prop_btn_row.setSpacing(8)
        self._modify_btn = QPushButton("我要改")
        self._modify_btn.setStyleSheet(
            f"QPushButton{{background:{_BTN_SEC}; color:{_TEXT}; border:none; "
            f"border-radius:4px; padding:5px 12px; font-size:12px;}}"
        )
        self._reject_btn = QPushButton("驳回")
        self._reject_btn.setStyleSheet(
            f"QPushButton{{background:{_BTN_SEC}; color:{_TEXT}; border:none; "
            f"border-radius:4px; padding:5px 12px; font-size:12px;}}"
        )
        self._adopt_btn = QPushButton("采纳并写入")
        self._adopt_btn.setStyleSheet(
            "QPushButton{background:#a6e3a1; color:#11111b; border:none; "
            "border-radius:4px; padding:5px 12px; font-size:12px;}"
        )
        self._modify_btn.clicked.connect(self._on_modify)
        self._reject_btn.clicked.connect(self._on_reject)
        self._adopt_btn.clicked.connect(self._on_adopt)
        # 关键修复：禁用所有按钮的 autoDefault/default，杜绝「回车激活默认按钮
        # 导致讨论对话框被 accept/reject/关闭」这一 Qt 经典坑（Enter 发送改由
        # eventFilter 拦截处理，见 eventFilter）。
        for _b in (self._modify_btn, self._reject_btn, self._adopt_btn):
            _b.setAutoDefault(False)
            _b.setDefault(False)
        prop_btn_row.addStretch(1)
        prop_btn_row.addWidget(self._modify_btn)
        prop_btn_row.addWidget(self._reject_btn)
        prop_btn_row.addWidget(self._adopt_btn)
        pvb.addLayout(prop_btn_row)
        self._prop_bar.setVisible(False)
        lv.addWidget(self._prop_bar)

        # 思考进度状态行（三阶段流水线实时显示，agent 干到哪了）
        self._think_label = QLabel("")
        self._think_label.setStyleSheet(
            f"color:#a6adc8; font-size:11px; background:transparent; "
            f"padding:2px 4px;"
        )
        self._think_label.setWordWrap(True)
        self._think_label.setVisible(False)
        lv.addWidget(self._think_label)

        # ---- 生成工具栏：简大纲→深大纲→正文 ----
        gen_bar = QHBoxLayout()
        gen_bar.setSpacing(6)
        gen_label = QLabel('内容生成:')
        gen_label.setStyleSheet(f'color:{_TEXT}; font-size:12px; font-weight:bold;')
        gen_bar.addWidget(gen_label)

        self._gen_short_btn = QPushButton('📋 简大纲')
        self._gen_short_btn.setStyleSheet(
            f'QPushButton{{background:{_BTN_SEC}; color:{_TEXT}; border:1px solid {_BORDER}; '
            f'border-radius:4px; padding:4px 10px; font-size:12px;}}'
            f'QPushButton:hover{{background:#585b70;}}'
            f'QPushButton:disabled{{background:#181825; color:#45475a;}}'
        )
        self._gen_short_btn.setToolTip('根据框架生成本章简大纲')
        self._gen_short_btn.setAutoDefault(False)
        self._gen_short_btn.clicked.connect(self._on_gen_short)

        self._gen_deep_btn = QPushButton('📖 深大纲')
        self._gen_deep_btn.setStyleSheet(
            f'QPushButton{{background:{_BTN_SEC}; color:{_TEXT}; border:1px solid {_BORDER}; '
            f'border-radius:4px; padding:4px 10px; font-size:12px;}}'
            f'QPushButton:hover{{background:#585b70;}}'
            f'QPushButton:disabled{{background:#181825; color:#45475a;}}'
        )
        self._gen_deep_btn.setToolTip('需要先生成简大纲')
        self._gen_deep_btn.setAutoDefault(False)
        self._gen_deep_btn.clicked.connect(self._on_gen_deep)

        self._gen_body_btn = QPushButton('✍️ 正文')
        self._gen_body_btn.setStyleSheet(
            f'QPushButton{{background:#89b4fa; color:#11111b; border:none; '
            f'border-radius:4px; padding:4px 10px; font-size:12px; font-weight:bold;}}'
            f'QPushButton:hover{{background:#b4befe;}}'
            f'QPushButton:disabled{{background:#181825; color:#45475a;}}'
        )
        self._gen_body_btn.setToolTip('需要先生成简大纲或深大纲')
        self._gen_body_btn.setAutoDefault(False)
        self._gen_body_btn.clicked.connect(self._on_gen_body)

        gen_bar.addWidget(self._gen_short_btn)
        gen_bar.addWidget(self._gen_deep_btn)
        gen_bar.addWidget(self._gen_body_btn)
        gen_bar.addStretch(1)
        lv.addLayout(gen_bar)

        # 输入区
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._input = QTextEdit()
        self._input.setPlaceholderText("和 agent 讨论剧情、人物、伏笔、节奏…（Enter 发送，Shift+Enter 换行）")
        self._input.setStyleSheet(
            f"background:#1e1e2e; color:{_TEXT}; border:1px solid {_BORDER}; "
            f"border-radius:6px; font-size:13px; padding:6px;"
        )
        self._input.setFixedHeight(56)
        self._input.installEventFilter(self)
        input_row.addWidget(self._input, 1)
        col = QVBoxLayout()
        col.setSpacing(6)
        self._send_btn = QPushButton("发送")
        self._send_btn.setStyleSheet(
            "QPushButton{background:#89b4fa; color:#11111b; border:none; "
            "border-radius:4px; padding:6px 16px; font-size:13px;}"
        )
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setAutoDefault(False)
        self._send_btn.setDefault(False)
        col.addWidget(self._send_btn)
        input_row.addLayout(col)
        lv.addLayout(input_row)

    # ------------------------------------------------------------------
    # 内容生成：简大纲→深大纲→正文
    # ------------------------------------------------------------------
    def _refresh_gen_state(self):
        cid = self._current_chapter_id
        if not cid or not self._pipeline:
            self._has_short = self._has_deep = self._has_body = False
        else:
            try:
                ol = self._pipeline.get_chapter_outline(cid)
                self._has_short = bool(ol)
                self._has_deep = bool(ol and ol.get('deep_outline'))
                if self._pm and self._project:
                    try:
                        text = self._pm.get_chapter_text(
                            getattr(self._project, 'project_root', ''), cid)
                        self._has_body = bool(text and text.strip())
                    except Exception:
                        self._has_body = False
                else:
                    self._has_body = False
            except Exception:
                self._has_short = self._has_deep = self._has_body = False
        self._update_gen_buttons()

    def _update_gen_buttons(self):
        cid = self._current_chapter_id
        has_foundation = bool(self._pipeline and getattr(self._pipeline, 'has_foundation', lambda: False)())
        self._gen_short_btn.setEnabled(bool(cid) and has_foundation and not self._has_short)
        self._gen_deep_btn.setEnabled(bool(cid) and self._has_short and not self._has_deep)
        self._gen_body_btn.setEnabled(bool(cid) and self._has_short and not self._has_body)
        if not has_foundation:
            self._gen_short_btn.setToolTip('请先生成整本书框架（阶段0）')
        elif self._has_short:
            self._gen_short_btn.setToolTip('简大纲已生成')
        else:
            self._gen_short_btn.setToolTip('根据框架生成本章简大纲')
        if not self._has_short:
            self._gen_deep_btn.setToolTip('请先生成简大纲')
        elif self._has_deep:
            self._gen_deep_btn.setToolTip('深大纲已生成')
        else:
            self._gen_deep_btn.setToolTip('将简大纲展开为深大纲')
        if not self._has_short:
            self._gen_body_btn.setToolTip('请先生成简大纲或深大纲')
        elif self._has_body:
            self._gen_body_btn.setToolTip('正文已生成')
        else:
            self._gen_body_btn.setToolTip('根据大纲生成本章正文')

    def _on_gen_short(self):
        if not self._current_chapter_id: return
        if not self._pipeline or not self._pipeline.has_foundation():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, '提示', '请先生成整本书框架（阶段0），再生成简大纲。')
            return
        self.gen_short_outline_requested.emit(self._current_chapter_id)

    def _on_gen_deep(self):
        if not self._current_chapter_id: return
        if not self._has_short:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, '提示', '请先生成简大纲，再生成深大纲。')
            return
        self.gen_deep_outline_requested.emit(self._current_chapter_id)

    def _on_gen_body(self):
        if not self._current_chapter_id: return
        if not self._has_short:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, '提示', '请先生成简大纲（或深大纲），再生成正文。')
            return
        self.write_chapter_requested.emit(self._current_chapter_id)

    def on_generation_done(self):
        self._refresh_gen_state()

    # ------------------------------------------------------------------
    # 右侧任务卡栏
    # ------------------------------------------------------------------
    def _build_task_panel(self) -> QWidget:
        """右侧栏：agent 每轮回答里可落地的改动，逐张生成任务卡待确认。

        卡上可见：改动对比（改前→改后，改前值取自 memory 真值）、影响范围
        （章节区间 + 实体清单）、剧情时间（大纲 time_anchor，软件确定性读取）、
        创建时间。确认执行 → 走 apply_proposal（写入前自动备份）。
        """
        panel = QWidget()
        panel.setFixedWidth(330)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        t = QLabel("任务卡")
        t.setStyleSheet(f"color:{_TEXT}; font-size:12px; font-weight:600;")
        head.addWidget(t)
        self._task_count = QLabel("0")
        self._task_count.setStyleSheet(
            "color:#11111b; background:#585b70; border-radius:8px; padding:1px 7px; font-size:10px;"
        )
        self._task_count.setFixedHeight(16)
        head.addWidget(self._task_count)
        head.addStretch(1)
        # 状态分类过滤
        self._task_filter = "all"  # all | pending | done | rejected
        self._task_filter_layout = QHBoxLayout()
        self._task_filter_layout.setSpacing(3)
        _filter_items = [("全部", "all"), ("待确认", "pending"), ("已执行", "done"), ("已忽略", "rejected")]
        self._filter_buttons: dict[str, QPushButton] = {}
        for label, key in _filter_items:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFixedHeight(20)
            btn.setStyleSheet(
                f"QPushButton{{background:{_BTN_SEC}; color:{_TEXT}; border:none; "
                f"border-radius:8px; padding:1px 7px; font-size:10px;}}"
                f"QPushButton:checked{{background:{_ACCENT}; color:#11111b;}}"
            )
            btn.clicked.connect(lambda _=False, k=key: self._on_filter_changed(k))
            self._filter_buttons[key] = btn
            self._task_filter_layout.addWidget(btn)
        self._filter_buttons["all"].setChecked(True)
        filter_widget = QWidget()
        filter_widget.setLayout(self._task_filter_layout)
        filter_widget.setStyleSheet("background:transparent;")
        lay.addWidget(filter_widget)
        head.addStretch(1)
        self._task_fold_btn = QPushButton("收起")
        self._task_fold_btn.setStyleSheet(
            f"QPushButton{{background:transparent; color:{_ACCENT}; border:none; font-size:11px;}}"
        )
        self._task_fold_btn.setAutoDefault(False)
        self._task_fold_btn.setDefault(False)
        self._task_fold_btn.clicked.connect(self._on_toggle_task_panel)
        head.addWidget(self._task_fold_btn)
        self._task_clear_btn = QPushButton("清空已完成")
        self._task_clear_btn.setStyleSheet(
            f"QPushButton{{background:transparent; color:#6c7086; border:none; font-size:11px;}}"
            "QPushButton:hover{color:#f38ba8;}"
        )
        self._task_clear_btn.setAutoDefault(False)
        self._task_clear_btn.setDefault(False)
        self._task_clear_btn.clicked.connect(self._on_clear_task_cards)
        head.addWidget(self._task_clear_btn)
        lay.addLayout(head)

        self._task_scroll = QScrollArea()
        self._task_scroll.setWidgetResizable(True)
        self._task_scroll.setStyleSheet("background:#181825; border:none;")
        self._task_content = QWidget()
        self._task_layout = QVBoxLayout(self._task_content)
        self._task_layout.setContentsMargins(2, 2, 2, 2)
        self._task_layout.setSpacing(6)
        self._task_layout.addStretch(1)
        self._task_scroll.setWidget(self._task_content)
        lay.addWidget(self._task_scroll, 1)

        self._task_empty = QLabel("暂无待确认的改动\n\nagent 在讨论里形成可落地结论后，\n会在这里生成任务卡")
        self._task_empty.setStyleSheet("color:#6c7086; font-size:11px;")
        self._task_empty.setWordWrap(True)
        self._task_empty.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self._task_empty)
        self._task_scroll.setVisible(False)
        return panel

    def _on_filter_changed(self, key: str):
        """切换任务卡状态过滤。"""
        self._task_filter = key
        self._render_task_cards()

    def _on_toggle_task_panel(self):
        try:
            show = not self._task_scroll.isVisible()
            self._task_scroll.setVisible(show)
            self._task_empty.setVisible(not show or not self._task_cards)
            self._task_fold_btn.setText("收起" if show else "展开")
        except Exception:
            pass

    def _render_task_cards(self):
        """按 _task_cards 重建右侧任务卡列表（最新的在最上面，按状态过滤）。"""
        try:
            while self._task_layout.count():
                it = self._task_layout.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        except Exception:
            pass
        # 按状态过滤
        filt = getattr(self, "_task_filter", "all")
        if filt == "all":
            cards = list(reversed(self._task_cards))
        else:
            cards = list(reversed([c for c in self._task_cards if c.get("status") == filt]))
        # 按状态分组渲染（待确认在前，已执行/已忽略在后）
        for c in cards:
            try:
                card = _TaskCard(c)
                card.apply_clicked.connect(self._on_task_apply)
                card.reject_clicked.connect(self._on_task_reject)
                self._task_layout.addWidget(card)
            except Exception:
                import traceback as _tb
                _tb.print_exc()
        self._task_layout.addStretch(1)
        pending = sum(1 for c in self._task_cards if c.get("status") == "pending")
        self._task_count.setText(str(pending) if pending else "0")
        # 更新过滤按钮标签带计数
        counts = {"all": len(self._task_cards), "pending": 0, "done": 0, "rejected": 0}
        for c in self._task_cards:
            st = c.get("status", "pending")
            if st in counts:
                counts[st] += 1
        for key, btn in getattr(self, "_filter_buttons", {}).items():
            label_map = {"all": "全部", "pending": "待确认", "done": "已执行", "rejected": "已忽略"}
            cnt = counts.get(key, 0)
            btn.setText(f"{label_map.get(key, key)}({cnt})")
        has = bool(self._task_cards)
        self._task_empty.setVisible(not has)
        self._task_scroll.setVisible(has)
        self._task_fold_btn.setVisible(has)

    def _on_task_apply(self, card_id: str):
        """确认执行某张任务卡 → apply_proposal 写入（写入前自动备份）。"""
        if self._busy:
            return
        card = next((c for c in self._task_cards if c.get("id") == card_id), None)
        if card is None or card.get("status") != "pending" or self._memory is None:
            return
        try:
            self._busy_card_id = card_id
            self._set_busy(True)
            self._worker = DiscussionWorker(
                self._agent, "apply", [], "",
                proposal={"proposal": card.get("proposal") or []},
                project_root=self._project.project_root if self._project else "",
                memory=self._memory, llm_client=self._llm, parent=self,
            )
            self._worker.finished_signal.connect(self._on_task_apply_done)
            self._worker.error_signal.connect(self._on_error)
            self._worker.start()
        except Exception:
            import traceback as _tb
            _tb.print_exc()
            try:
                self._set_busy(False)
            except Exception:
                pass

    def _on_task_apply_done(self, result: dict):
        """任务卡执行结果：标记卡片状态 + 写入聊天流摘要。"""
        try:
            self._set_busy(False)
            if result.get("type") != "apply":
                return
            cid = getattr(self, "_busy_card_id", "")
            r = result.get("result", {})
            applied = r.get("applied", []) or []
            errors = r.get("errors", []) or []
            guard = r.get("guard_issues", []) or []
            cascade = r.get("cascade_hints", []) or []
            card = next((c for c in self._task_cards if c.get("id") == cid), None)
            if card is not None:
                card["status"] = "done" if applied else "rejected"
                card["result"] = ("；".join(applied)[:160]) if applied else ("；".join(errors)[:160] or "无改动写入")
            msg = "【任务卡执行】\n" + ("\n".join(f"✓ {a}" for a in applied) if applied else "无改动写入。")
            if errors:
                msg += "\n【错误】\n" + "\n".join(f"✗ {e}" for e in errors)
            msg += "\n【guard 校验】" + (
                "\n".join(f"[{g.get('severity','')}] {g.get('description','')}" for g in guard)
                if guard else "未发现矛盾 / 破绽。"
            )
            if cascade:
                msg += "\n【级联同步提示】\n" + "\n".join(f"· {h.get('note','')}" for h in cascade)
            self._append_history("assistant", msg)
            if applied:
                self._context_break_idx = len(self._history) - 1
            self._render()
            self._render_task_cards()
            self._persist_task_cards()
            self._refresh_context_info()
            self._persist_history()
            self.data_changed.emit()
        except Exception:
            import traceback as _tb
            _tb.print_exc()
            try:
                self._set_busy(False)
            except Exception:
                pass

    def _on_task_reject(self, card_id: str):
        card = next((c for c in self._task_cards if c.get("id") == card_id), None)
        if card is None or card.get("status") != "pending":
            return
        try:
            card["status"] = "rejected"
            self._render_task_cards()
            self._persist_task_cards()
        except Exception:
            pass

    def _on_clear_task_cards(self):
        """清空已完成 / 已忽略的卡片，只保留待确认。"""
        try:
            before = len(self._task_cards)
            self._task_cards = [
                c for c in self._task_cards if c.get("status") == "pending"
            ]
            self._render_task_cards()
            self._persist_task_cards()
        except Exception:
            pass

    def _task_cards_path(self) -> str:
        if not self._project_root:
            return ""
        return os.path.join(self._project_root, ".novelforge", "task_cards.json")

    def _persist_task_cards(self):
        """任务卡按小说落盘，重开讨论框可回看历史（最近 50 张）。"""
        p = self._task_cards_path()
        if not p:
            return
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"cards": self._task_cards[-50:]}, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _load_task_cards(self):
        self._task_cards = []
        p = self._task_cards_path()
        if p and os.path.isfile(p):
            try:
                data = json.load(open(p, encoding="utf-8"))
                self._task_cards = list(data.get("cards") or [])
            except Exception:
                self._task_cards = []
        if hasattr(self, "_task_layout"):
            self._render_task_cards()

    def eventFilter(self, obj, event):
        try:
            if obj is self._input and event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Return and not (
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                ):
                    _flow_log("ENTER_via_eventFilter")
                    self._on_send()
                    return True
        except Exception:
            import traceback as _tb
            try:
                _tb.print_exc()
            except Exception:
                pass
            try:
                self._append_history("assistant", "⚠️ 处理按键时出错，请重试")
            except Exception:
                pass
        return super().eventFilter(obj, event)

    # ---- 讨论记录持久化（按小说分开存，切换/重启都保留）----
    def _history_path(self) -> str:
        return os.path.join(self._project_root, ".novelforge", "discussion_history.json")

    def _persist_history(self):
        """把当前小说的讨论记录写盘（最近 200 条，只存 user/assistant，系统提示不落盘）。"""
        if not self._project_root or not self._history:
            return
        try:
            os.makedirs(os.path.join(self._project_root, ".novelforge"), exist_ok=True)
            with open(self._history_path(), "w", encoding="utf-8") as f:
                keep = [m for m in self._history[-200:] if m.get("role") in ("user", "assistant")]
                json.dump({"history": keep}, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _load_history(self):
        """读当前小说的讨论记录并重建渲染（幂等：先清空再加载）。"""
        self._history = []
        if not self._project_root:
            return
        try:
            with open(self._history_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            for m in (data.get("history") or []):
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = str(m.get("content", ""))
                if not content:
                    continue
                self._history.append({"role": role, "content": content})
        except Exception:
            pass
        self._render()

    # ------------------------------------------------------------------
    def _rebuild_project_combo(self):
        """按 project_manager 枚举所有小说填充顶部下拉，并定位当前小说。"""
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        roots: list[str] = []
        if self._pm is not None:
            try:
                for p in self._pm.list_projects() or []:
                    root = getattr(p, "project_root", "") or ""
                    if root and root not in roots:
                        roots.append(root)
                        self._project_combo.addItem(getattr(p, "name", "") or "未命名", root)
            except Exception:
                pass
        # 「新建小说」入口（固定一行，放在列表末尾）
        self._project_combo.addItem("➕ 新建小说…", "__new__")
        idx = self._project_combo.findData(self._project_root)
        if idx >= 0:
            self._project_combo.setCurrentIndex(idx)
            self._project_combo.setVisible(True)
            self._refresh_context_info()
        elif self._project_combo.count() > 0:
            # 主窗口未打开项目 → 默认选第一本并加载
            self._project_combo.setCurrentIndex(0)
            self._project_combo.setVisible(True)
            root0 = self._project_combo.itemData(0)
            if root0 and root0 != self._project_root:
                self._load_project(root0)
            else:
                self._refresh_context_info()
        else:
            # 无可枚举项目：退化使用主窗口传入的 project/memory
            self._project_combo.setVisible(False)
            self._refresh_context_info()
        self._project_combo.blockSignals(False)

    def _on_project_switched(self, index: int):
        try:
            if index < 0:
                return
            root = self._project_combo.itemData(index)
            if root == "__new__":
                # 用户要新建小说：交给主窗口弹新建流程；先把下拉恢复为原选择
                prev = self._project_combo.findData(self._project_root)
                self.new_project_requested.emit()
                if prev >= 0:
                    self._project_combo.blockSignals(True)
                    self._project_combo.setCurrentIndex(prev)
                    self._project_combo.blockSignals(False)
                return
            if root and root != self._project_root and self._pm is not None:
                self._load_project(root)
        except Exception:
            pass

    def refresh_projects(self, select_root: str = ""):
        """主窗口新建/切换小说后调用：重建下拉；select_root 指定选中并加载的小说。"""
        if select_root:
            self._project_root = select_root
            self._rebuild_project_combo()
            self._load_project(select_root)
        else:
            self._rebuild_project_combo()

    def _load_project(self, root: str):
        """切换到指定小说项目：重建 memory/agent，并载入该小说的讨论记录（各存各的）。"""
        self._persist_history()  # 先把当前小说的记录存盘
        try:
            p = self._pm.open_project(root)
        except Exception as e:
            self._append_history("assistant", f"⚠️ 打开小说失败：{e}")
            self._render()
            return
        self._project = p
        self._project_root = root
        self._project_name = (getattr(p, "name", "") or "") if p else ""
        self._current_chapter_id = None
        try:
            self._memory = MemoryEngine(root, self._llm)
        except Exception as e:
            self._memory = None
        self._agent = DiscussionAgent(self._llm, self._config, self._memory, project_root=root)
        self._pending_proposal = None
        self._prop_bar.setVisible(False)
        # 载入该小说的历史记录；无记录时显示切换提示（提示仅内存展示，不落盘）
        self._load_history()
        self._cur_assistant = ""
        if not self._history:
            self._cur_assistant = f"已切换到小说「{self._project_name}」，开始讨论吧。"
        self._refresh_context_info()
        self._render()

    def _refresh_context_info(self):
        if not self._memory:
            self._ctx_label.setText("（未加载项目，仅作通用小说架构讨论）")
            return
        try:
            n_char = len(self._memory.archive.get_all_characters() or [])
            n_fs = len(self._memory.archive.get_active_foreshadows() or [])
            n_ev = len(self._memory.get_story_events() or [])
            name = self._project_name or (getattr(self._project, "name", "") or "") or "未命名"
            n_ch = 0
            try:
                if self._pm is not None:
                    n_ch = len(self._pm.get_chapter_list(self._project_root) or [])
                else:
                    chap_dir = os.path.join(self._project_root, "chapters")
                    if chap_dir and os.path.isdir(chap_dir):
                        n_ch = len([f for f in os.listdir(chap_dir) if f.endswith(".md")])
            except Exception:
                n_ch = 0
            label = f"📖 {name}"
            if n_ch:
                label += f" · {n_ch}章"
            label += f" · 人物 {n_char} · 活跃伏笔 {n_fs} · 事件 {n_ev}（agent 据此讨论）"
            self._ctx_label.setText(label)
        except Exception as e:
            self._ctx_label.setText(f"（读取上下文失败：{e}）")

    def _build_context(self, msg: str) -> str:
        return build_discussion_context(
            self._memory, msg, self._config,
            project_root=self._project_root,
            current_chapter_id=self._current_chapter_id,
        )

    # ---- 渲染（结构化卡片：标题/正文字体分层 + 明细可收起 + 流程/自检默认收起）----
    _HEAD_RE = re.compile(r"^\s*(?:##\s+(.+)|【(.+?)】)\s*$")
    _DETAIL_KW = ("明细", "详情", "对照", "逐章", "完整", "列表", "全表", "数据", "全文")

    def _render(self):
        """重建整条聊天流为结构化卡片（代码层渲染，标题/内容字体分层）。"""
        while self._chat_layout.count():
            w = self._chat_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        for idx, m in enumerate(self._history):
            # 默认折叠（聊天记录收起）；只有「最新一条」与「用户主动展开过」才展开
            last_idx = len(self._history) - 1
            default_collapsed = (idx != last_idx) and (idx not in self._expanded_indices)
            self._chat_layout.addWidget(
                self._build_card(
                    m["role"], m["content"],
                    collapsed=default_collapsed, idx=idx,
                )
            )
        if self._cur_assistant:
            self._chat_layout.addWidget(
                self._build_card("assistant", self._cur_assistant, False, -1)
            )
        self._chat_layout.addStretch(1)
        # 延迟到布局完成后再滚到底：重建大量 widget 后同一事件循环内
        # verticalScrollBar().maximum() 尚未更新为最终高度，立即 setValue 会差一截。
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        sb = self._chat_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---- 文本/标题标签（真正的字体层级，非提示词）----
    @staticmethod
    def _text_label(text: str, size: int, color: str, weight: int = 400) -> QLabel:
        lab = QLabel()
        lab.setWordWrap(True)
        esc = (
            str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        )
        lab.setText(esc)
        lab.setStyleSheet(
            f"color:{color}; font-size:{size}px; font-weight:{weight}; "
            f"background:transparent; line-height:1.55;"
        )
        return lab

    @staticmethod
    def _title_label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet(
            f"color:{_ACCENT}; font-size:13px; font-weight:700; "
            f"background:transparent; margin-top:6px;"
        )
        return lab

    # ---- 可收起区块（点击标题展开/收起）----
    class _Collapsible(QWidget):
        def __init__(self, title: str, parent=None, default_open: bool = False):
            super().__init__(parent)
            self._title = title
            self._body = QWidget()
            bl = QVBoxLayout(self._body)
            bl.setContentsMargins(12, 4, 4, 4)
            bl.setSpacing(4)
            self._body_layout = bl
            self._header = QPushButton()
            self._header.setStyleSheet(
                f"QPushButton{{background:transparent; color:{_ACCENT}; "
                f"border:none; padding:3px 0; "
                f"font-size:12px; text-align:left;}}"
                f"QPushButton:hover{{color:#b4befe;}}"
            )
            self._header.clicked.connect(self._toggle)
            self._open = default_open
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            lay.addWidget(self._header)
            lay.addWidget(self._body)
            self._body.setVisible(self._open)
            self._refresh()

        def _refresh(self):
            self._header.setText(("▾ " if self._open else "▸ ") + self._title)

        def _toggle(self):
            self._open = not self._open
            self._body.setVisible(self._open)
            self._refresh()

        def addWidget(self, w):
            self._body_layout.addWidget(w)

    # ---- 整条消息卡片：可整体折叠（点击头部收起/展开，折叠后只留角色+首行预览）----
    class _FoldableMessage(QWidget):
        def __init__(self, role, content_widget, preview, collapsed=False, on_toggle=None):
            super().__init__()
            self._role = role
            self._preview = preview
            self._collapsed = collapsed
            self._on_toggle = on_toggle
            self._header = QPushButton()
            self._header.setStyleSheet(
                "QPushButton{background:transparent; color:#9399b2; border:none; "
                "padding:2px 0; font-size:11px; text-align:left;}"
                "QPushButton:hover{color:#b4befe;}"
            )
            self._header.clicked.connect(self._toggle)
            self._content = content_widget
            self._content.setVisible(not collapsed)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            lay.addWidget(self._header)
            lay.addWidget(self._content)
            self._refresh_header()

        def _refresh_header(self):
            arrow = "▾ " if not self._collapsed else "▸ "
            role_label = "你" if self._role == "user" else "AI"
            self._header.setText(f"{arrow}{role_label}：{self._preview}")

        def _toggle(self):
            self._collapsed = not self._collapsed
            self._content.setVisible(not self._collapsed)
            self._refresh_header()
            if self._on_toggle:
                self._on_toggle(self._collapsed)

    @staticmethod
    def _msg_preview(text):
        """取消息首行作为折叠预览（跳过流程/自检/标记行）。"""
        for ln in (text or "").split("\n"):
            ln = ln.strip()
            if ln and not ln.startswith(("📌", "📋", "🛠", "🔍", "⚠️", "✅", "▾", "▸", "🔎")):
                return ln[:40] + ("…" if len(ln) > 40 else "")
        return (text or "").strip()[:40]

    # ---- 把一条消息拆成（流程元信息 / 最终回答 / 自检）三段 ----
    @staticmethod
    def _split_assistant(text: str):
        if "📌 最终回答：" in text:
            meta, after = text.split("📌 最终回答：", 1)
            m = re.search(r"(⚠️\s*自检|✅\s*自检|🔍\s*内容自检|⚠️\s*该回答未引用)", after)
            if m:
                answer = after[: m.start()].strip()
                selfcheck = after[m.start():].strip()
            else:
                answer, selfcheck = after.strip(), ""
            return meta.strip(), answer, selfcheck
        return "", text.strip(), ""

    @classmethod
    def _parse_answer_blocks(cls, answer: str):
        """把最终回答拆成结构化块：core(核心结论)/title+body/ detail(可收起明细)。"""
        blocks: list = []
        cur_title = None
        cur_body: list = []

        def flush():
            nonlocal cur_title, cur_body
            if cur_body:
                body = "\n".join(cur_body).strip()
                if body:
                    if cur_title is None:
                        blocks.append(("core", body))
                    elif any(k in cur_title for k in cls._DETAIL_KW):
                        blocks.append(("detail", cur_title, body))
                    else:
                        blocks.append(("title", cur_title))
                        blocks.append(("body", body))
            cur_title = None
            cur_body = []

        for ln in answer.split("\n"):
            m = cls._HEAD_RE.match(ln)
            if m:
                flush()
                cur_title = (m.group(1) or m.group(2)).strip()
            else:
                cur_body.append(ln)
        flush()
        return blocks

    def _build_user_card(self, text: str) -> QWidget:
        lab = self._text_label(text, 13, "#11111b")
        lab.setStyleSheet(
            f"background:{_USER_BG}; color:#11111b; border-radius:8px; "
            f"padding:6px 12px; font-size:13px;"
        )
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(48, 2, 0, 2)
        wl.addStretch(1)
        wl.addWidget(lab)
        return wrap

    def _build_assistant_card(self, text: str) -> QWidget:
        card = QWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(4)
        meta, answer, selfcheck = self._split_assistant(text)
        # 流程/检索元信息：默认收起（用户要求"流程东西别直接显示"）
        if meta:
            col = self._Collapsible("🛠 流程与检索（点击展开）", default_open=False)
            col.addWidget(self._text_label(meta, 11, _TEXT))
            lay.addWidget(col)
        # 自检结果：默认收起
        if selfcheck:
            col = self._Collapsible("🔍 自检结果（点击展开）", default_open=False)
            col.addWidget(self._text_label(selfcheck, 11, _TEXT))
            lay.addWidget(col)
        # 最终回答：核心结论常驻 + 分节标题/正文字体分层 + 明细可收起
        if answer:
            for block in self._parse_answer_blocks(answer):
                if block[0] == "core":
                    lab = self._text_label(block[1], 13, _TEXT, weight=600)
                    lay.addWidget(lab)
                elif block[0] == "title":
                    lay.addWidget(self._title_label(block[1]))
                elif block[0] == "body":
                    lay.addWidget(self._text_label(block[1], 12, _TEXT))
                elif block[0] == "detail":
                    col = self._Collapsible("▸ " + block[1] + "（点击展开明细）", default_open=False)
                    col.addWidget(self._text_label(block[2], 12, _TEXT))
                    lay.addWidget(col)
        return card

    def _build_card(self, role: str, text: str, collapsed: bool = False, idx: int = -1) -> QWidget:
        # 系统提示（role="system"：如「已加载 PATCH-v7」「⏳ 讨论仍在生成中」）：
        # 渲染为居中灰色提示行，不套 AI 消息卡片、不冒充 AI 回答。
        if role == "system":
            lab = QLabel()
            lab.setWordWrap(True)
            esc = (
                str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>")
            )
            lab.setText(esc)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setStyleSheet("color:#6c7086; font-size:11px; background:transparent; padding:2px 0;")
            return lab
        content = self._build_user_card(text) if role == "user" else self._build_assistant_card(text)

        def _on_toggle(state):
            if idx < 0:
                return
            if state:  # 折叠 -> 从展开集合移除
                self._expanded_indices.discard(idx)
            else:       # 展开 -> 加入展开集合
                self._expanded_indices.add(idx)

        return self._FoldableMessage(
            role, content, self._msg_preview(text),
            collapsed=collapsed, on_toggle=_on_toggle,
        )

    @staticmethod
    def _esc(text) -> str:
        return (
            str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    def _append_history(self, role: str, content: str):
        self._history.append({"role": role, "content": content})
        self._render()

    # ---- 动作 ----
    def _set_busy(self, busy: bool):
        self._busy = busy
        self._send_btn.setEnabled(not busy)

    def _on_send(self):
        try:
            if self._agent is None or self._busy:
                return
            text = self._input.toPlainText().strip()
            if not text:
                return
            self._input.clear()
            self._append_history("user", text)
            self._cur_assistant = ""
            self._render()
            ctx = self._build_context(text)
            self._set_busy(True)
            # 智能体模式（三阶段）：规划 → 检索 → 思考；改正文时生成提案卡待确认
            self._worker = DiscussionWorker(
                self._agent, "agent_loop", list(self._history[self._context_break_idx:]), text, ctx,
                chapter_id=self._current_chapter_id or 0, parent=self,
            )
            self._worker.finished_signal.connect(self._on_agent_loop_done)
            self._worker.error_signal.connect(self._on_error)
            self._worker.progress.connect(self._on_think_progress)
            # 启动时显示初始思考状态（worker 阶段回调会更新）
            self._think_label.setText("⏳ 思考中…")
            self._think_label.setVisible(True)
            self._worker.start()
        except Exception as _e:
            import traceback as _tb
            _tb.print_exc()
            try:
                self._set_busy(False)
                self._append_history("assistant", f"⚠️ 发送失败：{_e}")
                self._render()
            except Exception:
                pass

    def _on_think_progress(self, msg: str):
        """三阶段流水线进度：实时显示在思考状态行。"""
        try:
            self._think_label.setText(msg)
            self._think_label.setVisible(True)
        except Exception:
            pass

    def _render_proposal_html(self, view: dict) -> str:
        scope = view.get("scope", []) or []
        changes = view.get("changes", []) or []
        summary = view.get("summary", "")
        parts: list[str] = []
        parts.append(
            '<div style="font-weight:600; color:#cdd6f4; font-size:13px; margin-bottom:6px;">'
            '调整建议 · 改动范围</div>'
        )
        chips = "".join(
            f'<span style="display:inline-block; background:#313244; color:#89b4fa; '
            f'border:1px solid #45475a; border-radius:10px; padding:2px 9px; '
            f'font-size:11px; margin:2px 4px 2px 0;">{self._esc(s["label"])}</span>'
            for s in scope
        ) or '<span style="color:#6c7086; font-size:11px;">（无明确实体）</span>'
        parts.append(f'<div style="margin-bottom:8px;">{chips}</div>')
        if summary:
            parts.append(
                f'<div style="color:#a6adc8; font-size:11px; margin-bottom:8px; '
                f'white-space:pre-wrap;">{self._esc(summary)}</div>'
            )
        if changes:
            parts.append(
                '<div style="font-weight:600; color:#cdd6f4; font-size:12px; margin-bottom:4px;">'
                '调整对比（改前 → 改后）</div>'
            )
            rows = "".join(
                f'<tr>'
                f'<td style="padding:3px 6px; border-top:1px solid #313244; color:#a6adc8; font-size:11px;">'
                f'{self._esc(c["scope_label"])} · {self._esc(c["field"])}</td>'
                f'<td style="padding:3px 6px; border-top:1px solid #313244; color:#6c7086; font-size:11px;">'
                f'{self._esc(c["before"])}</td>'
                f'<td style="padding:3px 6px; border-top:1px solid #313244; color:#a6e3a1; font-size:11px;">'
                f'{self._esc(c["after"])}</td>'
                f'</tr>'
                for c in changes
            )
            parts.append(
                '<table style="border-collapse:collapse; width:100%;">'
                '<tr>'
                '<th style="text-align:left; padding:3px 6px; color:#9399b2; font-size:11px;">字段</th>'
                '<th style="text-align:left; padding:3px 6px; color:#9399b2; font-size:11px;">改前</th>'
                '<th style="text-align:left; padding:3px 6px; color:#9399b2; font-size:11px;">改后</th>'
                '</tr>' + rows + '</table>'
            )
        else:
            parts.append(
                '<div style="color:#6c7086; font-size:11px;">（仅新增类改动，无字段对比）</div>'
            )
        return "".join(parts)

    def _on_agent_loop_done(self, result: dict):
        try:
            self._set_busy(False)
            self._think_label.setVisible(False)
            self._think_label.setText("")
            if result.get("type") != "agent_loop":
                return
            data = result.get("result", {})
            final = str(data.get("content", "") or "")
            # 答案级单轨守卫（输出侧闭环，零 token 确定性改写）：
            # 若 agent 无视已注入的权威单轨设定、凭自身记忆写出双轨/分离主义表述，
            # 在此强制改写成单轨标准说法，保证用户看到的回答一定是单轨。
            try:
                from domains.novel.core.discussion.world_settings_integrity import (
                    enforce_single_track,
                )
                final, _st_changed, _st_issues = enforce_single_track(final)
            except Exception:
                _st_changed, _st_issues = False, []
            # 答案级复读去重（确定性，零 token）：模型在重设定注入下退化复述时，
            # 丢弃与上一句/段相邻的完全相同片段，避免「最后一句话重复 / 列表项重复」。
            final = _dedup_repeat(final)
            plan = data.get("plan", {}) or {}
            ev = data.get("evidence", {}) or {}
            verify = data.get("verify", {}) or {}
            # 流程展示块（任务拆解 / 检索计划 / 检索命中）放在尾部折叠里，
            # 只让『📌 最终回答：』当消息头——避免『📋 任务步骤：核对xxx』冒充标题。
            flow_parts: list[str] = []
            steps = plan.get("steps") or []
            if steps:
                flow_parts.append("📋 任务步骤：" + " → ".join(f"①{s}" for s in steps))
            kws = plan.get("keywords") or []
            if kws:
                flow_parts.append(
                    f"🔍 检索计划：关键词【{'、'.join(str(k) for k in kws[:6])}】· "
                    f"结构={plan.get('structure', 'timeline')}"
                )
            read_chs = ev.get("read_chapters") or []
            scan_n = ev.get("scanned_chapters", 0)
            if read_chs:
                flow_parts.append(
                    f"📚 全书命中 {scan_n} 章 · 精读：{'、'.join(f'第{c}章' for c in read_chs)}"
                )
            # final 空/异常兜底：若 LLM 返回空（_generate_answer 异常/超时/_dedup_repeat 全删），
            # 显式提示而不是把『📌 最终回答：』当头骗人。
            if not final.strip():
                final = "⚠️ 回答生成异常：模型未返回有效文本。请基于下方「📋 任务步骤/🔍 检索/⚠️ 自检」检查检索是否完成，或在追问中补充上下文。"
            answer_part = "📌 最终回答：\n" + final
            selfcheck_parts: list[str] = []
            cited = verify.get("cited") or []
            missing = verify.get("missing") or []
            suspicious = verify.get("suspicious") or []
            if cited:
                if missing or suspicious:
                    warn = []
                    if missing:
                        warn.append(f"引用了不存在的章节：第{'、'.join(str(c) for c in missing)}章")
                    if suspicious:
                        warn.append(f"引用了未检索过的章节（可能不可靠）：第{'、'.join(str(c) for c in suspicious)}章")
                    selfcheck_parts.append("⚠️ 自检：\n" + "\n".join(warn))
                else:
                    selfcheck_parts.append(f"✅ 自检：引用 {len(cited)} 处章节全部真实存在且在证据包内")
            if verify.get("no_citations"):
                selfcheck_parts.append("⚠️ 该回答未引用任何具体章节——若为剧情/设定类问题，请基于检索结果补充证据")
            content_check = verify.get("content") or []
            if content_check:
                c_lines = []
                for c in content_check:
                    cid = c.get("chapter")
                    miss = c.get("missing") or []
                    if miss:
                        c_lines.append(
                            f"⚠️ 第{cid}章 未找到『{'、'.join(str(w) for w in miss)}』——该内容可能不在原章，请核对"
                        )
                    else:
                        c_lines.append(
                            f"✓ 第{cid}章 『{'、'.join(str(w) for w in c.get('words', []))}』在正文中"
                        )
                selfcheck_parts.append("🔍 内容自检：\n" + "\n".join(c_lines))
            # 拼接顺序：答案头条 → 自检 → 流程展示（尾部折叠）
            parts: list[str] = [answer_part] + selfcheck_parts + flow_parts
            self._append_history("assistant", "\n".join(parts))
            self._render()
            self._refresh_context_info()
            self._persist_history()  # 回合完成：讨论记录落盘
            # 本轮可落地的改动 → 右侧任务卡（改前值取 memory 真值，剧情时间取大纲
            # time_anchor，均为软件确定性推导；底部旧提案条保留但不自动弹出）
            tasks = data.get("tasks") or []
            if not tasks:
                # 兼容旧返回：顶层 proposal 也可成卡
                legacy = data.get("proposal") or []
                if legacy:
                    tasks = [{"title": "本轮改动", "chapters": [], "rationale": "", "proposal": legacy}]
            if tasks:
                try:
                    cards = build_task_cards(self._memory, tasks, self._project_root, self._project_name)
                except Exception:
                    import traceback as _tb
                    _tb.print_exc()
                    cards = []
                if cards:
                    self._task_cards.extend(cards)
                    self._render_task_cards()
                    self._persist_task_cards()
            self.data_changed.emit()
        except Exception as _e:
            import traceback as _tb
            _tb.print_exc()
            try:
                self._set_busy(False)
                self._think_label.setVisible(False)
                self._think_label.setText("")
                self._append_history("assistant", f"⚠️ 处理返回结果时出错：{_e}")
                self._render()
            except Exception:
                pass

    def _on_modify(self):
        try:
            self._pending_proposal = None
            self._prop_bar.setVisible(False)
            self._adopt_btn.setEnabled(True)
            self._append_history("user", "（我要调整这条建议，请说明想怎么改）")
            self._render()
            self._input.setFocus()
        except Exception:
            pass

    @staticmethod
    def _fmt_ops(ops: list[dict]) -> str:
        lines = []
        for o in ops:
            kind = o.get("op")
            rationale = o.get("rationale", "")
            if kind == "add_foreshadow":
                lines.append(f"• 新增伏笔：{o.get('description')}（第{o.get('planted_chapter')}章，优先度{o.get('priority')}）— {rationale}")
            elif kind == "update_foreshadow":
                lines.append(f"• 更新伏笔[{o.get('id')}]→状态{o.get('status')} — {rationale}")
            elif kind == "set_event_status":
                lines.append(f"• 事件[{o.get('event_id')}]→{o.get('status')} — {rationale}")
            elif kind == "add_character":
                lines.append(f"• 新增人物：{o.get('name')}（{o.get('role')}）— {rationale}")
            elif kind == "update_character":
                lines.append(f"• 更新人物[{o.get('id')}] — {rationale}")
            elif kind == "edit_chapter_text":
                lines.append(
                    f"• 改写第{o.get('chapter_id')}章正文："
                    f"{str(o.get('before',''))[:30]}… → {str(o.get('after',''))[:30]}… — {rationale}"
                )
            else:
                lines.append(f"• {kind}")
        return "\n".join(lines) if lines else "（无）"

    def _on_adopt(self):
        if self._pending_proposal is None or self._memory is None or self._busy:
            return
        try:
            self._prop_bar.setVisible(False)
            self._adopt_btn.setEnabled(True)
            proposal = self._pending_proposal
            self._pending_proposal = None
            self._append_history("user", "（采纳调整建议，写入数据）")
            self._render()
            self._set_busy(True)
            self._worker = DiscussionWorker(
                self._agent, "apply", [], "",
                proposal=proposal,
                project_root=self._project.project_root if self._project else "",
                memory=self._memory, llm_client=self._llm, parent=self,
            )
            self._worker.finished_signal.connect(self._on_apply_done)
            self._worker.error_signal.connect(self._on_error)
            self._worker.start()
        except Exception as _e:
            import traceback as _tb
            _tb.print_exc()
            try:
                self._set_busy(False)
                self._append_history("assistant", f"⚠️ 采纳失败：{_e}")
                self._render()
            except Exception:
                pass

    def _on_apply_done(self, result: dict):
        try:
            self._set_busy(False)
            if result.get("type") != "apply":
                return
            r = result.get("result", {})
            applied = r.get("applied", [])
            errors = r.get("errors", [])
            guard = r.get("guard_issues", [])
            cascade = r.get("cascade_hints", [])
            msg = "【写入结果】\n" + "\n".join(f"✓ {a}" for a in applied) if applied else "【写入结果】无改动写入。"
            if errors:
                msg += "\n【错误】\n" + "\n".join(f"✗ {e}" for e in errors)
            if guard:
                msg += "\n【guard 校验】\n" + "\n".join(
                    f"[{g.get('severity', '')}] {g.get('description', '')}" for g in guard
                )
            else:
                msg += "\n【guard 校验】未发现矛盾 / 破绽。"
            if cascade:
                msg += "\n【级联同步提示】设定已变更，以下章节大纲引用了相关关键词，建议继续在讨论中调整：\n" + "\n".join(
                    f"· {h.get('note', '')}" for h in cascade
                )
            self._append_history("assistant", msg)
            if applied:
                # 调整节点：数据已写入，在此断开上下文——调整之前的对话不再延续给
                # 后续推理，避免旧设定结论污染新设定下的讨论；UI 仍展示完整历史。
                # 断点设在「写入结果」这条本身，使其后的对话（含写入结果）保持连续。
                self._context_break_idx = len(self._history) - 1
            self._render()
            self._refresh_context_info()
            self._persist_history()  # 写入结果后落盘
            self.data_changed.emit()
        except Exception as _e:
            import traceback as _tb
            _tb.print_exc()
            try:
                self._set_busy(False)
                self._append_history("assistant", f"⚠️ 写入结果处理出错：{_e}")
                self._render()
            except Exception:
                pass

    def _on_reject(self):
        try:
            self._pending_proposal = None
            self._prop_bar.setVisible(False)
            self._adopt_btn.setEnabled(True)
            self._append_history("user", "（驳回调整建议，继续讨论）")
            self._render()
        except Exception:
            pass

    def _on_error(self, err: str):
        try:
            self._cur_assistant = ""
            self._append_history("assistant", f"⚠️ 出错了：{err}")
            self._render()
            self._set_busy(False)
            self._think_label.setVisible(False)
            self._think_label.setText("")
            self._persist_history()
        except Exception:
            # 错误展示自身也失败时，只保证解除忙状态，避免界面卡死
            try:
                self._busy = False
            except Exception:
                pass
