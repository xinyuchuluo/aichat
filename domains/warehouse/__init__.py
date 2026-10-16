"""数仓元数据知识库域（Warehouse）

PRD 落地：4 模块 + 4 守卫 + 权威/草稿二分。

模块：
  1. 元数据采集：源系统表/字段/主键自动拉取
  2. SQL 血缘解析：字段血缘 + JOIN 关联 + 基数风险
  3. 审核入库：草稿→人工修正→权威知识库
  4. 查询校验：溯源、链路完整性、口径统一校验

守卫（零 token 纯 Python，PRD §4.3）：
  1. 循环依赖检测（DAG 成环驳回）
  2. 反向分层检测（下层不能依赖上层）
  3. 多对多强制说明（无说明禁止审核）
  4. 字段术语冲突检测（同名不同义、同义不同名告警）

分层方向：Source → ODS → DWD → DWS → ADS（单向 DAG）

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
    """构造数仓域主面板"""
    global _main_window
    if _main_window is not None:
        return _main_window

    from .widgets.main_window import MainWindow

    window = MainWindow()
    _main_window = window
    return window


def register_domain() -> None:
    """向 shell.main_window 注册 warehouse 域"""
    from shell.main_window import DomainManifest, register_domain as _register

    _register(
        DomainManifest(
            key="warehouse",
            display_name="数仓管控",
            widget_factory=_build_main_window,
            supports_standalone_window=True,
        )
    )


try:
    register_domain()
except Exception:  # noqa: BLE001
    pass
