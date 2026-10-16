"""关系/事件/DAG 原语：四种域共用一种"边集合 + 不变量校验"。

域适配：
    novel      event depends_on event          顶点=event       不变量=因果无环
    studio     shot → asset 引用              顶点=shot/asset  不变量=引用资产必须存在
    warehouse  field 血缘 field→field         顶点=field       不变量=分层 DAG 单向、无环
    analysis   (暂不用)

通用边表 + 域适配。每域自己的 SQLite（chat 不用），
表结构 edges(id, domain, src, dst, edge_type, properties_json, created_at) + 索引。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


SCHEMA_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    id            TEXT PRIMARY KEY,
    domain        TEXT NOT NULL,
    src           TEXT NOT NULL,
    dst           TEXT NOT NULL,
    edge_type     TEXT NOT NULL,
    properties    TEXT DEFAULT '{}',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_domain_src ON edges(domain, src);
CREATE INDEX IF NOT EXISTS idx_edges_domain_dst ON edges(domain, dst);
CREATE INDEX IF NOT EXISTS idx_edges_domain_type ON edges(domain, edge_type);
"""


@dataclass
class Edge:
    id: str = field(default_factory=_new_id)
    domain: str = ""
    src: str = ""
    dst: str = ""
    edge_type: str = ""  # depends_on | references | blood_lineage
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "domain": self.domain,
            "src": self.src,
            "dst": self.dst,
            "edge_type": self.edge_type,
            "properties": self.properties,
            "created_at": self.created_at,
        }


class GraphStore:
    """单个域内的 DAG 存储（一个 SQLite 文件）"""

    def __init__(self, db_path: Path, domain: str) -> None:
        self._path: Path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._domain: str = domain
        self._conn: sqlite3.Connection = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_EDGES)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- 写 ----
    def add_edge(self, edge: Edge) -> str:
        if edge.domain != self._domain:
            edge.domain = self._domain
        self._conn.execute(
            "INSERT OR REPLACE INTO edges(id, domain, src, dst, edge_type, properties, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                edge.id,
                edge.domain,
                edge.src,
                edge.dst,
                edge.edge_type,
                json.dumps(edge.properties, ensure_ascii=False),
                edge.created_at,
            ),
        )
        self._conn.commit()
        return edge.id

    def remove_edge(self, edge_id: str) -> None:
        self._conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
        self._conn.commit()

    def remove_vertex(self, vertex: str) -> None:
        """删除顶点相关的所有边"""
        self._conn.execute(
            "DELETE FROM edges WHERE src = ? OR dst = ?", (vertex, vertex)
        )
        self._conn.commit()

    # ---- 读 ----
    def _row_to_edge(self, row: sqlite3.Row) -> Edge:
        props = row["properties"]
        try:
            props_dict = json.loads(props) if props else {}
        except json.JSONDecodeError:
            props_dict = {}
        return Edge(
            id=row["id"],
            domain=row["domain"],
            src=row["src"],
            dst=row["dst"],
            edge_type=row["edge_type"],
            properties=props_dict,
            created_at=row["created_at"],
        )

    def edges_of(self, vertex: str) -> list[Edge]:
        cur = self._conn.execute(
            "SELECT * FROM edges WHERE domain = ? AND (src = ? OR dst = ?)",
            (self._domain, vertex, vertex),
        )
        return [self._row_to_edge(r) for r in cur.fetchall()]

    def out_edges(self, vertex: str) -> list[Edge]:
        cur = self._conn.execute(
            "SELECT * FROM edges WHERE domain = ? AND src = ?",
            (self._domain, vertex),
        )
        return [self._row_to_edge(r) for r in cur.fetchall()]

    def in_edges(self, vertex: str) -> list[Edge]:
        cur = self._conn.execute(
            "SELECT * FROM edges WHERE domain = ? AND dst = ?",
            (self._domain, vertex),
        )
        return [self._row_to_edge(r) for r in cur.fetchall()]

    def ancestors(self, vertex: str) -> list[str]:
        """上游：dst=vertex 的所有 src"""
        cur = self._conn.execute(
            "SELECT DISTINCT src FROM edges WHERE domain = ? AND dst = ?",
            (self._domain, vertex),
        )
        return [r[0] for r in cur.fetchall()]

    def descendants(self, vertex: str) -> list[str]:
        """下游：src=vertex 的所有 dst"""
        cur = self._conn.execute(
            "SELECT DISTINCT dst FROM edges WHERE domain = ? AND src = ?",
            (self._domain, vertex),
        )
        return [r[0] for r in cur.fetchall()]

    # ---- 不变量校验 ----
    def has_cycle(self) -> bool:
        """拓扑排序检测：能否完整排序"""
        # 收集所有顶点
        cur = self._conn.execute(
            "SELECT DISTINCT src FROM edges WHERE domain = ?"
            " UNION SELECT DISTINCT dst FROM edges WHERE domain = ?",
            (self._domain, self._domain),
        )
        all_vertices = {r[0] for r in cur.fetchall()}
        if not all_vertices:
            return False

        # 入度
        in_degree: dict[str, int] = {v: 0 for v in all_vertices}
        adj: dict[str, list[str]] = {v: [] for v in all_vertices}
        for edge in self.out_edges(""):  # placeholder; do real query below
            pass
        # 真正查所有边
        cur = self._conn.execute(
            "SELECT src, dst FROM edges WHERE domain = ?", (self._domain,)
        )
        for src, dst in cur.fetchall():
            adj[src].append(dst)
            in_degree[dst] += 1

        # Kahn 算法
        from collections import deque

        queue = deque([v for v, d in in_degree.items() if d == 0])
        visited = 0
        while queue:
            v = queue.popleft()
            visited += 1
            for nxt in adj.get(v, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        return visited != len(all_vertices)

    def topological_order(self) -> Optional[list[str]]:
        """拓扑排序，有环返回 None"""
        if self.has_cycle():
            return None
        cur = self._conn.execute(
            "SELECT DISTINCT src FROM edges WHERE domain = ?"
            " UNION SELECT DISTINCT dst FROM edges WHERE domain = ?",
            (self._domain, self._domain),
        )
        all_vertices = {r[0] for r in cur.fetchall()}
        in_degree: dict[str, int] = {v: 0 for v in all_vertices}
        adj: dict[str, list[str]] = {v: [] for v in all_vertices}
        cur = self._conn.execute(
            "SELECT src, dst FROM edges WHERE domain = ?", (self._domain,)
        )
        for src, dst in cur.fetchall():
            adj[src].append(dst)
            in_degree[dst] += 1
        from collections import deque

        queue = deque([v for v, d in in_degree.items() if d == 0])
        order: list[str] = []
        while queue:
            v = queue.popleft()
            order.append(v)
            for nxt in adj.get(v, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        return order


# 模块级：按 (db_path, domain) 缓存
_stores: dict[tuple[str, str], GraphStore] = {}


def for_domain(db_path: Path | str, domain: str) -> GraphStore:
    key = (str(db_path), domain)
    if key not in _stores:
        _stores[key] = GraphStore(Path(db_path), domain)
    return _stores[key]
