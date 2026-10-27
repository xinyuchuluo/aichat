"""知识图库原语：nodes + typed edges，四域共用一套"节点 + 边 + 遍历"。

这是 AIChat 内核的通用图存储。原本只支持 DAG（边集合 + 不变量校验），
现升级为完整知识图库：节点带类型/属性，边带类型/属性，支持多跳遍历与子图提取。

域适配：
    novel      event / 实体(人物·地点·物品) 关系边      不变量=因果无环
    studio     shot / asset 引用边                    不变量=引用资产必须存在
    warehouse  field 血缘边 (Source→ODS→DWD→DWS→ADS)  不变量=分层 DAG 单向、无环
    analysis   (暂不用)

通用节点表 + 通用边表 + 域适配。每域自己的 SQLite（chat 不用），
按 domain 字段做物理隔离。

表结构：
    nodes(id, domain, type, label, properties_json, created_at)
    edges(id, domain, src, dst, edge_type, properties_json, created_at)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


SCHEMA_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    domain      TEXT NOT NULL,
    type        TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    properties  TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_domain ON nodes(domain);
CREATE INDEX IF NOT EXISTS idx_nodes_domain_type ON nodes(domain, type);
"""

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


# --------------------------------------------------------------------------
# 数据对象
# --------------------------------------------------------------------------
@dataclass
class Node:
    id: str = field(default_factory=_new_id)
    domain: str = ""
    type: str = ""          # person | place | item | event | field | table | shot | asset ...
    label: str = ""         # 展示名
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "domain": self.domain,
            "type": self.type,
            "label": self.label,
            "properties": self.properties,
            "created_at": self.created_at,
        }


@dataclass
class Edge:
    id: str = field(default_factory=_new_id)
    domain: str = ""
    src: str = ""
    dst: str = ""
    edge_type: str = ""  # depends_on | references | blood_lineage | relation ...
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


# --------------------------------------------------------------------------
# 存储
# --------------------------------------------------------------------------
class GraphStore:
    """单个域内的图存储（一个 SQLite 文件，跨节点/边共享 domain 隔离）。"""

    def __init__(self, db_path: Path, domain: str) -> None:
        self._path: Path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._domain: str = domain
        self._conn: sqlite3.Connection = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        # 建表（已存在的库只补 nodes 表，向后兼容）
        self._conn.executescript(SCHEMA_NODES + SCHEMA_EDGES)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    # ===== 节点：写 =====
    def add_node(
        self,
        node_type: str,
        label: str = "",
        properties: Optional[dict] = None,
        node_id: Optional[str] = None,
        *,
        upsert: bool = True,
    ) -> str:
        """新增节点；node_id 可指定稳定 id（如 'char:张三'），不传则自动生成。

        upsert=True 时若 node_id 已存在则更新 type/label/properties。
        """
        nid = node_id or _new_id()
        if upsert:
            self._conn.execute(
                "INSERT INTO nodes(id, domain, type, label, properties, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " type=excluded.type, label=excluded.label,"
                " properties=excluded.properties",
                (
                    nid,
                    self._domain,
                    node_type,
                    label,
                    json.dumps(properties or {}, ensure_ascii=False),
                    _now_iso(),
                ),
            )
        else:
            self._conn.execute(
                "INSERT OR IGNORE INTO nodes(id, domain, type, label, properties, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (
                    nid,
                    self._domain,
                    node_type,
                    label,
                    json.dumps(properties or {}, ensure_ascii=False),
                    _now_iso(),
                ),
            )
        self._conn.commit()
        return nid

    def update_node(
        self, node_id: str, label: Optional[str] = None,
        properties: Optional[dict] = None,
    ) -> None:
        if label is not None and properties is not None:
            self._conn.execute(
                "UPDATE nodes SET label=?, properties=? WHERE id=? AND domain=?",
                (label, json.dumps(properties, ensure_ascii=False), node_id, self._domain),
            )
        elif label is not None:
            self._conn.execute(
                "UPDATE nodes SET label=? WHERE id=? AND domain=?",
                (label, node_id, self._domain),
            )
        elif properties is not None:
            self._conn.execute(
                "UPDATE nodes SET properties=? WHERE id=? AND domain=?",
                (json.dumps(properties, ensure_ascii=False), node_id, self._domain),
            )
        self._conn.commit()

    def remove_node(self, node_id: str) -> None:
        """删除节点及其所有关联边。"""
        self._conn.execute("DELETE FROM nodes WHERE id=? AND domain=?", (node_id, self._domain))
        self._conn.execute(
            "DELETE FROM edges WHERE domain=? AND (src=? OR dst=?)",
            (self._domain, node_id, node_id),
        )
        self._conn.commit()

    # ===== 节点：读 =====
    def get_node(self, node_id: str) -> Optional[Node]:
        cur = self._conn.execute(
            "SELECT * FROM nodes WHERE domain=? AND id=?", (self._domain, node_id)
        )
        row = cur.fetchone()
        return self._row_to_node(row) if row else None

    def list_nodes(self, node_type: Optional[str] = None) -> list[Node]:
        if node_type:
            cur = self._conn.execute(
                "SELECT * FROM nodes WHERE domain=? AND type=?",
                (self._domain, node_type),
            )
        else:
            cur = self._conn.execute("SELECT * FROM nodes WHERE domain=?", (self._domain,))
        return [self._row_to_node(r) for r in cur.fetchall()]

    def find_nodes(self, label_like: str) -> list[Node]:
        """按 label 模糊匹配（LIKE）。"""
        cur = self._conn.execute(
            "SELECT * FROM nodes WHERE domain=? AND label LIKE ?",
            (self._domain, f"%{label_like}%"),
        )
        return [self._row_to_node(r) for r in cur.fetchall()]

    # ===== 边：写（向后兼容 DAG 用法）=====
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
        """删除顶点相关的所有边（保留节点记录，向后兼容旧语义）。"""
        self._conn.execute(
            "DELETE FROM edges WHERE src = ? OR dst = ?", (vertex, vertex)
        )
        self._conn.commit()

    # ===== 边：读（向后兼容）=====
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

    def _row_to_node(self, row: sqlite3.Row) -> Node:
        props = row["properties"]
        try:
            props_dict = json.loads(props) if props else {}
        except json.JSONDecodeError:
            props_dict = {}
        return Node(
            id=row["id"],
            domain=row["domain"],
            type=row["type"],
            label=row["label"],
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

    # ===== 图遍历（新增）=====
    def neighbors(
        self, vertex: str, direction: str = "both",
        edge_types: Optional[list[str]] = None,
    ) -> list[Node]:
        """一层邻居节点。direction: out(出边)/in(入边)/both。"""
        et_clause, params = self._edge_type_clause(edge_types)
        if direction == "out":
            sql = (
                "SELECT n.* FROM nodes n JOIN edges e ON e.dst=n.id "
                f"WHERE e.domain=? AND e.src=?{et_clause}"
            )
            params = [self._domain, vertex] + params
        elif direction == "in":
            sql = (
                "SELECT n.* FROM nodes n JOIN edges e ON e.src=n.id "
                f"WHERE e.domain=? AND e.dst=?{et_clause}"
            )
            params = [self._domain, vertex] + params
        else:  # both
            sql = (
                "SELECT n.* FROM nodes n JOIN edges e ON (e.src=n.id OR e.dst=n.id) "
                f"WHERE e.domain=? AND (e.src=? OR e.dst=?) AND n.id <> ?{et_clause}"
            )
            params = [self._domain, vertex, vertex, vertex] + params
        cur = self._conn.execute(sql, params)
        return [self._row_to_node(r) for r in cur.fetchall()]

    def traverse(
        self, start: str, max_depth: int = 2, direction: str = "out",
        edge_types: Optional[list[str]] = None,
    ) -> list[tuple[str, int]]:
        """多跳遍历，返回 [(vertex, depth)]，含起点(depth=0)。

        用 SQLite 递归 CTE 实现，原生零依赖。direction=out 顺边(src→dst)，
        direction=in 逆边(dst→src)。
        """
        et_clause, et_params = self._edge_type_clause(edge_types)
        if direction == "in":
            join_on = "edges.dst = cte.id"
            next_col = "edges.src"
        else:
            join_on = "edges.src = cte.id"
            next_col = "edges.dst"
        sql = f"""
        WITH RECURSIVE cte(id, depth) AS (
            SELECT ?, 0
            UNION ALL
            SELECT {next_col}, cte.depth + 1
            FROM cte
            JOIN edges ON {join_on}
            WHERE cte.depth < ? AND edges.domain = ?{et_clause}
        )
        SELECT id, MIN(depth) AS depth FROM cte GROUP BY id ORDER BY depth
        """
        params = [start, max_depth, self._domain] + et_params
        cur = self._conn.execute(sql, params)
        return [(r[0], r[1]) for r in cur.fetchall()]

    def subgraph(
        self, starts: list[str], max_depth: int = 2, direction: str = "out",
        edge_types: Optional[list[str]] = None,
    ) -> tuple[list[Node], list[Edge]]:
        """从多个起点多跳展开，返回涉及的 (nodes, edges)，用于可视化/导出。"""
        visited: set[str] = set()
        for s in starts:
            for vid, _ in self.traverse(s, max_depth, direction, edge_types):
                visited.add(vid)
        if not visited:
            return [], []
        placeholders = ",".join("?" for _ in visited)
        ncur = self._conn.execute(
            f"SELECT * FROM nodes WHERE domain=? AND id IN ({placeholders})",
            [self._domain] + list(visited),
        )
        nodes = [self._row_to_node(r) for r in ncur.fetchall()]
        ecur = self._conn.execute(
            f"SELECT * FROM edges WHERE domain=? AND src IN ({placeholders}) "
            f"AND dst IN ({placeholders})",
            [self._domain] + list(visited) + list(visited),
        )
        edges = [self._row_to_edge(r) for r in ecur.fetchall()]
        return nodes, edges

    def _edge_type_clause(self, edge_types: Optional[list[str]]) -> tuple[str, list]:
        if not edge_types:
            return "", []
        ph = ",".join("?" for _ in edge_types)
        return f" AND e.edge_type IN ({ph})", list(edge_types)

    # ===== 不变量校验（向后兼容）=====
    def has_cycle(self) -> bool:
        """拓扑排序检测：能否完整排序"""
        cur = self._conn.execute(
            "SELECT DISTINCT src FROM edges WHERE domain = ?"
            " UNION SELECT DISTINCT dst FROM edges WHERE domain = ?",
            (self._domain, self._domain),
        )
        all_vertices = {r[0] for r in cur.fetchall()}
        if not all_vertices:
            return False
        in_degree: dict[str, int] = {v: 0 for v in all_vertices}
        adj: dict[str, list[str]] = {v: [] for v in all_vertices}
        cur = self._conn.execute(
            "SELECT src, dst FROM edges WHERE domain = ?", (self._domain,)
        )
        for src, dst in cur.fetchall():
            adj[src].append(dst)
            in_degree[dst] += 1
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
