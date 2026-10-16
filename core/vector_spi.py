"""向量检索 SPI：替代 ChromaDB 硬依赖，跨平台稳。

放弃 ChromaDB（C++ 依赖在 Win 偶尔抽风、PyInstaller 体积大），
走 SPI + 默认 sqlite-vec 实现三级降级（蓝本 NovelForge similarity.py）：
    SqliteVecStore   默认（sqlite-vec 扩展可用时）
    NumpyStore       降级 1（无扩展时纯 numpy 余弦）
    BM25Store         降级 2（无 embedding 时关键词倒排）

Embedding 生成走 core.llm_client，由配置选 embedding 模型。
使用：chat 域四层记忆的 L4、novel 域 RAG 层、其他域不挂。
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA_VECTORS = """
CREATE TABLE IF NOT EXISTS vectors (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL,
    embedding   TEXT,           -- JSON 序列化的 float list
    metadata    TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vectors_scope ON vectors(scope);
"""


class VectorSPI(ABC):
    """向量检索 SPI"""

    @abstractmethod
    def upsert(self, id: str, embedding: list[float], metadata: dict) -> None: ...

    @abstractmethod
    def query(
        self,
        query_embedding: list[float],
        scope: str,
        top_k: int = 10,
        filters: Optional[dict] = None,
    ) -> list[dict]: ...

    @abstractmethod
    def count(self, scope: str) -> int: ...

    @abstractmethod
    def is_available(self) -> bool: ...


class SqliteVecStore(VectorSPI):
    """默认实现：sqlite-vec 扩展"""

    def __init__(self, db_path: Path) -> None:
        self._path: Path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_VECTORS)
        self._conn.commit()
        self._vec_available: bool = False
        try:
            import sqlite_vec  # type: ignore

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self._vec_available = True
            logger.info("sqlite-vec extension loaded")
        except Exception as e:  # noqa: BLE001  -- 降级到 numpy
            logger.warning("sqlite-vec unavailable, will degrade to numpy: %s", e)

    def is_available(self) -> bool:
        return self._vec_available

    def upsert(self, id: str, embedding: list[float], metadata: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO vectors(id, scope, embedding, metadata, created_at)"
            " VALUES(?, ?, ?, ?, ?)",
            (
                id,
                metadata.get("scope", "default"),
                json.dumps(embedding),
                json.dumps(metadata, ensure_ascii=False),
                _now_iso(),
            ),
        )
        self._conn.commit()

    def query(
        self,
        query_embedding: list[float],
        scope: str,
        top_k: int = 10,
        filters: Optional[dict] = None,
    ) -> list[dict]:
        # sqlite-vec 真实扩展走 vec_distance_cosine；这里先用 numpy 兜底
        return NumpyStoreFallback(self._conn).query(query_embedding, scope, top_k, filters)

    def count(self, scope: str) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM vectors WHERE scope = ?", (scope,))
        return cur.fetchone()[0]


class NumpyStoreFallback:
    """sqlite-vec 不可用时的 numpy 余弦兜底（共用一个 sqlite 表）"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def query(
        self,
        query_embedding: list[float],
        scope: str,
        top_k: int = 10,
        filters: Optional[dict] = None,
    ) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, embedding, metadata FROM vectors WHERE scope = ?", (scope,)
        )
        rows = cur.fetchall()
        if not rows:
            return []
        try:
            import numpy as np
        except ImportError:
            return BM25Store._bm25_fallback(rows, query_embedding, top_k)
        q = np.array(query_embedding, dtype=np.float32)
        qn = np.linalg.norm(q) or 1.0
        scored = []
        for row in rows:
            try:
                vec = np.array(json.loads(row["embedding"]), dtype=np.float32)
            except (json.JSONDecodeError, ValueError):
                continue
            n = np.linalg.norm(vec) or 1.0
            score = float(np.dot(q, vec) / (qn * n))
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            if not _match_filters(meta, filters):
                continue
            scored.append({"id": row["id"], "score": score, "metadata": meta})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]


class NumpyStore(VectorSPI):
    """降级 1：纯 numpy 余弦"""

    def __init__(self, db_path: Path) -> None:
        self._inner = SqliteVecStore(db_path)  # 共用同一张表

    def is_available(self) -> bool:
        try:
            import numpy  # noqa: F401
        except ImportError:
            return False
        return True

    def upsert(self, id: str, embedding: list[float], metadata: dict) -> None:
        self._inner.upsert(id, embedding, metadata)

    def query(self, query_embedding, scope, top_k=10, filters=None):
        return NumpyStoreFallback(self._inner._conn).query(
            query_embedding, scope, top_k, filters
        )

    def count(self, scope: str) -> int:
        return self._inner.count(scope)


class BM25Store(VectorSPI):
    """降级 2：无 embedding 时关键词倒排"""

    @staticmethod
    def _bm25_fallback(rows, query_embedding, top_k):
        # query_embedding 此处当做 query token 频次（已是 list[float]）
        # 简化处理：按 metadata 里的 keyword 字段做命中
        scored = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            kw = meta.get("keywords", [])
            score = sum(1 for k in query_embedding if k in kw)  # type: ignore
            scored.append({"id": row["id"], "score": float(score), "metadata": meta})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def __init__(self, db_path: Path) -> None:
        self._inner = SqliteVecStore(db_path)

    def is_available(self) -> bool:
        return True

    def upsert(self, id: str, embedding: list[float], metadata: dict) -> None:
        self._inner.upsert(id, embedding, metadata)

    def query(self, query_embedding, scope, top_k=10, filters=None):
        cur = self._inner._conn.execute(
            "SELECT id, embedding, metadata FROM vectors WHERE scope = ?", (scope,)
        )
        rows = cur.fetchall()
        return self._bm25_fallback(rows, query_embedding, top_k)

    def count(self, scope: str) -> int:
        return self._inner.count(scope)


def _match_filters(meta: dict, filters: Optional[dict]) -> bool:
    if not filters:
        return True
    for k, v in filters.items():
        if meta.get(k) != v:
            return False
    return True


def select_default(db_path: Path | str) -> VectorSPI:
    """三级降级自动选：sqlite-vec → numpy → BM25"""
    path = Path(db_path)
    # 试 sqlite-vec
    store = SqliteVecStore(path)
    if store.is_available():
        return store
    # numpy
    np_store = NumpyStore(path)
    if np_store.is_available():
        return np_store
    # 最后兜底 BM25
    return BM25Store(path)
