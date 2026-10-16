"""AIChatStudio shell — 一体化壳

职责：
- main_window.py        主窗口 + 域面板挂载点
- session_hub.py        会话 + 任务卡中心（来自 proposal_gate 的 pending 池）
- window_manager.py     多窗口/模块拉起
- workspace_switcher.py 工作区切换器
- styles.py             浅色主题 QSS（全局唯一，各域不许改）

壳纪律（硬约束）：
1. 壳负责挂载、不负责业务逻辑
2. 域通过 DomainManifest + Facet 声明能力，壳按需挂界面
3. 各域不许 import shell.styles 之外的样式，不许改全局 QSS
"""

__all__ = [
    "main_window",
    "session_hub",
    "window_manager",
    "workspace_switcher",
    "styles",
]
