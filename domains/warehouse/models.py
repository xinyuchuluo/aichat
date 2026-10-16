"""数仓域 — 表 schema + 基础 CRUD。

PRD §0.1 数据身份二分：
  权威表（人工审核入库，AI 不可改）
  草稿表（SQL 解析/AI 推理产出，待审核）

分层方向（PRD §2.1）：Source → ODS → DWD → DWS → ADS（单向 DAG）
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 分层定义（PRD §2.1，代码写死，AI/用户不可改）
LAYERS = ["Source", "ODS", "DWD", "DWS", "ADS"]
LAYER_ORDER = {name: i for i, name in enumerate(LAYERS)}


SCHEMA = """
-- ===== 权威表（人工审核入库，AI 不可改） =====

CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    standard_name TEXT NOT NULL UNIQUE,   -- 标准字段名
    business_meaning TEXT NOT NULL,      -- 唯一业务含义
    data_type TEXT DEFAULT '',
    enum_values TEXT DEFAULT '',         -- 枚举值（逗号分隔）
    status TEXT DEFAULT 'active'        -- active/expired
);

CREATE TABLE IF NOT EXISTS source_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL UNIQUE,
    layer TEXT NOT NULL DEFAULT 'Source',
    business_domain TEXT DEFAULT '',     -- 业务域：订单/用户/支付等
    business_brief TEXT DEFAULT '',
    update_mode TEXT DEFAULT '',        -- 全量/增量/实时
    unique_key TEXT DEFAULT '',          -- 业务唯一键（用于一对多判定）
    granularity TEXT DEFAULT '',         -- 粒度描述
    status TEXT DEFAULT 'draft'          -- draft/audited（审核通过才能下游用）
);

CREATE TABLE IF NOT EXISTS source_columns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL,
    column_name TEXT NOT NULL,
    data_type TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    is_primary_key INTEGER DEFAULT 0,
    business_meaning TEXT DEFAULT '',
    FOREIGN KEY (table_id) REFERENCES source_tables(id) ON DELETE CASCADE,
    UNIQUE(table_id, column_name)
);

-- 血缘关系（审核通过后写入，权威）
CREATE TABLE IF NOT EXISTS lineage_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_table TEXT NOT NULL,
    source_table TEXT NOT NULL,
    target_layer TEXT NOT NULL,
    source_layer TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lineage_columns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_table TEXT NOT NULL,
    target_column TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_column TEXT NOT NULL,
    transform_expr TEXT DEFAULT '',      -- 清洗公式
    function_logic TEXT DEFAULT '',      -- 函数逻辑
    null_handling TEXT DEFAULT ''        -- 空值处理
);

CREATE TABLE IF NOT EXISTS join_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_table TEXT NOT NULL,
    left_table TEXT NOT NULL,
    right_table TEXT NOT NULL,
    join_type TEXT NOT NULL,             -- INNER/LEFT/RIGHT
    on_condition TEXT NOT NULL,          -- ON 关联字段
    cardinality TEXT DEFAULT '',         -- 一对一/一对多/多对多
    many_to_many_reason TEXT DEFAULT '', -- 多对多必须填写
    many_to_many_dedup TEXT DEFAULT '',  -- 去重方案
    status TEXT DEFAULT 'draft'          -- draft/audited
);

-- ===== 草稿表（SQL 解析产出，待审核） =====

CREATE TABLE IF NOT EXISTS draft_lineage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sql_text TEXT NOT NULL,              -- 原始 SQL
    parsed_json TEXT NOT NULL,          -- 解析结果 JSON（表依赖/字段血缘/JOIN/基数）
    status TEXT DEFAULT 'draft',         -- draft/audited/rejected
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    audit_note TEXT DEFAULT ''           -- 审核备注
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,           -- lineage/join/terms/source_table
    entity_id INTEGER NOT NULL,
    action TEXT NOT NULL,                -- approve/reject/edit
    operator TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


@dataclass
class Term:
    id: int = 0
    standard_name: str = ""
    business_meaning: str = ""
    data_type: str = ""
    enum_values: str = ""
    status: str = "active"


@dataclass
class SourceTable:
    id: int = 0
    table_name: str = ""
    layer: str = "Source"
    business_domain: str = ""
    business_brief: str = ""
    update_mode: str = ""
    unique_key: str = ""
    granularity: str = ""
    status: str = "draft"


@dataclass
class DraftLineage:
    id: int = 0
    sql_text: str = ""
    parsed_json: str = "{}"
    status: str = "draft"
    created_at: str = ""
    audit_note: str = ""


class WarehouseDB:
    """数仓域 SQLite 数据库封装。

    库文件位置：workspace_root/warehouse.db
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    # ===== Term CRUD =====
    def list_terms(self) -> list[Term]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM terms WHERE status='active' ORDER BY standard_name").fetchall()
            return [Term(**dict(r)) for r in rows]

    def insert_term(self, t: Term) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR REPLACE INTO terms(standard_name, business_meaning, data_type, enum_values, status) VALUES(?,?,?,?,?)",
                (t.standard_name, t.business_meaning, t.data_type, t.enum_values, t.status),
            )
            return cur.lastrowid

    def delete_term(self, tid: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE terms SET status='expired' WHERE id=?", (tid,))

    # ===== SourceTable CRUD =====
    def list_source_tables(self, layer: str = None) -> list[SourceTable]:
        with self._conn() as c:
            if layer:
                rows = c.execute("SELECT * FROM source_tables WHERE layer=? ORDER BY id", (layer,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM source_tables ORDER BY layer, id").fetchall()
            return [SourceTable(**dict(r)) for r in rows]

    def get_source_table(self, tid: int) -> Optional[SourceTable]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM source_tables WHERE id=?", (tid,)).fetchone()
            return SourceTable(**dict(r)) if r else None

    def insert_source_table(self, t: SourceTable) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO source_tables
                (table_name, layer, business_domain, business_brief, update_mode, unique_key, granularity, status)
                VALUES(?,?,?,?,?,?,?,?)""",
                (t.table_name, t.layer, t.business_domain, t.business_brief,
                 t.update_mode, t.unique_key, t.granularity, t.status),
            )
            return cur.lastrowid

    def update_source_table(self, t: SourceTable) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE source_tables SET
                table_name=?, layer=?, business_domain=?, business_brief=?,
                update_mode=?, unique_key=?, granularity=?, status=?
                WHERE id=?""",
                (t.table_name, t.layer, t.business_domain, t.business_brief,
                 t.update_mode, t.unique_key, t.granularity, t.status, t.id),
            )

    def audit_source_table(self, tid: int) -> None:
        """源表审核通过（PRD §3.3：未填唯一键的拦截）"""
        t = self.get_source_table(tid)
        if t and not t.unique_key:
            raise ValueError(f"源表 {t.table_name} 缺少业务唯一键，无法审核通过（PRD §3.3）")
        with self._conn() as c:
            c.execute("UPDATE source_tables SET status='audited' WHERE id=?", (tid,))

    # ===== DraftLineage CRUD =====
    def list_drafts(self) -> list[DraftLineage]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM draft_lineage ORDER BY id DESC").fetchall()
            return [DraftLineage(**dict(r)) for r in rows]

    def insert_draft(self, d: DraftLineage) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO draft_lineage(sql_text, parsed_json, status) VALUES(?,?,?)",
                (d.sql_text, d.parsed_json, d.status),
            )
            return cur.lastrowid

    def update_draft_status(self, did: int, status: str, note: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE draft_lineage SET status=?, audit_note=? WHERE id=?",
                (status, note, did),
            )

    # ===== 审计日志 =====
    def log_audit(self, entity_type: str, entity_id: int, action: str, operator: str = "", note: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO audit_log(entity_type, entity_id, action, operator, note) VALUES(?,?,?,?,?)",
                (entity_type, entity_id, action, operator, note),
            )


# 默认 DB 路径
_default_db: Optional[WarehouseDB] = None


def get_default_db() -> WarehouseDB:
    """获取当前工作区的 WarehouseDB 单例。"""
    global _default_db
    if _default_db is not None:
        return _default_db
    from core.workspace import current
    ws = current()
    if ws is None:
        db_path = Path("~/AichatStudio/default/warehouse.db").expanduser()
    else:
        db_path = Path(ws.root) / "warehouse.db"
    _default_db = WarehouseDB(db_path)
    return _default_db
