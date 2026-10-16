"""L3 树形记忆 — 异步批量生成，结构化归纳，不存原文。

树结构：
- root 节点：scope 级别的总摘要
- mid 节点：按时间段/话题分组的摘要
- leaf 节点：关联一组 raw_memories UUIDs 的摘要

生成时机：N 轮对话后 / 话题切换时触发，禁止每轮调用 LLM。
"""
from __future__ import annotations
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class TreeNode:
    """树形记忆节点。"""
    def __init__(self, node_id: str = "", parent_id: str | None = None,
                 summary: str = "", scope: str = "", level: str = "leaf",
                 raw_uuids: list[str] | None = None,
                 children_ids: list[str] | None = None,
                 metadata: dict | None = None,
                 timestamp: str = ""):
        self.node_id = node_id or uuid.uuid4().hex
        self.parent_id = parent_id
        self.summary = summary       # 结构化摘要，不存原文
        self.scope = scope
        self.level = level            # root / mid / leaf
        self.raw_uuids = raw_uuids or []
        self.children_ids = children_ids or []
        self.metadata = metadata or {}
        self.timestamp = timestamp or datetime.now().isoformat()


class TreeMemoryStore:
    """L3 树形记忆存储。"""

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
            """)

    def add_node(self, node: TreeNode) -> str:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tree_nodes
                   (node_id, parent_id, summary, scope, level, raw_uuids_json,
                    children_ids_json, metadata_json, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (node.node_id, node.parent_id, node.summary, node.scope,
                 node.level, json.dumps(node.raw_uuids, ensure_ascii=False),
                 json.dumps(node.children_ids, ensure_ascii=False),
                 json.dumps(node.metadata, ensure_ascii=False), node.timestamp)
            )
        return node.node_id

    def get_node(self, node_id: str) -> TreeNode | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tree_nodes WHERE node_id = ?", (node_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_node(row)

    def get_by_scope(self, scope: str, level: str = "") -> list[TreeNode]:
        conditions = ["scope = ?"]
        params: list[Any] = [scope]
        if level:
            conditions.append("level = ?")
            params.append(level)
        sql = f"SELECT * FROM tree_nodes WHERE {' AND '.join(conditions)} ORDER BY timestamp DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_node(r) for r in rows]

    def get_children(self, parent_id: str) -> list[TreeNode]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tree_nodes WHERE parent_id = ? ORDER BY timestamp ASC", (parent_id,)
            ).fetchall()
            return [self._row_to_node(r) for r in rows]

    def get_root(self, scope: str) -> TreeNode | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tree_nodes WHERE scope = ? AND level = 'root' LIMIT 1", (scope,)
            ).fetchone()
            return self._row_to_node(row) if row else None

    def get_all_uuids(self, scope: str) -> list[str]:
        """获取某 scope 下所有树节点关联的 raw_uuids（用于向量检索路由）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT raw_uuids_json FROM tree_nodes WHERE scope = ?", (scope,)
            ).fetchall()
            uuids: list[str] = []
            for r in rows:
                uuids.extend(json.loads(r["raw_uuids_json"]))
            return list(set(uuids))

    def count(self, scope: str = "") -> int:
        sql = "SELECT COUNT(*) FROM tree_nodes"
        params: list[Any] = []
        if scope:
            sql += " WHERE scope = ?"
            params.append(scope)
        with self._conn() as conn:
            return conn.execute(sql, params).fetchone()[0]

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

    def update_parent(self, node_id: str, parent_id: str) -> None:
        """回填节点的 parent_id（构建 FlowRun 父子树关系用）。"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE tree_nodes SET parent_id = ? WHERE node_id = ?",
                (parent_id, node_id)
            )
            conn.commit()

    def find_by_metadata_key(self, key: str, value, scope: str = "") -> list:
        """按 metadata 的键值查找节点（用于 flow_id 过期标记）。"""
        import json
        with self._conn() as conn:
            sql = "SELECT node_id, parent_id, summary, scope, level, raw_uuids_json, children_ids_json, metadata_json, timestamp FROM tree_nodes"
            params: list = []
            if scope:
                sql += " WHERE scope = ?"
                params.append(scope)
            rows = conn.execute(sql, params).fetchall()
            result = []
            for r in rows:
                try:
                    md = json.loads(r["metadata_json"])
                except Exception:
                    md = {}
                if md.get(key) == value:
                    from .tree_store import TreeNode
                    try:
                        raw = json.loads(r["raw_uuids_json"])
                    except Exception:
                        raw = []
                    try:
                        ch = json.loads(r["children_ids_json"])
                    except Exception:
                        ch = []
                    result.append(TreeNode(
                        node_id=r["node_id"], parent_id=r["parent_id"],
                        summary=r["summary"], scope=r["scope"], level=r["level"],
                        raw_uuids=raw, children_ids=ch, metadata=md,
                        timestamp=r["timestamp"],
                    ))
        return result

    def mark_expired(self, node_id: str) -> None:
        """把树节点标记为过期（仅标记 is_expired，不物理删除）。"""
        import json
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM tree_nodes WHERE node_id = ?",
                (node_id,)
            ).fetchone()
            if not row:
                return
            try:
                md = json.loads(row["metadata_json"])
            except Exception:
                md = {}
            md["is_expired"] = True
            conn.execute(
                "UPDATE tree_nodes SET metadata_json = ? WHERE node_id = ?",
                (json.dumps(md, ensure_ascii=False), node_id)
            )
            conn.commit()
