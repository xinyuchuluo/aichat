"""
一致性审查报告展示对话框。

在 UI 中的位置：
    写作流水线完成生成阶段后，若启用了一致性检查，
    以模态对话框形式弹出，覆盖在主窗口上方，阻塞用户操作直到做出选择。

作用：
    展示 ConsistencyReport（一致性审查报告）的结果，包括：
    - 审查摘要（通过/未通过，问题数量）
    - 具体问题列表（按严重程度排序：error > warning > info）
    - 每个问题的描述和建议修复方案

    用户可以根据报告选择：
    - 忽略问题并继续提交（将当前生成的内容保存到文件并更新记忆）
    - 重写本章（重新调用写作流水线，生成新的内容）
    - 手动修改（关闭对话框，回到编辑器手动编辑）

组件结构：
    IssueWidget (QFrame)        : 单个一致性问题的可视化展示组件
    ConsistencyViewDialog (QDialog) : 一致性审查报告对话框，包含问题列表和操作按钮
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton,
    QGroupBox, QFrame, QScrollArea, QWidget, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal

from domains.novel.core.consistency.checker import ConsistencyReport, ConsistencyIssue
from domains.novel.ui.styles import BTN_PRIMARY, BTN_SECONDARY


class IssueWidget(QFrame):
    """单个一致性问题的可视化展示组件。

    在 UI 中的位置：
        作为 ConsistencyViewDialog 中问题列表的一项，
        在 QScrollArea 内垂直排列展示。

    展示内容：
        +------------------------------------------+
        | ❌ 严重  [人物一致性]            |
        | 萧炎的修为在第3章是斗者，但第5章突然出现   |
        | 时使用斗师能力，存在境界跃升。              |
        | 💡 建议：在第4章添加萧炎突破斗师的过渡描写    |
        +------------------------------------------+

    样式说明：
        - error  : 红色边框 + 深红背景，文字为红色
        - warning: 橙色边框 + 深橙背景，文字为橙色
        - info   : 蓝色边框 + 深蓝背景，文字为蓝色
    """

    def __init__(self, issue: ConsistencyIssue, parent=None):
        """初始化单个问题展示组件。

        参数：
            issue:   ConsistencyIssue 对象，包含以下字段：
                     - severity : 严重程度（"error" / "warning" / "info"）
                     - category : 问题分类（如 "人物一致性" / "时间线" / "伏笔"）
                     - description: 问题描述
                     - suggestion : 建议修复方案（可选）
            parent:  父组件（滚动区域中的 QWidget）
        """
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(1)

        color_map = {
            "error": "#f38ba8",
            "warning": "#fab387",
            "info": "#89b4fa",
        }
        bg_map = {
            "error": "#311a20",
            "warning": "#2e2418",
            "info": "#1a1e2e",
        }
        color = color_map.get(issue.severity, "#cdd6f4")
        bg = bg_map.get(issue.severity, "#1e1e2e")
        self.setStyleSheet(f"QFrame {{ background-color: {bg}; border: 1px solid {color}; border-radius: 4px; padding: 8px; }}")

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        severity_text = {"error": "❌ 严重", "warning": "⚠️ 警告", "info": "ℹ️ 提示"}
        label = QLabel(severity_text.get(issue.severity, issue.severity))
        label.setStyleSheet(f"color: {color}; font-weight: bold;")
        header.addWidget(label)

        cat_label = QLabel(f"[{issue.category}]")
        cat_label.setStyleSheet("color: #6c7086;")
        header.addWidget(cat_label)
        header.addStretch()
        layout.addLayout(header)

        desc_label = QLabel(issue.description)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #cdd6f4;")
        layout.addWidget(desc_label)

        if issue.suggestion:
            sug_label = QLabel(f"💡 {issue.suggestion}")
            sug_label.setWordWrap(True)
            sug_label.setStyleSheet("color: #a6adc8; font-style: italic;")
            layout.addWidget(sug_label)


class ConsistencyViewDialog(QDialog):
    """一致性审查报告对话框。

    在 UI 中的位置：
        写作流水线完成生成阶段后弹出（模态对话框），
        覆盖在主窗口上方，用户必须做出选择才能继续。

    对话框布局：
        +------------------------------------------+
        | 一致性审查摘要（通过/未通过，绿色/红色）     |
        +------------------------------------------+
        | 问题列表（QScrollArea，可滚动）             |
        |   - IssueWidget 1 (error)                |
        |   - IssueWidget 2 (warning)              |
        |   - IssueWidget 3 (info)                 |
        |   ...                                    |
        +------------------------------------------+
        | [忽略并继续提交] [重写本章] [手动修改]       |
        +------------------------------------------+

    按钮逻辑：
        - 存在 error 级别问题时：
          [忽略并继续提交] -> 发出 ignore_and_continue 信号，关闭对话框
          [重写本章]       -> 发出 rewrite_requested 信号，关闭对话框
          [手动修改]       -> 关闭对话框（拒绝），回到编辑器

        - 不存在 error 级别问题时（只有 warning/info）：
          [确定]           -> 关闭对话框，自动继续提交流程

    信号说明：
        ignore_and_continue: 用户选择忽略问题并继续提交时发出
        rewrite_requested  : 用户选择重写本章时发出
                            （由 MainWindow 接收并重新启动写作流水线）
    """

    # 信号：用户选择忽略一致性问题并继续提交
    ignore_and_continue = pyqtSignal()
    # 信号：用户选择重写本章
    rewrite_requested = pyqtSignal()

    def __init__(self, report: ConsistencyReport, parent=None):
        """初始化一致性审查报告对话框。

        参数：
            report: ConsistencyReport 对象，包含以下字段：
                    - chapter_id   : 审查的章节 ID
                    - passed       : 是否通过审查（error_count == 0）
                    - summary      : 审查摘要文本
                    - issues       : ConsistencyIssue 列表
                    - error_count  : error 级别问题数量
                    - warning_count: warning 级别问题数量

        初始化内容：
            - 保存 ConsistencyReport 引用
            - 设置窗口标题（包含章节号）
            - 设置最小尺寸（宽600 x 高500）
            - 调用 _init_ui() 构建对话框布局
        """
        super().__init__(parent)
        self._report = report
        self.setWindowTitle(f"一致性审查报告 - 第{report.chapter_id}章")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)

        self._init_ui()

    def _init_ui(self):
        """构建一致性审查报告对话框的 UI 布局。

        布局结构：
            1. 摘要标签（顶部）：
               - 若审查通过（passed=True）：绿色粗体显示摘要
               - 若审查未通过：红色粗体显示摘要

            2. 问题列表区（中部，可滚动）：
               - 使用 QScrollArea 包裹一个 QWidget 作为滚动内容
               - 若无问题：显示「✓ 一致性检查通过，未发现问题」
               - 若有问题：按严重程度排序（error > warning > info），
                 为每个问题创建一个 IssueWidget，垂直排列

            3. 按钮区（底部）：
               - 若存在 error 级别问题：显示三个按钮
                 [忽略并继续提交] [重写本章] [手动修改]
               - 若不存在 error（只有 warning/info）：只显示 [确定] 按钮
        """
        layout = QVBoxLayout(self)

        # 摘要
        summary_label = QLabel(self._report.summary)
        if self._report.passed:
            summary_label.setStyleSheet("color: #a6e3a1; font-size: 16px; font-weight: bold; padding: 8px;")
        else:
            summary_label.setStyleSheet("color: #f38ba8; font-size: 16px; font-weight: bold; padding: 8px;")
        layout.addWidget(summary_label)

        # 问题列表（滚动区域）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        if not self._report.issues:
            no_issue = QLabel("✓ 一致性检查通过，未发现问题")
            no_issue.setStyleSheet("color: #a6e3a1; font-size: 14px; padding: 20px;")
            no_issue.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scroll_layout.addWidget(no_issue)
        else:
            # 按 severity 排序：error > warning > info
            sorted_issues = sorted(
                self._report.issues,
                key=lambda i: {"error": 0, "warning": 1, "info": 2}.get(i.severity, 3)
            )
            for issue in sorted_issues:
                scroll_layout.addWidget(IssueWidget(issue))

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        # 按钮区
        btn_layout = QHBoxLayout()

        if self._report.error_count > 0:
            # 有 error：显示三个选项
            ignore_btn = QPushButton("忽略并继续提交")
            ignore_btn.setStyleSheet(BTN_PRIMARY)
            ignore_btn.setMinimumHeight(32)
            ignore_btn.clicked.connect(lambda: (self.ignore_and_continue.emit(), self.accept()))
            btn_layout.addWidget(ignore_btn)

            rewrite_btn = QPushButton("重写本章")
            rewrite_btn.setStyleSheet(BTN_SECONDARY)
            rewrite_btn.setMinimumHeight(32)
            rewrite_btn.clicked.connect(lambda: (self.rewrite_requested.emit(), self.accept()))
            btn_layout.addWidget(rewrite_btn)

            cancel_btn = QPushButton("手动修改")
            cancel_btn.setStyleSheet(BTN_SECONDARY)
            cancel_btn.setMinimumHeight(32)
            cancel_btn.clicked.connect(self.reject)
            btn_layout.addWidget(cancel_btn)
        else:
            # 无 error：只需关闭
            ok_btn = QPushButton("确定")
            ok_btn.setStyleSheet(BTN_PRIMARY)
            ok_btn.setMinimumHeight(32)
            ok_btn.clicked.connect(self.accept)
            btn_layout.addStretch()
            btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)
