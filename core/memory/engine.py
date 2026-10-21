"""记忆内核引擎 — L1~L4 四层协调器。

写入：消息 → L1 内存 + L2 SQLite 追加（状态型同时写槽位）→ N 条后归纳 L3 → L4
检索：见 retrieve.py（FTS5 双路 + 可选向量 → 槽位覆盖 → 回查 L2）

内核纪律：本目录不得 import 任何 domains.*；域通过构造参数注入消息对象。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .raw_store import RawStore, derive_fact_slot
from .retrieve import Retriever
from .tree_store import TreeMemoryStore
from .types import (
    MemoryQuery,
    MemoryRecord,
    MemoryResult,
    TreeNode,
    normalize_observation,
    now_iso,
)
from .vector import Embedder, VectorIndex
from .working import WorkingMemory


class MemoryEngine:
    """四层记忆引擎（可被任意软件复用）。"""

    def __init__(self, data_dir: str | Path, embedder: Embedder | None = None):
        self._data_dir = Path(data_dir).expanduser()
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # L1 工作记忆（每会话独立，内存）
        self._working: dict[str, WorkingMemory] = {}

        # L2 原始真相库
        self._raw = RawStore(self._data_dir / "raw_memories.db")

        # L3 树形记忆
        self._tree = TreeMemoryStore(self._data_dir / "tree_memories.db")

        # L4 向量索引（无 embedder 自动关闭，检索退化为 FTS5）
        self._vector = VectorIndex(self._data_dir / "vector_index.db", embedder)

        self._retriever = Retriever(self._raw, self._tree, self._vector)

        self._summarize_threshold = 10
        self._summarize_callback: Callable[[list[str]], str] | None = None

    # ---- 只读视图（面板 / 导入器 / MCP 使用）----

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def sqlite(self) -> RawStore:
        return self._raw

    @property
    def tree(self) -> TreeMemoryStore:
        return self._tree

    @property
    def vector(self) -> VectorIndex:
        return self._vector

    def set_summarize_callback(self, cb: Callable[[list[str]], str] | None) -> None:
        self._summarize_callback = cb

    def set_summarize_threshold(self, n: int) -> None:
        self._summarize_threshold = max(1, int(n))

    def set_embedder(self, embedder: Embedder | None) -> None:
        self._vector.set_embedder(embedder)

    # ---- L1 ----

    def get_working_memory(self, session_id: str) -> WorkingMemory:
        if session_id not in self._working:
            self._working[session_id] = WorkingMemory()
        return self._working[session_id]

    # ---- 写入 ----

    def commit_message(
        self,
        msg: Any,
        session_id: str,
        scope: str = "",
        metadata: dict | None = None,
        observation_type: str = "",
    ) -> str:
        """提交一条消息 / 记忆项。

        msg 只需带 role 与 content（任意域的消息对象或 dict）。
        observation_type 优先取 metadata["memory_type"]，其次入参，默认 chat。
        """
        self.get_working_memory(session_id).add(msg)

        md = metadata if metadata is not None else getattr(msg, "metadata", None) or {}
        obs = normalize_observation(
            md.get("memory_type") if md else "", observation_type or "chat"
        )

        role = getattr(msg, "role", None)
        if isinstance(msg, dict):
            role = msg.get("role")
            content = str(msg.get("content", "") or "")
            timestamp = str(msg.get("timestamp", "") or now_iso())
        else:
            content = str(getattr(msg, "content", "") or "")
            timestamp = str(getattr(msg, "timestamp", "") or now_iso())
        record = MemoryRecord(
            session_id=session_id,
            content=content,
            role=str(getattr(role, "value", role) or ""),
            scope=scope,
            observation_type=obs,
            metadata=dict(md),
            timestamp=timestamp,
            fact_slot=derive_fact_slot(md, obs),
        )
        self._raw.append(record)
        self._try_summarize(scope)
        return record.uuid

    def append_record(self, record: MemoryRecord) -> str:
        """直接追加一条已构造好的记录（导入器 / 外部写入用）。"""
        self._raw.append(record)
        self._try_summarize(record.scope)
        return record.uuid

    # ---- L3 归纳 ----

    def _try_summarize(self, scope: str) -> None:
        pending = self._raw.get_unsummarized(
            scope, limit=self._summarize_threshold + 1
        )
        if len(pending) < self._summarize_threshold:
            return
        self._generate_tree_node(pending[: self._summarize_threshold], scope)

    def _generate_tree_node(self, records: list[MemoryRecord], scope: str) -> None:
        texts = [r.content for r in records]
        if self._summarize_callback:
            try:
                summary = self._summarize_callback(texts)
            except Exception:  # noqa: BLE001  -- 摘要失败回退拼接
                summary = self._fallback_summary(texts)
        else:
            summary = self._fallback_summary(texts)

        node = TreeNode(
            summary=summary,
            scope=scope,
            level="leaf",
            raw_uuids=[r.uuid for r in records],
            metadata={"record_count": len(records)},
        )
        self._tree.add_node(node)
        for r in records:
            self._raw.update_tree_node(r.uuid, node.node_id)
        self._vector.upsert_tree_node(node)

    @staticmethod
    def _fallback_summary(texts: list[str]) -> str:
        combined = " ".join(t for t in texts if t)
        return combined if len(combined) <= 200 else combined[:200] + "..."

    def trigger_summarize(self, scope: str = "") -> None:
        """手动触发归纳（话题切换 / 面板按钮）。"""
        pending = self._raw.get_unsummarized(scope)
        if pending:
            self._generate_tree_node(pending, scope)

    # ---- 检索 ----

    def retrieve(self, query: MemoryQuery) -> list[MemoryResult]:
        return self._retriever.search(query)

    def search(
        self,
        query_text: str,
        scope: str = "",
        top_k: int = 10,
        filters: dict | None = None,
        observation_types: tuple[str, ...] = (),
    ) -> list[MemoryResult]:
        """便捷检索入口（MCP / 外部软件可直接调用）。"""
        return self._retriever.search(MemoryQuery(
            query=query_text,
            scope=scope,
            top_k=top_k,
            filters=filters or {},
            observation_types=observation_types,
        ))

    def timeline(self, scope: str = "", limit: int = 50) -> list[MemoryRecord]:
        """按时间倒序取原始记录（时间线通道）。"""
        return self._raw.get_by_scope(scope, limit=limit)

    def get(self, uuid_str: str) -> MemoryRecord | None:
        return self._raw.get_by_uuid(uuid_str)

    # ---- 统计 ----

    def get_stats(self, scope: str = "") -> dict:
        return {
            "working_messages": sum(
                wm.get_stats()["message_count"] for wm in self._working.values()
            ),
            "sqlite_records": self._raw.count(scope),
            "tree_nodes": self._tree.count(scope),
            "vector_available": self._vector.is_available(),
            "vector_count": self._vector.count(scope),
        }

    def list_scopes(self) -> list[str]:
        return self._tree.list_scopes()
