"""
main_window.py — PyQt6 主窗口，四 Tab 布局
支持自定义 API 端点（URL + Key + Model）
内置 AI 对话功能
"""
from __future__ import annotations
import os
import sys
import webbrowser
import json
import uuid
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QTextEdit, QLineEdit,
    QFileDialog, QCheckBox, QScrollArea, QFrame, QProgressBar,
    QComboBox, QGroupBox, QFormLayout, QSplitter, QListWidget,
    QListWidgetItem, QMessageBox, QApplication, QSizePolicy,
    QTextBrowser, QSpinBox, QDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QTextCursor

from ..core.config_manager import load_config, save_config, DEFAULT_CONFIG, COMMON_MODELS
from ..core.pipeline import AnalysisPipeline
from ..core.file_loader import get_sheet_names, detect_file_type
from ..core.llm_client import test_connection, call_llm


THEME_PRESETS = {
    "深色": {
        "bg": "#0f1117", "bg2": "#1a1d27", "bg3": "#23263a",
        "text": "#e8eaf0", "text2": "#9096b0", "text3": "#5a6080",
        "accent": "#4f8ef7", "accent_hover": "#3a7de0",
        "border": "rgba(255,255,255,0.1)", "border2": "rgba(255,255,255,0.12)",
        "input_bg": "#1a1d27", "input_border": "rgba(255,255,255,0.1)",
        "btn_hover": "#2e3248", "btn_press": "#1a1d27",
        "progress_bg": "#23263a", "list_sel": "#23263a",
        "hint": "#5a6080", "header_bg": "#1a1d27",
    },
    "青蓝": {
        "bg": "#0a1628", "bg2": "#0d1f38", "bg3": "#122a4a",
        "text": "#e0f2fe", "text2": "#94a3b8", "text3": "#64748b",
        "accent": "#38bdf8", "accent_hover": "#0ea5e9",
        "border": "rgba(56,189,248,0.15)", "border2": "rgba(56,189,248,0.2)",
        "input_bg": "#0d1f38", "input_border": "rgba(56,189,248,0.12)",
        "btn_hover": "#143660", "btn_press": "#0d1f38",
        "progress_bg": "#122a4a", "list_sel": "#122a4a",
        "hint": "#64748b", "header_bg": "#0d1f38",
    },
    "白色": {
        "bg": "#f8fafc", "bg2": "#ffffff", "bg3": "#f1f5f9",
        "text": "#0f172a", "text2": "#475569", "text3": "#94a3b8",
        "accent": "#2563eb", "accent_hover": "#1d4ed8",
        "border": "#e2e8f0", "border2": "#cbd5e1",
        "input_bg": "#ffffff", "input_border": "#e2e8f0",
        "btn_hover": "#f1f5f9", "btn_press": "#e2e8f0",
        "progress_bg": "#e2e8f0", "list_sel": "#eff6ff",
        "hint": "#94a3b8", "header_bg": "#ffffff",
    },
}


def _get_style(theme: str) -> str:
    t = THEME_PRESETS.get(theme, THEME_PRESETS["深色"])
    return f"""
QMainWindow, QWidget {{ background-color: {t['bg']}; color: {t['text']}; font-family: 'Microsoft YaHei', 'PingFang SC', Arial; font-size: 13px; }}
QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 10px; background: {t['bg2']}; }}
QTabBar::tab {{ background: {t['bg2']}; color: {t['text2']}; padding: 10px 24px; border: none; font-size: 13px; }}
QTabBar::tab:selected {{ background: {t['bg3']}; color: {t['accent']}; border-bottom: 2px solid {t['accent']}; }}
QTabBar::tab:hover {{ background: {t['bg3']}; color: {t['text']}; }}
QGroupBox {{ border: 1px solid {t['border']}; border-radius: 10px; margin-top: 14px; padding-top: 10px; color: {t['text2']}; font-size: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; color: {t['text2']}; }}
QPushButton {{ background: {t['bg3']}; color: {t['text']}; border: 1px solid {t['border2']}; border-radius: 8px; padding: 8px 20px; font-size: 13px; }}
QPushButton:hover {{ background: {t['btn_hover']}; border-color: {t['border2']}; }}
QPushButton:pressed {{ background: {t['btn_press']}; }}
QPushButton#btn_start {{ background: {t['accent']}; color: white; border: none; font-size: 14px; padding: 10px 32px; font-weight: bold; border-radius: 8px; }}
QPushButton#btn_start:hover {{ background: {t['accent_hover']}; }}
QPushButton#btn_start:disabled {{ background: {t['bg3']}; color: {t['text3']}; }}
QLineEdit, QTextEdit {{ background: {t['input_bg']}; border: 1px solid {t['input_border']}; border-radius: 8px; color: {t['text']}; padding: 8px 12px; font-size: 13px; }}
QLineEdit:focus, QTextEdit:focus {{ border-color: {t['accent']}; }}
QComboBox {{ background: {t['input_bg']}; border: 1px solid {t['input_border']}; border-radius: 8px; color: {t['text']}; padding: 6px 12px; min-height: 32px; }}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{ background: {t['input_bg']}; border: 1px solid {t['input_border']}; color: {t['text']}; selection-background-color: {t['list_sel']}; }}
QProgressBar {{ background: {t['progress_bg']}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
QScrollArea {{ border: none; }}
QCheckBox {{ color: {t['text']}; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {t['input_border']}; border-radius: 4px; background: {t['input_bg']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
QLabel {{ color: {t['text']}; }}
QLabel#label_muted {{ color: {t['text3']}; font-size: 12px; }}
QLabel#label_hint {{ color: {t['text2']}; font-size: 12px; }}
QLabel#label_title {{ color: {t['text']}; font-size: 18px; font-weight: bold; }}
QLabel#label_section {{ color: {t['accent']}; font-size: 12px; font-weight: bold; }}
QListWidget {{ background: {t['bg2']}; border: 1px solid {t['border']}; border-radius: 8px; color: {t['text']}; }}
QListWidget::item {{ padding: 8px 12px; border-bottom: 1px solid {t['border']}; }}
QListWidget::item:selected {{ background: {t['list_sel']}; color: {t['accent']}; }}
QSplitter::handle {{ background: {t['border']}; }}
QScrollBar:vertical {{ background: {t['bg']}; width: 6px; border-radius: 3px; }}
QScrollBar::handle:vertical {{ background: {t['text3']}; border-radius: 3px; min-height: 20px; }}
QScrollBar::handle:vertical:hover {{ background: {t['text2']}; }}
QScrollBar:horizontal {{ background: {t['bg']}; height: 6px; border-radius: 3px; }}
QScrollBar::handle:horizontal {{ background: {t['text3']}; border-radius: 3px; min-width: 20px; }}
QScrollBar::handle:horizontal:hover {{ background: {t['text2']}; }}
"""


def _markdown_to_html(text: str) -> str:
    """简易 Markdown → HTML 转换器（适配深色主题气泡）"""
    import html as html_module
    import re

    # 先转义 HTML 特殊字符
    text = html_module.escape(text)

    # 代码块 ```...```
    def code_block_repl(m):
        code = m.group(1)
        # 去掉首尾的换行
        code = code.strip("\n")
        return (
            f'<pre style="background:#0f1117; padding:10px; border-radius:6px; '
            f'border:1px solid rgba(255,255,255,0.08); overflow-x:auto; margin:6px 0; '
            f'font-family:Consolas,monospace; font-size:13px; color:#c0c4d0;">'
            f'<code>{code}</code></pre>'
        )

    text = re.sub(r"```(.*?)```", code_block_repl, text, flags=re.DOTALL)

    # 行内代码 `...`
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background:#0f1117; padding:2px 5px; border-radius:4px; '
        r'font-family:Consolas,monospace; font-size:13px; color:#4f8ef7;">\1</code>',
        text,
    )

    # 粗体 **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # 斜体 *text*（注意：不在列表项开头）
    text = re.sub(r"(?<!\s\*)\*(?!\s)(.+?)\*(?!\s)", r"<i>\1</i>", text)

    # 标题
    text = re.sub(r"^### (.+)$", r'<div style="font-size:15px; font-weight:bold; margin:8px 0 4px; color:#4f8ef7;">\1</div>', text, flags=re.MULTILINE)
    text = re.sub(r"^## (.+)$", r'<div style="font-size:16px; font-weight:bold; margin:10px 0 4px; color:#4f8ef7;">\1</div>', text, flags=re.MULTILINE)
    text = re.sub(r"^# (.+)$", r'<div style="font-size:17px; font-weight:bold; margin:12px 0 4px; color:#4f8ef7;">\1</div>', text, flags=re.MULTILINE)

    # 无序列表
    def ul_repl(m):
        items = m.group(0).strip("\n").split("\n")
        lis = ""
        for item in items:
            item = re.sub(r"^[-*]\s+", "", item)
            if item:
                lis += f'<li style="margin:3px 0;">{item}</li>'
        return f'<ul style="margin:6px 0; padding-left:18px;">{lis}</ul>'

    text = re.sub(r"(?:^[-*]\s+.+\n?)+", ul_repl, text, flags=re.MULTILINE)

    # 有序列表
    def ol_repl(m):
        items = m.group(0).strip("\n").split("\n")
        lis = ""
        for item in items:
            item = re.sub(r"^\d+\.\s+", "", item)
            if item:
                lis += f'<li style="margin:3px 0;">{item}</li>'
        return f'<ol style="margin:6px 0; padding-left:18px;">{lis}</ol>'

    text = re.sub(r"(?:^\d+\.\s+.+\n?)+", ol_repl, text, flags=re.MULTILINE)

    # 链接 [text](url)
    text = re.sub(
        r"\[(.+?)\]\((.+?)\)",
        r'<a href="\2" style="color:#4f8ef7; text-decoration:none;">\1</a>',
        text,
    )

    # 换行 → <br>
    text = text.replace("\n", "<br>")

    return text


class ChatBubble(QWidget):
    """微信风格消息气泡（深色主题），AI 消息支持 Markdown 富文本"""

    def __init__(self, role, content, parent=None):
        super().__init__(parent)
        self.role = role

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        # 头像
        avatar = QLabel()
        avatar.setFixedSize(36, 36)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 消息内容
        self.msg_label = QTextBrowser()
        self.msg_label.setReadOnly(True)
        self.msg_label.setOpenExternalLinks(True)
        self.msg_label.setFrameShape(QFrame.Shape.NoFrame)
        self.msg_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.msg_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.msg_label.setMinimumHeight(36)

        if role == "user":
            avatar.setText("👤")
            avatar.setStyleSheet(
                "background:#4f8ef7; color:white; border-radius:18px; font-size:16px;"
            )
            self.msg_label.setStyleSheet(
                "background:#4f8ef7; color:white; padding:10px 14px; "
                "border-radius:12px 12px 4px 12px; font-size:14px; line-height:1.5;"
            )
            layout.addStretch()
            layout.addWidget(self.msg_label, alignment=Qt.AlignmentFlag.AlignTop)
            layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)
        else:
            avatar.setText("🤖")
            avatar.setStyleSheet(
                "background:#34c77b; color:white; border-radius:18px; font-size:16px;"
            )
            self.msg_label.setStyleSheet(
                "background:#23263a; color:#e8eaf0; padding:10px 14px; "
                "border-radius:12px 12px 12px 4px; font-size:14px; line-height:1.5; "
                "border:1px solid rgba(255,255,255,0.1);"
            )
            layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)
            layout.addWidget(self.msg_label, alignment=Qt.AlignmentFlag.AlignTop)
            layout.addStretch()

        self.set_content(content)

    def set_content(self, text):
        if self.role == "assistant":
            html = _markdown_to_html(text)
            self.msg_label.setHtml(html)
            # 根据内容自动调整高度
            doc = self.msg_label.document()
            doc.setTextWidth(self.msg_label.viewport().width() or 600)
            h = int(doc.size().height()) + 24
            self.msg_label.setMinimumHeight(min(h, 600))
            self.msg_label.setMaximumHeight(2000)
        else:
            self.msg_label.setPlainText(text)


class AiLogBubble(QWidget):
    """AI 日志气泡 —— 以对话形式展示 Prompt（蓝色）和 Response（绿色）"""

    def __init__(self, call_idx: int, role: str, model: str, call_time: str,
                 prompt: str, response: str, elapsed: float = None,
                 error: str = None, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(6)

        # ── 标题行 ───────────────────────────────────────────
        header_text = f"#{call_idx:02d}  {role.upper()}  ·  {model}  ·  {call_time}"
        if elapsed is not None:
            if error:
                header_text += f"  ❌ 失败 ({elapsed:.1f}s)"
            else:
                header_text += f"  ✅ {elapsed:.1f}s"
        header = QLabel(header_text)
        header.setStyleSheet(
            "color:#9096b0; font-size:11px; font-weight:bold; "
            "border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:4px;"
        )
        outer.addWidget(header)

        # ── Prompt 气泡（右对齐，蓝色，用户侧） ─────────────
        prompt_row = QHBoxLayout()
        prompt_row.setSpacing(8)
        p_avatar = QLabel("📤")
        p_avatar.setFixedSize(28, 28)
        p_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p_avatar.setStyleSheet(
            "background:#4f8ef7; border-radius:14px; font-size:13px;"
        )
        p_lbl = QTextBrowser()
        p_lbl.setReadOnly(True)
        p_lbl.setFrameShape(QFrame.Shape.NoFrame)
        p_lbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        p_lbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        p_lbl.setStyleSheet(
            "background:rgba(79,142,247,0.15); color:#c0d4ff; padding:8px 12px; "
            "border-radius:10px 10px 3px 10px; font-size:12px; font-family:Consolas,monospace; "
            "border:1px solid rgba(79,142,247,0.25);"
        )

        # 完整显示 Prompt，不截断
        import html as _html_mod
        p_lbl.setPlainText(prompt)
        # 自动高度 + 展开收起
        p_lbl.document().setTextWidth(480)
        ph = int(p_lbl.document().size().height()) + 20

        # 展开/收起按钮（当内容较长时才有意义）
        _prompt_full_height = min(ph, 2000)  # 上限保护
        _prompt_collapsed = ph > 350  # 超过 350px 默认折叠

        def _toggle_prompt():
            nonlocal _prompt_collapsed
            if _prompt_collapsed:
                p_lbl.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
                p_lbl.setMinimumHeight(min(_prompt_full_height, 400))
                p_toggle_btn.setText("▲ 收起 Prompt")
            else:
                p_lbl.setMaximumHeight(350)
                p_lbl.setMinimumHeight(min(ph, 200))
                p_toggle_btn.setText(f"▼ 展开完整 Prompt ({len(prompt)}字符)")
            _prompt_collapsed = not _prompt_collapsed

        p_toggle_btn = QPushButton(f"▼ 展开完整 Prompt ({len(prompt)}字符)" if _prompt_collapsed else "▲ 收起 Prompt")
        p_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        p_toggle_btn.setStyleSheet(
            "color:#4f8ef7; background:transparent; border:none; font-size:11px; "
            "padding:2px 6px; text-align:left;"
        )
        p_toggle_btn.clicked.connect(_toggle_prompt)

        # 初始状态：长内容折叠
        if _prompt_collapsed:
            p_lbl.setMaximumHeight(350)
            p_lbl.setMinimumHeight(min(ph, 200))
        else:
            p_lbl.setMaximumHeight(16777215)
            p_lbl.setMinimumHeight(min(_prompt_full_height, 400))

        prompt_col = QVBoxLayout()
        prompt_col.setSpacing(2)
        prompt_col.addWidget(p_lbl)
        prompt_col.addWidget(p_toggle_btn)

        prompt_row.addStretch()
        prompt_row.addLayout(prompt_col, 1)
        prompt_row.addWidget(p_avatar, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(prompt_row)

        # ── Response 气泡（左对齐，绿色，AI侧） ─────────────
        resp_row = QHBoxLayout()
        resp_row.setSpacing(8)
        r_avatar = QLabel("🤖")
        r_avatar.setFixedSize(28, 28)
        r_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        r_lbl = QTextBrowser()
        r_lbl.setReadOnly(True)
        r_lbl.setFrameShape(QFrame.Shape.NoFrame)
        r_lbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        r_lbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        if error:
            r_avatar.setStyleSheet(
                "background:rgba(245,81,81,0.8); border-radius:14px; font-size:13px;"
            )
            r_lbl.setStyleSheet(
                "background:rgba(245,81,81,0.12); color:#f55151; padding:8px 12px; "
                "border-radius:10px 10px 10px 3px; font-size:12px; font-family:Consolas,monospace; "
                "border:1px solid rgba(245,81,81,0.25);"
            )
            resp_text = f"❌ 调用失败\n{error}"
            _resp_long = False
        else:
            r_avatar.setStyleSheet(
                "background:#34c77b; border-radius:14px; font-size:13px;"
            )
            r_lbl.setStyleSheet(
                "background:rgba(52,199,123,0.1); color:#c0ffd8; padding:8px 12px; "
                "border-radius:10px 10px 10px 3px; font-size:12px; font-family:Consolas,monospace; "
                "border:1px solid rgba(52,199,123,0.2);"
            )
            # 完整显示 Response，不截断
            resp_text = response or "（等待响应...）"
            _resp_long = len(resp_text) > 500

        r_lbl.setPlainText(resp_text)
        r_lbl.document().setTextWidth(480)
        rh = int(r_lbl.document().size().height()) + 20

        # 展开/收起按钮
        _resp_full_height = min(rh, 2000)
        _resp_collapsed = rh > 350 and _resp_long and not error

        def _toggle_resp():
            nonlocal _resp_collapsed
            if _resp_collapsed:
                r_lbl.setMaximumHeight(16777215)
                r_lbl.setMinimumHeight(min(_resp_full_height, 400))
                r_toggle_btn.setText("▲ 收起响应")
            else:
                r_lbl.setMaximumHeight(350)
                r_lbl.setMinimumHeight(min(rh, 200))
                r_toggle_btn.setText(f"▼ 展开完整响应 ({len(resp_text)}字符)")
            _resp_collapsed = not _resp_collapsed

        r_toggle_btn = QPushButton(f"▼ 展开完整响应 ({len(resp_text)}字符)" if _resp_collapsed else "▲ 收起响应")
        r_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        r_toggle_btn.setStyleSheet(
            "color:#34c77b; background:transparent; border:none; font-size:11px; "
            "padding:2px 6px; text-align:left;"
        )
        r_toggle_btn.clicked.connect(_toggle_resp)

        if _resp_collapsed:
            r_lbl.setMaximumHeight(350)
            r_lbl.setMinimumHeight(min(rh, 200))
        elif error:
            r_lbl.setMinimumHeight(min(rh, 120))
            r_lbl.setMaximumHeight(600)
            r_toggle_btn.hide()  # 错误时不需要展开按钮
        else:
            r_lbl.setMaximumHeight(16777215)
            r_lbl.setMinimumHeight(min(_resp_full_height, 400))

        resp_col = QVBoxLayout()
        resp_col.setSpacing(2)
        resp_col.addWidget(r_lbl)
        resp_col.addWidget(r_toggle_btn)

        resp_row.addWidget(r_avatar, 0, Qt.AlignmentFlag.AlignTop)
        resp_row.addLayout(resp_col, 1)
        resp_row.addStretch()
        outer.addLayout(resp_row)

        # ── 分隔线 ─────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:rgba(255,255,255,0.06);")
        outer.addWidget(sep)


class ThinkingWidget(QWidget):
    """AI 思考中动画（深色主题）"""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        avatar = QLabel("🤖")
        avatar.setFixedSize(36, 36)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            "background:#34c77b; color:white; border-radius:18px; font-size:16px;"
        )

        # 气泡容器
        bubble = QWidget()
        bubble.setStyleSheet(
            "background:#23263a; border-radius:12px 12px 12px 4px; border:1px solid rgba(255,255,255,0.1);"
        )
        bubble_layout = QHBoxLayout(bubble)
        bubble_layout.setContentsMargins(16, 10, 16, 10)
        bubble_layout.setSpacing(6)

        self.dots = []
        colors = ["#5a6080", "#9096b0", "#c0c4d0"]
        for i in range(3):
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{colors[i]}; font-size:10px;")
            self.dots.append(dot)
            bubble_layout.addWidget(dot)

        layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addWidget(bubble, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addStretch()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._animate)
        self.timer.start(400)
        self._step = 0

    def _animate(self):
        colors = ["#5a6080", "#9096b0", "#c0c4d0"]
        for i, dot in enumerate(self.dots):
            idx = (self._step + i) % 3
            dot.setStyleSheet(f"color:{colors[idx]}; font-size:10px;")
        self._step = (self._step + 1) % 3

    def stop(self):
        self.timer.stop()


class AnalysisWorker(QThread):
    progress = pyqtSignal(int, int, str)
    log = pyqtSignal(str, str)  # (message, step_tag)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    # 逐次调用（保留用于文件日志，GUI不再逐条展示）
    ai_log = pyqtSignal(int, str, str, str, str, str, float, str)
    # 一次分析完成时发射汇总：session_info dict
    analysis_done = pyqtSignal(dict)

    def __init__(self, file_path, sheets, data_desc, analysis_focus,
                 validation_rules="", data_has_anomalies=False,
                 analysis_mode="auto", direct_token_threshold=30000):
        super().__init__()
        self.file_path = file_path
        self.sheets = sheets
        self.data_desc = data_desc
        self.analysis_focus = analysis_focus
        self.validation_rules = validation_rules
        self.data_has_anomalies = data_has_anomalies
        self.analysis_mode = analysis_mode
        self.direct_token_threshold = direct_token_threshold

    def run(self):
        pipeline = AnalysisPipeline(
            progress_callback=lambda s, t, m: self.progress.emit(s, t, m),
            log_callback=lambda m, **kw: self.log.emit(m, kw.get("step_tag", "")),
            ai_log_callback=lambda ci, ro, mo, ct, pr, rs, el, er: self.ai_log.emit(ci, ro, mo, ct, pr, rs, el, er),
            analysis_done_callback=lambda info: self.analysis_done.emit(info),
        )
        try:
            report_path = pipeline.run(
                file_path=self.file_path,
                sheets=self.sheets,
                data_description=self.data_desc,
                analysis_focus=self.analysis_focus,
                validation_rules=self.validation_rules,
                data_has_anomalies=self.data_has_anomalies,
                analysis_mode=self.analysis_mode,
                direct_token_threshold=self.direct_token_threshold,
            )
            self.finished.emit(report_path)
        except Exception as e:
            import traceback as tb
            tb_detail = tb.format_exc()
            # 通过 log signal 输出完整 traceback 到日志区域和文件
            self.log.emit(f"\n{'='*60}\n[致命错误] 分析过程抛出未处理异常:\n{tb_detail}\n{'='*60}", "error")
            # error signal 保留原始消息（用于弹窗）
            self.error.emit(f"{e}\n\n(完整错误信息已输出到上方日志区)")


class ChatWorker(QThread):
    """AI 对话工作线程"""
    response = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, base_url, model, api_key, messages, system_prompt, thinking=False):
        super().__init__()
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.messages = messages
        self.system_prompt = system_prompt
        self.thinking = thinking

    def run(self):
        try:
            # 构建 prompt（将消息历史组合）
            full_prompt = self._build_prompt()
            result = call_llm(
                base_url=self.base_url,
                model=self.model,
                api_key=self.api_key,
                prompt=full_prompt,
                system=self.system_prompt,
                temperature=0.7,
                max_tokens=8192,
                thinking=self.thinking,
            )
            self.response.emit(result)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()

    def _build_prompt(self):
        """将消息历史构建为 prompt"""
        lines = []
        for msg in self.messages:
            if msg["role"] == "user":
                lines.append(f"用户：{msg['content']}")
            elif msg["role"] == "assistant":
                lines.append(f"助手：{msg['content']}")
        return "\n\n".join(lines)



class TweakDialog(QDialog):
    """微调报告对话框：让用户输入微调描述"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("微调报告")
        self.setMinimumSize(560, 320)
        theme = getattr(parent, 'current_theme', '深色') if parent else '深色'
        self.setStyleSheet(_get_style(theme))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("用文字描述你想如何调整报告")
        title.setStyleSheet("font-size:14px; font-weight:bold; color:#e8eaf0;")
        layout.addWidget(title)

        hint = QLabel(
            "示例：\n"
            "• 结论里加上同比分析\n"
            "• 第一个图表从饼图改成柱状图\n"
            "• 标题加上'2025年Q1'后缀\n"
            "• 所有 KPI 加上环比数据\n"
            "• 重点分析某个品类的增长趋势"
        )
        hint.setStyleSheet("color:#5a6080; font-size:11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.txt_tweak = QTextEdit()
        self.txt_tweak.setPlaceholderText("请描述你想如何调整报告...")
        self.txt_tweak.setFixedHeight(100)
        layout.addWidget(self.txt_tweak)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(80)
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("开始微调")
        btn_ok.setObjectName("btn_start")
        btn_ok.setFixedWidth(120)
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def get_tweak_description(self) -> str:
        return self.txt_tweak.toPlainText().strip()


class TweakWorker(QThread):
    """后台运行微调的 Worker"""
    finished = pyqtSignal(str)   # 报告路径
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)  # 百分比, 消息

    def __init__(self, config, analysis_json, summary_json, tweak_description,
                 data_description="", analysis_focus="", output_dir=None):
        super().__init__()
        self.config = config
        self.analysis_json = analysis_json
        self.summary_json = summary_json
        self.tweak_description = tweak_description
        self.data_description = data_description
        self.analysis_focus = analysis_focus
        self.output_dir = output_dir

    def run(self):
        try:
            from core.pipeline import AnalysisPipeline
            pipeline = AnalysisPipeline(self.config)
            pipeline.on_progress = self.progress.emit
            report_path = pipeline.run_tweak(
                analysis_json=self.analysis_json,
                summary_json=self.summary_json,
                tweak_description=self.tweak_description,
                data_description=self.data_description,
                analysis_focus=self.analysis_focus,
                output_dir=self.output_dir,
            )
            self.finished.emit(report_path)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智能数据分析工具")
        self.setMinimumSize(1000, 720)
        self.resize(1100, 760)
        self.current_theme = "深色"
        self.setStyleSheet(_get_style(self.current_theme))

        self.config = {**DEFAULT_CONFIG, **load_config()}
        self.selected_file = ""
        self.sheet_checkboxes = []
        self.worker = None

        # 对话相关
        self.chat_worker = None
        self.chat_sessions = {}  # {session_id: {"title": str, "messages": []}}
        self.current_session_id = None
        self.thinking_widget = None  # 初始化思考动画 widget

        # 微调报告相关
        self.last_analysis_json = None
        self.last_summary_json = None
        self.last_session_dir = None
        self.btn_tweak = None
        self.tweak_worker = None

        # AI 日志面板相关
        self._ai_log_layout = None       # 日志容器布局，build_chat_tab 时赋值
        self._ai_log_empty_lbl = None    # 空状态标签
        self._ai_log_scroll = None       # 日志滚动区

        self._build_ui()
        self._load_chat_sessions()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部标题栏
        header = QWidget()
        header.setObjectName("header_bar")
        header.setFixedHeight(56)
        header.setStyleSheet("background:transparent; border-bottom:1px solid rgba(255,255,255,0.08);")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(24, 0, 24, 0)
        title = QLabel("智能数据分析工具")
        title.setStyleSheet("font-size:18px; font-weight:bold; color:#e8eaf0;")
        subtitle = QLabel("AI 驱动 · 多 Sheet · 自动报告")
        subtitle.setStyleSheet("font-size:12px; color:#5a6080; margin-left:12px;")
        h_layout.addWidget(title)
        h_layout.addWidget(subtitle)
        h_layout.addStretch()

        # 主题切换
        theme_label = QLabel("主题")
        theme_label.setStyleSheet("color:#5a6080; font-size:12px;")
        h_layout.addWidget(theme_label)
        self.cmb_theme = QComboBox()
        self.cmb_theme.addItems(["深色", "青蓝", "白色"])
        self.cmb_theme.setFixedWidth(90)
        self.cmb_theme.currentTextChanged.connect(self._on_theme_changed)
        h_layout.addWidget(self.cmb_theme)
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(tabs)

        tabs.addTab(self._build_analysis_tab(), "  分析任务  ")
        tabs.addTab(self._build_settings_tab(), "  API 配置  ")
        tabs.addTab(self._build_history_tab(), "  历史记录  ")
        tabs.addTab(self._build_chat_tab(), "  AI 对话  ")

    def _on_theme_changed(self, theme_name):
        """切换主题"""
        self.current_theme = theme_name
        self.setStyleSheet(_get_style(theme_name))
        # 重新设置标题栏背景（跟随主题）
        t = THEME_PRESETS[theme_name]
        for child in self.centralWidget().children():
            if isinstance(child, QWidget) and child.objectName() == "header_bar":
                child.setStyleSheet(f"background:{t['header_bg']}; border-bottom:1px solid {t['border']};")
                break

    # ── Tab 1: 分析任务 ──────────────────────────────────────
    def _build_analysis_tab(self):
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：文件 + Sheet 选择（包裹在 QScrollArea 中支持滚轮）
        left_scroll = QScrollArea()
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        left = QWidget()
        left.setFixedWidth(380)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        file_group = QGroupBox("1. 选择数据文件")
        fg_layout = QVBoxLayout(file_group)
        fg_layout.setContentsMargins(16, 20, 16, 16)
        fg_layout.setSpacing(12)

        file_btn_row = QHBoxLayout()
        btn_file = QPushButton("浏览选择...")
        btn_file.setFixedWidth(110)
        btn_file.setMinimumHeight(34)
        btn_file.clicked.connect(self._pick_file)
        file_btn_row.addWidget(btn_file)
        file_btn_row.addStretch()  # 按钮左对齐，不拉伸填满
        fg_layout.addLayout(file_btn_row)

        self.lbl_file = QLabel("未选择文件")
        self.lbl_file.setStyleSheet("color:#5a6080; font-size:13px;")
        self.lbl_file.setWordWrap(False)
        # 文件路径滚动容器，支持滚轮查看长路径
        file_path_scroll = QScrollArea()
        file_path_scroll.setWidgetResizable(True)
        file_path_scroll.setFixedHeight(36)
        file_path_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        file_path_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        file_path_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        file_path_scroll.setWidget(self.lbl_file)
        fg_layout.addWidget(file_path_scroll)

        # 文件预览标签（类型 / Token 预估 / 分析模式）
        self.lbl_file_preview = QLabel(" ")
        self.lbl_file_preview.setStyleSheet(
            "color:#34c77b; font-size:12px; padding:6px 12px; "
            "background:rgba(52,199,123,0.08); border-radius:6px;"
        )
        self.lbl_file_preview.setWordWrap(True)
        fg_layout.addWidget(self.lbl_file_preview)

        # 分析模式选择
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(10)
        mode_layout.addWidget(QLabel("分析模式："))
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["脚本分析（推荐）", "直接分析（跳过脚本）"])
        self.cmb_mode.setMinimumWidth(200)
        self.cmb_mode.setMinimumHeight(32)
        mode_layout.addWidget(self.cmb_mode)
        mode_layout.addStretch()
        fg_layout.addLayout(mode_layout)

        left_layout.addWidget(file_group)

        sheet_group = QGroupBox("2. 选择要分析的 Sheet（可多选）")
        self.sheet_group = sheet_group  # 保存引用，供 _load_sheets 隐藏/显示
        sg_layout = QVBoxLayout(sheet_group)
        sg_layout.setContentsMargins(12, 16, 12, 12)
        sg_layout.setSpacing(6)

        sheet_btn_row = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_all.clicked.connect(self._select_all_sheets)
        btn_none = QPushButton("取消")
        btn_none.clicked.connect(self._deselect_all_sheets)
        sheet_btn_row.addWidget(btn_all)
        sheet_btn_row.addWidget(btn_none)
        sheet_btn_row.addStretch()
        sg_layout.addLayout(sheet_btn_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(120)
        self.sheet_container = QWidget()
        self.sheet_layout = QVBoxLayout(self.sheet_container)
        self.sheet_layout.setContentsMargins(4, 4, 4, 4)
        self.sheet_layout.setSpacing(4)
        self.sheet_layout.addStretch()
        scroll.setWidget(self.sheet_container)
        sg_layout.addWidget(scroll)
        left_layout.addWidget(sheet_group)
        left_layout.addStretch()

        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)

        # 右侧：数据说明 + 分析重点（支持滚动自适应）
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        desc_group = QGroupBox("3. 数据说明（帮助 AI 理解数据边界，防止错误结论）")
        dg_layout = QVBoxLayout(desc_group)
        dg_layout.setContentsMargins(12, 16, 12, 12)
        hint1 = QLabel("例：数据时间范围 / 单位说明 / 特殊字段含义 / 已知缺陷")
        hint1.setStyleSheet("color:#5a6080; font-size:11px;")
        dg_layout.addWidget(hint1)
        self.txt_desc = QTextEdit()
        self.txt_desc.setPlaceholderText(
            "示例：\n"
            "数据时间范围：2024年1月～12月\n"
            "数据来源：ERP系统，仅含已成交订单\n"
            "注意：金额单位为万元；Q4数据含预估值\n"
            "渠道字段：1=线上，2=线下"
        )
        self.txt_desc.setFixedHeight(130)
        dg_layout.addWidget(self.txt_desc)
        right_layout.addWidget(desc_group)

        focus_group = QGroupBox("4. 分析重点与参考结论（引导 AI 分析方向，可选）")
        foc_layout = QVBoxLayout(focus_group)
        foc_layout.setContentsMargins(12, 16, 12, 12)
        hint2 = QLabel("例：重点关注指标 / 初步假设 / 需验证的问题 / 忽略的数据 / 图表偏好")
        hint2.setStyleSheet("color:#5a6080; font-size:11px;")
        foc_layout.addWidget(hint2)
        self.txt_focus = QTextEdit()
        self.txt_focus.setPlaceholderText(
            "示例：\n"
            "重点关注：月度销售趋势、TOP渠道对比\n"
            "参考结论：初步判断Q3增长与促销活动相关\n"
            "请验证：不同城市级别的转化率差异\n"
            "图表偏好：用饼图展示渠道占比，用折线图展示月度趋势\n"
            "忽略：用户名含'test'的测试数据\n"
            "\n"
            "支持的图表类型：\n"
            "  bar(柱状图) | line(折线图) | pie(饼图)\n"
            "  scatter(散点图) | bubble(气泡图)\n"
            "  funnel(漏斗图) | waterfall(瀑布图)\n"
            "  radar(雷达图) | gauge(仪表盘)\n"
            "  rose(玫瑰图) | boxplot(箱型图)\n"
            "  map(地图)\n"
            "\n"
            "AI 会主动从数据中发现更多有价值的信息"
        )
        self.txt_focus.setFixedHeight(160)
        foc_layout.addWidget(self.txt_focus)
        right_layout.addWidget(focus_group)

        # ── 数据校验与异常标记 ──
        valid_group = QGroupBox("5. 数据校验与异常标记")
        vg_layout = QVBoxLayout(valid_group)
        vg_layout.setContentsMargins(12, 16, 12, 12)
        vg_layout.setSpacing(8)

        hint3 = QLabel("用于自动校验 AI 生成脚本的统计结果是否准确，不匹配时自动重试")
        hint3.setStyleSheet("color:#5a6080; font-size:11px;")
        vg_layout.addWidget(hint3)

        self.txt_validation = QTextEdit()
        self.txt_validation.setPlaceholderText(
            "可选。每行一个校验规则，格式：sheet名.字段=预期值\n"
            "支持简写：直接写 字段=预期值（自动匹配第一个 Sheet）\n"
            "示例：\n"
            "间凌=10808                ← 简写，自动匹配 sheet1\n"
            "sheet1.rows=5000          ← 指定 Sheet\n"
            "sheet1.total_销售额=1234567.89\n"
            "sheet1.销售额.mean=5000.00\n"
            "\n"
            "支持：rows / cols / total_列名(总和) / mean_列名(均值)"
        )
        self.txt_validation.setFixedHeight(90)
        vg_layout.addWidget(self.txt_validation)

        self.chk_data_anomaly = QCheckBox("⚠️ 数据存在已知异常")
        self.chk_data_anomaly.setStyleSheet("color:#e8eaf0; font-size:12px;")
        self.chk_data_anomaly.setChecked(False)
        self.chk_data_anomaly.setToolTip(
            "勾选：数据有异常，AI 结论标注「初步判断」「可能」等不确定性措辞\n"
            "不勾选：数据已确认正确，AI 给出确定性结论"
        )
        vg_layout.addWidget(self.chk_data_anomaly)

        hint4 = QLabel(
            "勾选此项 → AI 结论用「初步判断」「可能」等措辞，不给绝对断言\n"
            "不勾选   → 数据已确认正确，AI 可直接给出确定性结论"
        )
        hint4.setStyleSheet("color:#5a6080; font-size:11px; padding-left:4px;")
        vg_layout.addWidget(hint4)

        right_layout.addWidget(valid_group)
        right_layout.addStretch()

        right_scroll.setWidget(right)
        splitter.addWidget(right_scroll)
        splitter.setSizes([360, 600])
        outer.addWidget(splitter)

        # 开始分析按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self.btn_start = QPushButton("开始分析 →")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.setFixedHeight(44)
        self.btn_start.clicked.connect(self._start_analysis)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_start)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        # 进度区
        progress_group = QGroupBox("执行进度")
        pg_layout = QVBoxLayout(progress_group)
        pg_layout.setContentsMargins(12, 16, 12, 12)
        pg_layout.setSpacing(8)

        steps_row = QHBoxLayout()
        steps_row.setSpacing(8)
        self.step_labels = []
        self.step_buttons = []  # 可点击的步骤过滤按钮
        step_names = ["读取表头", "生成脚本", "数据提炼", "AI 分析", "生成报告"]
        step_tags = ["step1", "step2", "step3", "step4", "step5"]
        for i, (name, tag) in enumerate(zip(step_names, step_tags)):
            btn = QPushButton(f"○ {name}")
            btn.setCheckable(True)
            btn.setChecked(False)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("step_tag", tag)  # 自定义属性：步骤标签
            btn.setStyleSheet("""
                QPushButton {
                    color:#5a6080; font-size:12px; padding:4px 10px; background:#1a1d27;
                    border:1px solid rgba(255,255,255,0.06); border-radius:4px;
                }
                QPushButton:hover { background:#23263a; color:#c8ccd8; border-color:rgba(79,142,247,0.3); }
                QPushButton:checked { color:#4f8ef7; background:rgba(79,142,247,0.12); border-color:rgba(79,142,247,0.5); }
            """)
            btn.clicked.connect(lambda checked, t=tag: self._filter_log_by_step(t))
            self.step_labels.append(btn)  # 兼容原有进度更新逻辑（setText/setStyleSheet）
            self.step_buttons.append(btn)
            steps_row.addWidget(btn)

        # 全部日志按钮
        btn_all = QPushButton("全部")
        btn_all.setCheckable(True)
        btn_all.setChecked(True)  # 默认选中
        btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_all.setStyleSheet("""
            QPushButton {
                color:#e8eaf0; font-size:12px; padding:4px 12px; background:rgba(79,142,247,0.15);
                border:1px solid rgba(79,142,247,0.4); border-radius:4px; font-weight:bold;
            }
            QPushButton:hover { background:rgba(79,142,247,0.25); }
            QPushButton:checked { color:#fff; background:rgba(79,142,247,0.35); border-color:#4f8ef7; }
        """)
        btn_all.clicked.connect(lambda: self._filter_log_by_step(""))
        steps_row.addStretch()
        steps_row.addWidget(btn_all)
        self.btn_all_logs = btn_all
        pg_layout.addLayout(steps_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)
        pg_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("等待开始...")
        self.lbl_status.setStyleSheet("color:#9096b0; font-size:12px;")
        pg_layout.addWidget(self.lbl_status)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFixedHeight(160)
        self.log_area.setStyleSheet(
            "background:#0a0c14; color:#6a7090; font-size:12px; "
            "font-family:'Consolas','Courier New',monospace; border-radius:6px;"
        )
        pg_layout.addWidget(self.log_area)

        self.btn_open = QPushButton("打开报告 ↗")
        self.btn_open.setVisible(False)
        self.btn_open.setStyleSheet("background:#34c77b; color:white; border:none; border-radius:6px; padding:8px 24px; font-size:13px;")
        self.btn_open.clicked.connect(self._open_report)
        pg_layout.addWidget(self.btn_open, alignment=Qt.AlignmentFlag.AlignCenter)

        self.btn_tweak = QPushButton("微调报告 ✎")
        self.btn_tweak.setVisible(False)
        self.btn_tweak.setStyleSheet(
            "background:#f59e0b; color:white; border:none;"
            "border-radius:6px; padding:8px 24px; font-size:13px;"
        )
        self.btn_tweak.clicked.connect(self._open_tweak_dialog)
        pg_layout.addWidget(self.btn_tweak, alignment=Qt.AlignmentFlag.AlignCenter)

        outer.addWidget(progress_group)
        return widget

    # ── Tab 2: API 配置（v2：命名接口配置模式）────────────────
    def _build_settings_tab(self):
        widget = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("API 接口配置")
        title.setStyleSheet("font-size:16px; font-weight:bold; color:#e8eaf0;")
        layout.addWidget(title)
        hint = QLabel("先定义命名的 API 接口配置，再在各步骤中选择使用哪个接口。支持任意 OpenAI 兼容服务。")
        hint.setStyleSheet("color:#5a6080; font-size:12px;")
        layout.addWidget(hint)

        # ════════════════════════════════════════════════
        # 区域 A：接口配置编辑/新增
        # ════════════════════════════════════════════════
        edit_grp = QGroupBox("接口配置（编辑 / 新增）")
        edit_layout = QVBoxLayout(edit_grp)
        edit_layout.setContentsMargins(16, 16, 16, 12)
        edit_layout.setSpacing(8)

        # 名称行
        name_row = QHBoxLayout()
        name_lbl = QLabel("名称:")
        name_lbl.setFixedWidth(50)
        self.profile_name_input = QLineEdit()
        self.profile_name_input.setPlaceholderText("如 GLM-4.6 直连、Gemini 中转 ...")
        self.profile_name_input.setFixedHeight(30)
        self.profile_name_input.setStyleSheet(_input_style())
        name_row.addWidget(name_lbl)
        name_row.addWidget(self.profile_name_input, 1)
        btn_add_profile = QPushButton("+ 添加/更新")
        btn_add_profile.setFixedHeight(30)
        btn_add_profile.setMinimumWidth(120)
        btn_add_profile.setStyleSheet(_btn_green_style())
        btn_add_profile.clicked.connect(self._add_or_update_profile)
        name_row.addWidget(btn_add_profile)
        edit_layout.addLayout(name_row)

        # URL 行
        url_row = QHBoxLayout()
        url_lbl = QLabel("URL:")
        url_lbl.setFixedWidth(50)
        self.profile_url_input = QLineEdit()
        self.profile_url_input.setPlaceholderText("https://open.bigmodel.cn/api/paas/v4/")
        self.profile_url_input.setFixedHeight(30)
        self.profile_url_input.setStyleSheet(_input_style())
        url_row.addWidget(url_lbl)
        url_row.addWidget(self.profile_url_input, 1)
        edit_layout.addLayout(url_row)

        # Key 行
        key_row = QHBoxLayout()
        key_lbl = QLabel("Key:")
        key_lbl.setFixedWidth(50)
        self.profile_key_input = QLineEdit()
        self.profile_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.profile_key_input.setPlaceholderText("sk-...")
        self.profile_key_input.setFixedHeight(30)
        self.profile_key_input.setStyleSheet(_input_style())
        key_row.addWidget(key_lbl)
        key_row.addWidget(self.profile_key_input, 1)
        btn_test_profile = QPushButton("测试连接")
        btn_test_profile.setFixedHeight(30)
        btn_test_profile.setMinimumWidth(100)
        btn_test_profile.setStyleSheet(_btn_blue_style())
        btn_test_profile.clicked.connect(self._test_profile_connection)
        key_row.addWidget(btn_test_profile)
        edit_layout.addLayout(key_row)

        # 模型 + 超时行
        model_timeout_row = QHBoxLayout()
        mt_lbl1 = QLabel("模型:")
        mt_lbl1.setFixedWidth(50)
        self.profile_model_combo = QComboBox()
        self.profile_model_combo.setEditable(True)
        self.profile_model_combo.setFixedHeight(30)
        self.profile_model_combo.addItems(COMMON_MODELS)
        self.profile_model_combo.setStyleSheet(_combo_style())
        mt_lbl2 = QLabel("超时:")
        mt_lbl2.setFixedWidth(40)
        self.profile_timeout_spin = QSpinBox()
        self.profile_timeout_spin.setRange(30, 1800)
        self.profile_timeout_spin.setSuffix(" 秒")
        self.profile_timeout_spin.setSingleStep(30)
        self.profile_timeout_spin.setValue(300)
        self.profile_timeout_spin.setFixedHeight(30)
        timeout_hint2 = QLabel("慢速模型建议 300~600s")
        timeout_hint2.setStyleSheet("color:#5a6080; font-size:11px;")
        model_timeout_row.addWidget(mt_lbl1)
        model_timeout_row.addWidget(self.profile_model_combo, 1)
        model_timeout_row.addWidget(mt_lbl2)
        model_timeout_row.addWidget(self.profile_timeout_spin)
        model_timeout_row.addWidget(timeout_hint2)
        self.profile_thinking_check = QCheckBox("深度思考")
        self.profile_thinking_check.setChecked(False)
        self.profile_thinking_check.setToolTip("启用后，部分模型（如 DeepSeek R1）会输出思考过程，提升复杂推理质量")
        self.profile_thinking_check.setStyleSheet("QCheckBox { color: #e8eaf0; } QCheckBox::indicator:hover { border: 1px solid #4f8ef7; }")
        model_timeout_row.addWidget(self.profile_thinking_check)
        edit_layout.addLayout(model_timeout_row)

        layout.addWidget(edit_grp)

        # ════════════════════════════════════════════════
        # 区域 B：已保存的接口列表
        # ════════════════════════════════════════════════
        list_grp = QGroupBox("已保存的接口列表")
        list_grp_layout = QVBoxLayout(list_grp)
        list_grp_layout.setContentsMargins(16, 16, 16, 12)
        list_grp_layout.setSpacing(6)

        self.profile_list_widget = QListWidget()
        self.profile_list_widget.setFixedHeight(120)
        self.profile_list_widget.setStyleSheet(_list_style())
        # 双击编辑
        self.profile_list_widget.itemDoubleClicked.connect(self._load_profile_to_editor)
        _load_profiles_to_list(self.profile_list_widget, self.config)
        list_grp_layout.addWidget(self.profile_list_widget)

        # 列表操作行
        list_op_row = QHBoxLayout()
        list_op_row.addStretch()
        btn_edit = QPushButton("✏ 编辑选中")
        btn_edit.setFixedHeight(26)
        btn_edit.setMinimumWidth(110)
        btn_edit.setStyleSheet(_btn_outline_style())
        btn_edit.clicked.connect(lambda: self._load_profile_to_editor(self.profile_list_widget.currentItem()))
        btn_del_profile = QPushButton("- 删除选中")
        btn_del_profile.setFixedHeight(26)
        btn_del_profile.setMinimumWidth(110)
        btn_del_profile.setStyleSheet(_btn_danger_style())
        btn_del_profile.clicked.connect(self._delete_selected_profile)
        list_op_row.addWidget(btn_edit)
        list_op_row.addWidget(btn_del_profile)
        list_grp_layout.addLayout(list_op_row)

        layout.addWidget(list_grp)

        # ════════════════════════════════════════════════
        # 区域 C：各步骤选择使用的接口
        # ════════════════════════════════════════════════
        self.step_profile_combos = {}  # {step_key: QComboBox}
        step_defs = [
            ("script", "Step 2 · 生成提炼脚本", "建议使用代码生成能力强的接口，如 Gemini"),
            ("analysis", "Step 4 · 深度推理分析", "建议使用推理能力强的接口，如 GLM-4.6、DeepSeek R1"),
            ("report", "Step 5 · 生成 HTML 报告", "建议使用代码生成能力强的接口"),
        ]

        for step_key, step_title, step_hint in step_defs:
            grp = QGroupBox(step_title)
            grp_layout = QHBoxLayout(grp)
            grp_layout.setContentsMargins(16, 14, 16, 14)

            use_lbl = QLabel("使用接口:")
            use_lbl.setFixedWidth(60)
            profile_combo = QComboBox()
            profile_combo.setFixedHeight(30)
            # 填充接口名称列表
            _populate_profile_names(profile_combo, self.config)
            # 恢复已选择的 profile
            current = self.config.get(f"step_{step_key}_profile", "")
            if current:
                idx = profile_combo.findText(current)
                if idx >= 0:
                    profile_combo.setCurrentIndex(idx)
                else:
                    profile_combo.setCurrentText(current)
            grp_layout.addWidget(use_lbl)
            grp_layout.addWidget(profile_combo, 1)
            hint_lbl2 = QLabel(step_hint)
            hint_lbl2.setStyleSheet("color:#5a6080; font-size:11px;")
            grp_layout.addWidget(hint_lbl2)

            self.step_profile_combos[step_key] = profile_combo
            layout.addWidget(grp)

        # 输出目录
        out_group = QGroupBox("输出目录")
        out_layout = QHBoxLayout(out_group)
        out_layout.setContentsMargins(16, 16, 16, 16)
        self.txt_output_dir = QLineEdit()
        self.txt_output_dir.setText(self.config.get("output_dir", str(Path.home() / "data_analyst_output")))
        self.txt_output_dir.setStyleSheet(_input_style())
        btn_choose_dir = QPushButton("选择...")
        btn_choose_dir.setStyleSheet(_btn_blue_style())
        btn_choose_dir.clicked.connect(self._choose_output_dir)
        out_layout.addWidget(self.txt_output_dir)
        out_layout.addWidget(btn_choose_dir)
        layout.addWidget(out_group)

        # 保存按钮
        btn_save = QPushButton("保存配置")
        btn_save.setStyleSheet("background:#4f8ef7; color:white; border:none; border-radius:6px; padding:8px 28px; font-size:13px; font-weight:bold;")
        btn_save.clicked.connect(self._save_settings)
        layout.addWidget(btn_save, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

        scroll.setWidget(container)
        outer2 = QVBoxLayout(widget)
        outer2.addWidget(scroll)
        return widget

    def _test_profile_connection(self):
        """测试当前编辑框中的接口连接"""
        url = self.profile_url_input.text().strip()
        key = self.profile_key_input.text().strip()
        model = self.profile_model_combo.currentText().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请先填写 URL")
            return
        if not key:
            QMessageBox.warning(self, "提示", "请先填写 API Key")
            return
        if not model:
            QMessageBox.warning(self, "提示", "请先选择/输入模型名称")
            return
        ok, msg = test_connection(url, model, key)
        if ok:
            QMessageBox.information(self, "连接测试", f"✓ {msg}")
        else:
            QMessageBox.warning(self, "连接失败", f"✗ {msg}")

    def _add_or_update_profile(self):
        """添加新接口或更新已有接口"""
        name = self.profile_name_input.text().strip()
        url = self.profile_url_input.text().strip()
        key = self.profile_key_input.text().strip()
        model = self.profile_model_combo.currentText().strip()
        timeout = self.profile_timeout_spin.value()
        thinking = self.profile_thinking_check.isChecked()

        if not name:
            QMessageBox.warning(self, "提示", "请输入接口名称")
            return
        if not url:
            QMessageBox.warning(self, "提示", "请输入 API URL")
            return

        profiles = self.config.get("api_profiles", [])
        # 检查是否已存在同名（更新模式）
        updated = False
        for i, p in enumerate(profiles):
            if p.get("name") == name:
                profiles[i] = {"name": name, "url": url, "key": key, "model": model, "timeout": timeout, "thinking": thinking}
                updated = True
                break
        if not updated:
            profiles.append({"name": name, "url": url, "key": key, "model": model, "timeout": timeout, "thinking": thinking})

        self.config["api_profiles"] = profiles
        # 刷新列表 UI
        _refresh_profile_list(self.profile_list_widget, profiles)
        # 刷新所有步骤的下拉框（含 AI 对话）
        _refresh_all_profile_combos(
            [self.step_profile_combos.get(k) for k in ("script", "analysis", "report")]
            + ([self.chat_profile_combo] if hasattr(self, 'chat_profile_combo') else []),
            profiles, name
        )
        # 清空输入框
        self.profile_name_input.clear()
        self.profile_key_input.clear()

    def _load_profile_to_editor(self, item):
        """双击或点击编辑按钮 → 将选中接口加载到编辑框"""
        if not item:
            return
        name = item.text()
        profiles = self.config.get("api_profiles", [])
        for p in profiles:
            if p.get("name") == name:
                self.profile_name_input.setText(p.get("name", ""))
                self.profile_url_input.setText(p.get("url", ""))
                self.profile_key_input.setText(p.get("key", ""))
                self.profile_model_combo.setCurrentText(p.get("model", ""))
                self.profile_timeout_spin.setValue(p.get("timeout", 300))
                self.profile_thinking_check.setChecked(bool(p.get("thinking", False)))
                break

    def _delete_selected_profile(self):
        """删除选中的接口配置"""
        item = self.profile_list_widget.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选中要删除的接口")
            return
        name = item.text()
        reply = QMessageBox.question(self, "确认删除",
                                      f"确定要删除接口「{name}」吗？\n使用该接口的步骤将需要重新选择。",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        profiles = self.config.get("api_profiles", [])
        self.config["api_profiles"] = [p for p in profiles if p.get("name") != name]
        _refresh_profile_list(self.profile_list_widget, self.config["api_profiles"])
        # 从各步骤下拉框中移除（AI 对话用模型选择，不受 profile 删除影响）
        for combo in list(self.step_profile_combos.values()):
            idx = combo.findText(name)
            if idx >= 0:
                combo.removeItem(idx)

    # ── Tab 3: 历史记录 ──────────────────────────────────────
    def _build_history_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("历史分析记录")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        layout.addWidget(title)

        btn_refresh = QPushButton("刷新列表")
        btn_refresh.clicked.connect(self._load_history)
        layout.addWidget(btn_refresh, alignment=Qt.AlignmentFlag.AlignLeft)

        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(self._open_history_item)
        layout.addWidget(self.history_list)

        hint = QLabel("双击条目可打开对应报告 · 报告文件保存在输出目录中")
        hint.setStyleSheet("color:#5a6080; font-size:11px;")
        layout.addWidget(hint)
        layout.addStretch()

        self._load_history()
        return widget

    # ── 事件处理 ─────────────────────────────────────────────
    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择数据文件", "",
            "所有支持文件 (*.xlsx *.xls *.csv *.docx *.doc);;"
            "Excel 文件 (*.xlsx *.xls);;"
            "Word 文档 (*.docx *.doc);;"
            "CSV 文件 (*.csv);;"
            "所有文件 (*.*)"
        )
        if not path:
            return
        self.selected_file = path
        self.lbl_file.setText(path)
        self.lbl_file.setStyleSheet("color:#4f8ef7; font-size:12px;")

        # 轻量文件信息（不读取内容，仅显示大小和类型）
        try:
            import os
            size_kb = os.path.getsize(path) // 1024
            if size_kb >= 1024:
                size_str = f"{size_kb / 1024:.1f} MB"
            else:
                size_str = f"{size_kb} KB"
            file_type = detect_file_type(path)
            type_map = {"excel": "Excel", "csv": "CSV", "word": "Word文档"}
            type_name = type_map.get(file_type, "未知文件")
            self.lbl_file_preview.setText(f"📄 {type_name} | 文件大小：{size_str}")
            self.lbl_file_preview.setStyleSheet(
                "color:#9096b0; font-size:11px; padding:6px 10px; "
                "background:rgba(255,255,255,0.03); border-radius:4px;"
            )
        except Exception:
            self.lbl_file_preview.setText("")

        # 清空旧 Sheet 选择，加载新文件的 Sheet 列表
        while self.sheet_layout.count() > 1:
            item = self.sheet_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.sheet_checkboxes.clear()
        self._load_sheets(path)

    def _show_file_preview(self, preview: dict):
        """根据文件预览信息更新 UI 显示"""
        file_type = preview.get("file_type", "?")
        sheets = preview.get("sheets", [])
        multi_sheets = preview.get("multi_header_sheets", [])
        tokens = preview.get("estimated_tokens", 0)
        size_kb = preview.get("size_kb", 0)

        # 文件类型标签
        type_map = {"excel": "Excel", "csv": "CSV", "word": "Word文档", "unknown": "未知文件"}
        type_name = type_map.get(file_type, file_type)

        # 预估 Token
        if tokens >= 1000:
            token_str = f"{tokens // 1000}K"
        else:
            token_str = str(tokens)

        # 分析模式判断
        if file_type == "word":
            if tokens == 0:
                mode_hint = "【直接分析】Word 文档（.doc 旧格式，分析时将自动转换）"
                color = "rgba(79,142,247,0.12)"
                text_color = "#4f8ef7"
            else:
                mode_hint = "【直接分析】Word 文档，跳过脚本执行"
                color = "rgba(79,142,247,0.12)"
                text_color = "#4f8ef7"
        elif multi_sheets:
            mode_hint = f"【直接分析】{len(multi_sheets)}个 Sheet 为多表头，跳过脚本执行"
            color = "rgba(255,165,0,0.12)"
            text_color = "#e69500"
        else:
            threshold_k = 30
            if tokens < threshold_k * 1000:
                mode_hint = f"【直接分析】预估 {token_str} tokens < {threshold_k}K，直接分析"
                color = "rgba(52,199,123,0.08)"
                text_color = "#34c77b"
            else:
                mode_hint = f"【脚本分析】预估 {token_str} tokens ≥ {threshold_k}K，将执行脚本统计"
                color = "rgba(255,165,0,0.08)"
                text_color = "#e69500"

        # 多表头提示
        multi_hint = ""
        if multi_sheets:
            multi_hint = f"\n⚠️ 多表头 Sheet：{', '.join(multi_sheets)}"

        # Sheet 信息
        sheet_info = ""
        if file_type == "word":
            sheet_info = f"段落/表格数：{preview.get('row_count', '?')}"
        else:
            sheet_info = f"共 {len(sheets)} 个 Sheet | 文件大小：{size_kb} KB"

        self.lbl_file_preview.setText(
            f"📄 {type_name} | {sheet_info} | 预估 {token_str} tokens\n"
            f"{mode_hint}{multi_hint}"
        )
        self.lbl_file_preview.setStyleSheet(
            f"color:{text_color}; font-size:11px; padding:6px 10px; "
            f"background:{color}; border-radius:4px; line-height:1.5;"
        )

    def _load_sheets(self, path: str):
        while self.sheet_layout.count() > 1:
            item = self.sheet_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.sheet_checkboxes.clear()

        file_type = detect_file_type(path)

        # Word 文件：隐藏 Sheet 选择，显示提示
        if file_type == "word":
            hint = QLabel("Word 文档，将直接分析内容（无需 Sheet 选择）")
            hint.setStyleSheet("color:#4f8ef7; font-size:12px; padding:8px; background:rgba(79,142,247,0.1); border-radius:4px;")
            self.sheet_layout.insertWidget(self.sheet_layout.count() - 1, hint)
            self.sheet_group.setVisible(False)
            return

        # Excel/CSV：显示正常 Sheet 选择
        self.sheet_group.setVisible(True)
        try:
            sheets = get_sheet_names(path)
            for s in sheets:
                cb = QCheckBox(s)
                cb.setChecked(True)
                self.sheet_layout.insertWidget(self.sheet_layout.count() - 1, cb)
                self.sheet_checkboxes.append(cb)
        except Exception as e:
            QMessageBox.warning(self, "读取失败", f"无法读取文件 Sheet 列表：{e}")

    def _select_all_sheets(self):
        for cb in self.sheet_checkboxes:
            cb.setChecked(True)

    def _deselect_all_sheets(self):
        for cb in self.sheet_checkboxes:
            cb.setChecked(False)

    def _start_analysis(self):
        if not self.selected_file:
            QMessageBox.warning(self, "提示", "请先选择数据文件")
            return

        # Word 文件：自动使用 ["Word文档"] 作为 sheets
        file_type = detect_file_type(self.selected_file)
        if file_type == "word":
            selected_sheets = ["Word文档"]
        else:
            selected_sheets = [cb.text() for cb in self.sheet_checkboxes if cb.isChecked()]
            if not selected_sheets:
                QMessageBox.warning(self, "提示", "请至少选择一个 Sheet")
                return

        # 分析模式
        mode_map = {
            0: "script",
            1: "direct",
        }
        analysis_mode = mode_map.get(self.cmb_mode.currentIndex(), "script")

        # 检查至少一个步骤的 API 已配置（v2: 通过 api_profiles 检查）
        from core.config_manager import resolve_profile
        has_any_api = False
        for step_key in ("script", "analysis", "report"):
            profile_name = self.config.get(f"step_{step_key}_profile", "")
            prof = resolve_profile(self.config, profile_name)
            if prof.get("url") and prof.get("key") and prof.get("model"):
                has_any_api = True
                break
        if not has_any_api:
            QMessageBox.warning(self, "提示", "请先在「API 配置」Tab 至少配置一个步骤的 API")
            return

        self.btn_start.setEnabled(False)
        self.btn_open.setVisible(False)
        if hasattr(self, 'btn_tweak') and self.btn_tweak:
            self.btn_tweak.setVisible(False)
        self.log_area.clear()
        self.progress_bar.setValue(0)
        self.report_path = None
        # 重置日志列表和过滤状态
        self._all_logs = []
        self._current_log_filter = ""
        # 重置步骤按钮为未选中状态
        if hasattr(self, 'step_buttons'):
            for btn in self.step_buttons:
                btn.setChecked(False)
                btn.setStyleSheet("""
                    QPushButton {
                        color:#5a6080; font-size:12px; padding:4px 10px; background:#1a1d27;
                        border:1px solid rgba(255,255,255,0.06); border-radius:4px;
                    }
                    QPushButton:hover { background:#23263a; color:#c8ccd8; border-color:rgba(79,142,247,0.3); }
                    QPushButton:checked { color:#4f8ef7; background:rgba(79,142,247,0.12); border-color:rgba(79,142,247,0.5); }
                """)
        if hasattr(self, 'btn_all_logs'):
            self.btn_all_logs.setChecked(True)

        default_names = ["读取表头", "生成脚本", "数据提炼", "AI 分析", "生成报告"]
        for i, lbl in enumerate(self.step_labels):
            name = default_names[i] if i < len(default_names) else "未知"
            lbl.setText(f"○ {name}")
            lbl.setStyleSheet("color:#5a6080; font-size:12px; padding:4px 10px; background:#1a1d27; border:1px solid rgba(255,255,255,0.06); border-radius:4px;")

        self.worker = AnalysisWorker(
            file_path=self.selected_file,
            sheets=selected_sheets,
            data_desc=self.txt_desc.toPlainText(),
            analysis_focus=self.txt_focus.toPlainText(),
            validation_rules=self.txt_validation.toPlainText(),
            data_has_anomalies=self.chk_data_anomaly.isChecked(),
            analysis_mode=analysis_mode,
            direct_token_threshold=30000,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._on_log)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.analysis_done.connect(self._append_analysis_session)
        self.worker.start()

    def _on_progress(self, step: int, total: int, msg: str):
        pct = int(step / total * 100)
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(f"Step {step}/{total}  {msg}")

        # 根据总步数选择对应的步骤名称
        if total == 3:
            names = ["解析文件", "AI 分析", "生成报告"]  # 直接分析路径
        else:
            names = ["读取表头", "生成脚本", "数据提炼", "AI 分析", "生成报告"]  # 脚本分析路径

        for i, lbl in enumerate(self.step_labels):
            if i < len(names):
                if i < step - 1:
                    lbl.setText(f"✓ {names[i]}")
                    lbl.setStyleSheet("color:#34c77b; font-size:12px; padding:4px 10px; background:rgba(52,199,123,0.1); border:1px solid rgba(52,199,123,0.3); border-radius:4px;")
                elif i == step - 1:
                    lbl.setText(f"● {names[i]}")
                    lbl.setStyleSheet("color:#4f8ef7; font-size:12px; padding:4px 10px; background:rgba(79,142,247,0.12); border:1px solid rgba(79,142,247,0.5); border-radius:4px;")
                else:
                    lbl.setText(f"○ {names[i]}")
                    lbl.setStyleSheet("color:#5a6080; font-size:12px; padding:4px 10px; background:#1a1d27; border:1px solid rgba(255,255,255,0.06); border-radius:4px;")

    def _log_to_status(self, msg: str):
        """向状态栏和日志区域同时输出一条消息"""
        self.lbl_status.setText(msg)
        self._on_log(msg, step_tag="")

    def _on_log(self, msg: str, step_tag: str = ""):
        """接收 pipeline 日志，追加到日志列表并根据当前过滤状态显示"""
        # 保存到完整日志列表（含步骤标签）
        if not hasattr(self, '_all_logs'):
            self._all_logs = []
            self._current_log_filter = ""  # 当前过滤标签，"" = 显示全部
        self._all_logs.append((step_tag, msg))

        # 根据过滤条件决定是否显示
        if self._current_log_filter == "" or step_tag == self._current_log_filter:
            self.log_area.append(msg)
            scrollbar = self.log_area.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _filter_log_by_step(self, tag: str):
        """按步骤标签过滤日志显示"""
        self._current_log_filter = tag

        # 更新按钮选中状态
        if tag == "":
            # 显示全部
            self.btn_all_logs.setChecked(True)
            for btn in self.step_buttons:
                btn.setChecked(False)
        else:
            self.btn_all_logs.setChecked(False)
            for btn in self.step_buttons:
                if btn.property("step_tag") == tag:
                    btn.setChecked(True)

        # 重新渲染日志区域
        self.log_area.clear()
        if not hasattr(self, '_all_logs'):
            return
        for st, m in self._all_logs:
            if tag == "" or st == tag:
                self.log_area.append(m)
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_finished(self, report_path: str):
        self.report_path = report_path
        self.btn_start.setEnabled(True)
        self.btn_open.setVisible(True)
        if hasattr(self, 'btn_tweak') and self.btn_tweak:
            self.btn_tweak.setVisible(True)
        self.progress_bar.setValue(100)
        self.lbl_status.setText(f"分析完成！报告已保存：{report_path}")
        # 读取上次分析的 JSON，供微调使用
        try:
            session_dir = str(Path(report_path).parent.parent)
            logs_dir = os.path.join(session_dir, "logs")
            analysis_path = os.path.join(logs_dir, "step4_analysis.json")
            summary_path = os.path.join(logs_dir, "step3_summary.json")
            if os.path.exists(analysis_path):
                with open(analysis_path, "r", encoding="utf-8") as f:
                    self.last_analysis_json = json.load(f)
            if os.path.exists(summary_path):
                with open(summary_path, "r", encoding="utf-8") as f:
                    self.last_summary_json = json.load(f)
            self.last_session_dir = session_dir
            self._log_to_status("✓ 已加载分析结果，可进行微调")
        except Exception as e:
            self._log_to_status(f"加载分析结果失败（微调不可用）: {e}")
        names = ["读取表头", "生成脚本", "数据提炼", "AI 分析", "生成报告"]
        for i, lbl in enumerate(self.step_labels):
            lbl.setText(f"✓ {names[i]}")
            lbl.setStyleSheet("color:#34c77b; font-size:12px; padding:4px 10px; background:rgba(52,199,123,0.1); border:1px solid rgba(52,199,123,0.3); border-radius:4px;")
        # 完成后自动切换回"全部日志"
        self._filter_log_by_step("")
        webbrowser.open(f"file:///{report_path.replace(os.sep, '/')}")
        os.startfile(str(Path(report_path).parent.parent))
        self._load_history()

    def _on_error(self, msg: str):
        self.btn_start.setEnabled(True)
        self.lbl_status.setText(f"错误：{msg}")
        self.lbl_status.setStyleSheet("color:#f55151; font-size:12px;")
        QMessageBox.critical(self, "分析失败", f"分析过程出错：\n\n{msg}")

    def _open_report(self):
        if self.report_path and os.path.exists(self.report_path):
            webbrowser.open(f"file:///{self.report_path.replace(os.sep, '/')}")

    def _open_tweak_dialog(self):
        """打开微调对话框，启动 AI 微调"""
        if not self.last_analysis_json:
            QMessageBox.warning(self, "提示", "请先完成一次分析，才能使用微调功能。")
            return

        dlg = TweakDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        desc = dlg.get_tweak_description()
        if not desc:
            QMessageBox.warning(self, "提示", "请输入微调描述。")
            return

        self._tweak_desc = desc
        self._start_tweak(desc)

    def _start_tweak(self, desc):
        """启动 AI 微调"""
        self.btn_tweak.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_status.setText("正在微调报告...")
        self.tweak_worker = TweakWorker(
            config=self.config,
            analysis_json=self.last_analysis_json,
            summary_json=self.last_summary_json,
            tweak_description=desc,
            output_dir=self.last_session_dir,
        )
        self.tweak_worker.progress.connect(self._on_tweak_progress)
        self.tweak_worker.finished.connect(self._on_tweak_finished)
        self.tweak_worker.error.connect(self._on_tweak_error)
        self.tweak_worker.start()

    def _on_tweak_finished(self, report_path):
        """微调完成回调"""
        self.report_path = report_path
        self.btn_start.setEnabled(True)
        self.btn_tweak.setEnabled(True)
        self.btn_open.setVisible(True)
        self.progress_bar.setValue(100)
        self.lbl_status.setText(f"微调完成！报告已保存：{report_path}")
        try:
            webbrowser.open(f"file:///{report_path.replace(os.sep, chr(47))}")
        except Exception:
            pass
        QMessageBox.information(self, "完成", f"微调完成！\n\n报告已保存：{report_path}")

    def _on_tweak_error(self, msg):
        """微调失败回调"""
        self.btn_start.setEnabled(True)
        self.btn_tweak.setEnabled(True)
        self.lbl_status.setText(f"微调错误：{msg}")
        self.lbl_status.setStyleSheet("color:#f55151; font-size:12px;")
        QMessageBox.critical(self, "微调失败", f"微调过程出错：\n\n{msg}")

    def _on_tweak_progress(self, pct, msg):
        """微调进度回调"""
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    def _tweak_zero_token(self, desc):
        """零 Token 模式"""
        QMessageBox.information(self, "提示", "零 Token 模式：直接修改标题/副标题，不使用 AI。\n（暂未完整实现）")

    def _save_settings(self):
        """保存配置（v2：api_profiles + 步骤映射）"""
        # 保存各步骤选择的接口名称
        for step_key, combo in self.step_profile_combos.items():
            self.config[f"step_{step_key}_profile"] = combo.currentText().strip()
        # 输出目录
        self.config["output_dir"] = self.txt_output_dir.text().strip()
        # api_profiles 已经在 _add_or_update_profile / _delete_selected_profile 中实时更新到 self.config 了
        save_config(self.config)
        QMessageBox.information(self, "已保存", "配置已加密保存到本地")

    def _choose_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.txt_output_dir.setText(d)

    def _load_history(self):
        self.history_list.clear()
        output_dir = Path(self.config.get("output_dir", str(Path.home() / "data_analyst_output")))
        if not output_dir.exists():
            return
        sessions = sorted(output_dir.iterdir(), reverse=True)
        for s in sessions:
            report = s / "reports" / "report.html"
            if report.exists():
                mtime = datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                item = QListWidgetItem(f"📊  {s.name}  |  {mtime}")
                item.setData(Qt.ItemDataRole.UserRole, str(report))
                self.history_list.addItem(item)

    def _open_history_item(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and os.path.exists(path):
            webbrowser.open(f"file:///{path.replace(os.sep, '/')}")

    # ── Tab 4: AI 对话 ──────────────────────────────────────
    def _build_chat_tab(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 左侧：Tab 切换（会话列表 / AI日志）─────────────
        left = QWidget()
        left.setFixedWidth(220)
        left.setStyleSheet("background:#1a1d27; border-right:1px solid rgba(255,255,255,0.08);")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # 左侧顶部 Tab 切换按钮
        tab_switch_row = QHBoxLayout()
        tab_switch_row.setSpacing(0)
        tab_switch_row.setContentsMargins(0, 0, 0, 0)

        self.btn_tab_sessions = QPushButton("会话列表")
        self.btn_tab_sessions.setFixedHeight(36)
        self.btn_tab_sessions.setStyleSheet("""
            QPushButton {
                background:#4f8ef7; color:white; border:none;
                border-radius:0; font-size:12px; font-weight:bold;
            }
        """)
        self.btn_tab_sessions.clicked.connect(lambda: self._switch_left_tab("sessions"))

        self.btn_tab_ailogs = QPushButton("AI 日志")
        self.btn_tab_ailogs.setFixedHeight(36)
        self.btn_tab_ailogs.setStyleSheet("""
            QPushButton {
                background:#1a1d27; color:#9096b0; border:none;
                border-radius:0; font-size:12px;
                border-bottom:1px solid rgba(255,255,255,0.06);
            }
        """)
        self.btn_tab_ailogs.clicked.connect(lambda: self._switch_left_tab("ailogs"))

        tab_switch_row.addWidget(self.btn_tab_sessions)
        tab_switch_row.addWidget(self.btn_tab_ailogs)
        left_layout.addLayout(tab_switch_row)

        # ── 会话列表面板 ──────────────────────────────────
        self.panel_sessions = QWidget()
        ps_layout = QVBoxLayout(self.panel_sessions)
        ps_layout.setContentsMargins(8, 8, 8, 8)
        ps_layout.setSpacing(8)

        self.btn_new_chat = QPushButton("+ 新建对话")
        self.btn_new_chat.setStyleSheet("""
            QPushButton { background: #4f8ef7; color: white; border: none;
                          border-radius: 6px; padding: 10px; font-size: 13px; }
            QPushButton:hover { background: #3a7de0; }
        """)
        self.btn_new_chat.clicked.connect(self._new_chat)
        ps_layout.addWidget(self.btn_new_chat)

        self.chat_list = QListWidget()
        self.chat_list.itemClicked.connect(self._select_chat)
        self.chat_list.setStyleSheet("""
            QListWidget { background: transparent; border: none; }
            QListWidget::item { padding: 8px 10px; border-radius: 4px;
                               background: transparent; margin: 2px 0; }
            QListWidget::item:selected { background: rgba(79,142,247,0.2); }
            QListWidget::item:hover { background: rgba(255,255,255,0.05); }
        """)
        ps_layout.addWidget(self.chat_list)
        left_layout.addWidget(self.panel_sessions)

        # ── AI 日志面板 ────────────────────────────────────
        self.panel_ailogs = QWidget()
        self.panel_ailogs.setVisible(False)
        al_layout = QVBoxLayout(self.panel_ailogs)
        al_layout.setContentsMargins(8, 8, 8, 8)
        al_layout.setSpacing(6)

        # 顶部工具栏
        ailog_toolbar = QHBoxLayout()
        lbl_ailog_title = QLabel("分析记录")
        lbl_ailog_title.setStyleSheet("color:#9096b0; font-size:12px; font-weight:bold;")
        ailog_toolbar.addWidget(lbl_ailog_title)
        ailog_toolbar.addStretch()
        btn_clear_log = QPushButton("清空")
        btn_clear_log.setFixedSize(44, 24)
        btn_clear_log.setStyleSheet("""
            QPushButton { background:transparent; color:#5a6080; border:1px solid rgba(255,255,255,0.1);
                          border-radius:4px; font-size:11px; }
            QPushButton:hover { color:#e8eaf0; border-color:rgba(255,255,255,0.2); }
        """)
        btn_clear_log.clicked.connect(self._clear_ai_log)
        ailog_toolbar.addWidget(btn_clear_log)
        al_layout.addLayout(ailog_toolbar)

        # 日志列表（点击显示详情）
        self.ailog_list = QListWidget()
        self.ailog_list.setStyleSheet("""
            QListWidget { background: transparent; border: 1px solid rgba(255,255,255,0.08);
                          border-radius:4px; }
            QListWidget::item { padding: 6px 8px; border-bottom: 1px solid rgba(255,255,255,0.05);
                               color: #9096b0; font-size: 11px; }
            QListWidget::item:selected { background: rgba(79,142,247,0.15); color:#e8eaf0; }
            QListWidget::item:hover { background: rgba(255,255,255,0.04); }
        """)
        self.ailog_list.itemClicked.connect(self._on_ailog_item_clicked)
        al_layout.addWidget(self.ailog_list)

        # 提示语
        hint_ailog = QLabel("点击条目在右侧查看详情")
        hint_ailog.setStyleSheet("color:#3a3f58; font-size:10px;")
        hint_ailog.setAlignment(Qt.AlignmentFlag.AlignCenter)
        al_layout.addWidget(hint_ailog)

        left_layout.addWidget(self.panel_ailogs)

        # ── 右侧主区域（Splitter 切换：对话区 / 日志详情区）─
        self.chat_right_stack = QWidget()
        stack_layout = QVBoxLayout(self.chat_right_stack)
        stack_layout.setContentsMargins(0, 0, 0, 0)
        stack_layout.setSpacing(0)

        # ── 右侧 A：AI 对话区域 ───────────────────────────
        self.panel_chat_area = QWidget()
        chat_area_layout = QVBoxLayout(self.panel_chat_area)
        chat_area_layout.setContentsMargins(16, 12, 16, 12)
        chat_area_layout.setSpacing(12)

        # 标题栏
        header = QWidget()
        header.setFixedHeight(40)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_title = QLabel("新对话")
        self.chat_title.setStyleSheet("font-size:15px; font-weight:bold; color:#e8eaf0;")
        header_layout.addWidget(self.chat_title)
        header_layout.addStretch()
        self.btn_delete_chat = QPushButton("删除")
        self.btn_delete_chat.setFixedWidth(60)
        self.btn_delete_chat.setStyleSheet("""
            QPushButton { background: transparent; color: #f55151; border: 1px solid #f55151;
                          border-radius: 4px; padding: 4px 8px; font-size: 12px; }
            QPushButton:hover { background: rgba(245,81,81,0.1); }
        """)
        self.btn_delete_chat.clicked.connect(self._delete_chat)
        header_layout.addWidget(self.btn_delete_chat)
        chat_area_layout.addWidget(header)

        # 消息显示区（QScrollArea + 动态 Widget 气泡）
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setStyleSheet("""
            QScrollArea { background: #0f1117; border: none; }
            QScrollArea > QWidget > QWidget { background: #0f1117; }
        """)

        self.chat_container = QWidget()
        self.chat_container.setStyleSheet("background:#0f1117;")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 8, 0, 8)
        self.chat_layout.setSpacing(0)
        self.chat_layout.addStretch()

        self.chat_scroll.setWidget(self.chat_container)
        chat_area_layout.addWidget(self.chat_scroll)

        # 空状态提示
        self.chat_empty_label = QLabel("开始新对话或选择一个历史会话...")
        self.chat_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chat_empty_label.setStyleSheet("color:#5a6080; font-size:13px; margin-top:40px; background:#0f1117;")
        self.chat_layout.insertWidget(0, self.chat_empty_label, alignment=Qt.AlignmentFlag.AlignTop)

        # 输入区（深色主题）
        input_area = QWidget()
        input_area.setFixedHeight(120)
        input_area.setStyleSheet("background:#1a1d27; border-radius:8px; border:1px solid rgba(255,255,255,0.1);")
        input_layout = QVBoxLayout(input_area)
        input_layout.setContentsMargins(10, 6, 10, 6)
        input_layout.setSpacing(6)

        self.chat_input = QTextEdit()
        self.chat_input.setPlaceholderText("输入您的问题，按 Enter 发送，Shift+Enter 换行...")
        self.chat_input.setStyleSheet("""
            QTextEdit {
                background: transparent;
                border: none;
                color: #e8eaf0;
                font-size: 14px;
            }
            QTextEdit:focus { border: none; outline: none; }
            QTextEdit::placeholder { color: #5a6080; }
        """)
        self.chat_input.setMaximumHeight(60)
        self.chat_input.installEventFilter(self)
        input_layout.addWidget(self.chat_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        # 模型选择（AI 对话：从已保存的 API 配置列表中选择，跟其他步骤一样）
        self.chat_profile_combo = QComboBox()
        self.chat_profile_combo.setFixedWidth(220)
        self.chat_profile_combo.setMaximumHeight(32)
        # 加载已保存的 api_profiles 列表
        profiles = self.config.get("api_profiles", [])
        for p in profiles:
            name = p.get("name", "")
            if name:
                self.chat_profile_combo.addItem(name)
        # 恢复上次选择
        chat_profile = self.config.get("chat_profile", "")
        if chat_profile:
            idx = self.chat_profile_combo.findText(chat_profile)
            if idx >= 0:
                self.chat_profile_combo.setCurrentIndex(idx)
        self.chat_profile_combo.setStyleSheet("""
            QComboBox {
                background: rgba(255,255,255,0.08); color: #e8eaf0; border: 1px solid rgba(255,255,255,0.15);
                border-radius: 5px; padding: 2px 8px; font-size: 12px;
            }
            QComboBox:hover { border-color: rgba(79,142,247,0.5); }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-top: 6px solid #9096b0; margin-right: 6px; }
            QComboBox QAbstractItemView {
                background: #1a1d27; color: #e8eaf0; selection-background-color: rgba(79,142,247,0.3);
                border: 1px solid rgba(255,255,255,0.15);
            }
        """)
        btn_row.addWidget(self.chat_profile_combo)

        # 对话深度思考复选框
        self.chat_thinking_check = QCheckBox("🧠 深度思考")
        self.chat_thinking_check.setToolTip("启用后，部分模型（如 DeepSeek R1）会输出思考过程，提升复杂推理质量")
        self.chat_thinking_check.setStyleSheet("QCheckBox { color: #e8eaf0; font-size: 12px; } QCheckBox::indicator:hover { border: 1px solid #4f8ef7; }")
        btn_row.addWidget(self.chat_thinking_check)

        self.btn_send = QPushButton("发送")
        self.btn_send.setFixedSize(80, 32)
        self.btn_send.setStyleSheet("""
            QPushButton {
                background: #4f8ef7;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background: #3a7de0; }
            QPushButton:disabled { background: #ccc; }
        """)
        self.btn_send.clicked.connect(self._send_message)
        btn_row.addWidget(self.btn_send)
        input_layout.addLayout(btn_row)
        chat_area_layout.addWidget(input_area)

        stack_layout.addWidget(self.panel_chat_area)

        # ── 右侧 B：AI 日志详情区域 ───────────────────────
        self.panel_ailog_detail = QWidget()
        self.panel_ailog_detail.setVisible(False)
        ailog_detail_layout = QVBoxLayout(self.panel_ailog_detail)
        ailog_detail_layout.setContentsMargins(16, 12, 16, 12)
        ailog_detail_layout.setSpacing(8)

        # 详情标题栏
        detail_header = QHBoxLayout()
        self.ailog_detail_title = QLabel("AI 调用详情")
        self.ailog_detail_title.setStyleSheet("font-size:15px; font-weight:bold; color:#e8eaf0;")
        detail_header.addWidget(self.ailog_detail_title)
        detail_header.addStretch()
        btn_close_detail = QPushButton("← 返回对话")
        btn_close_detail.setStyleSheet("""
            QPushButton { background:transparent; color:#4f8ef7; border:1px solid rgba(79,142,247,0.3);
                          border-radius:4px; padding:4px 10px; font-size:12px; }
            QPushButton:hover { background:rgba(79,142,247,0.1); }
        """)
        btn_close_detail.clicked.connect(self._close_ailog_detail)
        detail_header.addWidget(btn_close_detail)
        ailog_detail_layout.addLayout(detail_header)

        # 日志气泡滚动区
        self._ai_log_scroll = QScrollArea()
        self._ai_log_scroll.setWidgetResizable(True)
        self._ai_log_scroll.setStyleSheet("""
            QScrollArea { background: #0f1117; border: none; }
            QScrollArea > QWidget > QWidget { background: #0f1117; }
        """)

        self._ai_log_container = QWidget()
        self._ai_log_container.setStyleSheet("background:#0f1117;")
        self._ai_log_layout = QVBoxLayout(self._ai_log_container)
        self._ai_log_layout.setContentsMargins(0, 8, 0, 8)
        self._ai_log_layout.setSpacing(0)
        self._ai_log_layout.addStretch()

        self._ai_log_empty_lbl = QLabel("暂无分析记录\n开始分析任务后这里会显示每次分析的 AI 调用汇总")
        self._ai_log_empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ai_log_empty_lbl.setStyleSheet("color:#5a6080; font-size:13px; margin-top:40px;")
        self._ai_log_layout.insertWidget(0, self._ai_log_empty_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        self._ai_log_scroll.setWidget(self._ai_log_container)
        ailog_detail_layout.addWidget(self._ai_log_scroll)

        stack_layout.addWidget(self.panel_ailog_detail)

        layout.addWidget(left)
        layout.addWidget(self.chat_right_stack, 1)

        # 内部状态
        self._ailog_items = []  # 存储日志数据 list of dict
        self._current_left_tab = "sessions"

        return widget

    def _switch_left_tab(self, tab_name):
        """切换左侧 Tab（sessions / ailogs）"""
        self._current_left_tab = tab_name
        if tab_name == "sessions":
            self.panel_sessions.setVisible(True)
            self.panel_ailogs.setVisible(False)
            # 同步切换右侧回对话区
            self._close_ailog_detail()
            self.btn_tab_sessions.setStyleSheet("""
                QPushButton { background:#4f8ef7; color:white; border:none;
                              border-radius:0; font-size:12px; font-weight:bold; }
            """)
            self.btn_tab_ailogs.setStyleSheet("""
                QPushButton { background:#1a1d27; color:#9096b0; border:none;
                              border-radius:0; font-size:12px;
                              border-bottom:1px solid rgba(255,255,255,0.06); }
            """)
        else:
            self.panel_sessions.setVisible(False)
            self.panel_ailogs.setVisible(True)
            # 切换右侧到日志详情区
            self.panel_chat_area.setVisible(False)
            self.panel_ailog_detail.setVisible(True)
            self.btn_tab_sessions.setStyleSheet("""
                QPushButton { background:#1a1d27; color:#9096b0; border:none;
                              border-radius:0; font-size:12px;
                              border-bottom:1px solid rgba(255,255,255,0.06); }
            """)
            self.btn_tab_ailogs.setStyleSheet("""
                QPushButton { background:#34c77b; color:white; border:none;
                              border-radius:0; font-size:12px; font-weight:bold; }
            """)

    def _close_ailog_detail(self):
        """关闭日志详情，回到对话区"""
        self.panel_chat_area.setVisible(True)
        self.panel_ailog_detail.setVisible(False)

    def _on_ailog_item_clicked(self, item):
        """点击日志列表条目，在右侧展示对应气泡"""
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self._ailog_items):
            return
        entry = self._ailog_items[idx]
        self._show_ailog_detail(entry)

    def _show_ailog_detail(self, session: dict):
        """在右侧展示一次分析的汇总详情（含所有 LLM 调用气泡）"""
        self.panel_chat_area.setVisible(False)
        self.panel_ailog_detail.setVisible(True)

        file_name = session.get("file_name", "未知文件")
        start_time = session.get("start_time", "")
        total_calls = session.get("total_calls", 0)
        total_elapsed = session.get("total_elapsed", 0)
        status = session.get("status", "")

        status_text = "❌ 失败" if status == "error" else f"✅ 完成 · {total_elapsed:.1f}s"
        self.ailog_detail_title.setText(
            f"{file_name}  ·  {start_time}  ·  {status_text}"
        )

        # 清空旧气泡
        for i in range(self._ai_log_layout.count() - 1, -1, -1):
            item = self._ai_log_layout.itemAt(i)
            w = item.widget()
            if w and w is not self._ai_log_empty_lbl:
                self._ai_log_layout.removeWidget(w)
                w.deleteLater()

        self._ai_log_empty_lbl.setVisible(False)

        # 为该分析中的每次 LLM 调用创建一个气泡
        calls = session.get("calls", [])
        for call in calls:
            bubble = AiLogBubble(
                call_idx=call["call_idx"],
                role=call["role"],
                model=call["model"],
                call_time=call["call_time"],
                prompt=call["prompt"],
                response=call["response"],
                elapsed=call.get("elapsed"),
                error=call.get("error"),
            )
            self._ai_log_layout.insertWidget(self._ai_log_layout.count() - 1, bubble)

        # 如果没有调用记录，显示提示
        if not calls:
            from PyQt6.QtWidgets import QLabel
            no_calls = QLabel("  本次分析无 AI 调用记录  ")
            no_calls.setStyleSheet("color:#5a6080; font-size:13px; padding:20px;")
            self._ai_log_layout.insertWidget(self._ai_log_layout.count() - 1, no_calls)

        # 滚到顶部（查看 Prompt）
        self._ai_log_scroll.verticalScrollBar().setValue(0)

    def _append_analysis_session(self, session_info: dict):
        """接收 pipeline 的 analysis_done 信号，一次分析=一条记录"""
        self._ailog_items.append(session_info)
        idx = len(self._ailog_items) - 1

        # 状态图标
        status = session_info.get("status", "")
        icon = "❌" if status == "error" else "✅"

        # 左侧列表标题：文件名 · N次调用 · 总耗时
        file_name = session_info.get("file_name", "未知文件")
        total_calls = session_info.get("total_calls", 0)
        total_elapsed = session_info.get("total_elapsed", 0)
        start_time = session_info.get("start_time", "")

        # 文件名截断显示
        short_file = file_name[:18] + "..." if len(file_name) > 18 else file_name
        label_text = f"{icon} {short_file} · {total_calls}次调用 · {total_elapsed:.0f}s"

        item = QListWidgetItem(label_text)
        item.setData(Qt.ItemDataRole.UserRole, idx)
        item.setToolTip(f"{file_name}\n时间: {start_time}\n调用: {total_calls}次\n耗时: {total_elapsed:.1f}s")
        self.ailog_list.addItem(item)
        self.ailog_list.scrollToBottom()

        # 如果当前在日志面板，自动展示最新一条
        if self._current_left_tab == "ailogs" and self.panel_ailog_detail.isVisible():
            self._show_ailog_detail(session_info)

    def _clear_ai_log(self):
        """清空 AI 日志"""
        self._ailog_items.clear()
        self.ailog_list.clear()
        # 清空右侧气泡
        for i in range(self._ai_log_layout.count() - 1, -1, -1):
            item = self._ai_log_layout.itemAt(i)
            w = item.widget()
            if w and w is not self._ai_log_empty_lbl:
                self._ai_log_layout.removeWidget(w)
                w.deleteLater()
        self._ai_log_empty_lbl.setVisible(True)

    def _get_chat_sessions_dir(self):
        """获取对话会话存储目录"""
        base_dir = Path.home() / "data_analyst_output" / "chat_sessions"
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    def _load_chat_sessions(self):
        """加载对话会话列表"""
        sessions_dir = self._get_chat_sessions_dir()
        self.chat_sessions.clear()
        self.chat_list.clear()

        for f in sorted(sessions_dir.glob("*.json"), reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    sid = f.stem
                    self.chat_sessions[sid] = data
                    item = QListWidgetItem(data.get("title", "未命名对话")[:20])
                    item.setData(Qt.ItemDataRole.UserRole, sid)
                    self.chat_list.addItem(item)
            except:
                pass

        # 如果没有会话，自动创建一个新的
        if not self.chat_sessions:
            self._new_chat()

    def _new_chat(self):
        """创建新对话"""
        sid = str(uuid.uuid4())[:8]
        self.chat_sessions[sid] = {
            "title": "新对话",
            "messages": [],
            "created_at": datetime.now().isoformat()
        }
        self.current_session_id = sid
        self._save_session(sid)
        self._load_chat_sessions()
        self._select_session(sid)

    def _select_chat(self, item):
        """选择对话会话"""
        sid = item.data(Qt.ItemDataRole.UserRole)
        self._select_session(sid)

    def _select_session(self, sid):
        """加载并显示指定会话"""
        self.current_session_id = sid
        session = self.chat_sessions.get(sid)
        if not session:
            return

        self.chat_title.setText(session.get("title", "新对话")[:30])

        # 清空消息区
        self._clear_chat_messages()

        # 重新加载消息
        for msg in session.get("messages", []):
            self._append_message_widget(msg["role"], msg["content"])

    def _clear_chat_messages(self):
        """清空消息区的所有 widget（保留 stretch 和空状态标签）"""
        # 倒序遍历，安全删除消息 widget，跳过 stretch 和空状态标签
        for i in range(self.chat_layout.count() - 1, -1, -1):
            item = self.chat_layout.itemAt(i)
            w = item.widget()
            if w is self.chat_empty_label:
                continue
            if w:
                self.chat_layout.removeWidget(w)
                w.deleteLater()
        self.chat_empty_label.setVisible(True)
        self.thinking_widget = None

    def _append_message(self, role, content):
        """添加消息到对话区（外部调用入口）"""
        self._append_message_widget(role, content)

    def _append_message_widget(self, role, content):
        """创建并添加消息气泡 widget"""
        # 隐藏空状态提示
        self.chat_empty_label.setVisible(False)

        bubble = ChatBubble(role, content)
        # 插入到 stretch 之前
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        """滚动到底部"""
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _show_thinking(self):
        """显示思考中动画"""
        self.chat_empty_label.setVisible(False)
        self.thinking_widget = ThinkingWidget()
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self.thinking_widget)
        self._scroll_to_bottom()

    def _remove_thinking(self):
        """移除思考中动画"""
        if self.thinking_widget:
            self.thinking_widget.stop()
            self.thinking_widget.deleteLater()
            self.thinking_widget = None

    def _send_message(self):
        """发送消息（从选中的 API 配置 profile 获取连接参数）"""
        try:
            content = self.chat_input.toPlainText().strip()
            if not content:
                return
            if not self.current_session_id:
                self._new_chat()

            # 从选中的 chat_profile 获取完整连接参数
            from core.config_manager import resolve_profile
            chat_profile_name = self.chat_profile_combo.currentText().strip()
            prof = resolve_profile(self.config, chat_profile_name)
            url = prof.get("url", "")
            key = prof.get("key", "")
            model = prof.get("model", "")
            if not url or not key or not model:
                QMessageBox.warning(self, "提示", "请先在「API 配置」页面配置好接口，并在对话窗口选择接口")
                return

            # 保存当前选择的对话接口
            self.config["chat_profile"] = chat_profile_name

            # 添加用户消息
            session = self.chat_sessions[self.current_session_id]
            session["messages"].append({"role": "user", "content": content})
            self._append_message("user", content)
            self.chat_input.clear()

            # 更新标题（取第一条用户消息）
            if len(session["messages"]) == 1:
                session["title"] = content[:25] + "..." if len(content) > 25 else content
                self.chat_title.setText(session["title"])
                self._save_session(self.current_session_id)
                self._load_chat_sessions()

            # 禁用发送按钮，显示思考动画
            self.btn_send.setEnabled(False)
            self.btn_send.setText("思考中...")
            self._show_thinking()

            # 启动对话线程
            system_prompt = "你是一名专业的数据分析师。请用简洁、专业的语言回答用户的问题。如果涉及数据分析，请给出有价值的洞察。"
            chat_thinking = self.chat_thinking_check.isChecked()
            self.chat_worker = ChatWorker(url, model, key, session["messages"], system_prompt, thinking=chat_thinking)
            self.chat_worker.response.connect(lambda r: self._on_chat_response(r))
            self.chat_worker.error.connect(lambda e: self._on_chat_error(e))
            self.chat_worker.finished.connect(self._on_chat_finished)
            self.chat_worker.start()
        except Exception as e:
            import traceback
            self._remove_thinking()
            self.btn_send.setEnabled(True)
            self.btn_send.setText("发送")
            self._append_message("assistant", f"❌ 发送失败：{str(e)}")
            self.log_area.append(f"[Chat Send Error] {traceback.format_exc()}")

    def _on_chat_response(self, response):
        """处理 AI 响应"""
        # 移除思考动画
        self._remove_thinking()
        # 添加 AI 回复
        self._append_message("assistant", response)

        # 保存到会话
        if self.current_session_id:
            session = self.chat_sessions[self.current_session_id]
            session["messages"].append({"role": "assistant", "content": response})
            self._save_session(self.current_session_id)

    def _on_chat_error(self, error_msg):
        """处理对话错误"""
        # 移除思考动画
        self._remove_thinking()
        # 显示错误消息气泡（深色主题）
        bubble = ChatBubble("assistant", f"❌ 发生错误：{error_msg}")
        bubble.msg_label.setStyleSheet(
            "background:rgba(245,81,81,0.15); color:#f55151; padding:10px 14px; "
            "border-radius:12px 12px 12px 4px; font-size:14px; line-height:1.5; "
            "border:1px solid rgba(245,81,81,0.3);"
        )
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def _on_chat_finished(self):
        """对话完成"""
        self.btn_send.setEnabled(True)
        self.btn_send.setText("发送")

    def _delete_chat(self):
        """删除当前对话"""
        if not self.current_session_id:
            return
        reply = QMessageBox.question(self, "确认删除", "确定要删除当前对话吗？",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            sid = self.current_session_id
            # 删除文件
            f = self._get_chat_sessions_dir() / f"{sid}.json"
            if f.exists():
                f.unlink()
            # 删除内存数据
            if sid in self.chat_sessions:
                del self.chat_sessions[sid]
            self._load_chat_sessions()
            self._new_chat()

    def _save_session(self, sid):
        """保存会话到文件"""
        session = self.chat_sessions.get(sid)
        if not session:
            return
        f = self._get_chat_sessions_dir() / f"{sid}.json"
        with open(f, "w", encoding="utf-8") as fp:
            json.dump(session, fp, ensure_ascii=False, indent=2)

    def eventFilter(self, obj, event):
        """事件过滤器，支持 Enter 发送"""
        if obj == self.chat_input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                try:
                    self._send_message()
                except Exception as e:
                    import traceback
                    self._append_message("assistant", f"❌ 内部错误：{str(e)}")
                    self.log_area.append(f"[Chat Error] {traceback.format_exc()}")
                return True
        return super().eventFilter(obj, event)


# ════════════════════════════════════════════════════════════
#  模块级辅助函数（API 配置 Tab v2 UI 样式与工具函数）
# ════════════════════════════════════════════════════════════

def _input_style():
    return "background:#1a1d27; border:1px solid rgba(255,255,255,0.12); border-radius:6px; color:#e8eaf0; padding:5px 10px; font-size:13px;"

def _btn_green_style():
    return "background:rgba(52,199,123,0.15); color:#34c77b; border:1px solid rgba(52,199,123,0.4); border-radius:6px; font-weight:bold; font-size:12px;"

def _btn_blue_style():
    return "background:rgba(79,142,247,0.12); color:#4f8ef7; border:1px solid rgba(79,142,247,0.35); border-radius:6px; font-size:12px;"

def _combo_style():
    return "background:#1a1d27; border:1px solid rgba(255,255,255,0.12); border-radius:6px; color:#e8eaf0; padding:3px 8px; font-size:13px;"

def _list_style():
    return "background:#1a1d27; border:1px solid rgba(255,255,255,0.08); border-radius:6px; color:#e8eaf0; font-size:12px;"

def _btn_outline_style():
    return "background:transparent; color:#9096b0; border:1px solid rgba(255,255,255,0.15); border-radius:6px; font-size:11px;"

def _btn_danger_style():
    return "background:rgba(245,81,81,0.1); color:#f55151; border:1px solid rgba(245,81,81,0.3); border-radius:6px; font-size:11px;"


def _load_profiles_to_list(list_widget, config):
    """将 api_profiles 名称加载到 QListWidget"""
    list_widget.clear()
    for p in config.get("api_profiles", []):
        name = p.get("name", "")
        if name:
            list_widget.addItem(name)


def _populate_profile_names(combo, config):
    """填充 QComboBox 为所有 profile 名称"""
    current = combo.currentText()
    combo.clear()
    for p in config.get("api_profiles", []):
        name = p.get("name", "")
        if name:
            combo.addItem(name)
    # 尝试恢复之前的选择
    if current:
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)


def _refresh_profile_list(list_widget, profiles):
    """刷新列表 Widget 内容"""
    list_widget.clear()
    for p in profiles:
        name = p.get("name", "") if isinstance(p, dict) else str(p)
        if name:
            list_widget.addItem(name)


def _refresh_all_profile_combos(combos, profiles, new_name=""):
    """
    刷新一组 QComboBox 的选项列表。
    new_name 非空时确保它出现在每个 combo 中。
    """
    for combo in combos:
        if not combo:
            continue
        current = combo.currentText()
        combo.clear()
        for p in profiles:
            name = p.get("name", "") if isinstance(p, dict) else str(p)
            if name:
                combo.addItem(name)
        # 如果有新名称且不在列表中，追加
        if new_name and combo.findText(new_name) < 0:
            combo.addItem(new_name)
        # 恢复选择
        if current:
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
