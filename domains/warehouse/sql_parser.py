"""数仓域 — SQL 血缘解析（PRD §4）。

用 sqlglot 解析 Hive/Spark/Flink SQL，提取：
  1. 表级依赖（目标表 ← 来源表）
  2. 字段级血缘（目标字段 ← 来源字段 + 清洗公式）
  3. JOIN 关联（左表/右表/JOIN 类型/ON 条件）

当前阶段：占位骨架，sqlglot 依赖可选（未装时返回提示）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

try:
    import sqlglot
    from sqlglot import exp as sql_exp
    HAS_SQLGLOT = True
except ImportError:
    HAS_SQLGLOT = False


@dataclass
class ParsedLineage:
    """SQL 解析结果"""
    target_table: str = ""
    source_tables: list[str] = None
    column_lineage: list[dict] = None       # [{target_col, source_table, source_col, expr}]
    joins: list[dict] = None                # [{left_table, right_table, join_type, on_condition}]
    errors: list[str] = None

    def __post_init__(self):
        if self.source_tables is None: self.source_tables = []
        if self.column_lineage is None: self.column_lineage = []
        if self.joins is None: self.joins = []
        if self.errors is None: self.errors = []

    def to_json(self) -> str:
        return json.dumps({
            "target_table": self.target_table,
            "source_tables": self.source_tables,
            "column_lineage": self.column_lineage,
            "joins": self.joins,
            "errors": self.errors,
        }, ensure_ascii=False, indent=2)


def parse_sql(sql_text: str, dialect: str = "hive") -> ParsedLineage:
    """解析 SQL，提取血缘 + JOIN 关联。

    Args:
        sql_text: SQL 脚本
        dialect: hive / spark / flink / mysql / postgres

    Returns: ParsedLineage
    """
    if not HAS_SQLGLOT:
        return ParsedLineage(errors=["sqlglot 未安装，无法解析 SQL。运行 pip install sqlglot"])

    result = ParsedLineage()
    try:
        statements = sqlglot.parse(sql_text, dialect=dialect)
    except Exception as e:  # noqa: BLE001
        result.errors.append(f"SQL 解析失败: {e}")
        return result

    for stmt in statements:
        if stmt is None:
            continue
        if isinstance(stmt, sql_exp.Create):
            _extract_create(stmt, result)
        elif isinstance(stmt, sql_exp.Insert):
            _extract_insert(stmt, result)
        elif isinstance(stmt, sql_exp.Select):
            _extract_select(stmt, result)

    return result


def _extract_create(stmt: sql_exp.Create, result: ParsedLineage) -> None:
    """CREATE TABLE ... AS SELECT"""
    # 目标表
    target = stmt.this
    if isinstance(target, sql_exp.Schema):
        target = target.this
    if target:
        result.target_table = target.sql()

    # 来源（SELECT 子句）
    select = stmt.args.get("expression")
    if isinstance(select, sql_exp.Select):
        _extract_select(select, result)


def _extract_insert(stmt: sql_exp.Insert, result: ParsedLineage) -> None:
    """INSERT INTO ... SELECT"""
    target = stmt.this
    if target:
        result.target_table = target.sql()

    select = stmt.expression
    if isinstance(select, sql_exp.Select):
        _extract_select(select, result)


def _extract_select(stmt: sql_exp.Select, result: ParsedLineage) -> None:
    """SELECT 语句：提取来源表 + JOIN + 字段血缘"""
    # FROM + JOIN
    from_clause = stmt.args.get("from")
    if from_clause:
        for tbl in from_clause.find_all(sql_exp.Table):
            name = tbl.sql()
            if name and name not in result.source_tables:
                result.source_tables.append(name)

    # JOIN
    for join in stmt.find_all(sql_exp.Join):
        join_type = join.args.get("kind", "INNER")
        right_table = join.this
        if isinstance(right_table, sql_exp.Table):
            r_name = right_table.sql()
            if r_name not in result.source_tables:
                result.source_tables.append(r_name)
            # ON 条件
            on_cond = ""
            condition = join.args.get("on")
            if condition:
                on_cond = condition.sql()
            result.joins.append({
                "left_table": result.source_tables[0] if result.source_tables else "",
                "right_table": r_name,
                "join_type": str(join_type).upper(),
                "on_condition": on_cond,
            })

    # 字段血缘（SELECT 列）
    for proj in stmt.expressions:
        if isinstance(proj, sql_exp.Alias):
            target_col = proj.alias_or_name
            source_expr = proj.this
            source_tables_in_expr = [t.sql() for t in source_expr.find_all(sql_exp.Table)]
            result.column_lineage.append({
                "target_col": target_col,
                "source_table": source_tables_in_expr[0] if source_tables_in_expr else "",
                "source_col": source_expr.sql(),
                "expr": source_expr.sql(),
            })
        elif isinstance(proj, sql_exp.Column):
            result.column_lineage.append({
                "target_col": proj.sql(),
                "source_table": proj.table or "",
                "source_col": proj.name,
                "expr": proj.sql(),
            })
