"""NovelForge 全局统一样式（多皮肤主题）。

设计原则：暗底亮字，高对比度，简洁统一。
支持多套皮肤主题，用户可在设置中切换。

皮肤体系：
- 每套皮肤定义完整色板（base/mantle/crust/text/subtext/surface/overlay/accent 等）
- 由色板动态生成 GLOBAL_STYLESHEET + QPalette + 按钮样式
- 配置持久化在 config.json 的 skin 字段
"""

from __future__ import annotations
from dataclasses import dataclass
from PyQt6.QtWidgets import QProxyStyle, QStyle
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QPolygonF, QPalette
from PyQt6.QtCore import QPointF


# ===== 皮肤定义 =====

@dataclass
class SkinColors:
    """一套皮肤的完整色板。"""
    base: str          # 最深底色（主背景）
    mantle: str        # 次深底色（工具栏/侧栏）
    crust: str         # 第三层底色（折叠页）
    surface0: str      # 输入区/卡片底色
    surface1: str      # 悬浮/选中底色
    surface2: str      # 按钮底色
    overlay: str       # 边框/分隔线
    text: str          # 主文字
    subtext: str       # 次要文字
    accent: str        # 强调色（蓝/紫，链接/选中）
    accent_hover: str  # 强调色悬浮
    green: str         # 成功/已执行
    yellow: str        # 待确认/警告
    red: str           # 危险/错误
    mauve: str         # 紫色强调


@dataclass
class Skin:
    """一套完整皮肤。"""
    key: str
    name: str
    desc: str
    colors: SkinColors


SKINS: dict[str, Skin] = {
    "mocha": Skin(
        key="mocha", name="摩卡夜", desc="Catppuccin Mocha · 经典暗紫",
        colors=SkinColors(
            base="#1e1e2e", mantle="#181825", crust="#11111b",
            surface0="#313244", surface1="#45475a", surface2="#585b70",
            overlay="#45475a", text="#cdd6f4", subtext="#a6adc8",
            accent="#89b4fa", accent_hover="#b4befe",
            green="#a6e3a1", yellow="#f9e2af", red="#f38ba8", mauve="#cba6f7",
        ),
    ),
    "nord": Skin(
        key="nord", name="北境蓝", desc="Nord · 冷冽冰川蓝",
        colors=SkinColors(
            base="#2e3440", mantle="#272c36", crust="#21262e",
            surface0="#3b4252", surface1="#434c5e", surface2="#4c566a",
            overlay="#4c566a", text="#d8dee9", subtext="#9aa6bd",
            accent="#81a1c1", accent_hover="#8fbcbb",
            green="#a3be8c", yellow="#ebcb8b", red="#bf616a", mauve="#b48ead",
        ),
    ),
    "tokyonight": Skin(
        key="tokyonight", name="东京夜", desc="Tokyo Night · 霓虹紫蓝",
        colors=SkinColors(
            base="#1a1b26", mantle="#16161e", crust="#13131a",
            surface0="#24253a", surface1="#2a2b3d", surface2="#363649",
            overlay="#3b3f54", text="#c0caf5", subtext="#9aa5d1",
            accent="#7aa2f7", accent_hover="#89b4fa",
            green="#9ece6a", yellow="#e0af68", red="#f7768e", mauve="#bb9af7",
        ),
    ),
    "dracula": Skin(
        key="dracula", name="德古拉", desc="Dracula · 经典吸血鬼紫",
        colors=SkinColors(
            base="#282a36", mantle="#21222c", crust="#1a1b22",
            surface0="#383a4a", surface1="#44475a", surface2="#50545d",
            overlay="#44475a", text="#f8f8f2", subtext="#b9bac4",
            accent="#bd93f9", accent_hover="#caa6ff",
            green="#50fa7b", yellow="#f1fa8c", red="#ff5555", mauve="#ff79c6",
        ),
    ),
    "gruvbox": Skin(
        key="gruvbox", name="熔岩暖", desc="Gruvbox · 温暖大地色",
        colors=SkinColors(
            base="#282828", mantle="#1d2021", crust="#141518",
            surface0="#3c3836", surface1="#504945", surface2="#665c54",
            overlay="#504945", text="#ebdbb2", subtext="#a89984",
            accent="#83a598", accent_hover="#a9a587",
            green="#b8bb26", yellow="#fabd2f", red="#fb4934", mauve="#d3869b",
        ),
    ),
    "rosepine": Skin(
        key="rosepine", name="玫瑰松", desc="Rosé Pine · 优雅玫瑰金",
        colors=SkinColors(
            base="#1f1d2e", mantle="#191726", crust="#16131f",
            surface0="#2a283a", surface1="#34304a", surface2="#403954",
            overlay="#403954", text="#e0def4", subtext="#908caa",
            accent="#c4a7e7", accent_hover="#d4b8ff",
            green="#9ccfd8", yellow="#f6c177", red="#eb6f92", mauve="#ebbcba",
        ),
    ),
    "onedark": Skin(
        key="onedark", name="暗夜青", desc="Atom One Dark · 简洁深邃",
        colors=SkinColors(
            base="#282c34", mantle="#21252c", crust="#1b1d22",
            surface0="#323842", surface1="#3e4451", surface2="#4b5263",
            overlay="#3e4451", text="#abb2bf", subtext="#848a9a",
            accent="#61afef", accent_hover="#7ec0ff",
            green="#98c379", yellow="#e5c07b", red="#e06c75", mauve="#c678dd",
        ),
    ),
    "github_dark": Skin(
        key="github_dark", name="暗夜码", desc="GitHub Dark · 清爽开发风",
        colors=SkinColors(
            base="#0d1117", mantle="#010409", crust="#000000",
            surface0="#161b22", surface1="#21262d", surface2="#30363d",
            overlay="#30363d", text="#e6edf3", subtext="#8b949e",
            accent="#58a6ff", accent_hover="#79c0ff",
            green="#3fb950", yellow="#d29922", red="#f85149", mauve="#bc8cff",
        ),
    ),
}

DEFAULT_SKIN = "mocha"


def get_skin(key: str) -> Skin:
    """按 key 获取皮肤，不存在则回退到默认。"""
    return SKINS.get(key, SKINS[DEFAULT_SKIN])


def skin_keys() -> list[str]:
    """返回所有皮肤 key（有序）。"""
    return list(SKINS.keys())


def generate_stylesheet(skin_key: str = DEFAULT_SKIN) -> str:
    """根据皮肤色板动态生成全局 QSS 样式表。"""
    s = get_skin(skin_key)
    c = s.colors
    return f"""
/* ---- 基础容器 ---- */
QWidget {{
    background-color: {c.base};
    color: {c.text};
}}
QFrame {{ background-color: {c.base}; color: {c.text}; }}
QStackedWidget {{ background-color: {c.base}; }}
QMainWindow {{ background-color: {c.base}; }}
QDialog {{ background-color: {c.base}; color: {c.text}; }}
QToolBar {{ background-color: {c.mantle}; border: none; spacing: 4px; padding: 2px; }}
QToolBar::separator {{ background-color: {c.overlay}; width: 1px; margin: 4px 2px; }}

/* ---- 输入框 / 下拉 / 数字框 ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background-color: {c.surface0};
    color: {c.text};
    border: 1px solid {c.overlay};
    border-radius: 4px;
    padding: 5px 8px;
    selection-background-color: {c.surface2};
    selection-color: #ffffff;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {c.accent_hover};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background-color: {c.surface0};
    color: {c.text};
    selection-background-color: {c.surface2};
    selection-color: #ffffff;
    border: 1px solid {c.overlay};
}}

/* ---- 表头 ---- */
QHeaderView::section {{
    background-color: {c.surface0};
    color: {c.subtext};
    border: 1px solid {c.overlay};
    padding: 4px 8px;
    font-size: 12px;
}}

/* ---- 列表 / 树 / 表格 ---- */
QListWidget, QTreeWidget, QTableWidget {{
    background-color: {c.base};
    color: {c.text};
    border: 1px solid {c.overlay};
    border-radius: 4px;
    outline: none;
}}
QListWidget::item, QTreeWidget::item, QTableWidget::item {{
    color: {c.text};
    padding: 4px 8px;
    border: none;
}}
QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover {{
    background-color: {c.surface0};
    color: #ffffff;
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
    background-color: {c.surface1};
    color: #ffffff;
}}

/* ---- 表格网格 ---- */
QTableWidget {{
    gridline-color: {c.surface0};
}}

/* ---- 分组框 / 面板 ---- */
QGroupBox {{
    color: {c.text};
    border: 1px solid {c.overlay};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 16px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}

/* ---- 标签页 ---- */
QTabWidget::pane {{
    background-color: {c.base};
    border: 1px solid {c.overlay};
    border-radius: 4px;
}}
QTabBar::tab {{
    background-color: {c.surface0};
    color: {c.subtext};
    padding: 6px 14px;
    border: 1px solid {c.overlay};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 1px;
}}
QTabBar::tab:selected {{
    background-color: {c.surface1};
    color: {c.text};
    font-weight: bold;
}}
QTabBar::tab:hover:!selected {{
    background-color: {c.surface0};
    color: {c.text};
}}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{
    background-color: {c.base};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background-color: {c.overlay};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background-color: {c.surface2}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background-color: {c.base};
    height: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background-color: {c.overlay};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background-color: {c.surface2}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---- 分隔线 / 分割器 ---- */
QSplitter::handle {{
    background-color: {c.overlay};
}}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* ---- 工具提示 ---- */
QToolTip {{
    background-color: {c.surface0};
    color: {c.text};
    border: 1px solid {c.overlay};
    padding: 4px 6px;
    border-radius: 3px;
}}

/* ---- 菜单 ---- */
QMenuBar {{
    background-color: {c.base};
    color: {c.text};
}}
QMenuBar::item:selected {{
    background-color: {c.surface1};
}}
QMenu {{
    background-color: {c.base};
    color: {c.text};
    border: 1px solid {c.overlay};
}}
QMenu::item:selected {{
    background-color: {c.surface1};
}}
QMenu::separator {{
    height: 1px;
    background-color: {c.overlay};
    margin: 4px 8px;
}}

/* ---- 状态栏 ---- */
QStatusBar {{
    background-color: {c.base};
    color: {c.subtext};
    font-size: 11px;
}}

/* ---- 按钮通用 ---- */
QPushButton {{
    background-color: {c.surface1};
    border: 1px solid {c.overlay};
    padding: 6px 14px;
    border-radius: 4px;
    color: {c.text};
    font-size: 13px;
}}
QPushButton:hover {{ background-color: {c.surface2}; }}
QPushButton:pressed {{ background-color: {c.accent}; color: #ffffff; }}
QPushButton:disabled {{ background-color: {c.mantle}; color: {c.overlay}; }}

/* ---- 复选框 ---- */
QCheckBox {{ background-color: transparent; color: {c.text}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {c.overlay};
    border-radius: 3px;
    background-color: {c.surface0};
}}
QCheckBox::indicator:checked {{
    background-color: {c.accent};
    border-color: {c.accent};
}}

/* ---- 进度条 ---- */
QProgressBar {{
    background-color: {c.mantle};
    border: 1px solid {c.overlay};
    border-radius: 4px;
    text-align: center;
    color: {c.text};
}}
QProgressBar::chunk {{ background-color: {c.accent}; border-radius: 3px; }}
"""


def generate_palette(skin_key: str = DEFAULT_SKIN) -> QPalette:
    """根据皮肤色板生成 QPalette。"""
    s = get_skin(skin_key)
    c = s.colors
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(c.base))
    p.setColor(QPalette.ColorRole.WindowText, QColor(c.text))
    p.setColor(QPalette.ColorRole.Base, QColor(c.base))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(c.surface0))
    p.setColor(QPalette.ColorRole.Text, QColor(c.text))
    p.setColor(QPalette.ColorRole.Button, QColor(c.surface0))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(c.text))
    p.setColor(QPalette.ColorRole.Highlight, QColor(c.surface1))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link, QColor(c.accent))
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(c.mauve))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(c.subtext))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(c.surface2))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c.surface2))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c.surface2))
    return p


def apply_skin(app, skin_key: str = DEFAULT_SKIN) -> None:
    """应用指定皮肤到 QApplication。"""
    app.setPalette(generate_palette(skin_key))
    app.setStyleSheet(generate_stylesheet(skin_key))


# ===== 兼容旧代码：保留 GLOBAL_STYLESHEET / set_dark_palette 作为默认皮肤别名 =====

GLOBAL_STYLESHEET = generate_stylesheet(DEFAULT_SKIN)


def set_dark_palette(app) -> None:
    """设置全局暗色调色板（兼容旧调用，使用默认皮肤）。"""
    app.setPalette(generate_palette(DEFAULT_SKIN))


# ===== 按钮样式（从默认皮肤色板生成，兼容旧导入）=====

_c = get_skin(DEFAULT_SKIN).colors

BTN_PRIMARY = (
    f"QPushButton {{ background-color: {_c.surface2}; color: {_c.text}; "
    f"border: none; padding: 6px 14px; border-radius: 4px; font-size: 13px; }}"
    f"QPushButton:hover {{ background-color: {_c.accent}; color: #ffffff; }}"
    f"QPushButton:pressed {{ background-color: {_c.accent_hover}; }}"
    f"QPushButton:disabled {{ background-color: {_c.surface0}; color: {_c.surface2}; }}"
)

BTN_SECONDARY = (
    f"QPushButton {{ background-color: {_c.surface0}; color: {_c.text}; "
    f"border: 1px solid {_c.overlay}; padding: 6px 14px; border-radius: 4px; font-size: 13px; }}"
    f"QPushButton:hover {{ background-color: {_c.surface1}; }}"
    f"QPushButton:pressed {{ background-color: {_c.surface2}; }}"
    f"QPushButton:disabled {{ background-color: {_c.base}; color: {_c.overlay}; border-color: {_c.surface0}; }}"
)

BTN_DANGER = (
    f"QPushButton {{ background-color: {_c.red}; color: #ffffff; "
    f"border: none; padding: 6px 14px; border-radius: 4px; font-size: 13px; font-weight: bold; }}"
    f"QPushButton:hover {{ background-color: #bc4f53; }}"
    f"QPushButton:pressed {{ background-color: #903236; }}"
    f"QPushButton:disabled {{ background-color: {_c.surface0}; color: {_c.surface2}; }}"
)

HINT_STYLE = f"color: {_c.subtext}; font-size: 13px; line-height: 1.4em; padding: 4px 0;"
LABEL_STYLE = f"color: {_c.text}; font-size: 13px;"
TITLE_STYLE = f"color: {_c.text}; font-size: 13px; font-weight: bold; padding: 2px 4px;"
SUCCESS_TEXT = f"color: {_c.green}; font-weight: bold;"
ERROR_TEXT = f"color: {_c.red}; font-weight: bold;"
CURRENT_LABEL = f"font-size: 11px; color: {_c.subtext}; padding: 2px 4px;"

TOOLBAR_BTN = (
    f"QPushButton {{ background-color: {_c.surface1}; color: {_c.text}; "
    f"border: none; border-radius: 3px; font-size: 11px; padding: 2px 10px; }}"
    f"QPushButton:hover {{ background-color: {_c.surface2}; }}"
    f"QPushButton:pressed {{ background-color: {_c.accent}; color: #ffffff; }}"
)

TOOLBAR_BTN_ACCENT = (
    f"QPushButton {{ background-color: {_c.surface2}; color: #ffffff; "
    f"border: none; border-radius: 3px; font-size: 11px; padding: 2px 10px; font-weight: bold; }}"
    f"QPushButton:hover {{ background-color: {_c.accent}; }}"
    f"QPushButton:pressed {{ background-color: {_c.accent_hover}; }}"
)

BTN_SM = (
    f"QPushButton {{ background-color: {_c.surface0}; color: {_c.text}; "
    f"border: 1px solid {_c.overlay}; border-radius: 3px; font-size: 11px; padding: 3px 10px; }}"
    f"QPushButton:hover {{ background-color: {_c.surface1}; }}"
    f"QPushButton:pressed {{ background-color: {_c.surface2}; }}"
    f"QPushButton:disabled {{ background-color: {_c.base}; color: {_c.surface2}; }}"
)
BTN_SM_ACCENT = (
    f"QPushButton {{ background-color: {_c.surface2}; color: #ffffff; "
    f"border: none; border-radius: 3px; font-size: 11px; padding: 3px 10px; font-weight: bold; }}"
    f"QPushButton:hover {{ background-color: {_c.accent}; }}"
    f"QPushButton:pressed {{ background-color: {_c.accent_hover}; }}"
    f"QPushButton:disabled {{ background-color: {_c.surface0}; color: {_c.surface2}; }}"
)
BTN_SM_DANGER = (
    f"QPushButton {{ background-color: {_c.red}; color: #ffffff; "
    f"border: none; border-radius: 3px; font-size: 11px; padding: 3px 10px; font-weight: bold; }}"
    f"QPushButton:hover {{ background-color: #bc4f53; }}"
    f"QPushButton:pressed {{ background-color: #903236; }}"
)

LIST_STYLE = f"""
QListWidget {{
    background-color: {_c.base};
    color: {_c.text};
    border: 1px solid {_c.overlay};
    border-radius: 4px;
    outline: none;
    padding: 2px;
}}
QListWidget::item {{
    color: {_c.text};
    padding: 6px 10px;
    border-radius: 3px;
}}
QListWidget::item:alternate {{ background-color: {_c.mantle}; }}
QListWidget::item:hover {{ background-color: {_c.surface0}; }}
QListWidget::item:selected {{
    background-color: {_c.surface1};
    color: #ffffff;
}}
"""


class TreeBranchStyle(QProxyStyle):
    """清晰的树折叠三角。

    macOS 原生折叠三角在暗色主题下几乎不可见，
    拦截 PE_IndicatorBranch 自绘一个明亮的实心三角。
    """

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_IndicatorBranch:
            state = option.state
            has_children = bool(state & QStyle.StateFlag.State_Children)
            if not has_children:
                return
            is_open = bool(state & QStyle.StateFlag.State_Open)
            rect = option.rect
            cx, cy = rect.center().x(), rect.center().y()
            s = 5.0
            color = QColor("#cdd6f4")
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(QBrush(color))
            if is_open:
                poly = QPolygonF([
                    QPointF(cx - s, cy - s * 0.6),
                    QPointF(cx + s, cy - s * 0.6),
                    QPointF(cx, cy + s * 0.9),
                ])
            else:
                poly = QPolygonF([
                    QPointF(cx - s * 0.6, cy - s),
                    QPointF(cx - s * 0.6, cy + s),
                    QPointF(cx + s * 0.9, cy),
                ])
            painter.drawPolygon(poly)
            painter.restore()
            return
        return super().drawPrimitive(element, option, painter, widget)


def apply_tree_branch_style(tree) -> None:
    """给 QTreeWidget 安装清晰折叠三角。"""
    base = tree.style()
    tree.setStyle(TreeBranchStyle(base))


def set_dark_palette(app) -> None:
    """设置全局暗色调色板（治白底根因之二）。

    macOS 原生风格下，未显式设定样式的控件（默认按钮、滚动条、菜单、
    面板背景等）会按系统浅色渲染。设置调色板后，所有原生控件默认即暗色，
    与 GLOBAL_STYLESHEET 的 QWidget 基础背景互为双保险。
    """
    p = QPalette()
    crate = QColor  # 简写
    p.setColor(QPalette.ColorRole.Window, crate("#1e1e2e"))
    p.setColor(QPalette.ColorRole.WindowText, crate("#cdd6f4"))
    p.setColor(QPalette.ColorRole.Base, crate("#1e1e2e"))
    p.setColor(QPalette.ColorRole.AlternateBase, crate("#222232"))
    p.setColor(QPalette.ColorRole.Text, crate("#cdd6f4"))
    p.setColor(QPalette.ColorRole.Button, crate("#313244"))
    p.setColor(QPalette.ColorRole.ButtonText, crate("#cdd6f4"))
    p.setColor(QPalette.ColorRole.Highlight, crate("#45475a"))
    p.setColor(QPalette.ColorRole.HighlightedText, crate("#ffffff"))
    p.setColor(QPalette.ColorRole.Link, crate("#89b4fa"))
    p.setColor(QPalette.ColorRole.LinkVisited, crate("#cba6f7"))
    p.setColor(QPalette.ColorRole.PlaceholderText, crate("#6c7086"))
    # 禁用态
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, crate("#585b70"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, crate("#585b70"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, crate("#585b70"))
    app.setPalette(p)
