"""小说域（来自 NovelForge）。

实体档案库（八张表）+ RAG + 摘要 + 提案→闸门→权威落库 +
六层零 token 守卫 + 讨论功能（proposal/pending 任务卡/apply_proposal）

清场说明：原 NovelForge 包根目录的 13 个 regen/fix 一次性脚本
和 3 个 backup 目录未迁入（天然清场）。

向一体化壳注册 ChatFacet（MainWindow widget_factory）。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

# 单例缓存
_main_window: Optional["object"] = None


def _build_main_window():
    """构造小说域主面板"""
    global _main_window
    if _main_window is not None:
        return _main_window

    from .ui.main_window import MainWindow

    # NovelForge MainWindow 需要先初始化 Config + ProjectManager
    # 这里做最小初始化，让主面板能起来
    from .config import Config
    config = Config.load()
    config.save()

    window = MainWindow(config)
    _main_window = window
    return window


def register_domain() -> None:
    """向 shell.main_window 注册 novel 域"""
    from shell.main_window import DomainManifest, register_domain as _register

    _register(
        DomainManifest(
            key="novel",
            display_name="小说创作",
            widget_factory=_build_main_window,
            supports_standalone_window=True,
        )
    )


try:
    register_domain()
except Exception:  # noqa: BLE001
    pass
