"""数仓域 — 主面板（4 Tab + 权威/草稿双栏）。

PRD §1 四模块：
  Tab1 规范录入：术语词典 CRUD + 分层规则展示（只读）
  Tab2 源采集：源表列表 + 审核（触发守卫）
  Tab3 SQL 解析：粘贴 SQL → 解析 → 草稿列表
  Tab4 查询校验：双栏 UI（权威 §7.1 / 草稿 §7.2）+ 溯源查询
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QWidget, QMainWindow, QTabWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QListWidget, QListWidgetItem,
    QFormLayout, QDialog, QDialogButtonBox, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QSplitter, QGroupBox,
)
from PyQt6.QtCore import Qt

from ..models import (
    WarehouseDB, get_default_db, Term, SourceTable, DraftLineage, LAYERS,
)
from ..guards import run_all_guards, check_term_conflicts
from ..sql_parser import parse_sql, HAS_SQLGLOT

logger = logging.getLogger(__name__)


class TermDialog(QDialog):
    """术语词典录入/编辑弹窗"""

    def __init__(self, parent=None, term: Term = None):
        super().__init__(parent)
        self.setWindowTitle("编辑术语" if term else "新增术语")
        self.setMinimumWidth(400)
        form = QFormLayout(self)
        self.name_edit = QLineEdit(term.standard_name if term else "")
        self.meaning_edit = QLineEdit(term.business_meaning if term else "")
        self.type_edit = QLineEdit(term.data_type if term else "")
        self.enum_edit = QLineEdit(term.enum_values if term else "")
        form.addRow("标准字段名", self.name_edit)
        form.addRow("业务含义", self.meaning_edit)
        form.addRow("数据类型", self.type_edit)
        form.addRow("枚举值（逗号分隔）", self.enum_edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        form.addRow(btns)
        self._term = term

    def _on_ok(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "校验", "标准字段名不能为空")
            return
        self.accept()

    def to_term(self) -> Term:
        if self._term:
            self._term.standard_name = self.name_edit.text().strip()
            self._term.business_meaning = self.meaning_edit.text().strip()
            self._term.data_type = self.type_edit.text().strip()
            self._term.enum_values = self.enum_edit.text().strip()
            return self._term
        return Term(
            standard_name=self.name_edit.text().strip(),
            business_meaning=self.meaning_edit.text().strip(),
            data_type=self.type_edit.text().strip(),
            enum_values=self.enum_edit.text().strip(),
        )


class SourceTableDialog(QDialog):
    """源表录入/编辑弹窗"""

    def __init__(self, parent=None, table: SourceTable = None):
        super().__init__(parent)
        self.setWindowTitle("编辑源表" if table else "新增源表")
        self.setMinimumWidth(440)
        form = QFormLayout(self)
        self.name_edit = QLineEdit(table.table_name if table else "")
        self.layer_combo = QComboBox()
        self.layer_combo.addItems(LAYERS)
        if table:
            self.layer_combo.setCurrentText(table.layer)
        self.domain_edit = QLineEdit(table.business_domain if table else "")
        self.brief_edit = QTextEdit(table.business_brief if table else "")
        self.brief_edit.setMaximumHeight(60)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["", "全量", "增量", "实时"])
        if table:
            self.mode_combo.setCurrentText(table.update_mode)
        self.key_edit = QLineEdit(table.unique_key if table else "")
        self.gran_edit = QLineEdit(table.granularity if table else "")
        form.addRow("表名", self.name_edit)
        form.addRow("分层", self.layer_combo)
        form.addRow("业务域", self.domain_edit)
        form.addRow("业务简介", self.brief_edit)
        form.addRow("更新模式", self.mode_combo)
        form.addRow("业务唯一键", self.key_edit)
        form.addRow("粒度", self.gran_edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        form.addRow(btns)
        self._table = table

    def _on_ok(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "校验", "表名不能为空")
            return
        self.accept()

    def to_table(self) -> SourceTable:
        t = self._table or SourceTable()
        t.table_name = self.name_edit.text().strip()
        t.layer = self.layer_combo.currentText()
        t.business_domain = self.domain_edit.text().strip()
        t.business_brief = self.brief_edit.toPlainText().strip()
        t.update_mode = self.mode_combo.currentText()
        t.unique_key = self.key_edit.text().strip()
        t.granularity = self.gran_edit.text().strip()
        return t


class TermsTab(QWidget):
    """Tab1: 规范录入（术语词典 CRUD + 分层规则只读展示）"""

    def __init__(self, db: WarehouseDB):
        super().__init__()
        self._db = db
        layout = QVBoxLayout(self)

        # 顶部：分层规则只读
        rules_group = QGroupBox("系统预置分层规则（代码写死，AI/用户不可改）")
        rules_layout = QVBoxLayout(rules_group)
        rules_layout.addWidget(QLabel(" → ".join(LAYERS) + "（单向 DAG）"))
        rules_layout.addWidget(QLabel("禁止：反向依赖、循环依赖、多对多无说明"))
        layout.addWidget(rules_group)

        # 术语词典
        bar = QHBoxLayout()
        bar.addWidget(QLabel("📘 术语词典"))
        bar.addStretch()
        btn_new = QPushButton("新增术语")
        btn_new.clicked.connect(self._on_new)
        bar.addWidget(btn_new)
        btn_check = QPushButton("校验冲突")
        btn_check.clicked.connect(self._on_check_conflicts)
        bar.addWidget(btn_check)
        layout.addLayout(bar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["标准字段名", "业务含义", "数据类型", "枚举值"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self):
        terms = self._db.list_terms()
        self.table.setRowCount(len(terms))
        for i, t in enumerate(terms):
            self.table.setItem(i, 0, QTableWidgetItem(t.standard_name))
            self.table.setItem(i, 1, QTableWidgetItem(t.business_meaning))
            self.table.setItem(i, 2, QTableWidgetItem(t.data_type))
            self.table.setItem(i, 3, QTableWidgetItem(t.enum_values))

    def _on_new(self):
        dlg = TermDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._db.insert_term(dlg.to_term())
            self.refresh()

    def _on_check_conflicts(self):
        result = check_term_conflicts(self._db)
        if result.warnings:
            QMessageBox.warning(self, "术语冲突告警", "\n".join(result.warnings))
        else:
            QMessageBox.information(self, "校验通过", "无术语冲突")


class SourceTablesTab(QWidget):
    """Tab2: 源系统元数据采集 + 审核"""

    def __init__(self, db: WarehouseDB):
        super().__init__()
        self._db = db
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("📡 源系统元数据"))
        bar.addStretch()
        btn_new = QPushButton("新增源表")
        btn_new.clicked.connect(self._on_new)
        bar.addWidget(btn_new)
        btn_audit = QPushButton("审核通过")
        btn_audit.clicked.connect(self._on_audit)
        bar.addWidget(btn_audit)
        layout.addLayout(bar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["表名", "分层", "业务域", "唯一键", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self):
        tables = self._db.list_source_tables()
        self.table.setRowCount(len(tables))
        for i, t in enumerate(tables):
            self.table.setItem(i, 0, QTableWidgetItem(t.table_name))
            self.table.setItem(i, 1, QTableWidgetItem(t.layer))
            self.table.setItem(i, 2, QTableWidgetItem(t.business_domain))
            self.table.setItem(i, 3, QTableWidgetItem(t.unique_key))
            self.table.setItem(i, 4, QTableWidgetItem(t.status))
            self.table.item(i, 0).setData(Qt.ItemDataRole.UserRole, t.id)

    def _on_new(self):
        dlg = SourceTableDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._db.insert_source_table(dlg.to_table())
            self.refresh()

    def _on_audit(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一行")
            return
        tid = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        try:
            self._db.audit_source_table(tid)
            self._db.log_audit("source_table", tid, "approve")
            self.refresh()
            QMessageBox.information(self, "审核通过", "源表已审核")
        except ValueError as e:
            QMessageBox.critical(self, "审核拦截", str(e))


class SQLParseTab(QWidget):
    """Tab3: SQL 血缘解析"""

    def __init__(self, db: WarehouseDB):
        super().__init__()
        self._db = db
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("🔬 SQL 血缘解析"))
        bar.addStretch()
        if not HAS_SQLGLOT:
            bar.addWidget(QLabel("⚠ sqlglot 未安装"))
        layout.addLayout(bar)

        self.sql_edit = QTextEdit()
        self.sql_edit.setPlaceholderText("粘贴 Hive/Spark/Flink SQL ...")
        self.sql_edit.setMaximumHeight(200)
        layout.addWidget(self.sql_edit)

        btn_row = QHBoxLayout()
        self.dialect_combo = QComboBox()
        self.dialect_combo.addItems(["hive", "spark", "flink", "mysql", "postgres"])
        btn_row.addWidget(QLabel("方言:"))
        btn_row.addWidget(self.dialect_combo)
        btn_row.addStretch()
        btn_parse = QPushButton("解析 SQL")
        btn_parse.clicked.connect(self._on_parse)
        btn_row.addWidget(btn_parse)
        layout.addLayout(btn_row)

        # 解析结果
        self.result_edit = QTextEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText("解析结果：表依赖 / 字段血缘 / JOIN 关联 ...")
        layout.addWidget(self.result_edit, 1)

        # 草稿列表
        layout.addWidget(QLabel("📋 草稿列表（待审核）"))
        self.drafts_list = QListWidget()
        layout.addWidget(self.drafts_list)
        self._refresh_drafts()

    def _on_parse(self):
        sql = self.sql_edit.toPlainText().strip()
        if not sql:
            return
        result = parse_sql(sql, self.dialect_combo.currentText())
        self.result_edit.setPlainText(result.to_json())

        # 存入草稿表
        draft = DraftLineage(sql_text=sql, parsed_json=result.to_json())
        self._db.insert_draft(draft)
        self._refresh_drafts()

    def _refresh_drafts(self):
        self.drafts_list.clear()
        for d in self._db.list_drafts():
            item = QListWidgetItem(f"#{d.id} [{d.status}] {d.created_at} — {d.sql_text[:60]}...")
            item.setData(Qt.ItemDataRole.UserRole, d.id)
            self.drafts_list.addItem(item)


class QueryTab(QWidget):
    """Tab4: 查询校验（权威/草稿双栏，PRD §7）"""

    def __init__(self, db: WarehouseDB):
        super().__init__()
        self._db = db
        layout = QVBoxLayout(self)

        # PRD §7 双栏界面
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左：权威结果
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel(" 知识库权威结果（可信）"))
        self.authority_table = QTableWidget(0, 3)
        self.authority_table.setHorizontalHeaderLabels(["类型", "名称", "详情"])
        self.authority_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        left_layout.addWidget(self.authority_table)
        splitter.addWidget(left)

        # 右：AI/草稿建议
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("💡 AI/草稿建议（仅参考，不可直接入库）"))
        self.draft_table = QTableWidget(0, 3)
        self.draft_table.setHorizontalHeaderLabels(["类型", "名称", "详情"])
        self.draft_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        right_layout.addWidget(self.draft_table)
        splitter.addWidget(right)

        splitter.setSizes([500, 500])
        layout.addWidget(splitter, 1)

        self.refresh()

    def refresh(self):
        # 左栏：已审核源表 + 术语
        authority_rows: list[list[str]] = []
        for t in self._db.list_source_tables():
            if t.status == "audited":
                authority_rows.append(["源表", t.table_name, f"{t.layer} | 唯一键: {t.unique_key}"])
        for term in self._db.list_terms():
            authority_rows.append(["术语", term.standard_name, term.business_meaning])
        self.authority_table.setRowCount(len(authority_rows))
        for i, row in enumerate(authority_rows):
            for j, val in enumerate(row):
                self.authority_table.setItem(i, j, QTableWidgetItem(val))

        # 右栏：草稿
        drafts = self._db.list_drafts()
        self.draft_table.setRowCount(len(drafts))
        for i, d in enumerate(drafts):
            self.draft_table.setItem(i, 0, QTableWidgetItem("血缘草稿"))
            self.draft_table.setItem(i, 1, QTableWidgetItem(f"#{d.id}"))
            self.draft_table.setItem(i, 2, QTableWidgetItem(f"[{d.status}] {d.sql_text[:80]}..."))


class MainWindow(QMainWindow):
    """数仓域主面板：4 Tab"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("数仓元数据知识库")
        self.setMinimumSize(1000, 700)

        self._db = get_default_db()

        self.tabs = QTabWidget()
        self.tab_terms = TermsTab(self._db)
        self.tab_sources = SourceTablesTab(self._db)
        self.tab_sql = SQLParseTab(self._db)
        self.tab_query = QueryTab(self._db)

        self.tabs.addTab(self.tab_terms, "📘 规范录入")
        self.tabs.addTab(self.tab_sources, "📡 源采集")
        self.tabs.addTab(self.tab_sql, "🔬 SQL 解析")
        self.tabs.addTab(self.tab_query, "🔍 查询校验")

        self.setCentralWidget(self.tabs)
