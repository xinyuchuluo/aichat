"""AIChat 全局样式 — 多皮肤主题（与 NovelForge 共享皮肤体系）。"""
from __future__ import annotations
from dataclasses import dataclass
from PyQt6.QtGui import QPalette, QColor


@dataclass
class SkinColors:
    base: str; mantle: str; crust: str
    surface0: str; surface1: str; surface2: str
    overlay: str; text: str; subtext: str
    accent: str; accent_hover: str
    green: str; yellow: str; red: str; mauve: str


@dataclass
class Skin:
    key: str; name: str; desc: str; colors: SkinColors


SKINS: dict[str, Skin] = {
    "mocha": Skin("mocha", "摩卡夜", "Catppuccin Mocha · 经典暗紫",
        SkinColors("#1e1e2e","#181825","#11111b","#313244","#45475a","#585b70",
                    "#45475a","#cdd6f4","#a6adc8","#89b4fa","#b4befe",
                    "#a6e3a1","#f9e2af","#f38ba8","#cba6f7")),
    "nord": Skin("nord", "北境蓝", "Nord · 冷冽冰川蓝",
        SkinColors("#2e3440","#272c36","#21262e","#3b4252","#434c5e","#4c566a",
                    "#4c566a","#d8dee9","#9aa6bd","#81a1c1","#8fbcbb",
                    "#a3be8c","#ebcb8b","#bf616a","#b48ead")),
    "tokyonight": Skin("tokyonight", "东京夜", "Tokyo Night · 霓虹紫蓝",
        SkinColors("#1a1b26","#16161e","#13131a","#24253a","#2a2b3d","#363649",
                    "#3b3f54","#c0caf5","#9aa5d1","#7aa2f7","#89b4fa",
                    "#9ece6a","#e0af68","#f7768e","#bb9af7")),
    "dracula": Skin("dracula", "德古拉", "Dracula · 经典吸血鬼紫",
        SkinColors("#282a36","#21222c","#1a1b22","#383a4a","#44475a","#50545d",
                    "#44475a","#f8f8f2","#b9bac4","#bd93f9","#caa6ff",
                    "#50fa7b","#f1fa8c","#ff5555","#ff79c6")),
    "gruvbox": Skin("gruvbox", "熔岩暖", "Gruvbox · 温暖大地色",
        SkinColors("#282828","#1d2021","#141518","#3c3836","#504945","#665c54",
                    "#504945","#ebdbb2","#a89984","#83a598","#a9a587",
                    "#b8bb26","#fabd2f","#fb4934","#d3869b")),
    "rosepine": Skin("rosepine", "玫瑰松", "Rosé Pine · 优雅玫瑰金",
        SkinColors("#1f1d2e","#191726","#16131f","#2a283a","#34304a","#403954",
                    "#403954","#e0def4","#908caa","#c4a7e7","#d4b8ff",
                    "#9ccfd8","#f6c177","#eb6f92","#ebbcba")),
    "onedark": Skin("onedark", "暗夜青", "Atom One Dark · 简洁深邃",
        SkinColors("#282c34","#21252c","#1b1d22","#323842","#3e4451","#4b5263",
                    "#3e4451","#abb2bf","#848a9a","#61afef","#7ec0ff",
                    "#98c379","#e5c07b","#e06c75","#c678dd")),
    "github_dark": Skin("github_dark", "暗夜码", "GitHub Dark · 清爽开发风",
        SkinColors("#0d1117","#010409","#000000","#161b22","#21262d","#30363d",
                    "#30363d","#e6edf3","#8b949e","#58a6ff","#79c0ff",
                    "#3fb950","#d29922","#f85149","#bc8cff")),
"cyberpunk": Skin("cyberpunk", "赛博霓虹", "Cyberpunk · 青霓虹+电光紫 · 未来科技风",
        SkinColors("#05070d","#0b1020","#050812","#131a2e","#1a2545","#23325e",
                    "#2d4580","#d7f9ff","#8fb4c7","#00e5ff","#7afcff",
                    "#24ffa8","#ffd166","#ff3d71","#b56bff")),
    "breeze": Skin("breeze", "清风白", "Breeze · 清新浅绿 · 清爽护眼浅色",
        SkinColors("#fbfdff","#f0f5fa","#e4ecf4","#f3f6fa","#e3eaf2","#cbd5e1",
                    "#cbd5e1","#0f172a","#64748b","#10b981","#34d399",
                    "#22c55e","#f59e0b","#ef4444","#8b5cf6")),
}

DEFAULT_SKIN = "cyberpunk"
_CURRENT: str = DEFAULT_SKIN

# ===== 运行时皮肤跟踪 + 派生对比色（确保文字/背景对比度） =====
def set_current(key: str) -> None:
    """设置当前运行时皮肤（切皮肤时调用，UI 组件可以 current_colors() 动态拿色）。"""
    global _CURRENT
    if key in SKINS:
        _CURRENT = key

def current_key() -> str:
    return _CURRENT

def current_colors() -> SkinColors:
    return get_skin(_CURRENT).colors

def is_light_theme() -> bool:
    """判断当前皮肤是否浅色（用 base 亮度计算），用于自动选深色字/浅色字对比。"""
    b = current_colors().base.lstrip("#")
    r, g, b_ = int(b[0:2],16), int(b[2:4],16), int(b[4:6],16)
    # 感知亮度（YIQ）
    yiq = (r*299 + g*587 + b_*114) / 1000
    return yiq >= 160   # ≥160 认为浅色，文字用深色

# === 派生色：确保 "上面的标题" 等关键元素文字/背景永远够色差 ===
def titlebar_colors() -> tuple[str, str, str, str]:
    """返回：(背景左色, 背景右色, 标题文字色, 副标题字色)。"""
    c = current_colors()
    if is_light_theme():
        # 浅底：柔和渐变 + 深色字（对比度强）
        bg_l, bg_r = c.surface0, c.mantle
        text = "#0b1220"      # 深墨蓝（极深，放浅底上很清晰，= 标题级对比）
        sub  = "#475569"      # slate-600 副文字
    else:
        # 深底：青紫霓虹渐变 + 亮青霓虹字
        bg_l = c.crust
        bg_r = "#0d1634"      # 略深青紫
        text = c.accent_hover # 亮青（#7afcff / 对应 breeze -> accent_hover）
        sub  = c.subtext
    return bg_l, bg_r, text, sub

def chip_colors() -> tuple[str, str, str]:
    """会话列表选中/会话芯片：(背景色, 边框色, 文字色)"""
    c = current_colors()
    if is_light_theme():
        return "#ffffff", c.overlay, c.text
    return c.surface0, c.surface1, c.text

def bubble_colors(role: str) -> tuple[str, str, str, str]:
    """气泡颜色：(背景, 边框, 正文文字色, 标签文字色)。role='user' | 'ai' | 'system'"""
    c = current_colors()
    if role == "user":
        if is_light_theme():
            # 浅底上的用户气泡：青绿渐变 + 白色文字（对比度强）
            return c.accent, c.accent, "#ffffff", "#ffffff"
        else:
            # 深底：青霓虹渐变 + 深色字（渐变很亮，文字必须深）
            return c.accent, c.accent_hover, "#041520", "#05202e"
    if role == "ai":
        if is_light_theme():
            return "#ffffff", c.overlay, "#0f172a", c.accent
        else:
            return c.surface0, c.surface1, c.text, c.mauve
    # system
    if is_light_theme():
        return "#fffbeb", "#fde68a", "#78350f", "#b45309"
    return "#1a1626", "#5b4422", c.yellow, c.yellow

def input_colors() -> tuple[str, str, str, str, str]:
    """输入卡片/输入框颜色：
    (卡片背景, 输入框背景, 输入框边框, 输入框聚焦边框, 文字色)"""
    c = current_colors()
    if is_light_theme():
        return ("#ffffff", "#f8fafc", c.overlay, c.accent, c.text)
    return ("#0d1426", "#060913", "#1e3055", c.accent, c.text)



def get_skin(key: str) -> Skin:
    return SKINS.get(key, SKINS[DEFAULT_SKIN])


def skin_keys() -> list[str]:
    return list(SKINS.keys())


def generate_stylesheet(skin_key: str = DEFAULT_SKIN) -> str:
    s = get_skin(skin_key)
    c = s.colors
    return f"""
QWidget {{ background-color: {c.base}; color: {c.text}; }}
QLabel {{ background: transparent; }} /* 避免标签继承全局背景导致文字出现丑陋底色 */
QFrame {{ background: transparent; }} /* Frame 默认透明，气泡/卡片自行设背景 */
QMainWindow {{ background-color: {c.base}; }}
QToolBar {{ background-color: {c.mantle}; border: none; spacing: 4px; padding: 2px; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background-color: {c.surface0}; color: {c.text};
    border: 1px solid {c.overlay}; border-radius: 4px; padding: 5px 8px;
    selection-background-color: {c.surface2}; selection-color: #fff;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {c.accent_hover}; }}
QListWidget, QTreeWidget, QTableWidget {{
    background-color: {c.base}; color: {c.text};
    border: 1px solid {c.overlay}; border-radius: 4px; outline: none;
}}
QListWidget::item:hover {{ background-color: {c.surface0}; }}
QListWidget::item:selected {{ background-color: {c.surface1}; color: #fff; }}
QTabWidget::pane {{ background-color: {c.base}; border: 1px solid {c.overlay}; }}
QTabBar::tab {{ background-color: {c.surface0}; color: {c.subtext}; padding: 6px 14px; border: 1px solid {c.overlay}; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
QTabBar::tab:selected {{ background-color: {c.surface1}; color: {c.text}; font-weight: bold; }}
QScrollBar:vertical {{ background-color: {c.base}; width: 8px; border-radius: 4px; }}
QScrollBar::handle:vertical {{ background-color: {c.overlay}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background-color: {c.surface2}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background-color: {c.base}; height: 8px; border-radius: 4px; }}
QScrollBar::handle:horizontal {{ background-color: {c.overlay}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{ background-color: {c.overlay}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QToolTip {{ background-color: {c.surface0}; color: {c.text}; border: 1px solid {c.overlay}; padding: 4px 6px; border-radius: 3px; }}
QMenu {{ background-color: {c.base}; color: {c.text}; border: 1px solid {c.overlay}; }}
QMenu::item:selected {{ background-color: {c.surface1}; }}
QStatusBar {{ background-color: {c.base}; color: {c.subtext}; font-size: 11px; }}
QPushButton {{ background-color: {c.surface1}; border: 1px solid {c.overlay}; padding: 5px 12px; border-radius: 6px; color: {c.text}; font-size: 13px; }}
QPushButton:hover {{ background-color: {c.surface2}; }}
QPushButton:pressed {{ background-color: {c.accent}; color: #fff; }}
QPushButton:disabled {{ background-color: {c.mantle}; color: {c.overlay}; }}
QCheckBox {{ background-color: transparent; color: {c.text}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c.overlay}; border-radius: 3px; background-color: {c.surface0}; }}
QCheckBox::indicator:checked {{ background-color: {c.accent}; border-color: {c.accent}; }}
QProgressBar {{ background-color: {c.mantle}; border: 1px solid {c.overlay}; border-radius: 4px; text-align: center; color: {c.text}; }}
QProgressBar::chunk {{ background-color: {c.accent}; border-radius: 3px; }}
QGroupBox {{ color: {c.text}; border: 1px solid {c.overlay}; border-radius: 4px; margin-top: 8px; padding-top: 16px; font-weight: bold; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
QHeaderView::section {{ background-color: {c.surface0}; color: {c.subtext}; border: 1px solid {c.overlay}; padding: 4px 8px; font-size: 12px; }}
"""


def generate_palette(skin_key: str = DEFAULT_SKIN) -> QPalette:
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
    return p


def apply_skin(app, skin_key: str = DEFAULT_SKIN) -> None:
    set_current(skin_key)
    app.setPalette(generate_palette(skin_key))
    app.setStyleSheet(generate_stylesheet(skin_key))

def apply_skin_widget(widget, skin_key: str | None = None) -> None:
    """仅给某个 QWidget 重新应用样式（切皮肤时各组件 rebuild 内部硬编码样式后调用，触发 repolish）。"""
    if skin_key:
        set_current(skin_key)
    from PyQt6.QtWidgets import QStyle
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
