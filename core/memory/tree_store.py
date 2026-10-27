"""L3 树形记忆 — 异步批量归纳的结构化摘要，不存原文。

结构：root（scope 总览）/ mid（话题或流程分支）/ leaf（一组 raw UUID 的摘要）。
生成时机：N 轮对话或话题切换时批量触发，禁止每轮调 LLM。
检索：摘要走 FTS5(trigram)，命中后只返回 raw_uuids，由 L2 回查事实。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .types import TreeNode

_TERM_RE = re.compile(r"[0-9A-Za-z_\u3400-\u4dbf\u4e00-\u9fff]+")
_TRIGRAM_MIN = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS tree_nodes (
    node_id TEXT PRIMARY KEY,
    parent_id TEXT,
    summary TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'leaf',
    raw_uuids_json TEXT NOT NULL DEFAULT '[]',
    children_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tree_scope ON tree_nodes(scope);
CREATE INDEX IF NOT EXISTS idx_tree_parent ON tree_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_tree_level ON tree_nodes(level);
"""


class TreeMemoryStore:
    """L3 树形记忆存储（SQLite + FTS5）。"""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._fts = self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> bool:
        fts = False
        for attempt in range(20):
            try:
                with self._conn() as conn:
                    current = conn.execute("PRAGMA journal_mode").fetchone()[0]
                    if current != "wal":
                        conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.executescript(SCHEMA)
                    fts = self._create_fts(conn)
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 19:
                    raise
                time.sleep(0.05)
        return fts

    @staticmethod
    def _create_fts(conn: sqlite3.Connection) -> bool:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS tree_fts USING fts5(summary, tokenize='trigram')"
            )
            return True
        except sqlite3.OperationalError:
            try:
                conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS tree_fts USING fts5(summary)")
                return True
            except sqlite3.OperationalError:
                return False

    # ---- 写入 ----

    def add_node(self, node: TreeNode) -> str:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO tree_nodes
                   (node_id, parent_id, summary, scope, level, raw_uuids_json,
                    children_ids_json, metadata_json, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (node.node_id, node.parent_id, node.summary, node.scope,
                 node.level, json.dumps(node.raw_uuids, ensure_ascii=False),
                 json.dumps(node.children_ids, ensure_ascii=False),
                 json.dumps(node.metadata, ensure_ascii=False), node.timestamp),
            )
            if cur.rowcount and self._fts:
                conn.execute(
                    "INSERT INTO tree_fts(rowid, summary) VALUES (?, ?)",
                    (cur.lastrowid, node.summary),
                )
        return node.node_id

    def update_parent(self, node_id: str, parent_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE tree_nodes SET parent_id = ? WHERE node_id = ?",
                (parent_id, node_id),
            )

    def mark_expired(self, node_id: str) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM tree_nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            if row is None:
                return
            try:
                md = json.loads(row["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                md = {}
            md["is_expired"] = True
            conn.execute(
                "UPDATE tree_nodes SET metadata_json = ? WHERE node_id = ?",
                (json.dumps(md, ensure_ascii=False), node_id),
            )

    # ---- 读取 ----

    def get_node(self, node_id: str) -> TreeNode | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tree_nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
        return self._row_to_node(row) if row else None

    def get_by_scope(self, scope: str, level: str = "") -> list[TreeNode]:
        conditions = ["scope = ?"]
        params: list[Any] = [scope]
        if level:
            conditions.append("level = ?")
            params.append(level)
        sql = (
            f"SELECT * FROM tree_nodes WHERE {' AND '.join(conditions)}"
            " ORDER BY timestamp DESC"
        )
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_children(self, parent_id: str) -> list[TreeNode]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tree_nodes WHERE parent_id = ? ORDER BY timestamp ASC",
                (parent_id,),
            ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_all_uuids(self, scope: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT raw_uuids_json FROM tree_nodes WHERE scope = ?", (scope,)
            ).fetchall()
        uuids: list[str] = []
        for r in rows:
            uuids.extend(json.loads(r["raw_uuids_json"]))
        return list(set(uuids))

    def list_scopes(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT scope FROM tree_nodes WHERE scope != ''"
            ).fetchall()
        return [r["scope"] for r in rows]

    def count(self, scope: str = "") -> int:
        sql = "SELECT COUNT(*) FROM tree_nodes"
        params: list[Any] = []
        if scope:
            sql += " WHERE scope = ?"
            params.append(scope)
        with self._conn() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def find_by_metadata_key(self, key: str, value, scope: str = "") -> list[TreeNode]:
        sql = "SELECT * FROM tree_nodes"
        params: list[Any] = []
        if scope:
            sql += " WHERE scope = ?"
            params.append(scope)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[TreeNode] = []
        for r in rows:
            try:
                md = json.loads(r["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                md = {}
            if md.get(key) == value:
                out.append(self._row_to_node(r))
        return out

    # ---- 关键词检索 ----

    def search_summaries(
        self, query: str, scope: str = "", limit: int = 20
    ) -> list[tuple[TreeNode, float]]:
        """返回 [(节点, 0~1 归一化得分)]，命中后由调用方展开 raw_uuids。"""
        terms = [t for t in _TERM_RE.findall(query or "") if t]
        if not terms:
            return []
        long_terms = [t for t in terms if len(t) >= _TRIGRAM_MIN]
        rows: list[tuple[sqlite3.Row, float]] = []

        if self._fts and long_terms:
            sql = (
                "SELECT t.*, bm25(tree_fts) AS rank"
                " FROM tree_fts JOIN tree_nodes t ON t.rowid = tree_fts.rowid"
                " WHERE tree_fts MATCH ?"
            )
            params: list[Any] = [" ".join(long_terms)]
            if scope:
                sql += " AND t.scope = ?"
                params.append(scope)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
            try:
                with self._conn() as conn:
                    found = conn.execute(sql, params).fetchall()
                rows = [(r, -float(r["rank"])) for r in found]
            except sqlite3.OperationalError:
                rows = []

        if not rows:
            sql = "SELECT * FROM tree_nodes WHERE summary LIKE ?"
            params = [f"%{terms[0]}%"]
            if scope:
                sql += " AND scope = ?"
                params.append(scope)
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            with self._conn() as conn:
                found = conn.execute(sql, params).fetchall()
            rows = [(r, 0.5) for r in found]

        if not rows:
            return []
        top = max(s for _, s in rows) or 1.0
        out: list[tuple[TreeNode, float]] = []
        for r, s in rows:
            node = self._row_to_node(r)
            if node.metadata.get("is_expired"):
                continue
            out.append((node, s / top))
        return out

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> TreeNode:
        return TreeNode(
            node_id=row["node_id"],
            parent_id=row["parent_id"],
            summary=row["summary"],
            scope=row["scope"],
            level=row["level"],
            raw_uuids=json.loads(row["raw_uuids_json"]),
            children_ids=json.loads(row["children_ids_json"]),
            metadata=json.loads(row["metadata_json"]),
            timestamp=row["timestamp"],
        )
