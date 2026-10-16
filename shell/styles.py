"""浅色主题 QSS（全局唯一）。

色板：
    背景 #F7F7F8（接近白但不刺眼）
    卡片 #FFFFFF + 1px 浅灰边 rgba(23,23,23,0.12)
    主文字 #171717
    次文字 #52525B
    品牌色 #0FDC78（focus，不大面积铺）

字体 fallback 链（跨平台自动适配，Mac 用 SF/PingFang，Win 用雅黑/Segoe）：
    "SF Pro Text", "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, "Segoe UI"

各域不许改全局 QSS，只能在自己 widget 内加局部样式。
"""

from __future__ import annotations

from typing import Literal

Theme = Literal["light", "dark"]

FONT_FAMILY = (
    '"PingFang SC", "Helvetica Neue", "Microsoft YaHei", '
    'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
)

# 浅色 token（对齐 chat 域 breeze 皮肤，视觉统一）
LIGHT_TOKENS = {
    "bg": "#fbfdff",
    "bg_card": "#FFFFFF",
    "bg_hover": "#f0f5fa",
    "bg_pressed": "#e3eaf2",
    "border": "rgba(15, 23, 42, 0.12)",
    "border_strong": "rgba(15, 23, 42, 0.20)",
    "text": "#0f172a",
    "text_muted": "#475569",
    "text_disabled": "#94a3b8",
    "brand": "#10b981",
    "brand_hover": "#059669",
    "brand_pressed": "#047857",
    "brand_text": "#ffffff",
    "brand_soft": "#ecfdf5",
    "danger": "#ef4444",
    "warning": "#f59e0b",
    "info": "#0ea5e9",
}

# 暗色 token（备用，未来切换时用）
DARK_TOKENS = {
    "bg": "#1A1A1F",
    "bg_card": "#232328",
    "bg_hover": "#2D2D33",
    "bg_pressed": "#383840",
    "border": "rgba(255, 255, 255, 0.10)",
    "border_strong": "rgba(255, 255, 255, 0.18)",
    "text": "#F4F4F5",
    "text_muted": "#A1A1AA",
    "text_disabled": "#52525B",
    "brand": "#0FDC78",
    "brand_hover": "#36F08C",
    "brand_pressed": "#0BC768",
    "brand_text": "#0FDC78",
    "brand_soft": "#0A2A1D",
    "danger": "#FF6369",
    "warning": "#FAB348",
    "info": "#3CC4F0",
}


def build_qss(theme: Theme = "light") -> str:
    """根据主题生成 QSS"""
    t = LIGHT_TOKENS if theme == "light" else DARK_TOKENS
    return f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: 14px;
    color: {t["text"]};
}}
QMainWindow, QDialog {{
    background-color: {t["bg"]};
}}
QWidget#Sidebar {{
    background-color: {t["bg_card"]};
    border-right: 1px solid {t["border"]};
}}
QLabel {{
    background: transparent;
    color: {t["text"]};
}}
QLabel#Caption {{
    color: {t["text_muted"]};
    font-size: 12px;
}}
QLabel#Title {{
    font-size: 16px;
    font-weight: 500;
}}
QLabel#BrandAccent {{
    color: {t["brand_text"]};
}}
QPushButton {{
    background-color: {t["bg_card"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    padding: 6px 12px;
    border-radius: 6px;
}}
QPushButton:hover {{
    background-color: {t["bg_hover"]};
    border: 1px solid {t["border_strong"]};
}}
QPushButton:pressed {{
    background-color: {t["bg_pressed"]};
}}
QPushButton:disabled {{
    color: {t["text_disabled"]};
    background-color: {t["bg"]};
    border: 1px solid {t["border"]};
}}
QPushButton#Primary {{
    background-color: {t["brand"]};
    color: {t["brand_text"]};
    border: none;
    font-weight: 500;
}}
QPushButton#Primary:hover {{
    background-color: {t["brand_hover"]};
}}
QPushButton#Primary:pressed {{
    background-color: {t["brand_pressed"]};
}}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {t["bg_card"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    padding: 6px 8px;
    border-radius: 6px;
    selection-background-color: {t["brand"]};
    selection-color: {t["brand_text"]};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1px solid {t["brand"]};
}}
QListWidget, QTreeWidget, QTableWidget {{
    background-color: {t["bg_card"]};
    border: 1px solid {t["border"]};
    border-radius: 6px;
    outline: 0;
}}
QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover {{
    background-color: {t["bg_hover"]};
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
    background-color: {t["brand_soft"]};
    color: {t["brand_text"]};
}}
QTabWidget::pane {{
    border: 1px solid {t["border"]};
    border-radius: 6px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {t["text_muted"]};
    padding: 8px 16px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {t["text"]};
    border-bottom: 2px solid {t["brand"]};
}}
QTabBar::tab:hover:!selected {{
    color: {t["text"]};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {t["border_strong"]};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {t["text_disabled"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background-color: {t["border_strong"]};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {t["text_disabled"]};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QToolTip {{
    background-color: {t["bg_card"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    padding: 4px 8px;
    border-radius: 4px;
}}
QStatusBar {{
    background-color: {t["bg_card"]};
    color: {t["text_muted"]};
    border-top: 1px solid {t["border"]};
}}
QMenuBar {{
    background-color: {t["bg"]};
    color: {t["text"]};
    border-bottom: 1px solid {t["border"]};
}}
QMenuBar::item:selected {{
    background-color: {t["bg_hover"]};
}}
QMenu {{
    background-color: {t["bg_card"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {t["bg_hover"]};
}}
QSplitter::handle {{
    background-color: {t["border"]};
}}
QGroupBox {{
    border: 1px solid {t["border"]};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 12px;
}}
QGroupBox::title {{
    color: {t["text_muted"]};
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}
QCheckBox, QRadioButton {{
    background: transparent;
    color: {t["text"]};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
}}
QHeaderView::section {{
    background-color: {t["bg_hover"]};
    color: {t["text_muted"]};
    padding: 6px 8px;
    border: none;
    border-right: 1px solid {t["border"]};
    border-bottom: 1px solid {t["border"]};
    font-weight: 500;
}}
"""


# 全局单例：当前主题
_current_theme: Theme = "light"


def apply_theme(app, theme: Theme = "light") -> None:
    """在 QApplication 上应用主题"""
    global _current_theme
    _current_theme = theme
    app.setStyleSheet(build_qss(theme))


def current_theme() -> Theme:
    return _current_theme


def toggle_theme(app) -> Theme:
    """切换主题"""
    new_theme: Theme = "dark" if _current_theme == "light" else "light"
    apply_theme(app, new_theme)
    return new_theme
