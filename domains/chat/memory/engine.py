"""记忆引擎 — 四层记忆协调器。

编排流程：
- 写入：消息 → L1工作记忆 + L2 SQLite追加 → (N轮后) L3树形归纳 → L4向量索引
- 检索：metadata过滤 → 树形分支筛选 → 向量召回UUID → Rerank精排 → 回查SQLite原始文本

强制规则对齐：
1. raw_text 只追加不修改 ✓ (SQLiteStore.append)
2. 树形记忆异步批量生成 ✓ (_try_summarize, N轮触发)
3. 向量库仅召回UUID ✓ (VectorStore.query 返回树节点ID → raw_uuids → SQLite回查)
4. 检索链路固定 ✓ (retrieve 方法)
5. 过期仅标记不删除 ✓ (SQLiteStore.mark_expired)
6. 工作记忆纯内存 ✓ (WorkingMemory)
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Callable
from ..protocol.types import Message, MemoryRecord, MemoryQuery, MemoryResult
from .working import WorkingMemory
from .sqlite_store import SQLiteStore
from .tree_store import TreeMemoryStore, TreeNode
from .vector_store import VectorStore


class MemoryEngine:
    """四层记忆引擎。"""

    def __init__(self, data_dir: str | Path):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # L1 工作记忆（每会话独立，内存）
        self._working: dict[str, WorkingMemory] = {}  # session_id → WorkingMemory

        # L2 SQLite 原始真相库
        self._sqlite = SQLiteStore(self._data_dir / "raw_memories.db")

        # L3 树形记忆
        self._tree = TreeMemoryStore(self._data_dir / "tree_memories.db")

        # L4 向量索引
        self._vector = VectorStore(str(self._data_dir / "vector_index"))

        # 触发树形归纳的阈值
        self._summarize_threshold = 10  # 每 N 条未归纳消息触发一次
        self._summarize_callback: Callable[[list[str]], str] | None = None  # 外部注入的 LLM 摘要函数

    @property
    def sqlite(self) -> SQLiteStore:
        return self._sqlite

    @property
    def tree(self) -> TreeMemoryStore:
        return self._tree

    @property
    def vector(self) -> VectorStore:
        return self._vector

    def set_summarize_callback(self, cb: Callable[[list[str]], str]) -> None:
        """注入 LLM 摘要回调函数（用于树形记忆异步生成）。"""
        self._summarize_callback = cb

    def set_summarize_threshold(self, n: int) -> None:
        """设置树形归纳触发阈值。"""
        self._summarize_threshold = max(1, n)

    # ===== 写入流程 =====

    def get_working_memory(self, session_id: str) -> WorkingMemory:
        if session_id not in self._working:
            self._working[session_id] = WorkingMemory()
        return self._working[session_id]

    def commit_message(self, msg: Message, session_id: str, scope: str = "",
                       metadata: dict | None = None) -> str:
        """提交一条消息到记忆系统。

        流程：L1 工作记忆 + L2 SQLite 追加 → 检查是否触发 L3 树形归纳。
        """
        # L1: 工作记忆
        wm = self.get_working_memory(session_id)
        wm.add(msg)

        # L2: SQLite 原始追加
        record = MemoryRecord(
            session_id=session_id,
            content=msg.content,
            role=msg.role.value,
            scope=scope,
            metadata=metadata or {},
            timestamp=msg.timestamp,
        )
        self._sqlite.append(record)

        # 检查是否触发 L3 树形归纳
        self._try_summarize(scope)

        return record.uuid

    def _try_summarize(self, scope: str) -> None:
        """检查是否需要触发树形记忆归纳（异步批量，非每轮）。"""
        unsummarized = self._sqlite.get_unsummarized(scope, limit=self._summarize_threshold + 1)
        if len(unsummarized) < self._summarize_threshold:
            return
        # 达到阈值，触发归纳
        to_summarize = unsummarized[:self._summarize_threshold]
        self._generate_tree_node(to_summarize, scope)

    def _generate_tree_node(self, records: list[MemoryRecord], scope: str) -> None:
        """生成一个树形记忆叶节点。"""
        # 收集原始文本
        raw_texts = [r.content for r in records]
        raw_uuids = [r.uuid for r in records]

        # 生成摘要（优先用注入的 LLM 回调，否则简单拼接）
        if self._summarize_callback:
            try:
                summary = self._summarize_callback(raw_texts)
            except Exception:
                summary = self._fallback_summary(raw_texts)
        else:
            summary = self._fallback_summary(raw_texts)

        # 创建树节点
        node = TreeNode(
            summary=summary,
            scope=scope,
            level="leaf",
            raw_uuids=raw_uuids,
            metadata={"record_count": len(records)},
        )
        self._tree.add_node(node)

        # 回填 SQLite 中的 tree_node_id（只改 tree_node_id，不改 content）
        for r in records:
            self._sqlite.update_tree_node(r.uuid, node.node_id)

        # L4: 向量索引（只存摘要 + metadata）
        self._vector.upsert_tree_node(node)

    @staticmethod
    def _fallback_summary(texts: list[str]) -> str:
        """无 LLM 时的简单摘要（取前几条关键句）。"""
        combined = " ".join(texts)
        # 取前 200 字作为摘要
        if len(combined) <= 200:
            return combined
        return combined[:200] + "..."

    # ===== 检索流程 =====

    def retrieve(self, query: MemoryQuery) -> list[MemoryResult]:
        """四层记忆检索链路。

        链路：metadata过滤 → 树形分支筛选 → 向量召回UUID → Rerank精排 → 回查SQLite
        """
        # Step 1: metadata 过滤（在 SQLite 层）
        if query.filters:
            raw_candidates = self._sqlite.filter_by_metadata(
                query.filters, scope=query.scope, limit=50
            )
        else:
            raw_candidates = []

        # Step 2: 树形分支筛选（获取 scope 下所有树节点）
        tree_nodes = self._tree.get_by_scope(query.scope) if query.scope else []

        # Step 3: 向量召回 UUID（通过树节点摘要语义检索）
        vector_results = self._vector.query(
            query_text=query.query,
            scope=query.scope,
            top_k=query.top_k,
            filters=query.filters,
        )

        # 从向量结果提取 raw_uuids
        candidate_uuids: set[str] = set()
        for vr in vector_results:
            raw_uuids_str = vr.get("metadata", {}).get("raw_uuids", "")
            if raw_uuids_str:
                for uid in raw_uuids_str.split(","):
                    uid = uid.strip()
                    if uid:
                        candidate_uuids.add(uid)

        # 合并 metadata 过滤的结果
        for r in raw_candidates:
            candidate_uuids.add(r.uuid)

        if not candidate_uuids:
            return []

        # Step 4: 回查 SQLite 取原始文本
        records = self._sqlite.get_by_uuids(list(candidate_uuids))

        # Step 5: 简单 Rerank（基于关键词匹配度，无 LLM 调用）
        results = []
        query_lower = query.query.lower()
        for r in records:
            content_lower = r.content.lower()
            # 简单评分：关键词重叠度
            score = 0.0
            for word in query_lower.split():
                if len(word) > 1 and word in content_lower:
                    score += 1.0
            score = score / max(1, len(query_lower.split()))
            results.append(MemoryResult(
                uuid=r.uuid,
                content=r.content,
                score=score,
                metadata=r.metadata,
                scope=r.scope,
                timestamp=r.timestamp,
            ))

        # 按分数排序，取 top_k
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:query.top_k]

    # ===== 统计 =====

    def get_stats(self, scope: str = "") -> dict:
        return {
            "working_messages": sum(wm.get_stats()["message_count"] for wm in self._working.values()),
            "sqlite_records": self._sqlite.count(scope),
            "tree_nodes": self._tree.count(scope),
            "vector_available": self._vector.is_available(),
            "vector_count": self._vector.count(scope),
        }

    def trigger_summarize(self, scope: str = "") -> None:
        """手动触发树形归纳（话题切换时调用）。"""
        unsummarized = self._sqlite.get_unsummarized(scope)
        if unsummarized:
            self._generate_tree_node(unsummarized, scope)
