"""L2 原始真相库 — 唯一事实源。

硬规则：
1. content 只追加，永不修改；过期只写 is_expired 标记，不做物理删除
2. 检索命中走 FTS5(trigram)，中文按三元组切分；低版本 SQLite 自动降级为 LIKE
3. 状态型记忆（fact/preference/entity/reminder）额外写 fact_slots 槽位表，
   检索时只把槽位当前值当作有效事实；旧记录仍保留在 L2
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .types import (
    MemoryRecord,
    SLOT_OBSERVATIONS,
    now_iso,
)

# 只保留中英文、数字、下划线，避免 FTS5 查询语法注入
_TERM_RE = re.compile(r"[0-9A-Za-z_\u3400-\u4dbf\u4e00-\u9fff]+")
_TRIGRAM_MIN = 3  # trigram 至少需要 3 个字符

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_memories (
    uuid TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    content TEXT NOT NULL,
    role TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    observation_type TEXT NOT NULL DEFAULT 'chat',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL,
    is_expired INTEGER NOT NULL DEFAULT 0,
    tree_node_id TEXT,
    fact_slot TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_session ON raw_memories(session_id);
CREATE INDEX IF NOT EXISTS idx_scope ON raw_memories(scope);
CREATE INDEX IF NOT EXISTS idx_expired ON raw_memories(is_expired);
CREATE INDEX IF NOT EXISTS idx_timestamp ON raw_memories(timestamp);
CREATE INDEX IF NOT EXISTS idx_observation ON raw_memories(observation_type);

CREATE TABLE IF NOT EXISTS fact_slots (
    scope TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    uuid TEXT NOT NULL,
    observation_type TEXT NOT NULL DEFAULT 'fact',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, slot_key)
);
"""


class RawStore:
    """L2 原始真相存储（SQLite + FTS5）。"""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._fts = self._init_db()

    # ---- 基础设施 ----

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> bool:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            fts = self._create_fts(conn)
        return fts

    @staticmethod
    def _create_fts(conn: sqlite3.Connection) -> bool:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS raw_fts USING fts5(content, tokenize='trigram')"
            )
            return True
        except sqlite3.OperationalError:
            # 老版本 SQLite 无 trigram → 退回 unicode61；再不行就纯 LIKE
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS raw_fts USING fts5(content)"
                )
                return True
            except sqlite3.OperationalError:
                return False

    # ---- 写入 ----

    def append(self, record: MemoryRecord) -> str:
        """追加一条原始记忆（永不修改 content）。"""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO raw_memories
                   (uuid, session_id, content, role, scope, observation_type,
                    metadata_json, timestamp, is_expired, tree_node_id, fact_slot)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.uuid, record.session_id, record.content, record.role,
                 record.scope, record.observation_type,
                 json.dumps(record.metadata, ensure_ascii=False),
                 record.timestamp, int(record.is_expired),
                 record.tree_node_id, record.fact_slot),
            )
            if cur.rowcount and self._fts:
                conn.execute(
                    "INSERT INTO raw_fts(rowid, content) VALUES (?, ?)",
                    (cur.lastrowid, record.content),
                )
            if record.fact_slot and record.observation_type in SLOT_OBSERVATIONS:
                conn.execute(
                    """INSERT INTO fact_slots
                       (scope, slot_key, value, uuid, observation_type, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(scope, slot_key) DO UPDATE SET
                         value = excluded.value,
                         uuid = excluded.uuid,
                         observation_type = excluded.observation_type,
                         updated_at = excluded.updated_at""",
                    (record.scope, record.fact_slot,
                     str(record.metadata.get("value", record.content)),
                     record.uuid, record.observation_type, now_iso()),
                )
        return record.uuid

    def update_tree_node(self, uuid_str: str, tree_node_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE raw_memories SET tree_node_id = ? WHERE uuid = ?",
                (tree_node_id, uuid_str),
            )

    def mark_expired(self, uuid_str: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE raw_memories SET is_expired = 1 WHERE uuid = ?", (uuid_str,)
            )

    # ---- 读取 ----

    def get_by_uuid(self, uuid_str: str) -> MemoryRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM raw_memories WHERE uuid = ?", (uuid_str,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_uuids(self, uuids: list[str]) -> list[MemoryRecord]:
        if not uuids:
            return []
        placeholders = ",".join("?" * len(uuids))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM raw_memories WHERE uuid IN ({placeholders})", uuids
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_by_session(self, session_id: str, limit: int = 100) -> list[MemoryRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM raw_memories WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_by_scope(self, scope: str, limit: int = 100) -> list[MemoryRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM raw_memories
                   WHERE scope = ? AND is_expired = 0
                   ORDER BY timestamp DESC LIMIT ?""",
                (scope, limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_unsummarized(self, scope: str = "", limit: int = 20) -> list[MemoryRecord]:
        conditions = ["tree_node_id IS NULL", "is_expired = 0"]
        params: list[Any] = []
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        sql = (
            f"SELECT * FROM raw_memories WHERE {' AND '.join(conditions)}"
            " ORDER BY timestamp ASC LIMIT ?"
        )
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def filter_by_metadata(
        self, filters: dict, scope: str = "", limit: int = 100
    ) -> list[MemoryRecord]:
        conditions = ["is_expired = 0"]
        params: list[Any] = []
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        for k, v in filters.items():
            conditions.append(f"json_extract(metadata_json, '$.{k}') = ?")
            params.append(str(v))
        sql = (
            f"SELECT * FROM raw_memories WHERE {' AND '.join(conditions)}"
            " ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def find_by_metadata_key(self, key: str, value, scope: str = "") -> list[str]:
        sql = "SELECT uuid, metadata_json, is_expired FROM raw_memories"
        params: list[Any] = []
        if scope:
            sql += " WHERE scope = ?"
            params.append(scope)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[str] = []
        for r in rows:
            if r["is_expired"]:
                continue
            try:
                md = json.loads(r["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                md = {}
            if md.get(key) == value:
                out.append(r["uuid"])
        return out

    def slots(self, scope: str = "") -> dict[str, str]:
        """返回 {(scope, slot_key): 当前有效 uuid}。"""
        sql = "SELECT scope, slot_key, uuid FROM fact_slots"
        params: list[Any] = []
        if scope:
            sql += " WHERE scope = ?"
            params.append(scope)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {(r["scope"], r["slot_key"]): r["uuid"] for r in rows}

    def count(self, scope: str = "") -> int:
        sql = "SELECT COUNT(*) FROM raw_memories"
        params: list[Any] = []
        if scope:
            sql += " WHERE scope = ?"
            params.append(scope)
        with self._conn() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    # ---- 关键词检索 ----

    def search_text(
        self,
        query: str,
        scope: str = "",
        limit: int = 50,
        observation_types: Iterable[str] = (),
    ) -> list[tuple[str, float]]:
        """返回 [(uuid, 0~1 归一化得分)]。FTS5 优先，短词/无扩展时降级 LIKE。"""
        terms = [t for t in _TERM_RE.findall(query or "") if t]
        if not terms:
            return []

        obs = tuple(observation_types)
        long_terms = [t for t in terms if len(t) >= _TRIGRAM_MIN]
        rows: list[tuple[str, float]] = []

        if self._fts and long_terms:
            sql = (
                "SELECT r.uuid AS uuid, bm25(raw_fts) AS rank"
                " FROM raw_fts JOIN raw_memories r ON r.rowid = raw_fts.rowid"
                " WHERE raw_fts MATCH ? AND r.is_expired = 0"
            )
            params: list[Any] = [" ".join(long_terms)]
            if scope:
                sql += " AND r.scope = ?"
                params.append(scope)
            if obs:
                sql += f" AND r.observation_type IN ({','.join('?' * len(obs))})"
                params.extend(obs)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
            try:
                with self._conn() as conn:
                    found = conn.execute(sql, params).fetchall()
                rows = [(r["uuid"], -float(r["rank"])) for r in found]
            except sqlite3.OperationalError:
                rows = []

        if not rows:
            rows = self._like_search(terms, scope, limit, obs)

        if not rows:
            return []
        top = max(s for _, s in rows) or 1.0
        return [(u, s / top) for u, s in rows]

    def _like_search(
        self,
        terms: list[str],
        scope: str,
        limit: int,
        obs: tuple[str, ...],
    ) -> list[tuple[str, float]]:
        clauses = ["is_expired = 0"]
        params: list[Any] = []
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if obs:
            clauses.append(f"observation_type IN ({','.join('?' * len(obs))})")
            params.extend(obs)
        like_clauses = []
        for t in terms:
            like_clauses.append("content LIKE ?")
            params.append(f"%{t}%")
        clauses.append("(" + " OR ".join(like_clauses) + ")")
        sql = (
            f"SELECT uuid, content FROM raw_memories WHERE {' AND '.join(clauses)}"
            " ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        scored = []
        for r in rows:
            hits = sum(1 for t in terms if t in r["content"])
            scored.append((r["uuid"], hits / max(1, len(terms))))
        return scored

    # ---- 行转换 ----

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
        try:
            md = json.loads(row["metadata_json"])
        except (json.JSONDecodeError, TypeError):
            md = {}
        return MemoryRecord(
            uuid=row["uuid"],
            session_id=row["session_id"],
            content=row["content"],
            role=row["role"],
            scope=row["scope"],
            observation_type=row["observation_type"],
            metadata=md,
            timestamp=row["timestamp"],
            is_expired=bool(row["is_expired"]),
            tree_node_id=row["tree_node_id"],
            fact_slot=row["fact_slot"] or "",
        )


def derive_fact_slot(metadata: dict, observation_type: str) -> str:
    """从 metadata 推导状态型记忆的槽位键；无键则不参与覆盖。"""
    if observation_type not in SLOT_OBSERVATIONS:
        return ""
    slot = metadata.get("key") or metadata.get("slot") or metadata.get("name") or ""
    return str(slot).strip()
