"""聊天消息视图 — 赛博朋克科技风。

特性：
- 深色 + 霓虹青/紫光晕背景渐变（CSS 渐变背景 + 发光边框）
- 用户气泡：青霓虹渐变+外发光
- AI 气泡：深黑半透 + 紫光晕描边
- 系统气泡：深灰 + 黄线
- 输入区：悬浮玻璃卡片 + 霓虹聚焦发光
- 流式输出：脉冲"思考中…" + 打字光标
"""
from __future__ import annotations
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
    QLabel, QScrollArea, QFrame, QSizePolicy, QGraphicsDropShadowEffect,
    QTextBrowser, QToolButton, QFileDialog, QSpacerItem, QLineEdit, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtGui import QColor, QPalette
from ..protocol.types import Message, ChatRole
from .styles import (
    current_colors, bubble_colors, input_colors, chip_colors,
    is_light_theme, apply_skin_widget,
)
from PyQt6.QtCore import QUrl as _QU
from PyQt6.QtGui import QDesktopServices
from .md_render import md_to_html, extract_copy_code_idx


def _neon_glow(color_hex: str, blur: int = 24, xoff: int = 0, yoff: int = 0) -> QGraphicsDropShadowEffect:
    """生成霓虹发光效果。"""
    eff = QGraphicsDropShadowEffect()
    eff.setBlurRadius(blur)
    eff.setXOffset(xoff)
    eff.setYOffset(yoff)
    eff.setColor(QColor(color_hex))
    return eff


# ============================================================
# 主题感知样式生成器（所有文字/背景/边框色从当前皮肤派生，保证对比度）
# ============================================================
class _T:
    """所有动态样式集中管理，保证切皮肤后能一致刷新。"""

    @staticmethod
    def edit_dlg_info() -> str:
        c = current_colors()
        return "color:" + c.subtext + "; font-size:11px; padding-bottom:6px; background:transparent;"

    @staticmethod
    def edit_dlg_editor() -> str:
        c = current_colors()
        if is_light_theme():
            return ("QTextEdit { background:#ffffff; color:" + c.text +
                    "; border:1px solid " + c.overlay + "; border-radius:6px; padding:8px; }")
        return ("QTextEdit { background:#060913; color:#e8f9ff;"
                " border:1px solid #1f4060; border-radius:6px; padding:8px; }")

    @staticmethod
    def bubble_frame(role):
        """(stylesheet, glow_color_hex)"""
        bg, border, txt, tag = bubble_colors(role)
        c = current_colors()
        if role == "user":
            if is_light_theme():
                return (
                    "QFrame#UserBubble {"
                    " background: " + bg + ";"
                    " border: 1px solid " + border + ";"
                    " border-radius: 12px;"
                    " border-top-right-radius: 2px;"
                    "}",
                    c.accent
                )
            return (
                "QFrame#UserBubble {"
                " background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                "   stop:0 " + c.accent + ", stop:1 " + c.accent_hover + ");"
                " border: 1px solid " + c.accent_hover + ";"
                " border-radius: 12px;"
                " border-top-right-radius: 2px;"
                "}",
                c.accent
            )
        if role == "system":
            return (
                "QFrame#SystemBubble {"
                " background: " + bg + ";"
                " border: 1px dashed " + border + ";"
                " border-radius: 10px;"
                "}",
                border
            )
        # ai
        if is_light_theme():
            return (
                "QFrame#AIBubble {"
                " background: " + bg + ";"
                " border: 1px solid " + border + ";"
                " border-radius: 12px;"
                " border-top-left-radius: 2px;"
                "}",
                c.mauve
            )
        return (
            "QFrame#AIBubble {"
            " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "   stop:0 " + c.surface0 + ", stop:1 " + c.crust + ");"
            " border: 1px solid " + c.surface1 + ";"
            " border-radius: 12px;"
            " border-top-left-radius: 2px;"
            "}",
            c.mauve
        )

    @staticmethod
    def bubble_role_tag(role):
        _, _, _, tag_c = bubble_colors(role)
        letter = "0.5px" if role == "ai" else "1px"
        return ("color:" + tag_c + "; font-weight:bold; font-size:11px;"
                "letter-spacing:" + letter + "; padding:0; background:transparent;")

    @staticmethod
    def bubble_ts(role):
        c = current_colors()
        return "color:" + c.subtext + "; font-size:10px; padding:0; background:transparent; opacity:0.85;"

    @staticmethod
    def ai_breathe(on):
        c = current_colors()
        if is_light_theme():
            off = "#cbd5e1"
            return (
                ("color:" + c.mauve + "; font-size:12px;" if on else "color:" + off + "; font-size:12px;"),
                "color:" + c.mauve + "; font-weight:bold; font-size:11px; letter-spacing:2px; padding:0; background:transparent;"
            )
        return (
            ("color:" + c.mauve + "; font-size:12px;" if on else "color:#5e3a9c; font-size:12px;"),
            "color:" + c.mauve + "; font-weight:bold; font-size:11px; letter-spacing:2px; padding:0; background:transparent;"
        )

    @staticmethod
    def bubble_sepline(role):
        c = current_colors()
        if role == "user":
            if is_light_theme():
                return "background: " + c.accent + "; height:1px; max-height:1px; color:transparent;"
            return ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 " + c.accent + ", stop:1 transparent);"
                    " height:1px; max-height:1px; color:transparent;")
        if role == "system":
            return "background: " + c.yellow + "; height:1px; max-height:1px; color:transparent;"
        return ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                " stop:0 " + c.mauve + ", stop:0.5 " + (c.accent_hover if not is_light_theme() else c.accent) + ", stop:1 transparent);"
                " height:1px; max-height:1px; color:transparent;")

    @staticmethod
    def bubble_content(role):
        _, _, txt, _ = bubble_colors(role)
        lh = "1.6em" if role != "ai" else "1.7em"
        style = "color:" + txt + "; font-size:14px; line-height:" + lh + "; background:transparent;"
        if role == "system":
            style += "font-style: italic;"
        return style

    @staticmethod
    def chat_bg():
        """(bg_style, s1 (header lbl1), s2 (header lbl2))"""
        c = current_colors()
        if is_light_theme():
            bg = (
                "QFrame#ChatBg {"
                " background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                "  stop:0 #ecfdf5, stop:0.4 #fbfdff, stop:1 #f0f7ff);"
                "}"
            )
            return (bg,
                    "color:" + c.accent + "; font-size:12px; letter-spacing:1px; background:transparent;",
                    "color:" + c.mauve + "; font-size:12px; letter-spacing:2px; font-weight:bold; background:transparent;")
        bg = (
            "QFrame#ChatBg {"
            " background-color: " + c.base + ";"
            "}"
        )
        return (bg,
                "color:" + c.accent_hover + "; font-size:12px; letter-spacing:1px; background:transparent;",
                "color:" + c.mauve + "; font-size:12px; letter-spacing:2px; font-weight:bold; background:transparent;")

    @staticmethod
    def header_dot(idx):
        c = current_colors()
        col = c.accent
        return "color:" + col + "; font-size:10px; opacity:0.35; background:transparent;"

    @staticmethod
    def attach_btn():
        c = current_colors()
        if is_light_theme():
            return (
                "QPushButton {"
                " background: #ffffff; color:" + c.accent + ";"
                " border: 1px solid " + c.overlay + "; border-radius: 8px; font-size: 16px;"
                "}"
                " QPushButton:hover { background:" + c.surface0 + "; border-color:" + c.accent + "; color:" + c.text + "; }"
                " QPushButton:pressed { background: #e2e8f0; }"
            )
        return (
            "QPushButton {"
            " background: " + c.surface0 + "; color:" + c.accent_hover + ";"
            " border: 1px solid #1e3055; border-radius: 8px; font-size: 16px;"
            "}"
            " QPushButton:hover { background: " + c.surface1 + "; border-color: " + c.accent + "; color: #ffffff; }"
            " QPushButton:pressed { background: #1a2a60; }"
        )

    @staticmethod
    def cyber_input(focus=False):
        _, inp_bg, border, focus_border, text = input_colors()
        if is_light_theme():
            sel_bg, sel_txt = "#e0e7ff", "#0f172a"
        else:
            sel_bg, sel_txt = "#005566", "#ffffff"
        return (
            "QTextEdit#CyberInput {"
            " background: " + inp_bg + ";"
            " color: " + text + ";"
            " border: 1px solid " + (focus_border if focus else border) + ";"
            " border-radius: 10px;"
            " padding: 10px 12px;"
            " font-size: 14px;"
            " selection-background-color: " + sel_bg + ";"
            " selection-color: " + sel_txt + ";"
            "}"
        )

    @staticmethod
    def send_btn(state="normal"):
        c = current_colors()
        if state == "disabled":
            if is_light_theme():
                return ("QPushButton#SendBtn {"
                        " background: " + c.surface0 + "; color:" + c.overlay + ";"
                        " border: 1px solid " + c.overlay + ";"
                        " border-radius: 8px; font-size: 13px; font-weight: 800; letter-spacing: 1px;"
                        "}")
            return ("QPushButton#SendBtn {"
                    " background: #1a2040; color: #4a5880;"
                    " border: 1px solid #223060;"
                    " border-radius: 8px; font-size: 13px; font-weight: 800; letter-spacing: 1px;"
                    "}")
        if is_light_theme():
            return (
                "QPushButton#SendBtn {"
                " background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "   stop:0 " + c.accent + ", stop:1 " + c.mauve + ");"
                " color: #ffffff;"
                " border: none; border-radius: 8px;"
                " font-size: 13px; font-weight: 800; letter-spacing: 1px;"
                "}"
                " QPushButton#SendBtn:hover {"
                " background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "   stop:0 " + c.accent_hover + ", stop:1 #a78bfa);"
                " color: #ffffff;"
                "}"
                " QPushButton#SendBtn:pressed { padding-left: 2px; }"
            )
        return (
            "QPushButton#SendBtn {"
            " background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "   stop:0 " + c.accent + ", stop:1 " + c.mauve + ");"
            " color: #051422;"
            " border: none; border-radius: 8px;"
            " font-size: 13px; font-weight: 800; letter-spacing: 1px;"
            "}"
            " QPushButton#SendBtn:hover {"
            " background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "   stop:0 " + c.accent_hover + ", stop:1 #d99bff);"
            " color: #000000;"
            "}"
            " QPushButton#SendBtn:pressed { padding-left: 2px; }"
        )

    @staticmethod
    def input_card():
        card_bg, inp_bg, border, focus_border, text = input_colors()
        c = current_colors()
        if is_light_theme():
            return (
                "QFrame#InputCard {"
                " background: " + card_bg + ";"
                " border-top: 1px solid " + c.overlay + ";"
                "}"
            )
        return (
            "QFrame#InputCard {"
            " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "   stop:0 rgba(16,22,42,0.92), stop:1 rgba(8,10,22,0.96));"
            " border-top: 1px solid " + c.surface1 + ";"
            "}"
        )

    @staticmethod
    def hint_text():
        c = current_colors()
        return "color:" + c.subtext + "; font-size:10px; letter-spacing:0.3px; padding: 0 2px; background:transparent;"

    @staticmethod
    def attach_chip():
        c = current_colors()
        if is_light_theme():
            return (
                "QFrame {"
                " background: #ecfdf5;  border: 1px solid #a7f3d0; border-radius: 6px;"
                "}",
                "color:#065f46; font-size:11px; background:transparent;",
                "QToolButton { color:#ef4444; font-size:15px; font-weight:bold; border-radius:9px; background:transparent; }"
                " QToolButton:hover { background: rgba(239,68,68,0.1); }"
            )
        return (
            "QFrame { background: rgba(0,229,255,0.06);"
            " border: 1px solid #1f4060; border-radius: 6px; }",
            "color:" + c.accent_hover + "; font-size:11px; background:transparent;",
            "QToolButton { color:#ff3d71; font-size:15px; font-weight:bold; border-radius:9px; background:transparent; }"
            " QToolButton:hover { background: rgba(255,61,113,0.2); }"
        )

    @staticmethod
    def streaming_card():
        c = current_colors()
        if is_light_theme():
            return (
                "QFrame#AIStreamingCard {"
                " background: #ffffff;  border: 1px solid " + c.overlay + ";"
                " border-radius: 14px; border-top-left-radius: 2px;"
                "}",
                c.accent + "30"
            )
        return (
            "QFrame#AIStreamingCard {"
            " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "   stop:0 " + c.surface0 + ", stop:1 " + c.crust + ");"
            " border: 1px solid " + c.surface1 + ";"
            " border-radius: 14px; border-top-left-radius: 2px;"
            "}",
            c.mauve + "50"
        )

    @staticmethod
    def streaming_label():
        _, _, text, _ = bubble_colors("ai")
        return "color:" + text + "; font-size:14px; line-height:1.7em; padding: 2px 0 0 0; background:transparent;"



# ============================================================
# 消息气泡
# ============================================================

# ============================================================
# FlowCardWidget — 流程蓝图任务卡（展示 FlowRun 步骤和状态）
# ============================================================
class FlowCardWidget(QFrame):
    """任务卡：展示 FlowRun 的步骤状态 + 确认执行按钮。"""

    flow_confirmed = pyqtSignal(dict)

    def __init__(self, flow_data: dict, parent=None):
        super().__init__(parent)
        self._flow_data = flow_data
        self._step_widgets: dict[str, tuple] = {}   # step_label -> (icon, summary)
        self._btn_container = None
        self._build()

    def _build(self):
        self.setObjectName("FlowCard")
        bz = is_light_theme()
        bg = "#f0f4ff" if bz else "#0e1023"
        border = "#c4b5fd" if bz else "#3a2d70"
        text_c = "#1e293b" if bz else "#e8eaf0"
        text2 = "#64748b" if bz else "#9096b0"
        text3 = "#94a3b8" if bz else "#6a6e8a"
        self.setStyleSheet(
            "QFrame#FlowCard { background: " + bg + ";"
            " border: 1px solid " + border + ";"
            " border-radius: 10px; margin: 4px 8px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)

        # 标题行
        title_row = QHBoxLayout()
        icon = QLabel("📋")
        title = QLabel(self._flow_data.get("title", "任务卡"))
        title.setStyleSheet("font-weight:700; font-size:13px; color:" + text_c + ";")
        bp_key = self._flow_data.get("blueprint_key", "")
        tag = QLabel("流程蓝图 · " + bp_key)
        tag.setStyleSheet("font-size:10px; color:" + text2 + ";")
        title_row.addWidget(icon)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(tag)
        lay.addLayout(title_row)

        # 步骤列表
        for s in self._flow_data.get("steps", []):
            step_label = s.get("label", s.get("name", ""))
            row = QHBoxLayout()
            status_icon = QLabel("⏳")
            name_lbl = QLabel(step_label)
            name_lbl.setStyleSheet("font-size:12px; color:" + text2 + ";")
            row.addWidget(status_icon)
            row.addWidget(name_lbl)
            row.addStretch()
            lay.addLayout(row)
            summary_lbl = QLabel(s.get("summary", ""))
            summary_lbl.setStyleSheet("font-size:11px; color:" + text3 + ";")
            summary_lbl.setWordWrap(True)
            sub_row = QHBoxLayout()
            sub_row.addSpacing(20)
            sub_row.addWidget(summary_lbl)
            lay.addLayout(sub_row)
            self._step_widgets[step_label] = (status_icon, summary_lbl)

        # 操作按钮区
        self._btn_container = QHBoxLayout()
        self._btn_container.addStretch()
        self._render_buttons()
        lay.addLayout(self._btn_container)

    def _render_buttons(self):
        """根据 status 渲染按钮区。"""
        # 清空
        while self._btn_container.count():
            item = self._btn_container.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._btn_container.addStretch()
        bz = is_light_theme()
        status = self._flow_data.get("status", "pending")
        if status == "pending":
            btn = QPushButton("✅ 确认执行")
            accent = "#6366f1" if bz else "#4f8ef7"
            hover = "#4f46e5" if bz else "#3a7de0"
            btn.setStyleSheet(
                "QPushButton { background: " + accent + ";"
                " color: white; border: none; border-radius: 6px;"
                " padding: 6px 16px; font-size: 12px; font-weight: 600; }"
                "QPushButton:hover { background: " + hover + "; }"
            )
            btn.clicked.connect(self._on_confirm)
            self._btn_container.addWidget(btn)
        elif status == "running":
            lbl = QLabel("🔄 执行中...")
            c = "#6366f1" if bz else "#9060ff"
            lbl.setStyleSheet("font-size:12px; color:" + c + ";")
            self._btn_container.addWidget(lbl)
        elif status == "done":
            lbl = QLabel("✅ 已完成")
            c = "#16a34a" if bz else "#22c55e"
            lbl.setStyleSheet("font-size:12px; color:" + c + ";")
            self._btn_container.addWidget(lbl)
        elif status == "failed":
            lbl = QLabel("❌ 执行失败")
            c = "#dc2626" if bz else "#ef4444"
            lbl.setStyleSheet("font-size:12px; color:" + c + ";")
            self._btn_container.addWidget(lbl)

    def _on_confirm(self):
        self.flow_confirmed.emit(self._flow_data)

    def update_step(self, step_label: str, status: str, summary: str):
        """更新某步的状态（实时进度回调）。"""
        icons = {"pending": "⏳", "running": "🔄", "done": "✅", "failed": "❌"}
        if step_label in self._step_widgets:
            icon_lbl, summary_lbl = self._step_widgets[step_label]
            icon_lbl.setText(icons.get(status, "⏳"))
            if summary:
                summary_lbl.setText(summary)

    def set_finished(self, flow_dict: dict):
        """任务卡执行完毕，更新所有步骤状态。"""
        self._flow_data = flow_dict
        icons = {"pending": "⏳", "running": "🔄", "done": "✅", "failed": "❌"}
        for s in flow_dict.get("steps", []):
            label = s.get("label", s.get("name", ""))
            if label in self._step_widgets:
                icon_lbl, summary_lbl = self._step_widgets[label]
                icon_lbl.setText(icons.get(s.get("status", "pending"), "⏳"))
                if s.get("summary"):
                    summary_lbl.setText(s["summary"])
                elif s.get("error"):
                    summary_lbl.setText("错误: " + s["error"])
        self._render_buttons()


class MessageBubble(QFrame):
    """单条消息气泡（科技风）。"""

    edit_requested = None  # 由 ChatView 注入：Callable[[str, str], None]

    def __init__(self, msg: Message, parent=None):
        super().__init__(parent)
        self._msg = msg
        self.setObjectName("MessageBubble")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._build()

    def _on_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu, QDialog, QVBoxLayout, QDialogButtonBox, QTextEdit
        menu = QMenu(self)
        copy_act = menu.addAction("📋 复制内容")
        edit_act = menu.addAction("✏️  编辑内容")
        menu.addSeparator()
        copy_id_act = menu.addAction("🆔 复制消息 ID")
        action = menu.exec(self.mapToGlobal(pos))
        # 复制内容
        if action == copy_act:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(self._msg.content)
            return
        # 复制 ID
        if action == copy_id_act:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(self._msg.id)
            return
        # 编辑内容
        if action == edit_act:
            dlg = QDialog(self)
            dlg.setWindowTitle(f"编辑消息 — {self._msg.role.value}")
            dlg.setMinimumWidth(520)
            dlg.setMinimumHeight(260)
            lay = QVBoxLayout(dlg)
            info = QLabel("修改完后点『保存并重新发送』会以新内容重新请求 AI（这条消息会被替换显示）。")
            info.setWordWrap(True)
            info.setStyleSheet("color:#8fb4c7; font-size:11px; padding-bottom:6px;")
            lay.addWidget(info)
            ed = QTextEdit(); ed.setPlainText(self._msg.content)
            ed.setStyleSheet("QTextEdit { background:#060913; color:#e8f9ff; border:1px solid #1f4060; border-radius:6px; padding:8px; }")
            lay.addWidget(ed, 1)
            btns = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
            )
            btns.button(QDialogButtonBox.StandardButton.Save).setText("💾 保存并重新发送")
            btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            lay.addWidget(btns)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_text = ed.toPlainText().strip()
                if new_text and new_text != self._msg.content and callable(self.edit_requested):
                    self.edit_requested(self._msg.id, new_text)
        # 忽略其他菜单操作
        

    def _build(self):
        is_user = self._msg.role == ChatRole.USER
        is_system = self._msg.role == ChatRole.SYSTEM
        is_tool = self._msg.role == ChatRole.TOOL
        role_str = "user" if is_user else ("system" if is_system else ("tool" if is_tool else "ai"))
        skin = "breeze" if is_light_theme() else "cyberpunk"

        # 读取 metadata：model_name / capabilities
        md = self._msg.metadata or {}
        model_name = md.get("model_name", "")
        caps = md.get("capabilities", {}) or {}
        provider = md.get("provider", "")

        # 布局：左/右对齐整体容器
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 气泡本体
        bubble = QFrame()
        obj_name_map = {"user": "UserBubble", "system": "SystemBubble", "tool": "ToolBubble", "ai": "AIBubble"}
        bubble.setObjectName(obj_name_map.get(role_str, "AIBubble"))
        bubble.setMaximumWidth(680)
        bubble.setMinimumWidth(120)

        inner = QVBoxLayout(bubble)
        inner.setContentsMargins(14, 9, 14, 11)
        inner.setSpacing(3)

        # ====== 顶部 meta_row：角色 + 时间 ======
        meta_row = QHBoxLayout()
        if is_user:
            # 给"你"加一个青色竖条 + 实心方块（避免旧的空心菱看起来像星号，和 AI 区分一眼可见）
            bar = QLabel(" ")
            bar.setFixedWidth(4); bar.setFixedHeight(14)
            bar.setStyleSheet(
                "background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #00e5ff, stop:1 #00c2a8);"
                "border-radius:2px;"
            )
            role_tag = QLabel("■ 你")
            role_tag.setStyleSheet(
                "color:#00c2a8;font-weight:900;font-size:11px;"
                "letter-spacing:1px;padding:0;"
            )
            ts_lbl = QLabel(self._fmt_ts())
            ts_color = "#94a3b8" if is_light_theme() else "#5a5280"
            ts_lbl.setStyleSheet(f"color:{ts_color};font-size:9.5px;padding:0;")
            meta_row.addWidget(bar, 0); meta_row.addSpacing(4)
            meta_row.addWidget(role_tag, 0, Qt.AlignmentFlag.AlignLeft)
            meta_row.addStretch(1)
            meta_row.addWidget(ts_lbl, 0, Qt.AlignmentFlag.AlignRight)
        elif is_system:
            role_tag = QLabel("◇ SYSTEM")
            role_tag.setStyleSheet("color:#ffd166;font-weight:800;font-size:10px;letter-spacing:1px;padding:0;")
            ts_lbl = QLabel(self._fmt_ts())
            ts_lbl.setStyleSheet("color:#7a6a3a;font-size:10px;")
            meta_row.addWidget(role_tag, 0); meta_row.addStretch(1); meta_row.addWidget(ts_lbl, 0)
        elif is_tool:
            role_tag = QLabel("🛠  TOOL 结果")
            role_tag.setStyleSheet("color:#ff9e64;font-weight:800;font-size:10px;letter-spacing:1px;padding:0;")
            ts_lbl = QLabel(self._fmt_ts())
            ts_lbl.setStyleSheet("color:#5a5280;font-size:10px;")
            meta_row.addWidget(role_tag, 0); meta_row.addStretch(1); meta_row.addWidget(ts_lbl, 0)
        else:
            # AI 头部：细紫青竖条(2px, 高14) + 呼吸点 + CORE AI，时间戳再收一级 9.5px 灰
            bar = QLabel(" ")
            bar.setFixedWidth(2); bar.setFixedHeight(14)
            bar.setStyleSheet(
                "background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #b56bff, stop:1 #7afcff);"
                "border-radius:1px;"
            )
            dot = QLabel("●")
            dot.setStyleSheet("color:#b56bff;font-size:10px;")
            self._breath_timer = QTimer(self)
            self._breath_on = False
            def _flick():
                self._breath_on = not self._breath_on
                if self._breath_on:
                    dot.setStyleSheet("color:#b56bff;font-size:10px;")
                else:
                    dot.setStyleSheet("color:#5e3a9c;font-size:10px;")
            self._breath_timer.timeout.connect(_flick)
            self._breath_timer.start(1400)
            role_tag = QLabel("CORE AI")
            role_tag.setStyleSheet("color:#b56bff;font-weight:700;font-size:10px;letter-spacing:1px;padding:0;")
            ts_lbl = QLabel(self._fmt_ts())
            ts_color = "#94a3b8" if is_light_theme() else "#5a5280"
            ts_lbl.setStyleSheet(f"color:{ts_color};font-size:9.5px;")
            meta_row.addWidget(bar, 0); meta_row.addSpacing(4)
            meta_row.addWidget(dot, 0); meta_row.addWidget(role_tag, 0)
            meta_row.addStretch(1); meta_row.addWidget(ts_lbl, 0)
        inner.addLayout(meta_row)

        # ====== 新增行：AI 才显示 模型名徽章 + 能力小标签 ======
        if (not is_user) and (not is_system) and (not is_tool) and model_name:
            sub_row = QHBoxLayout()
            sub_row.setSpacing(6)
            # 模型名徽章
            badge = QLabel()
            short_model = model_name
            if len(short_model) > 28:
                short_model = short_model[:26] + "…"
            provider_part = ("  ·  " + provider) if provider else ""
            badge.setText("🧠 " + short_model + provider_part)
            bg = "#1d1640" if not is_light_theme() else "#ede9fe"
            fg = "#d7bbff" if not is_light_theme() else "#4338ca"
            border = "#3b2a66" if not is_light_theme() else "#c4b5fd"
            badge.setStyleSheet(
                f"background:{bg};color:{fg};border:1px solid {border};"
                f"border-radius:999px;padding:1px 10px;font-size:10px;font-weight:700;"
            )
            sub_row.addWidget(badge, 0)
            # 能力标签
            # 每个能力都明确写文字，不然一个 🌐 没人知道是"联网"还是"多语言"
            CAP_TAGS = [
                ("web_search",  "🌐 联网搜索"),
                ("vision",      "🖼 支持看图"),
                ("file_upload", "📁 支持上传文件"),
                ("tool_use",    "🛠 调用工具"),
                ("code_exec",   "💻 执行代码"),
            ]
            tag_bg = "rgba(122,252,255,0.08)" if not is_light_theme() else "rgba(37,99,235,0.06)"
            tag_border = "#1f4060" if not is_light_theme() else "#bfdbfe"
            tag_fg = "#7afcff" if not is_light_theme() else "#1d4ed8"
            for k, label in CAP_TAGS:
                if not caps.get(k):
                    continue
                t = QLabel(label)
                t.setStyleSheet(
                    f"background:{tag_bg};color:{tag_fg};border:1px solid {tag_border};"
                    f"border-radius:999px;padding:0 8px;font-size:10px;font-weight:700;"
                )
                sub_row.addWidget(t, 0)
            sub_row.addStretch(1)
            inner.addLayout(sub_row)

        # 分隔线（色带）
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        if is_user:
            line.setStyleSheet(
                "color: transparent; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #00e5ff, stop:1 transparent);"
                " height:1px; max-height:1px;"
            )
        elif is_system:
            line.setStyleSheet("color:transparent;background:#ffd166;height:1px;max-height:1px;")
        elif is_tool:
            line.setStyleSheet(
                "color:transparent;background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ff9e64, stop:1 transparent);"
                " height:1px; max-height:1px;"
            )
        else:
            line.setStyleSheet(
                "color: transparent; background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                " stop:0 #b56bff, stop:0.5 #7afcff, stop:1 transparent); height:1px; max-height:1px;"
            )
        line.setFixedHeight(1); inner.addWidget(line)

        # ====== 图片缩略图带 ======
        imgs = md.get("images", []) or []
        if imgs:
            from PyQt6.QtGui import QPixmap
            import base64 as _b64
            img_row = QHBoxLayout(); img_row.setContentsMargins(0, 2, 0, 6); img_row.setSpacing(8)
            for img in imgs[:8]:
                frame = QFrame()
                frame.setStyleSheet(
                    "QFrame { border-radius:8px; border:1px solid "
                    + ("#1f4060" if not is_light_theme() else "#cbd5e1")
                    + "; background:#060a16; }"
                )
                vb = QVBoxLayout(frame); vb.setContentsMargins(4,4,4,4); vb.setSpacing(2)
                thumb_lbl = QLabel()
                thumb_lbl.setFixedSize(150, 112); thumb_lbl.setScaledContents(True)
                thumb_lbl.setStyleSheet("QLabel { border-radius:6px; background:#03050c; }")
                pm = None
                try:
                    b64 = img.get("b64") or ""
                    if b64:
                        raw = _b64.b64decode(b64)
                        pm = QPixmap(); pm.loadFromData(raw)
                except Exception:
                    pm = None
                if pm and not pm.isNull():
                    scaled = pm.scaled(
                        150, 112, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    thumb_lbl.setPixmap(scaled)
                    thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    thumb_lbl.setText("🖼 图片")
                    thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cap = QLabel(img.get("name") or "图片")
                cap_c = "#005b64" if is_light_theme() else "#7870a8"
                cap.setStyleSheet(f"color:{cap_c};font-size:10px;padding:0 2px;")
                cap.setWordWrap(True)
                vb.addWidget(thumb_lbl, 0, Qt.AlignmentFlag.AlignCenter)
                vb.addWidget(cap, 0)
                img_row.addWidget(frame, 0)
            img_row.addStretch(1); inner.addLayout(img_row)

        # ====== 正文（Markdown / 代码块渲染用 QTextBrowser） ======
        raw_txt = self._msg.content or ""
        _has_text = bool(raw_txt.strip())

        class MDView(QTextBrowser):
            """轻量 Markdown view：支持链接点击打开浏览器 + 复制代码按钮拦截 + 自动高度。"""
            def __init__(self, parent_bubble, text_=None, skin_="cyberpunk"):
                super().__init__(parent_bubble)
                self._skin = skin_
                self._raw_text = text_ or ""
                self._html_last = ""
                self.setOpenLinks(False)  # 我们自己处理，区分"浏览器链接"和"复制代码内部锚"
                self.setOpenExternalLinks(False)
                self.anchorClicked.connect(self._on_anchor)
                self.setFrameShape(QFrame.Shape.NoFrame)
                self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                bg = "transparent"
                self.setStyleSheet(
                    "QTextEdit { background: " + bg + "; border: none; padding: 2px 0 0 0;"
                    " color: inherit; selection-background-color: rgba(122,252,255,0.25); }"
                )
                self.document().setDocumentMargin(0)
                if text_ is not None:
                    self.setContent(text_)

            def setContent(self, raw_text):
                self._raw_text = raw_text or ""
                html = md_to_html(raw_text, skin=self._skin)
                self._html_last = html
                self.setHtml(html)
                self._auto_height()

            def refresh_skin(self, skin):
                self._skin = skin
                if self._raw_text:
                    self.setContent(self._raw_text)

            def _auto_height(self):
                doc = self.document()
                doc.setTextWidth(self.viewport().width())
                h = int(doc.size().height()) + 6
                self.setMinimumHeight(max(16, h))

            def resizeEvent(self, ev):
                super().resizeEvent(ev)
                self._auto_height()

            def _on_anchor(self, qurl: Qt.QUrl if False else None):
                from PyQt6.QtCore import QUrl as _QU
                url = qurl.toString() if hasattr(qurl, "toString") else str(qurl)
                idx = extract_copy_code_idx(url)
                if idx is not None:
                    self._do_copy_code(idx)
                    return
                # 普通 http(s) 链接 → 打开浏览器
                if url.startswith("http://") or url.startswith("https://"):
                    QDesktopServices.openUrl(_QU(url))

            def _do_copy_code(self, idx: int):
                """按索引找出原始文本里的第 idx 个 ``` 代码块内容，复制到剪贴板。"""
                import re as _re
                blocks = []
                for mm in _re.finditer(r"```([a-zA-Z0-9_+\-]*)\n(.*?)```", self._raw_text, _re.DOTALL):
                    blocks.append(mm.group(2).rstrip("\n"))
                if 0 <= idx < len(blocks):
                    QApplication.clipboard().setText(blocks[idx])
                    # 给用户反馈：把该链接文字临时变 "✅ 已复制"（用 statusTip 近似）
                    self.parent().setToolTip(f"✅ 代码块 {idx+1} 内容已复制到剪贴板（{len(blocks[idx])} 字符）")
                    from PyQt6.QtCore import QTimer as _QT
                    _QT.singleShot(2000, lambda: self.parent().setToolTip(""))

        if _has_text or is_tool:
            body_color = ("#0f172a" if is_light_theme() else
                          ("#e8f9ff" if not is_tool else "#ffe8d1"))
            font_size = 14
            if role_str == "system":
                font_size = 13
            md_view = MDView(self, raw_txt if _has_text else "（工具调用摘要）", skin)
            md_view.setMinimumWidth(420 if role_str == "ai" else 260)
            inner.addWidget(md_view)
            self._md_view = md_view
            self._content_lbl = None  # 旧引用（避免别处 NPE 保留字段）
        else:
            # 纯图片无文字
            empty = QLabel("")
            empty.setFixedHeight(0); empty.setStyleSheet("background:transparent;border:none;")
            inner.addWidget(empty)
            self._content_lbl = None
            self._md_view = None

        # 气泡样式表 + 发光
        ss, glow = _T.bubble_frame(role_str)
        bubble.setStyleSheet(ss)
        bubble.setGraphicsEffect(_neon_glow(glow + "66", blur=18))

        self._bubble_frame = bubble
        self._sep_line = line
        self._role_tag = role_tag
        self._ts_lbl = ts_lbl
        self._md_skin = skin

        # 对齐
        if is_user:
            outer.addStretch(1); outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
        else:
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft); outer.addStretch(1)


    def _fmt_ts(self) -> str:
        ts = self._msg.timestamp or ""
        if len(ts) >= 16:
            return ts[11:16]
        return ts


# ============================================================
# 思考中脉冲点动画
# ============================================================
class ThinkingDots(QWidget):
    """< ● ● ● > 三点脉冲动画，表示思考中。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        lay.addStretch(1)
        label = QLabel("⚡  正在")
        label.setStyleSheet("color:#7afcff; font-size:12px; letter-spacing:1px;")
        lay.addWidget(label)

        self._dots = []
        for i in range(3):
            d = QLabel("●")
            d.setStyleSheet(f"color:#00e5ff; font-size:10px; opacity:0.3;")
            self._dots.append(d)
            lay.addWidget(d)

        label2 = QLabel("计算中")
        label2.setStyleSheet("color:#b56bff; font-size:12px; letter-spacing:2px; font-weight:bold;")
        lay.addWidget(label2)
        lay.addStretch(1)

        self._step = 0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(320)

    def _tick(self):
        self._step = (self._step + 1) % 5
        for i, d in enumerate(self._dots):
            if i == (self._step % 3):
                d.setStyleSheet(
                    "color:#00e5ff; font-size:14px;"
                )
            else:
                d.setStyleSheet(
                    "color:#1f3a52; font-size:8px;"
                )

    def stop(self):
        if self._t.isActive():
            self._t.stop()


# ============================================================
# 聊天主视图
# ============================================================
class ChatView(QWidget):
    """聊天主视图：消息列表（科技背景）+ 输入卡片。"""

    message_sent = pyqtSignal(str)                        # 用户点发送：纯文本
    files_attached = pyqtSignal(list)                     # 附加文件：list[dict(name, path, size, preview)]
    message_edit_requested = pyqtSignal(str, str)         # 编辑消息请求：(msg_id, new_content)
    # 新增：上层 (main_window) 提供 『放弃等待并重新发送』 点击信号
    user_requested_retry = pyqtSignal()
    flow_confirmed = pyqtSignal(dict)                     # 用户确认执行 FlowRun 任务卡

    # ---- 拖拽支持 ----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dropEvent(self, e):
        if not e.mimeData().hasUrls():
            return
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if paths:
            self._attach_files(paths)
            e.acceptProposedAction()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatView")
        self.setAcceptDrops(True)                           # 接受文件拖拽
        self._bubbles: list[MessageBubble] = []
        self._flow_cards: dict = {}   # flow_id -> FlowCardWidget
        self._streaming_card: QFrame | None = None
        self._streaming_label: QLabel | None = None
        self._thinking: ThinkingDots | None = None
        self._cursor_blink = True
        self._attachments: list[dict] = []                   # 已附加的文件（发送前预览）
        self._build()

        # 光标闪烁定时器
        self._cur_timer = QTimer(self)
        self._cur_timer.timeout.connect(self._blink_cursor)
        self._cur_timer.start(500)

    # ---- UI 构建 ----
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 消息滚动区（带渐变背景的容器）
        self._scroll = QScrollArea()
        self._scroll.setObjectName("ChatScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._scroll.setAcceptDrops(True)  # 滚动区也接受文件拖放
        # 让 scroll 区域的 drop 转发到自身处理
        self._scroll.dragEnterEvent = lambda e: (e.acceptProposedAction() if e.mimeData().hasUrls() else super(type(self._scroll), self._scroll).dragEnterEvent(e))
        self._scroll.dropEvent = lambda e: (self._attach_files([u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]) or e.acceptProposedAction()) if e.mimeData().hasUrls() else None
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.setStyleSheet("""
            QScrollArea#ChatScroll {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 6px 0 6px 0;
            }
            QScrollBar::handle:vertical {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #00e5ff, stop:1 #b56bff);
                border-radius: 3px;
                min-height: 40px;
            }
            QScrollBar::handle:vertical:hover {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #7afcff, stop:1 #d99bff);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)

        self._msg_container = QWidget()
        self._msg_container.setObjectName("MsgContainer")
        # 背景：深蓝黑 + 扫描线 + 四角光晕
        self._msg_container.setStyleSheet("""
            QWidget#MsgContainer {
                background-color: #05070d;
                background-image:
                    qradialgradient(cx:0.1, cy:0.0, radius:0.9, stop:0 #101a3a, stop:1 transparent),
                    qradialgradient(cx:0.95, cy:1.0, radius:0.9, stop:0 #240a3a, stop:1 transparent),
                    repeating-linear-gradient(angle 0deg, stop:0 transparent, stop:2px transparent, stop:3px rgba(0,229,255,0.025));
                border: none;
            }""")

        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(24, 24, 24, 24)
        self._msg_layout.setSpacing(14)
        self._msg_layout.addStretch()

        self._scroll.setWidget(self._msg_container)
        root.addWidget(self._scroll, 1)

        # 底部输入区 — 清爽紧凑玻璃卡片
        input_card = QFrame()
        input_card.setObjectName("InputCard")
        input_card.setStyleSheet("""
            QFrame#InputCard {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(16,22,42,0.92), stop:1 rgba(8,10,22,0.96));
                border-top: 1px solid #1c294d;
            }""")
        card_glow = _neon_glow("#00e5ff22", blur=18, yoff=-4)
        input_card.setGraphicsEffect(card_glow)

        in_root = QVBoxLayout(input_card)
        in_root.setContentsMargins(12, 8, 12, 10)
        in_root.setSpacing(6)

        # 附件预览区（发送前显示已拖拽/选择的文件，可 × 移除）
        self._attach_wrap = QFrame()
        self._attach_wrap.setFixedHeight(44)
        self._attach_wrap.setObjectName("AttachWrap")
        self._attach_wrap_lay = QHBoxLayout(self._attach_wrap)
        self._attach_wrap_lay.setContentsMargins(0, 0, 0, 0)
        self._attach_wrap_lay.setSpacing(6)
        self._attach_wrap_lay.addStretch(1)
        self._attach_wrap.hide()  # 无附件时不占空间
        in_root.addWidget(self._attach_wrap)

        # 主行：📎 附件按钮 + 输入框 + 发送按钮（尺寸大幅缩小，清爽）
        row = QHBoxLayout()
        row.setSpacing(8)

        # 附件按钮（小巧）
        attach_btn = QPushButton("📎")
        attach_btn.setToolTip("附加文件（也可直接拖文件到窗口）")
        attach_btn.setFixedSize(38, 38)
        attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        attach_btn.setStyleSheet("""
            QPushButton {
                background: #0e1630; color: #7afcff;
                border: 1px solid #1e3055; border-radius: 8px; font-size: 16px;
            }
            QPushButton:hover { background: #12204a; border-color: #00e5ff; color: #ffffff; }
            QPushButton:pressed { background: #1a2a60; }
        """)
        attach_btn.clicked.connect(self._on_pick_files)
        row.addWidget(attach_btn, 0, Qt.AlignmentFlag.AlignBottom)

        # 输入框（高度缩小，清爽）
        self._input = QTextEdit()
        self._input.setObjectName("CyberInput")
        self._input.setPlaceholderText("▍ 说点什么…（拖拽文件到此处可附加）")
        self._input.setMinimumHeight(52)
        self._input.setMaximumHeight(180)
        self._input.setStyleSheet("""
            QTextEdit#CyberInput {
                background: #060913;
                color: #e8f9ff;
                border: 1px solid #1e3055;
                border-radius: 10px;
                padding: 10px 12px;
                font-size: 14px;
                selection-background-color: #005566;
                selection-color: #ffffff;
            }
            QTextEdit#CyberInput:focus {
                border: 1px solid #00e5ff;
                background: #081325;
            }
        """)
        self._input.installEventFilter(self)
        # 自适应高度（根据内容，最多 180）
        self._input.textChanged.connect(self._autosize_input)

        # 发送按钮（清爽小尺寸，不再是大厚块）
        self._send_btn = QPushButton("◈ 发送")
        self._send_btn.setObjectName("SendBtn")
        self._send_btn.setFixedSize(80, 38)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setStyleSheet("""
            QPushButton#SendBtn {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #00e5ff, stop:1 #b56bff);
                color: #051422;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QPushButton#SendBtn:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #7afcff, stop:1 #d99bff);
                color: #000000;
            }
            QPushButton#SendBtn:pressed { padding-left: 2px; }
            QPushButton#SendBtn:disabled {
                background: #1a2040; color: #4a5880;
                border: 1px solid #223060;
            }
        """)
        send_eff = _neon_glow("#00e5ff60", blur=14)
        self._send_btn.setGraphicsEffect(send_eff)
        self._send_btn.clicked.connect(self._on_send)

        self._input.keyPressEvent = self._on_key_press

        row.addWidget(self._input, 1)
        row.addWidget(self._send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        in_root.addLayout(row)

        # 快捷键提示（缩小，放最底行，淡色不抢视觉）
        hint = QLabel("↵ ENTER 发送   ·   ⇧↵ 换行   ·   ⌃K 清空   ·   拖拽/📎 附加文件")
        hint.setStyleSheet("color:#3d546e; font-size:10px; letter-spacing:0.3px; padding: 0 2px;")
        in_root.addWidget(hint, 0)

        root.addWidget(input_card)

    # ---- 事件：输入框发光 / 按键 ----
    def eventFilter(self, obj, event):
        if obj is self._input:
            if event.type() == event.Type.FocusIn:
                obj.setGraphicsEffect(_neon_glow("#00e5ff60", blur=22))
            elif event.type() == event.Type.FocusOut:
                eff = obj.graphicsEffect()
                if eff: eff.setEnabled(False)
                obj.setGraphicsEffect(None)
        return super().eventFilter(obj, event)

    def _on_key_press(self, event):
        from PyQt6.QtWidgets import QTextEdit
        if (event.key() == Qt.Key.Key_Return
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                and not event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._on_send()
            return
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_K):
            self._input.clear()
            return
        QTextEdit.keyPressEvent(self._input, event)

    def _on_send(self):
        if self._send_btn.isEnabled() is False:
            return
        text = self._input.toPlainText().strip()
        # 区分图片 vs 普通文件：图片通过 _pending_images 注入到用户消息 metadata，文件拼正文
        attachments = list(self._attachments)
        images = [a for a in attachments if a.get("kind") == "image" and a.get("b64")]
        files = [a for a in attachments if a.get("kind") != "image"]
        
        if attachments:
            self._attachments = []
            self._render_attachments()
            self.files_attached.emit([])
        
        if files:
            header_parts = []
            for a in files:
                pv = a.get("preview") or ""
                title = "📄 附件【" + a.get("name","?") + "】（" + self._fmt_size(a.get("size",0)) + "）："
                block = "```\n" + pv.rstrip() + "\n```"
                header_parts.append(title + "\n" + block)
            header = "\n\n".join(header_parts)
            if text:
                text = header + "\n\n---\n\n" + text
            else:
                text = header
        
        # 图片挂到 _pending_images，main_window 接收 message_sent 时一起读
        self._pending_images = [
            {"name": a["name"], "mime": a.get("mime","image/png"),
             "b64": a.get("b64",""), "path": a.get("path",""),
             "size": a.get("size", 0)}
            for a in images
        ]
        
        if not text and not images:
            return
        display_text = text if text else ("[图片 ×" + str(len(images)) + "]" if images else "")
        self._input.clear()
        self.message_sent.emit(display_text)
    @staticmethod
    def _fmt_size(size: int) -> str:
        s = size
        unit = "B"
        for u in ["KB","MB","GB"]:
            if s >= 1024:
                s /= 1024
                unit = u
            else: break
        if unit == "B":
            return f"{int(s)} B"
        return f"{s:.1f} {unit}"

    # ---- 附件管理 ----
    def _autosize_input(self):
        """输入框根据文本行数在 52~180 之间自动调整高度。"""
        doc = self._input.document()
        # doc.size().height() 返回内容所需高度
        h = int(doc.size().height()) + 20  # 加 padding
        h = max(52, min(h, 180))
        if abs(self._input.height() - h) > 3:
            self._input.setFixedHeight(h)

    def _on_pick_files(self):
        """📎 按钮：系统文件选择对话框"""
        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择文件", "",
            "所有文件 (*);;文本 (*.txt *.md *.py *.json *.csv);;图片 (*.png *.jpg *.jpeg *.gif *.webp)"
        )
        if paths:
            self._attach_files(paths)

    def _attach_files(self, paths: list[str]):
        """附加文件：构造 name/path/size/preview 并显示预览 chip。"""
        from pathlib import Path
        import os
        new_items = []
        for p in paths:
            try:
                st = os.stat(p)
            except OSError:
                continue
            fpath = Path(p)
            name = fpath.name
            size = st.st_size
            preview = self._make_preview(p, max_chars=800)
            meta = {
                "name": name,
                "path": str(fpath.absolute()),
                "size": size,
                "preview": preview,
            }
            self._attachments.append(meta)
            new_items.append(meta)
        if new_items:
            self._render_attachments()
            self.files_attached.emit(list(self._attachments))

    def _make_preview(self, path: str, max_chars: int = 800) -> str:
        """文本型文件读前 N 字符作为预览；图片/二进制给一句话描述。"""
        from pathlib import Path
        import os
        p = Path(path)
        text_exts = {".txt",".md",".markdown",".py",".js",".ts",".tsx",".jsx",".json",".yaml",".yml",
                     ".toml",".ini",".cfg",".csv",".xml",".html",".css",".sh",".zsh",".bash",
                     ".rs",".go",".java",".c",".cpp",".h",".rb",".php",".swift",".kt",".scala",".sql"}
        if p.suffix.lower() in text_exts and p.stat().st_size <= 512*1024:  # 512K 以下文本预览
            try:
                txt = p.read_text(encoding='utf-8', errors='replace')
                if len(txt) > max_chars:
                    return txt[:max_chars] + f"\n…（共 {len(txt)} 字，截断预览）"
                return txt
            except Exception as e:
                return f"（读取失败：{e}）"
        # 其他类型：描述
        size = p.stat().st_size
        unit = "B"
        s = size
        for u in ["KB","MB","GB"]:
            if s >= 1024:
                s //= 1024
                unit = u
            else:
                break
        return f"[文件：{p.suffix.lower() or '未知类型'}  大小：{s}{unit}]"

    def _render_attachments(self):
        """把 self._attachments 渲染为可移除的 chip 列表。"""
        # 清空
        while self._attach_wrap_lay.count() > 0:
            item = self._attach_wrap_lay.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
            sp = item.spacerItem()
            if sp: break

        from PyQt6.QtWidgets import QToolButton
        for idx, att in enumerate(self._attachments):
            chip = QFrame()
            chip.setStyleSheet(
                "QFrame { background: rgba(0,229,255,0.06);"
                " border: 1px solid #1f4060; border-radius: 6px; }"
            )
            lay2 = QHBoxLayout(chip)
            lay2.setContentsMargins(8, 4, 4, 4)
            lay2.setSpacing(4)
            icon = QLabel("📄")
            icon.setStyleSheet("font-size:13px;")
            # 显示：文件名 + 大小
            size = att["size"]
            s = size
            unit = "B"
            for u in ["KB","MB","GB"]:
                if s >= 1024:
                    s //= 1024
                    unit = u
                else: break
            label = QLabel(f"{att['name']} · {s}{unit}")
            label.setStyleSheet("color:#9fe9ff; font-size:11px;")
            label.setToolTip(att["path"])
            close_btn = QToolButton()
            close_btn.setText("×")
            close_btn.setAutoRaise(True)
            close_btn.setFixedSize(18, 18)
            close_btn.setStyleSheet(
                "QToolButton { color:#ff3d71; font-size:15px; font-weight:bold; border-radius:9px; }"
                " QToolButton:hover { background: rgba(255,61,113,0.2); }"
            )
            def _make_cb(i):
                return lambda _: self._detach_file(i)
            close_btn.clicked.connect(_make_cb(idx))
            lay2.addWidget(icon, 0)
            lay2.addWidget(label, 0)
            lay2.addWidget(close_btn, 0)
            self._attach_wrap_lay.addWidget(chip, 0)
        self._attach_wrap_lay.addStretch(1)
        self._attach_wrap.setVisible(bool(self._attachments))

    def _detach_file(self, idx: int):
        if 0 <= idx < len(self._attachments):
            del self._attachments[idx]
            self._render_attachments()
            self.files_attached.emit(list(self._attachments))

    # ---- 公共 API：添加消息 / 流式输出 ----
    def set_sending(self, busy: bool):
        """设置发送态（按钮禁用 + 提示）。"""
        self._send_btn.setDisabled(busy)
        self._input.setDisabled(busy)
        if busy:
            self._send_btn.setText("⏳ 思考中")
        else:
            self._send_btn.setText("◈ 发送")

    def set_pending_images(self, items):
        """兼容入口（当前 ChatView 内部已用 _pending_images/_pending_attachments 列表）。"""
        if not hasattr(self, '_pending_images'):
            setattr(self, '_pending_images', list(items) if items else [])
        else:
            setattr(self, '_pending_images', list(items) if items else [])

    def set_pending_attachments(self, items):
        """兼容入口（当前 ChatView 内部已用 _pending_images/_pending_attachments 列表）。"""
        if not hasattr(self, '_pending_attachments'):
            setattr(self, '_pending_attachments', list(items) if items else [])
        else:
            setattr(self, '_pending_attachments', list(items) if items else [])

    def _pop_stretch(self):
        # 移除尾部 stretch
        if self._msg_layout.count() > 0:
            last = self._msg_layout.itemAt(self._msg_layout.count() - 1)
            if last and last.spacerItem():
                self._msg_layout.takeAt(self._msg_layout.count() - 1)


    def set_llm_context(self, model_name: str, capabilities: dict | None,
                        provider_display: str = ""):
        """上层调用：告诉 ChatView 当前在用的模型 & 能力。"""
        self._llm_model = model_name or ""
        self._llm_caps = capabilities or {}
        self._llm_provider = provider_display or ""

    def _apply_llm_metadata(self, msg) -> None:
        """给 msg metadata 打上 model/caps/provider（在 add_message 前调用）。"""
        md = msg.metadata or {}
        if self._llm_model and msg.role == ChatRole.ASSISTANT:
            md.setdefault("model_name", self._llm_model)
            md.setdefault("capabilities", dict(self._llm_caps))
            if self._llm_provider:
                md.setdefault("provider", self._llm_provider)
            msg.metadata = md

    def add_message(self, msg: Message):
        """添加一条消息气泡（ASSISTANT/TOOL 会自动贴上当前 LLM 元信息）。"""
        self._pop_stretch()
        self._apply_llm_metadata(msg)
        bubble = MessageBubble(msg)
        bubble.edit_requested = self._on_bubble_edit_requested
        self._bubbles.append(bubble)
        self._msg_layout.addWidget(bubble)
        # 流程蓝图任务卡
        flow_data = (msg.metadata or {}).get("flow_run")
        if flow_data:
            card = FlowCardWidget(flow_data)
            card.flow_confirmed.connect(self._on_flow_card_confirmed)
            self._flow_cards[flow_data.get("id", "")] = card
            self._msg_layout.addWidget(card)
        self._msg_layout.addStretch()
        QTimer.singleShot(40, self._scroll_to_bottom)

    def add_flow_card(self, flow_data: dict):
        """添加一个流程蓝图任务卡到聊天区底部（流式完成后由 main_window 调用）。"""
        self._pop_stretch()
        card = FlowCardWidget(flow_data)
        card.flow_confirmed.connect(self._on_flow_card_confirmed)
        self._flow_cards[flow_data.get("id", "")] = card
        self._msg_layout.addWidget(card)
        self._msg_layout.addStretch()
        QTimer.singleShot(40, self._scroll_to_bottom)

    def _on_flow_card_confirmed(self, flow_dict):
        """任务卡确认执行 → 转发给上层 main_window。"""
        self.flow_confirmed.emit(flow_dict)

    def update_flow_card(self, flow_id: str, step_label: str, status: str, summary: str):
        """更新任务卡中某步的状态（由 main_window 的 _FlowWorker 回调）。"""
        card = self._flow_cards.get(flow_id)
        if card:
            card.update_step(step_label, status, summary)

    def finish_flow_card(self, flow_id: str, flow_dict: dict):
        """任务卡执行完成。"""
        card = self._flow_cards.get(flow_id)
        if card:
            card.set_finished(flow_dict)

    def _on_bubble_edit_requested(self, msg_id: str, new_text: str):
        """气泡点击『保存并重新发送』时被调用：发射给上层。"""
        self.message_edit_requested.emit(msg_id, new_text)

    def start_streaming(self):
        """开始流式输出（AI 思考卡 + 脉冲点 + 空内容占位）。"""
        self._pop_stretch()

        card = QFrame()
        card.setObjectName("AIStreamingCard")
        card.setMaximumWidth(640)
        card.setMinimumWidth(240)
        card.setStyleSheet("""
            QFrame#AIStreamingCard {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #13152a, stop:1 #0b0a1a);
                border: 1px solid #3a2d70;
                border-radius: 14px;
                border-top-left-radius: 2px;
            }""")
        card.setGraphicsEffect(_neon_glow("#9060ff50", blur=18))

        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)

        # 头部
        meta_row = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet("color:#b56bff; font-size:10px;")
        tag = QLabel("CORE AI · 生成中")
        tag.setStyleSheet(
            "color:#b56bff; font-weight:700; font-size:10px; letter-spacing:1px;"
        )
        meta_row.addWidget(dot, 0)
        meta_row.addWidget(tag, 0)
        meta_row.addStretch(1)
        # 模型名徽章（流式中就已经显示给你，让你知道"是哪个模型在答"）
        if self._llm_model:
            short_m = self._llm_model if len(self._llm_model) <= 28 else self._llm_model[:26] + "…"
            part = (" · " + self._llm_provider) if self._llm_provider else ""
            badge = QLabel("🧠 " + short_m + part)
            is_bz = is_light_theme()
            badge.setStyleSheet(
                "background:" + ("#ede9fe" if is_bz else "#1d1640") + ";"
                "color:" + ("#4338ca" if is_bz else "#d7bbff") + ";"
                "border:1px solid " + ("#c4b5fd" if is_bz else "#3b2a66") + ";"
                "border-radius:999px;padding:1px 10px;font-size:10px;font-weight:700;"
            )
            meta_row.addWidget(badge, 0)
        meta_row.addWidget(QLabel(""), 1)
        lay.addLayout(meta_row)

        # 能力标签（流式中直接显示在色带上）
        caps = self._llm_caps or {}
        if any(caps.values()):
            cap_row = QHBoxLayout()
            cap_row.setSpacing(6)
            CAP_TAGS2 = [
                ("web_search",  "🌐 联网搜索"),
                ("vision",      "🖼 支持看图"),
                ("file_upload", "📁 支持上传文件"),
                ("tool_use",    "🛠 调用工具"),
                ("code_exec",   "💻 执行代码"),
            ]
            is_bz = is_light_theme()
            tag_bg = "rgba(37,99,235,0.06)" if is_bz else "rgba(122,252,255,0.08)"
            tag_border = "#bfdbfe" if is_bz else "#1f4060"
            tag_fg = "#1d4ed8" if is_bz else "#7afcff"
            for k, label in CAP_TAGS2:
                if not caps.get(k):
                    continue
                t = QLabel(label)
                t.setStyleSheet(
                    f"background:{tag_bg};color:{tag_fg};border:1px solid {tag_border};"
                    f"border-radius:999px;padding:0 8px;font-size:10px;font-weight:700;"
                )
                cap_row.addWidget(t, 0)
            cap_row.addStretch(1)
            lay.addLayout(cap_row)

        # 色带
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 #b56bff, stop:0.5 #7afcff, stop:1 transparent);"
            " max-height:1px; height:1px; color:transparent;"
        )
        line.setFixedHeight(1)
        lay.addWidget(line)

        # 脉冲点
        self._thinking = ThinkingDots()
        lay.addWidget(self._thinking)

        # 内容区（动态增长）
        self._streaming_label = QLabel("▍")
        self._streaming_label.setWordWrap(True)
        self._streaming_label.setStyleSheet(
            "color:#e8f9ff; font-size:14px; line-height:1.7em;"
            "padding: 2px 0 0 0;"
        )
        lay.addWidget(self._streaming_label)

        # 对齐（AI 在左）
        wrap = QWidget()
        wrap_lay = QHBoxLayout(wrap)
        wrap_lay.setContentsMargins(0, 0, 0, 0)
        wrap_lay.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)
        wrap_lay.addStretch(1)

        self._streaming_card = wrap
        self._msg_layout.addWidget(wrap)
        self._msg_layout.addStretch()
        QTimer.singleShot(30, self._scroll_to_bottom)
        self.set_sending(True)
        # 卡死检测心跳：记录开始时间 + 每 3 秒检查一次
        import time
        self._last_chunk_at = time.time()
        if getattr(self, "_heartbeat_timer", None) is None:
            self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._heartbeat_check_stuck)
        self._heartbeat_timer.start(3000)

    def _heartbeat_check_stuck(self):
        """每 3s 检查流式输出是否超过 18s 无新字符。"""
        import time
        if not self._send_btn.isEnabled():   # 还在发送中
            elapsed = time.time() - self._last_chunk_at
            if elapsed > 18 and getattr(self, "_stuck_notice_widget", None) is None:
                self._show_stuck_notice(elapsed)

    def _show_stuck_notice(self, elapsed):
        """流式卡片底部加一个"疑似卡住，点我取消并重新发送"按钮。"""
        if self._streaming_card is None:
            return
        frame = None
        # 找到 card (AIStreamingCard) 的 lay
        card = None
        w = self._streaming_card
        # wrap → lay → card 是第一子 widget(Frame)
        from PyQt6.QtWidgets import QLayout
        inner = self._streaming_card.layout()
        if inner is None: return
        for idx in range(inner.count()):
            it = inner.itemAt(idx)
            cand = it.widget() if it else None
            if cand and cand.objectName() == "AIStreamingCard":
                card = cand; break
        if card is None: return
        vlay = card.layout()
        if vlay is None: return
        is_bz = is_light_theme()
        wrap_w = QFrame()
        wrap_w.setStyleSheet(
            "QFrame { background: " + ("#fef3c7" if is_bz else "#2a1e05") +
            "; color: " + ("#7c2d12" if is_bz else "#ffd36b") +
            "; border-radius: 8px; border: 1px dashed " +
            ("#d97706" if is_bz else "#a8761c") + "; }"
        )
        hr = QHBoxLayout(wrap_w); hr.setContentsMargins(10, 6, 10, 6); hr.setSpacing(8)
        lb = QLabel(f"⚠️  已等待 {int(elapsed)}s 未收到新内容，疑似卡住了？")
        lb.setStyleSheet(
            "color:" + ("#7c2d12" if is_bz else "#ffd36b") +
            ";font-size:11px;font-weight:700;"
        )
        retry_btn = QToolButton(); retry_btn.setText("🔄 取消并重新发送")
        retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        retry_btn.setStyleSheet(
            "QToolButton { background:" + ("#f59e0b" if is_bz else "#c27b00") +
            ";color:#ffffff;border-radius:6px;padding:4px 10px;font-size:11px;font-weight:800; }"
            " QToolButton:hover { background:" + ("#d97706" if is_bz else "#e0920d") + "; }"
        )
        def _do_retry(*_):
            # 停掉流式 & 发射信号让上层重发
            try:
                if self._heartbeat_timer: self._heartbeat_timer.stop()
            except Exception: pass
            self.set_sending(False)
            self.finish_streaming("")
            self.user_requested_retry.emit()
        retry_btn.clicked.connect(_do_retry)
        hr.addWidget(lb, 1); hr.addWidget(retry_btn, 0)
        vlay.addWidget(wrap_w)
        self._stuck_notice_widget = wrap_w
        QTimer.singleShot(40, self._scroll_to_bottom)

    def append_stream_chunk(self, chunk: str):
        """追加流式块。"""
        import time
        self._last_chunk_at = time.time()
        if self._streaming_label and chunk:
            t = self._streaming_label.text()
            # 如果是光标占位，先去掉
            if t == "▍":
                t = ""
            # 光标永远在最后
            t = t.rstrip("▍")
            self._streaming_label.setText(t + chunk + "▍")
            self._cursor_blink = True
            QTimer.singleShot(8, self._scroll_to_bottom)

    def _blink_cursor(self):
        if not self._streaming_label:
            return
        t = self._streaming_label.text()
        if not t:
            return
        # 末尾光标切换显示
        if t.endswith("▍"):
            self._streaming_label.setText(t[:-1])
        else:
            self._streaming_label.setText(t + "▍")

    def finish_streaming(self, full_text: str):
        """完成流式：转正式气泡。"""
        self.set_sending(False)
        # 停卡死心跳
        tmr = getattr(self, "_heartbeat_timer", None)
        if tmr is not None:
            try: tmr.stop()
            except Exception: pass
            self._heartbeat_timer = None
        sw = getattr(self, "_stuck_notice_widget", None)
        if sw is not None:
            try:
                sw.setParent(None)
                sw.deleteLater()
            except Exception: pass
            self._stuck_notice_widget = None
        # 停掉思考/光标动画
        if self._thinking:
            try:
                self._thinking.stop()
            except Exception:
                pass
            self._thinking.deleteLater()
            self._thinking = None
        if self._streaming_card:
            # 从布局里移除流式卡片
            self._msg_layout.removeWidget(self._streaming_card)
            self._streaming_card.deleteLater()
            self._streaming_card = None
        self._streaming_label = None

        if full_text:
            # 去掉末尾光标
            full_text = full_text.rstrip("▍").rstrip()
            from ..protocol.types import Message, ChatRole
            msg = Message(role=ChatRole.ASSISTANT, content=full_text)
            # 强制再打一次（上层可能没 set_llm_context），并把它真正写进 msg.metadata
            if (not msg.metadata) or "model_name" not in (msg.metadata or {}):
                self._apply_llm_metadata(msg)
            md = msg.metadata or {}
            if self._llm_model and "model_name" not in md:
                md["model_name"] = self._llm_model
                md.setdefault("capabilities", dict(self._llm_caps))
                if self._llm_provider: md.setdefault("provider", self._llm_provider)
                msg.metadata = md
            self.add_message(msg)

    def clear_messages(self):
        """清空所有消息。"""
        # 先确保没有 streaming 卡
        self.finish_streaming("")
        # 倒序删除（保留最后 stretch）
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._bubbles.clear()

    def load_messages(self, messages: list[Message]):
        """加载历史消息。"""
        self.clear_messages()
        for msg in messages:
            self.add_message(msg)

    def _scroll_to_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
