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
_main_window_root: str = ""


def _build_main_window():
    """构造 chat 域主面板（原 AIChat MainWindow）

    首次调用时初始化 Config / MemoryEngine / SessionManager，缓存 MainWindow 实例。
    同一工作区内复用缓存；切换工作区后数据源变了，自动重建。

    记忆内核来自 core.memory（与具体域解耦），数据落共享档案
    ~/AichatStudio/_memory/<profile>/，与 MemSync MCP / CLI 同库。
    首次升级会把旧 <workspace>/chat/memory/ 的库原样接管进来。
    """
    global _main_window, _main_window_root

    from core import workspace as ws_mod

    # 必须用 current()：ensure_default() 会把当前工作区重置为 default
    ws = ws_mod.current() or ws_mod.ensure_default()
    root = str(ws.root)
    if _main_window is not None and _main_window_root == root:
        return _main_window

    # 工作区变了：丢弃旧面板，避免继续读写上一个工作区的数据
    _main_window = None

    from pathlib import Path

    from core.memory import MemoryEngine
    from core.memory.paths import resolve_profile
    from domains.chat.config import Config
    from domains.chat.session.session import SessionManager
    from domains.chat.widgets.main_window import MainWindow

    config = Config.load()
    config.save()
    chat_dir = Path(config.data_dir)
    profile = resolve_profile(getattr(ws.meta, "memory_profile", "") or "")
    profile.import_legacy(chat_dir / "memory")
    memory = MemoryEngine(profile.root)
    memory.set_summarize_threshold(config.summarize_threshold)
    sessions = SessionManager(chat_dir / "sessions")

    window = MainWindow(config, memory, sessions)

    # 注册适配器（沿用原 AIChat 的 register_adapters）
    try:
        from domains.chat.adapters import register_adapters
        register_adapters(window, config)
    except Exception as e:  # noqa: BLE001
        logger.warning("register_adapters failed: %s", e)

    _main_window = window
    _main_window_root = root
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
