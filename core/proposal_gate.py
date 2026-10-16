"""提案→闸门→权威落库（统一写入通道，三档严格度）。

蓝本：NovelForge 现有 discussion/proposal.py + power_canon.py，抽象到内核
让所有域共享。回应"权威/草稿二分"诉求：用"走过闸门没有"代替"谁说的"。

写入流程：
    LLM 产出 → 打包成 Proposal(source=ai|parser)
            → 落入 pending 卡片（task_cards.json，状态 pending）
            → 零 token 闸门先过一遍（guard_registry）
            → 用户点确认 → apply → 落库为权威数据（带版本号 + 快照）
            → 用户点拒绝 → status=rejected（保留可追溯）

source 标签：
    human   人当场写的，直接权威，不走闸门
    ai      LLM 推断的，走 pending → 闸门 → 人确认
    parser  SQL 解析器、pandas 脚本执行结果等，走 pending → 闸门 → 人确认

三档严格度（按域配置）：
    chat        off     消息即真相，不走闸门
    novel       medium  实体/设定/正文变更走闸门，闲聊不走
    studio      off     无 LLM 产出
    warehouse   strict  全拦，所有 SQL 解析草稿必须人工审核
    analysis    off     分析结果就是结果，用户看了就用
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# 严格度
STRICT_OFF = "off"
STRICT_MEDIUM = "medium"
STRICT_STRICT = "strict"

# source 标签
SOURCE_HUMAN = "human"
SOURCE_AI = "ai"
SOURCE_PARSER = "parser"

# 状态
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_REJECTED = "rejected"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Proposal:
    """提案单元"""

    id: str = field(default_factory=_new_id)
    source: str = SOURCE_AI  # human | ai | parser
    domain: str = "chat"  # chat | novel | studio | warehouse | analysis
    title: str = ""
    operations: list[dict[str, Any]] = field(default_factory=list)  # 提案操作列表
    rationale: str = ""  # AI 推理理由
    status: str = STATUS_PENDING  # pending | done | rejected
    created_at: str = field(default_factory=_now_iso)
    applied_at: Optional[str] = None
    rejected_reason: Optional[str] = None
    snapshot_before: Optional[dict[str, Any]] = None  # 落库前快照（用于回滚）
    version: int = 1  # 落库后版本号
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        return cls(
            id=d.get("id", _new_id()),
            source=d.get("source", SOURCE_AI),
            domain=d.get("domain", "chat"),
            title=d.get("title", ""),
            operations=d.get("operations", []),
            rationale=d.get("rationale", ""),
            status=d.get("status", STATUS_PENDING),
            created_at=d.get("created_at", _now_iso()),
            applied_at=d.get("applied_at"),
            rejected_reason=d.get("rejected_reason"),
            snapshot_before=d.get("snapshot_before"),
            version=d.get("version", 1),
            extras=d.get("extras", {}),
        )


@dataclass
class Issue:
    """守卫返回的问题项"""

    level: str  # high | medium | low
    category: str
    title: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApplyResult:
    """apply 的返回"""

    proposal_id: str
    success: bool
    version: int = 0
    issues: list[Issue] = field(default_factory=list)
    error: Optional[str] = None


# 严格度配置（域 → 档位）
DOMAIN_STRICTNESS: dict[str, str] = {
    "chat": STRICT_OFF,
    "novel": STRICT_MEDIUM,
    "studio": STRICT_OFF,
    "warehouse": STRICT_STRICT,
    "analysis": STRICT_OFF,
}


class ProposalGate:
    """提案→闸门→权威落库"""

    def __init__(self, data_root: Path, domain_strictness: Optional[dict[str, str]] = None) -> None:
        self._root: Path = Path(data_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._cards_path: Path = self._root / "task_cards.json"
        self._strictness: dict[str, str] = dict(DOMAIN_STRICTNESS)
        if domain_strictness:
            self._strictness.update(domain_strictness)
        # apply 回调：domain → callable(proposal, snapshot) → Any
        # 由各域在启动时注册自己的落库逻辑
        self._appliers: dict[str, Callable[[Proposal, dict], Any]] = {}
        # 快照回调：domain → callable(proposal) → dict
        self._snapshotters: dict[str, Callable[[Proposal], dict]] = {}

    # ---- 域注册 ----
    def register_applier(self, domain: str, fn: Callable[[Proposal, dict], Any]) -> None:
        self._appliers[domain] = fn

    def register_snapshotters(self, domain: str, fn: Callable[[Proposal], dict]) -> None:
        self._snapshotters[domain] = fn

    def set_strictness(self, domain: str, level: str) -> None:
        if level not in (STRICT_OFF, STRICT_MEDIUM, STRICT_STRICT):
            raise ValueError(f"invalid strictness level {level!r}")
        self._strictness[domain] = level

    # ---- 读取卡片池 ----
    def _load_cards(self) -> list[dict]:
        if not self._cards_path.exists():
            return []
        try:
            data = json.loads(self._cards_path.read_text(encoding="utf-8"))
            return data.get("cards", [])
        except (json.JSONDecodeError, KeyError):
            return []

    def _save_cards(self, cards: list[dict]) -> None:
        payload = {"cards": cards, "updated_at": _now_iso()}
        self._cards_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 提交 ----
    def submit(self, proposal: Proposal) -> str:
        """入 pending 池"""
        if proposal.source == SOURCE_HUMAN:
            # 人写的直接权威：跳过 pending，立即 apply
            return self._apply_immediate(proposal)
        # ai / parser → pending
        cards = self._load_cards()
        cards.append(proposal.to_dict())
        self._save_cards(cards)
        return proposal.id

    def _apply_immediate(self, proposal: Proposal) -> str:
        proposal.status = STATUS_DONE
        proposal.applied_at = _now_iso()
        applier = self._appliers.get(proposal.domain)
        if applier:
            snapshot = self._take_snapshot(proposal)
            applier(proposal, snapshot)
        return proposal.id

    def list_pending(self, domain: Optional[str] = None) -> list[Proposal]:
        cards = self._load_cards()
        result: list[Proposal] = []
        for item in cards:
            if item.get("status") != STATUS_PENDING:
                continue
            if domain is not None and item.get("domain") != domain:
                continue
            result.append(Proposal.from_dict(item))
        return result

    def list_all(self, domain: Optional[str] = None) -> list[Proposal]:
        cards = self._load_cards()
        result: list[Proposal] = []
        for item in cards:
            if domain is not None and item.get("domain") != domain:
                continue
            result.append(Proposal.from_dict(item))
        return result

    # ---- 快照 ----
    def _take_snapshot(self, proposal: Proposal) -> dict:
        fn = self._snapshotters.get(proposal.domain)
        if fn is None:
            return {}
        try:
            return fn(proposal) or {}
        except Exception:  # noqa: BLE001
            return {}

    # ---- 应用 / 拒绝 ----
    def apply(self, proposal_id: str) -> ApplyResult:
        """人点确认时调用"""
        cards = self._load_cards()
        target_idx: Optional[int] = None
        target: Optional[Proposal] = None
        for idx, item in enumerate(cards):
            if item.get("id") == proposal_id and item.get("status") == STATUS_PENDING:
                target_idx = idx
                target = Proposal.from_dict(item)
                break
        if target is None or target_idx is None:
            return ApplyResult(proposal_id=proposal_id, success=False, error="pending proposal not found")

        # 闸门：通过 core.guard_registry 跑守卫（此处延迟导入避免循环）
        from core.guard_registry import registry as guard_registry  # type: ignore

        issues: list[Issue] = []
        strictness = self._strictness.get(target.domain, STRICT_OFF)
        if strictness != STRICT_OFF:
            raw_issues = guard_registry.run(target.domain, target, context={"strictness": strictness})
            issues = [Issue(**i) if isinstance(i, dict) else i for i in raw_issues]
            # high 级别问题直接拒绝
            if any(i.level == "high" for i in issues) and strictness == STRICT_STRICT:
                target.status = STATUS_REJECTED
                target.rejected_reason = "guard high-level issue"
                target.applied_at = _now_iso()
                cards[target_idx] = target.to_dict()
                self._save_cards(cards)
                return ApplyResult(
                    proposal_id=proposal_id, success=False, issues=issues, error="guard rejected"
                )

        # 快照 + apply
        snapshot = self._take_snapshot(target)
        target.snapshot_before = snapshot
        applier = self._appliers.get(target.domain)
        if applier:
            try:
                applier(target, snapshot)
            except Exception as e:  # noqa: BLE001
                return ApplyResult(proposal_id=proposal_id, success=False, issues=issues, error=str(e))

        target.status = STATUS_DONE
        target.applied_at = _now_iso()
        target.version += 1
        cards[target_idx] = target.to_dict()
        self._save_cards(cards)
        return ApplyResult(
            proposal_id=proposal_id, success=True, version=target.version, issues=issues
        )

    def reject(self, proposal_id: str, reason: str) -> None:
        cards = self._load_cards()
        for idx, item in enumerate(cards):
            if item.get("id") == proposal_id and item.get("status") == STATUS_PENDING:
                item["status"] = STATUS_REJECTED
                item["rejected_reason"] = reason
                item["applied_at"] = _now_iso()
                cards[idx] = item
                self._save_cards(cards)
                return

    def rollback(self, proposal_id: str) -> bool:
        """回滚到 snapshot_before：要求域 applier 实现回滚逻辑"""
        cards = self._load_cards()
        for item in cards:
            if item.get("id") == proposal_id and item.get("status") == STATUS_DONE:
                snapshot = item.get("snapshot_before") or {}
                applier = self._appliers.get(item.get("domain"))
                if applier and hasattr(applier, "rollback"):
                    try:
                        applier.rollback(item, snapshot)  # type: ignore[attr-defined]
                        item["status"] = STATUS_REJECTED
                        item["rejected_reason"] = "rolled back"
                        self._save_cards(cards)
                        return True
                    except Exception:  # noqa: BLE001
                        return False
        return False


# 模块级：按工作区分实例
_gates: dict[str, ProposalGate] = {}


def for_workspace(data_root: Path | str, strictness: Optional[dict[str, str]] = None) -> ProposalGate:
    key = str(data_root)
    if key not in _gates:
        _gates[key] = ProposalGate(Path(data_root), strictness)
    return _gates[key]
