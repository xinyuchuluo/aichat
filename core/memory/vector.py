"""L4 向量索引 — 只存树节点摘要 + metadata，只用于召回 UUID。

不硬依赖 ChromaDB：底层走 core.vector_spi 的三级降级
（sqlite-vec → numpy 余弦 → BM25）。

没有 embedding 模型时（embedder=None）本层整体关闭，
检索自动退化为 L2/L3 的 FTS5 关键词双路，不影响可用性。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

from .types import TreeNode

Embedder = Callable[[str], Optional[list[float]]]


class VectorIndex:
    """树节点摘要向量索引。"""

    def __init__(self, db_path: str | Path, embedder: Embedder | None = None):
        self._db_path = Path(db_path)
        self._embedder = embedder
        self._store: Any = None

    # ---- 生命周期 ----

    def _ensure(self):
        if self._store is not None:
            return self._store
        if self._embedder is None:
            return None
        from core.vector_spi import select_default

        self._store = select_default(self._db_path)
        return self._store

    def set_embedder(self, embedder: Embedder | None) -> None:
        self._embedder = embedder

    def is_available(self) -> bool:
        store = self._ensure()
        return bool(store and store.is_available())

    # ---- 写入 ----

    def upsert_tree_node(self, node: TreeNode) -> None:
        store = self._ensure()
        if not store or not store.is_available():
            return
        try:
            embedding = self._embedder(node.summary) if self._embedder else None
        except Exception:  # noqa: BLE001  -- embedding 失败不影响主链路
            embedding = None
        if not embedding:
            return
        metadata: dict[str, Any] = {
            "scope": node.scope or "default",
            "level": node.level,
            "timestamp": node.timestamp,
            "raw_uuids": ",".join(node.raw_uuids),
        }
        for k, v in node.metadata.items():
            metadata[k] = str(v)
        store.upsert(node.node_id, embedding, metadata)

    def delete_by_ids(self, ids: list[str]) -> None:
        store = self._ensure()
        if not store or not ids:
            return
        conn = getattr(store, "_conn", None) or getattr(
            getattr(store, "_inner", None), "_conn", None
        )
        if conn is None:
            return
        try:
            conn.executemany("DELETE FROM vectors WHERE id = ?", [(i,) for i in ids])
            conn.commit()
        except sqlite3.Error:
            pass

    def mark_expired_by_flow(self, flow_id: str, scope: str = "") -> int:
        """把 flow_id 相关向量条目标记过期（metadata 更新，不删除）。"""
        store = self._ensure()
        if not store:
            return 0
        conn = getattr(store, "_conn", None) or getattr(
            getattr(store, "_inner", None), "_conn", None
        )
        if conn is None:
            return 0
        try:
            sql = "SELECT id, metadata FROM vectors"
            params: list[Any] = []
            if scope:
                sql += " WHERE scope = ?"
                params.append(scope)
            rows = conn.execute(sql, params).fetchall()
            updated = 0
            for row in rows:
                try:
                    md = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError, IndexError):
                    continue
                if md.get("flow_id") != flow_id or md.get("is_expired"):
                    continue
                md["is_expired"] = True
                conn.execute(
                    "UPDATE vectors SET metadata = ? WHERE id = ?",
                    (json.dumps(md, ensure_ascii=False), row["id"]),
                )
                updated += 1
            conn.commit()
            return updated
        except sqlite3.Error:
            return 0

    # ---- 检索 ----

    def query(
        self,
        query_text: str,
        scope: str = "",
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[dict]:
        """返回 [{id, score, metadata}]；metadata.raw_uuids 为逗号分隔的 L2 UUID。"""
        store = self._ensure()
        if not store or not store.is_available() or not self._embedder:
            return []
        try:
            embedding = self._embedder(query_text)
        except Exception:  # noqa: BLE001
            embedding = None
        if not embedding:
            return []
        try:
            hunks = store.query(embedding, scope or "default", top_k, filters)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for hit in hunks:
            md = hit.get("metadata") or {}
            if md.get("is_expired"):
                continue
            out.append(hit)
        return out

    def count(self, scope: str = "") -> int:
        store = self._ensure()
        if not store:
            return 0
        try:
            return store.count(scope or "default")
        except Exception:  # noqa: BLE001
            return 0
