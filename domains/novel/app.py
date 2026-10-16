"""NovelForge 应用入口。"""
from __future__ import annotations

import sys
from PyQt6.QtWidgets import QApplication

from domains.novel.config import Config
from domains.novel.ui.main_window import MainWindow


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("NovelForge")
    app.setOrganizationName("NovelForge")

    # 加载配置
    config = Config.load()

    # 暗色主题
    app.setStyleSheet(_DARK_STYLE)

    window = MainWindow(config)
    window.show()
    return app.exec()


_DARK_STYLE = """
QMainWindow, QDialog { background-color: #1e1e2e; color: #cdd6f4; }
QWidget { color: #cdd6f4; }
QMenuBar { background-color: #181825; color: #cdd6f4; border-bottom: 1px solid #313244; }
QMenuBar::item:selected { background-color: #313244; }
QMenu { background-color: #1e1e2e; border: 1px solid #313244; }
QMenu::item:selected { background-color: #313244; }
QToolBar { background-color: #181825; border-bottom: 1px solid #313244; padding: 4px; }
QTreeWidget { background-color: #1e1e2e; border: 1px solid #313244; }
QTreeWidget::item:selected { background-color: #313244; }
QPlainTextEdit, QTextEdit, QLineEdit { background-color: #181825; border: 1px solid #313244; color: #cdd6f4; font-size: 14px; }
QTabWidget::pane { border: 1px solid #313244; }
QTabBar::tab { background-color: #181825; padding: 8px 16px; border: 1px solid #313244; }
QTabBar::tab:selected { background-color: #1e1e2e; border-bottom: none; }
QTableWidget { background-color: #1e1e2e; gridline-color: #313244; }
QTableWidget::item:selected { background-color: #313244; }
QHeaderView::section { background-color: #181825; border: 1px solid #313244; padding: 4px; }
QPushButton { background-color: #313244; border: 1px solid #45475a; padding: 6px 16px; border-radius: 4px; }
QPushButton:hover { background-color: #45475a; }
QPushButton:pressed { background-color: #585b70; }
QPushButton:disabled { background-color: #181825; color: #585b70; }
QStatusBar { background-color: #181825; border-top: 1px solid #313244; }
QSplitter::handle { background-color: #313244; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }
QLabel { color: #cdd6f4; }
QGroupBox { border: 1px solid #313244; border-radius: 4px; margin-top: 8px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QComboBox { background-color: #181825; border: 1px solid #313244; padding: 4px; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background-color: #1e1e2e; border: 1px solid #313244; }
QSpinBox, QDoubleSpinBox { background-color: #181825; border: 1px solid #313244; padding: 4px; }
QProgressBar { background-color: #181825; border: 1px solid #313244; text-align: center; }
QProgressBar::chunk { background-color: #89b4fa; }
"""
