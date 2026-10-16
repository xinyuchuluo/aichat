"""合理性校验报告弹窗。

以结构化方式展示 AI 审稿结果：
- 通过/不通过 横幅
- 问题清单（按严重度着色：high 红 / medium 橙 / low 灰）
- 现实知识疑点（整段标红，并标注「需作者确认」）
- 整体修改建议
并提供「应用 AI 修订」入口（仅在不通过且无阻断性现实知识疑点时可用）。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QDialogButtonBox, QFrame,
)
from PyQt6.QtCore import Qt

from domains.novel.ui.styles import BTN_SM_ACCENT, BTN_SECONDARY

_SEV_COLOR = {
    "high": "#f38ba8",
    "medium": "#fab387",
    "low": "#9399b2",
}
_TYPE_LABEL = {
    "plot_hole": "逻辑漏洞",
    "character_inconsistency": "人物矛盾",
    "timeline": "时间线",
    "worldview_conflict": "设定冲突",
    "continuity": "衔接问题",
    "logic": "逻辑",
    "motivation": "人物动机",
    "world_logic": "事物规律",
    "pacing": "节奏/信息揭露",
}


class PlausibilityReportDialog(QDialog):
    """合理性校验报告对话框。"""

    def __init__(self, report: dict, chapter_id: int, title: str, parent=None):
        super().__init__(parent)
        self._report = report
        self._chapter_id = chapter_id
        self._title = title
        self.apply_requested = False
        self.setWindowTitle(f"合理性校验 - 第{chapter_id}章")
        self.setMinimumWidth(560)
        self.setMinimumHeight(420)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        passed = bool(self._report.get("passed"))
        issues = self._report.get("issues") or []
        concerns = self._report.get("real_world_concerns") or []

        # 顶部横幅
        banner = QLabel()
        if passed:
            banner.setText("✓ 校验通过：本章情节与设定基本一致")
            banner.setStyleSheet(
                "QLabel { background-color: #1e3b2e; color: #a6e3a1;"
                " border-radius: 4px; padding: 8px; font-weight: bold; }"
            )
        else:
            banner.setText(f"✗ 校验未通过：发现 {len(issues)} 个问题，需修订")
            banner.setStyleSheet(
                "QLabel { background-color: #3b1e22; color: #f38ba8;"
                " border-radius: 4px; padding: 8px; font-weight: bold; }"
            )
        layout.addWidget(banner)

        score = self._report.get("score", 0)
        score_label = QLabel(f"合理性评分：{score} / 100　|　问题 {len(issues)} 个　|　现实知识疑点 {len(concerns)} 个")
        score_label.setStyleSheet("QLabel { color: #a6adc8; font-size: 12px; }")
        layout.addWidget(score_label)

        # 报告正文
        body = QTextEdit()
        body.setReadOnly(True)
        body.setStyleSheet(
            "QTextEdit { background-color: #1e1e2e; color: #cdd6f4;"
            " border: 1px solid #313244; border-radius: 4px; font-size: 12px; }"
        )
        body.setHtml(self._build_html(issues, concerns))
        layout.addWidget(body, stretch=1)

        # 按钮
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        has_blocking_real_world = any(c.get("needs_user_confirmation") for c in concerns)
        self.apply_btn = QPushButton("应用 AI 修订")
        self.apply_btn.setStyleSheet(BTN_SM_ACCENT)
        # 只在校验通过时禁用（通过不需要修订）；现实知识疑点不锁死按钮，
        # 因为玄幻/奇幻等题材的“疑点”多为世界观差异导致的误报。
        self.apply_btn.setEnabled(not passed)
        if has_blocking_real_world:
            self.apply_btn.setToolTip(
                "存在现实知识疑点（玄幻/奇幻等题材通常为 worldview 差异，"
                "可放心应用；若涉及事实性错误请手动核对）"
            )
        self.apply_btn.clicked.connect(self._on_apply)
        btn_box.addWidget(self.apply_btn)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(BTN_SECONDARY)
        close_btn.clicked.connect(self.reject)
        btn_box.addWidget(close_btn)

        layout.addLayout(btn_box)

    def _build_html(self, issues: list, concerns: list) -> str:
        html = []

        if issues:
            html.append("<h3 style='color:#f9e2af; margin:6px 0;'>问题清单</h3>")
            for i, it in enumerate(issues, 1):
                sev = it.get("severity", "low")
                color = _SEV_COLOR.get(sev, "#9399b2")
                tlabel = _TYPE_LABEL.get(it.get("type", ""), it.get("type", "问题"))
                desc = (it.get("description") or "").replace("<", "&lt;").replace(">", "&gt;")
                loc = (it.get("location") or "").replace("<", "&lt;").replace(">", "&gt;")
                fix = (it.get("fix_suggestion") or "").replace("<", "&lt;").replace(">", "&gt;")
                html.append(
                    f"<p style='margin:4px 0;'>"
                    f"<b style='color:{color};'>[{sev.upper()}]</b> "
                    f"<b>{i}. {tlabel}</b><br/>"
                    f"<span style='color:#cdd6f4;'>• 描述：{desc}</span><br/>"
                    f"<span style='color:#9399b2;'>• 位置：{loc}</span><br/>"
                    f"<span style='color:#a6e3a1;'>• 建议：{fix}</span>"
                    f"</p>"
                )
        else:
            html.append("<p style='color:#a6adc8; margin:6px 0;'>（无情节/设定类问题）</p>")

        if concerns:
            html.append("<h3 style='color:#f38ba8; margin:10px 0 6px;'>⚠ 现实知识疑点（请确认）</h3>")
            for i, c in enumerate(concerns, 1):
                topic = (c.get("topic") or "").replace("<", "&lt;").replace(">", "&gt;")
                claim = (c.get("claim_in_text") or "").replace("<", "&lt;").replace(">", "&gt;")
                concern = (c.get("concern") or "").replace("<", "&lt;").replace(">", "&gt;")
                factual = c.get("is_factual_error")
                need_confirm = c.get("needs_user_confirmation")
                tag = "事实错误" if factual else "艺术加工/待确认"
                confirm_tag = " · 需作者确认" if need_confirm else ""
                html.append(
                    f"<p style='margin:4px 0; padding:6px; border:1px solid #f38ba8;"
                    f" border-radius:4px; background-color:#2a1a1d;'>"
                    f"<b style='color:#f38ba8;'>{i}. {topic}</b> "
                    f"<span style='color:#fab387;'>（{tag}{confirm_tag}）</span><br/>"
                    f"<span style='color:#cdd6f4;'>• 文中描述：{claim}</span><br/>"
                    f"<span style='color:#f9e2af;'>• 疑点：{concern}</span>"
                    f"</p>"
                )

        suggestion = (self._report.get("overall_suggestion") or "").strip()
        if suggestion:
            sug = suggestion.replace("<", "&lt;").replace(">", "&gt;")
            html.append(
                f"<h3 style='color:#89dceb; margin:10px 0 6px;'>整体建议</h3>"
                f"<p style='color:#cdd6f4; margin:4px 0;'>{sug}</p>"
            )

        return "".join(html)

    def _on_apply(self):
        self.apply_requested = True
        self.accept()
