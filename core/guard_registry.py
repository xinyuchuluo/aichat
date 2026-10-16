"""零 token 守卫注册框架。

蓝本：NovelForge 的 event_ledger_guard.py + power_canon.py + consistency/checker.py
设计：注册式守卫，零 token（纯代码校验，不调 LLM）。
闸门调用：issues 含 high 级别 → apply 拒绝（strict 模式下）。

初始注册的守卫（迁移 NF 现有 + 数仓 PRD 新写）：
    novel:event_ledger           事件窗口落点 / 单事件不跨卷 / 因果顺序
    novel:power_canon            力量体系单轨校验
    novel:arc_subplot            弧不跨卷 / 暗线节点连续性
    novel:continuity             物品/地点/时间线连续性
    novel:world_setting_integrity 设定写入双轨闸门
    warehouse:cycle_detection    链路成环
    warehouse:reverse_layer      反向分层
    warehouse:term_conflict      字段术语冲突
    warehouse:many_to_many       多对多强制说明
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class Guard(Protocol):
    """守卫契约：实现 check 方法即可"""

    domain: str
    category: str

    def check(self, proposal: Any, context: dict) -> list[dict]:
        """返回 Issue 字典列表：{level, category, title, detail, evidence}"""
        ...


class GuardRegistry:
    """按 domain 注册守卫，运行时统一调用"""

    def __init__(self) -> None:
        # domain → list[Guard | Callable]
        self._guards: dict[str, list[Any]] = {}

    def register(self, domain: str, guard: Any) -> None:
        """注册守卫。guard 可以是 Guard Protocol 实例或一个 Callable"""
        self._guards.setdefault(domain, []).append(guard)

    def register_func(
        self, domain: str, category: str, fn: Callable[[Any, dict], list[dict]]
    ) -> None:
        """函数式注册：fn(proposal, context) → list[Issue dict]"""

        class _FuncGuard:
            pass

        wrapper = _FuncGuard()
        wrapper.domain = domain  # type: ignore[attr-defined]
        wrapper.category = category  # type: ignore[attr-defined]
        wrapper.check = fn  # type: ignore[attr-defined]
        self.register(domain, wrapper)

    def unregister(self, domain: str, guard: Any) -> None:
        if domain in self._guards:
            try:
                self._guards[domain].remove(guard)
            except ValueError:
                pass

    def list_guards(self, domain: str) -> list[Any]:
        return list(self._guards.get(domain, []))

    def run(self, domain: str, proposal: Any, context: Optional[dict] = None) -> list[dict]:
        """跑该 domain 下所有守卫，合并 issues"""
        ctx = context or {}
        all_issues: list[dict] = []
        for guard in self._guards.get(domain, []):
            try:
                # Protocol 实例走 check；Callable 直接调
                if hasattr(guard, "check"):
                    issues = guard.check(proposal, ctx)
                else:
                    issues = guard(proposal, ctx)
                if issues:
                    all_issues.extend(issues)
            except Exception as e:  # noqa: BLE001
                logger.warning("guard %s on %s raised: %s", guard, domain, e)
                all_issues.append(
                    {
                        "level": "medium",
                        "category": getattr(guard, "category", "internal"),
                        "title": "guard execution error",
                        "detail": str(e),
                        "evidence": {},
                    }
                )
        return all_issues


# 模块级单例
registry = GuardRegistry()


def register(domain: str, guard: Any) -> None:
    registry.register(domain, guard)


def register_func(domain: str, category: str, fn: Callable[[Any, dict], list[dict]]) -> None:
    registry.register_func(domain, category, fn)


def run(domain: str, proposal: Any, context: Optional[dict] = None) -> list[dict]:
    return registry.run(domain, proposal, context)
