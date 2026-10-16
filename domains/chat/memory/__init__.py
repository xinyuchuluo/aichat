"""四层记忆架构。

L1 工作记忆（内存临时）→ L2 SQLite原始真相库（唯一事实源）→ L3 树形记忆（结构化归纳）→ L4 Chroma向量索引（检索路由）

强制规则：
1. SQLite raw_text 字段仅追加、永不修改
2. 树形记忆异步批量生成，N轮对话/话题切换触发，禁止每轮调用LLM
3. 向量库仅做UUID召回，业务事实必须溯源SQLite原始文本
4. 检索链路：metadata过滤 → 树形分支筛选 → 向量召回 → Rerank精排
5. 记忆过期：is_expired布尔标记，无物理删除
6. 工作记忆纯内存运行，无持久化逻辑
"""
from .working import WorkingMemory
from .sqlite_store import SQLiteStore
from .tree_store import TreeMemoryStore
from .vector_store import VectorStore
from .engine import MemoryEngine
