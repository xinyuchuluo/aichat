"""检索链路 — FTS5 双路关键词 + 可选向量，最终回查 L2。

固定顺序：
1. metadata 过滤（SQLite）
2. L2 原文 FTS5 召回
3. L3 摘要 FTS5 召回 → 展开 raw_uuids
4. L4 向量召回（有 embedding 时）→ 展开 raw_uuids
5. 槽位覆盖：状态型记忆只保留槽位当前值
6. 回查 L2 原始文本，按分数排序取 top_k

L1 工作记忆不进入本链路：近期消息不得压过长期事实。
"""

from __future__ import annotations

from .raw_store import RawStore
from .tree_store import TreeMemoryStore
from .types import MemoryQuery, MemoryResult, SLOT_OBSERVATIONS
from .vector import VectorIndex

# 各通道权重：原始文本最可信，摘要是归纳，向量是近似
_W_RAW = 1.0
_W_TREE = 0.6
_W_VECTOR = 0.8
_W_FILTER = 0.4
_W_RECENT = 0.1


class Retriever:
    def __init__(self, raw: RawStore, tree: TreeMemoryStore, vector: VectorIndex):
        self._raw = raw
        self._tree = tree
        self._vector = vector

    def search(self, query: MemoryQuery) -> list[MemoryResult]:
        scores: dict[str, float] = {}
        obs = tuple(query.observation_types)

        def bump(uid: str, score: float) -> None:
            if uid and score > scores.get(uid, 0.0):
                scores[uid] = score

        # 1) metadata 过滤
        if query.filters:
            for rec in self._raw.filter_by_metadata(
                query.filters, scope=query.scope, limit=200
            ):
                bump(rec.uuid, _W_FILTER)

        # 2) L2 原文关键词
        limit = max(50, query.top_k * 5)
        for uid, s in self._raw.search_text(
            query.query, scope=query.scope, limit=limit, observation_types=obs
        ):
            bump(uid, _W_RAW * s)

        # 3) L3 摘要关键词 → 展开原始 UUID
        if query.query.strip():
            for node, s in self._tree.search_summaries(
                query.query, scope=query.scope, limit=max(20, query.top_k * 3)
            ):
                for uid in node.raw_uuids:
                    bump(uid, _W_TREE * s)

        # 4) L4 向量 → 展开原始 UUID
        if query.query.strip() and self._vector.is_available():
            for hit in self._vector.query(
                query.query, scope=query.scope,
                top_k=max(10, query.top_k * 3), filters=query.filters,
            ):
                raw_uuids = str((hit.get("metadata") or {}).get("raw_uuids", ""))
                for uid in raw_uuids.split(","):
                    bump(uid.strip(), _W_VECTOR * float(hit.get("score", 0.0)))

        # 5) 空查询兜底：按时间取最近
        if not scores and not query.query.strip() and query.scope:
            for rec in self._raw.get_by_scope(query.scope, limit=query.top_k):
                bump(rec.uuid, _W_RECENT)

        if not scores:
            return []

        # 控制回查规模，避免超过 SQLite 变量上限
        if len(scores) > 500:
            top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:500]
            scores = dict(top)

        # 6) 回查 L2，过滤过期、观察类型与已被覆盖的槽位值
        records = self._raw.get_by_uuids(list(scores))
        slots = self._raw.slots(query.scope) if any(
            r.observation_type in SLOT_OBSERVATIONS for r in records
        ) else {}

        results: list[MemoryResult] = []
        for rec in records:
            if rec.is_expired:
                continue
            if obs and rec.observation_type not in obs:
                continue
            if rec.observation_type in SLOT_OBSERVATIONS and rec.fact_slot:
                current = slots.get((rec.scope, rec.fact_slot))
                if current and current != rec.uuid:
                    continue  # 已被同槽位的新值覆盖
            results.append(MemoryResult(
                uuid=rec.uuid,
                content=rec.content,
                score=round(scores.get(rec.uuid, 0.0), 4),
                metadata=rec.metadata,
                scope=rec.scope,
                timestamp=rec.timestamp,
                observation_type=rec.observation_type,
                source="raw",
            ))

        results.sort(key=lambda r: (r.score, r.timestamp), reverse=True)
        return results[: query.top_k]
