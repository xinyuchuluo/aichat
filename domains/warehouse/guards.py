"""数仓域 — 4 个守卫（PRD §4.3，零 token 纯 Python）。

1. 循环依赖检测：链路是否成环，成环驳回
2. 反向分层检测：下层不能依赖上层（Source→ODS→DWD→DWS→ADS 单向）
3. 多对多强制说明：无业务原因/去重方案，禁止审核
4. 字段术语冲突检测：同名不同义、同义不同名告警
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import LAYER_ORDER, WarehouseDB


@dataclass
class GuardResult:
    """守卫校验结果"""
    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_violation(self, msg: str) -> None:
        self.violations.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def check_cyclic_dependency(
    target_table: str,
    source_table: str,
    db: WarehouseDB,
) -> GuardResult:
    """守卫 1：循环依赖检测。

    检查 source_table → target_table 这条边加入后是否会形成环。
    方法：从 target_table 出发，沿 lineage_tables 反向遍历，看能否到达 source_table。
    """
    result = GuardResult(passed=True)
    if target_table == source_table:
        result.add_violation(f"自依赖：{target_table} 依赖自己")
        return result

    # BFS：从 target 反向找上游
    visited: set[str] = set()
    queue: list[str] = [target_table]
    with db._conn() as c:
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            rows = c.execute(
                "SELECT source_table FROM lineage_tables WHERE target_table=?",
                (current,),
            ).fetchall()
            for r in rows:
                up = r["source_table"]
                if up == source_table:
                    result.add_violation(
                        f"循环依赖：{source_table} → {target_table} 会形成环"
                        f"（{target_table} 已经是 {source_table} 的上游）"
                    )
                    return result
                queue.append(up)
    return result


def check_reverse_layer(
    target_layer: str,
    source_layer: str,
) -> GuardResult:
    """守卫 2：反向分层检测。

    下层不能依赖上层：source 的 LAYER_ORDER 必须 < target 的 LAYER_ORDER。
    Source(0) → ODS(1) → DWD(2) → DWS(3) → ADS(4)
    """
    result = GuardResult(passed=True)
    s = LAYER_ORDER.get(source_layer, -1)
    t = LAYER_ORDER.get(target_layer, -1)
    if s < 0 or t < 0:
        result.add_violation(f"未知分层：source={source_layer}, target={target_layer}")
        return result
    if s >= t:
        result.add_violation(
            f"反向分层依赖：{source_layer}({s}) → {target_layer}({t})，"
            f"下层不能依赖上层（PRD §2.1 单向 DAG）"
        )
    return result


def check_many_to_many(
    cardinality: str,
    many_to_many_reason: str = "",
    many_to_many_dedup: str = "",
) -> GuardResult:
    """守卫 3：多对多强制说明。

    基数为多对多时，必须填写业务原因 + 去重方案，否则禁止审核。
    """
    result = GuardResult(passed=True)
    if cardinality == "多对多":
        if not many_to_many_reason.strip():
            result.add_violation("多对多 JOIN 必须填写业务原因（PRD §4.3.4）")
        if not many_to_many_dedup.strip():
            result.add_violation("多对多 JOIN 必须填写去重方案（PRD §4.3.4）")
    return result


def check_term_conflicts(db: WarehouseDB) -> GuardResult:
    """守卫 4：字段术语冲突检测。

    同名不同义：相同 standard_name 但不同 business_meaning
    同义不同名：相同 business_meaning 但不同 standard_name
    """
    result = GuardResult(passed=True)
    with db._conn() as c:
        rows = c.execute(
            "SELECT standard_name, business_meaning FROM terms WHERE status='active'"
        ).fetchall()

    # 同名不同义
    by_name: dict[str, set[str]] = {}
    for r in rows:
        by_name.setdefault(r["standard_name"], set()).add(r["business_meaning"])
    for name, meanings in by_name.items():
        if len(meanings) > 1:
            result.add_warning(
                f"同名不同义：{name} 有 {len(meanings)} 个不同业务含义"
            )

    # 同义不同名
    by_meaning: dict[str, set[str]] = {}
    for r in rows:
        by_meaning.setdefault(r["business_meaning"], set()).add(r["standard_name"])
    for meaning, names in by_meaning.items():
        if len(names) > 1:
            result.add_warning(
                f"同义不同名：{meaning} 有 {len(names)} 个不同字段名：{', '.join(names)}"
            )
    return result


def run_all_guards(
    target_table: str,
    source_table: str,
    target_layer: str,
    source_layer: str,
    cardinality: str = "",
    many_to_many_reason: str = "",
    many_to_many_dedup: str = "",
    db: WarehouseDB = None,
) -> GuardResult:
    """跑全部 4 个守卫，返回汇总结果。"""
    result = GuardResult(passed=True)
    if db is not None:
        r1 = check_cyclic_dependency(target_table, source_table, db)
        result.violations.extend(r1.violations)
        result.warnings.extend(r1.warnings)
    r2 = check_reverse_layer(target_layer, source_layer)
    result.violations.extend(r2.violations)
    result.warnings.extend(r2.warnings)
    r3 = check_many_to_many(cardinality, many_to_many_reason, many_to_many_dedup)
    result.violations.extend(r3.violations)
    result.warnings.extend(r3.warnings)
    if db is not None:
        r4 = check_term_conflicts(db)
        result.warnings.extend(r4.warnings)
    if result.violations:
        result.passed = False
    return result
