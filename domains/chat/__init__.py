"""聊天记忆域（来自原 AIChat）。

四层记忆架构（working → sqlite → tree → vector）+ ChatAdapter 协议 +
Agent Skill 功能池 + FlowBlueprint/FlowRun 任务卡流程。

向一体化壳注册 ChatFacet（MainWindow widget_factory）。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


# 单例缓存：避免每次开 Tab 都重建 memory/sessions/MainWindow
_main_window: Optional["object"] = None


def _build_main_window():
    """构造 chat 域主面板（原 AIChat MainWindow）

    首次调用时初始化 Config / MemoryEngine / SessionManager，缓存 MainWindow 实例。
    后续调用直接返回缓存（避免数据重复加载）。
    """
    global _main_window
    if _main_window is not None:
        return _main_window

    from domains.chat.config import Config
    from domains.chat.memory.engine import MemoryEngine
    from domains.chat.session.session import SessionManager
    from domains.chat.widgets.main_window import MainWindow

    config = Config.load()
    config.save()
    memory = MemoryEngine(config.data_dir)
    memory.set_summarize_threshold(config.summarize_threshold)
    sessions = SessionManager(config.data_dir)

    window = MainWindow(config, memory, sessions)

    # 注册适配器（沿用原 AIChat 的 register_adapters）
    try:
        from domains.chat.adapters import register_adapters
        register_adapters(window, config)
    except Exception as e:  # noqa: BLE001
        logger.warning("register_adapters failed: %s", e)

    _main_window = window
    return window


def register_domain() -> None:
    """向 shell.main_window 的域注册表注册 chat 域"""
    from shell.main_window import DomainManifest, register_domain as _register

    _register(
        DomainManifest(
            key="chat",
            display_name="聊天记忆",
            widget_factory=_build_main_window,
            supports_standalone_window=True,
        )
    )


# shell.main_window.load_domains() 会 import 本模块触发注册
# 但本模块被 import 时不立即注册（避免 shell 还没初始化），由 load_domains 显式调
# 这里做兜底：try register，失败说明 shell 还没 ready，等 load_domains 显式调
try:
    register_domain()
except Exception:  # noqa: BLE001
    # shell.main_window 还没 import 进来（启动时 import 顺序问题）
    # 由 shell.main_window.load_domains 后续显式调
    pass
