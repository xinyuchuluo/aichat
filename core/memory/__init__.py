"""记忆内核 — 可复用的四层记忆后端。

对外契约：
- MemoryEngine(data_dir)：写入 / 检索 / 时间线 / 统计
- MemoryQuery / MemoryResult / MemoryRecord / TreeNode：数据类型
- 只读检索入口：engine.search / engine.timeline / engine.get
- 写入入口：engine.commit_message（域适配）/ engine.append_record（导入器）

内核不依赖任何域；向量层可选（无 embedding 时自动退化为 FTS5）。
"""

from .engine import MemoryEngine
from .raw_store import RawStore
from .retrieve import Retriever
from .tree_store import TreeMemoryStore
from .types import (
    MemoryQuery,
    MemoryRecord,
    MemoryResult,
    OBSERVATION_BUGFIX,
    OBSERVATION_CHAT,
    OBSERVATION_DECISION,
    OBSERVATION_ENTITY,
    OBSERVATION_EVENT,
    OBSERVATION_FACT,
    OBSERVATION_FLOW_STEP,
    OBSERVATION_PREFERENCE,
    OBSERVATION_REMINDER,
    TreeNode,
    normalize_observation,
)
from .vector import VectorIndex
from .working import WorkingMemory

__all__ = [
    "MemoryEngine",
    "MemoryQuery",
    "MemoryRecord",
    "MemoryResult",
    "RawStore",
    "Retriever",
    "TreeMemoryStore",
    "TreeNode",
    "VectorIndex",
    "WorkingMemory",
    "normalize_observation",
    "OBSERVATION_CHAT",
    "OBSERVATION_FACT",
    "OBSERVATION_PREFERENCE",
    "OBSERVATION_DECISION",
    "OBSERVATION_BUGFIX",
    "OBSERVATION_EVENT",
    "OBSERVATION_ENTITY",
    "OBSERVATION_REMINDER",
    "OBSERVATION_FLOW_STEP",
]
