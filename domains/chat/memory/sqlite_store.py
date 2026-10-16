"""L2 SQLite 原始真相库 — 唯一事实源，只追加不修改，过期仅标记。

存储结构：
- raw_memories 表：uuid, session_id, content, role, scope, metadata_json, timestamp, is_expired, tree_node_id
- 原始文本 content 字段永不修改
- is_expired 布尔标记替代删除
"""
from __future__ import annotations
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from ..protocol.types import MemoryRecord


class SQLiteStore:
    """L2 SQLite 原始真相存储。"""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS raw_memories (
                    uuid TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    timestamp TEXT NOT NULL,
                    is_expired INTEGER NOT NULL DEFAULT 0,
                    tree_node_id TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_session ON raw_memories(session_id);
                CREATE INDEX IF NOT EXISTS idx_scope ON raw_memories(scope);
                CREATE INDEX IF NOT EXISTS idx_expired ON raw_memories(is_expired);
                CREATE INDEX IF NOT EXISTS idx_timestamp ON raw_memories(timestamp);
            """)

    def append(self, record: MemoryRecord) -> str:
        """追加一条原始记忆（永不修改）。"""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO raw_memories
                   (uuid, session_id, content, role, scope, metadata_json, timestamp, is_expired, tree_node_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.uuid, record.session_id, record.content, record.role,
                 record.scope, json.dumps(record.metadata, ensure_ascii=False),
                 record.timestamp, int(record.is_expired), record.tree_node_id)
            )
        return record.uuid

    def get_by_uuid(self, uuid: str) -> MemoryRecord | None:
        """按 UUID 回查原始文本。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM raw_memories WHERE uuid = ?", (uuid,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_record(row)

    def get_by_uuids(self, uuids: list[str]) -> list[MemoryRecord]:
        """批量按 UUID 回查。"""
        if not uuids:
            return []
        placeholders = ",".join("?" * len(uuids))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM raw_memories WHERE uuid IN ({placeholders})", uuids
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_by_session(self, session_id: str, limit: int = 100) -> list[MemoryRecord]:
        """按会话查询。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM raw_memories WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_by_scope(self, scope: str, limit: int = 100) -> list[MemoryRecord]:
        """按 scope 查询。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM raw_memories WHERE scope = ? AND is_expired = 0 ORDER BY timestamp DESC LIMIT ?",
                (scope, limit)
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def filter_by_metadata(self, filters: dict, scope: str = "", limit: int = 100) -> list[MemoryRecord]:
        """按 metadata 过滤。"""
        conditions = ["is_expired = 0"]
        params: list[Any] = []
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        for k, v in filters.items():
            conditions.append(f"json_extract(metadata_json, '$.{k}') = ?")
            params.append(str(v))
        sql = f"SELECT * FROM raw_memories WHERE {' AND '.join(conditions)} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_record(r) for r in rows]

    def mark_expired(self, uuid: str) -> None:
        """标记过期（不物理删除）。"""
        with self._conn() as conn:
            conn.execute("UPDATE raw_memories SET is_expired = 1 WHERE uuid = ?", (uuid,))
            conn.commit()

    def find_by_metadata_key(self, key: str, value, scope: str = "") -> list[str]:
        """按 metadata 的键值查找 UUID（用于 flow_id 过期标记，不返回 raw_text）。"""
        import json
        with self._conn() as conn:
            sql = "SELECT uuid, metadata_json, is_expired FROM raw_memories"
            params = []
            if scope:
                sql += " WHERE scope = ?"
                params.append(scope)
            rows = conn.execute(sql, params).fetchall()
            uuids = []
            for r in rows:
                if r["is_expired"]:
                    continue
                try:
                    md = json.loads(r["metadata_json"])
                except Exception:
                    md = {}
                if md.get(key) == value:
                    uuids.append(r["uuid"])
            return uuids


    def update_tree_node(self, uuid: str, tree_node_id: str) -> None:
        """更新关联的树节点 ID（只改 tree_node_id，不改 content）。"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE raw_memories SET tree_node_id = ? WHERE uuid = ?",
                (tree_node_id, uuid)
            )

    def get_unsummarized(self, scope: str = "", limit: int = 20) -> list[MemoryRecord]:
        """获取尚未被树形记忆归纳的记录（tree_node_id IS NULL）。"""
        conditions = ["tree_node_id IS NULL", "is_expired = 0"]
        params: list[Any] = []
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        sql = f"SELECT * FROM raw_memories WHERE {' AND '.join(conditions)} ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_record(r) for r in rows]

    def count(self, scope: str = "") -> int:
        conditions = []
        params: list[Any] = []
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        sql = f"SELECT COUNT(*) FROM raw_memories"
        if conditions:
            sql += f" WHERE {' AND '.join(conditions)}"
        with self._conn() as conn:
            return conn.execute(sql, params).fetchone()[0]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            uuid=row["uuid"],
            session_id=row["session_id"],
            content=row["content"],
            role=row["role"],
            scope=row["scope"],
            metadata=json.loads(row["metadata_json"]),
            timestamp=row["timestamp"],
            is_expired=bool(row["is_expired"]),
            tree_node_id=row["tree_node_id"],
        )
