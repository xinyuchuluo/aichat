"""记忆内核数据类型 — 与具体域解耦，chat 域与外部软件共用同一套契约。

四层模型：
- L1 工作记忆：内存，只影响当轮上下文，不参与检索排序
- L2 原始真相库：SQLite 只追加，任何事实必须回查这里
- L3 树形记忆：异步批量归纳的结构化摘要
- L4 向量/关键词索引：只召回 UUID，不输出事实

观察类型（observation_type）：
- 状态型（fact / preference / entity / reminder）：同槽位新值覆盖旧值，旧记录仍留在 L2
- 时间线型（decision / bugfix / event / flow_step / chat）：只追加，不覆盖
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ---- 观察类型常量 ----
OBSERVATION_CHAT = "chat"
OBSERVATION_FACT = "fact"
OBSERVATION_PREFERENCE = "preference"
OBSERVATION_DECISION = "decision"
OBSERVATION_BUGFIX = "bugfix"
OBSERVATION_EVENT = "event"
OBSERVATION_ENTITY = "entity"
OBSERVATION_REMINDER = "reminder"
OBSERVATION_FLOW_STEP = "flow_step"

# 状态型：走 fact_slots 槽位覆盖
SLOT_OBSERVATIONS = frozenset({
    OBSERVATION_FACT,
    OBSERVATION_PREFERENCE,
    OBSERVATION_ENTITY,
    OBSERVATION_REMINDER,
})

# 时间线型：只追加
TIMELINE_OBSERVATIONS = frozenset({
    OBSERVATION_CHAT,
    OBSERVATION_DECISION,
    OBSERVATION_BUGFIX,
    OBSERVATION_EVENT,
    OBSERVATION_FLOW_STEP,
})

VALID_OBSERVATIONS = SLOT_OBSERVATIONS | TIMELINE_OBSERVATIONS


def now_iso() -> str:
    return datetime.now().isoformat()


def normalize_observation(value: str, default: str = OBSERVATION_CHAT) -> str:
    v = (value or "").strip().lower()
    return v if v in VALID_OBSERVATIONS else default


@dataclass
class MemoryRecord:
    """一条 L2 原始记忆（只追加，不修改）。"""

    uuid: str = field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str = ""
    content: str = ""
    role: str = ""
    scope: str = ""
    observation_type: str = OBSERVATION_CHAT
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=now_iso)
    is_expired: bool = False
    tree_node_id: str | None = None
    # 状态型记忆的槽位键（如 preference 的 key）；空串表示不参与覆盖
    fact_slot: str = ""


@dataclass
class MemoryQuery:
    """记忆检索请求。"""

    query: str = ""
    session_id: str = ""
    scope: str = ""
    top_k: int = 10
    filters: dict[str, Any] = field(default_factory=dict)
    # 只检索指定观察类型；空表示全部
    observation_types: tuple[str, ...] = ()
    fetch_raw: bool = True


@dataclass
class MemoryResult:
    """检索结果项（内容始终来自 L2 回查）。"""

    uuid: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    scope: str = ""
    timestamp: str = ""
    observation_type: str = ""
    source: str = "raw"  # raw / tree / fact


@dataclass
class TreeNode:
    """L3 树形记忆节点。"""

    node_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    summary: str = ""
    scope: str = ""
    level: str = "leaf"  # leaf / mid / root
    raw_uuids: list[str] = field(default_factory=list)
    children_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=now_iso)
