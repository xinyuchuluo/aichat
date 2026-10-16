"""短片制片工作台域（Studio）

5 张 SQLite 表 + @资产 正则解析 + 5 套导出模板 + 4 Tab UI。
无 LLM、无向量、无联网（PRD 第六节红线）。

表结构（PRD §二）：
  projects / project_assets / asset_materials / storyboards / shot_asset_link

向一体化壳注册 ChatFacet（MainWindow widget_factory）。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

# 单例缓存
_main_window: Optional["object"] = None


def _build_main_window():
    """构造短片域主面板"""
    global _main_window
    if _main_window is not None:
        return _main_window

    from .widgets.main_window import MainWindow

    window = MainWindow()
    _main_window = window
    return window


def register_domain() -> None:
    """向 shell.main_window 注册 studio 域"""
    from shell.main_window import DomainManifest, register_domain as _register

    _register(
        DomainManifest(
            key="studio",
            display_name="短片制片",
            widget_factory=_build_main_window,
            supports_standalone_window=True,
        )
    )


try:
    register_domain()
except Exception:  # noqa: BLE001
    pass
