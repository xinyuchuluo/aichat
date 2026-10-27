"""记忆内核 — 可复用的四层记忆后端。

对外契约：
- MemoryEngine(data_dir)：写入 / 检索 / 时间线 / 统计
- MemoryQuery / MemoryResult / MemoryRecord / TreeNode：数据类型
- 只读检索入口：engine.search / engine.timeline / engine.get
- 写入入口：engine.commit_message（域适配）/ engine.append_record（导入器）
- 档案与项目：paths（personal/company 物理隔离）/ project_store（cwd 最长匹配）
- 项目简报：brief（PROJECT_MEMORY.md 人工段 + 自动段）

内核不依赖任何域；向量层可选（无 embedding 时自动退化为 FTS5）。
"""

from .brief import build_auto_section, refresh_project_file, render_brief
from .engine import MemoryEngine
from .paths import MemoryProfile, resolve_profile
from .project_store import Project, ProjectStore, project_scope
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
    "MemoryProfile",
    "MemoryQuery",
    "MemoryRecord",
    "MemoryResult",
    "Project",
    "ProjectStore",
    "RawStore",
    "Retriever",
    "TreeMemoryStore",
    "TreeNode",
    "VectorIndex",
    "WorkingMemory",
    "build_auto_section",
    "normalize_observation",
    "project_scope",
    "refresh_project_file",
    "render_brief",
    "resolve_profile",
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
