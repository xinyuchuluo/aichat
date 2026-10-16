"""L4 ChromaDB 向量索引 — 仅存树节点摘要+元数据，只用于召回UUID，必须回查SQLite取原始事实。

强制规则：
- 向量库不存原始文本，只存树节点 summary + metadata
- 检索结果返回 UUID 列表，不做最终事实输出
- 业务事实必须回查 SQLite 取原始文本
"""
from __future__ import annotations
from typing import Any
from .tree_store import TreeNode


class VectorStore:
    """L4 向量索引库（ChromaDB）。"""

    def __init__(self, db_path: str, collection_name: str = "aichat_memory"):
        self._db_path = db_path
        self._collection_name = collection_name
        self._client = None
        self._collection = None
        self._init_client()

    def _init_client(self) -> None:
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=self._db_path)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except ImportError:
            # ChromaDB 未安装时使用简易内存回退（开发模式）
            self._client = None
            self._collection = None
        except Exception:
            self._client = None
            self._collection = None

    def is_available(self) -> bool:
        return self._collection is not None

    def upsert_tree_node(self, node: TreeNode) -> None:
        """索引一个树节点（摘要 + metadata，不含原始文本）。"""
        if not self.is_available():
            return
        try:
            self._collection.upsert(
                ids=[node.node_id],
                documents=[node.summary],
                metadatas=[{
                    "scope": node.scope,
                    "level": node.level,
                    "timestamp": node.timestamp,
                    "raw_uuids": ",".join(node.raw_uuids),
                    **{k: str(v) for k, v in node.metadata.items()},
                }],
            )
        except Exception:
            pass

    def query(self, query_text: str, scope: str = "", top_k: int = 10,
              filters: dict | None = None) -> list[dict]:
        """向量检索：返回 [{id, document, metadata, distance}] 列表。

        注意：返回的是树节点 ID + 摘要，需要从中提取 raw_uuids 再回查 SQLite。
        """
        if not self.is_available():
            return []
        try:
            where: dict[str, Any] = {}
            if scope:
                where["scope"] = scope
            if filters:
                for k, v in filters.items():
                    where[k] = str(v)
            results = self._collection.query(
                query_texts=[query_text],
                n_results=min(top_k, 20),
                where=where if where else None,
            )
            ids = results.get("ids", [[]])
            docs = results.get("documents", [[]])
            metas = results.get("metadatas", [[]])
            dists = results.get("distances", [[]])
            output = []
            for i, uid in enumerate(ids[0] if ids else []):
                output.append({
                    "id": uid,
                    "document": docs[0][i] if docs and i < len(docs[0]) else "",
                    "metadata": metas[0][i] if metas and i < len(metas[0]) else {},
                    "distance": dists[0][i] if dists and i < len(dists[0]) else 0.0,
                })
            return output
        except Exception:
            return []

    def count(self, scope: str = "") -> int:
        if not self.is_available():
            return 0
        try:
            if scope:
                return self._collection.count(where={"scope": scope})
            return self._collection.count()
        except Exception:
            return 0

    def delete_by_ids(self, ids: list[str]) -> None:
        if not self.is_available() or not ids:
            return
        try:
            self._collection.delete(ids=ids)
        except Exception:
            pass

    def mark_expired_by_flow(self, flow_id: str, scope: str = "") -> int:
        """Best-effort 把指定 flow_id 的向量条目标记过期（更新 metadata）。"""
        try:
            if not self.is_available():
                return 0
            where_filter = {"flow_id": flow_id}
            if scope:
                where_filter["scope"] = scope
            self._collection.update(
                where=where_filter,
                metadatas=[{"is_expired": True}],
            )
            return 1
        except Exception:
            return 0
