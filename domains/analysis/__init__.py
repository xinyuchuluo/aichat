"""数据分析域（来自 data-analyst-tool）。

6 步流水线：load_spreadsheet → intent → pandas_script → execute → stats → report
AES 加密 API Key + 每步可配不同模型（profile 级路由）

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
    """构造数据分析域主面板"""
    global _main_window
    if _main_window is not None:
        return _main_window

    from .gui.main_window import MainWindow

    window = MainWindow()
    _main_window = window
    return window


def register_domain() -> None:
    """向 shell.main_window 的域注册表注册 analysis 域"""
    from shell.main_window import DomainManifest, register_domain as _register

    _register(
        DomainManifest(
            key="analysis",
            display_name="数据分析",
            widget_factory=_build_main_window,
            supports_standalone_window=True,
        )
    )


# 兜底注册（shell 未 ready 时 try-except，由 load_domains 显式调）
try:
    register_domain()
except Exception:  # noqa: BLE001
    pass
