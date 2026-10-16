"""主窗口：左侧侧边栏列启用域，右侧 QTabWidget 挂各域主面板。

- 工作区切换器在顶部
- 任务卡中心在底部 dock
- 各域通过 DomainManifest 注册，启动时自动挂载
"""

from __future__ import annotations

import importlib
import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import workspace as ws_mod
from shell import styles
from shell.session_hub import SessionHub
from shell.window_manager import get_manager
from shell.workspace_switcher import WorkspaceSwitcher

logger = logging.getLogger(__name__)


class DomainManifest:
    """域清单（域在 domains/<name>/__init__.py 里声明）"""

    def __init__(
        self,
        key: str,
        display_name: str,
        widget_factory=None,
        supports_standalone_window: bool = False,
    ) -> None:
        self.key = key
        self.display_name = display_name
        self.widget_factory = widget_factory
        self.supports_standalone_window = supports_standalone_window


# 已注册的域清单（域启动时通过 register_domain 注册）
_registry: dict[str, DomainManifest] = {}


def register_domain(manifest: DomainManifest) -> None:
    _registry[manifest.key] = manifest


def list_registered() -> list[DomainManifest]:
    return list(_registry.values())


def find_domain(key: str) -> Optional[DomainManifest]:
    return _registry.get(key)


class MainWindow(QMainWindow):
    """一体化壳主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AIChatStudio")
        self.resize(1440, 900)

        # 中央：左 sidebar + 右 tabs
        central = QSplitter(Qt.Orientation.Horizontal)

        # 左：域侧边栏
        self._sidebar = QTreeWidget()
        self._sidebar.setObjectName("Sidebar")
        self._sidebar.setHeaderHidden(True)
        self._sidebar.setMinimumWidth(180)
        self._sidebar.setMaximumWidth(280)
        self._sidebar.itemClicked.connect(self._on_domain_click)
        central.addWidget(self._sidebar)

        # 右：域 Tab
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._on_tab_close)
        central.addWidget(self._tabs)

        central.setStretchFactor(0, 0)
        central.setStretchFactor(1, 1)
        self.setCentralWidget(central)

        # 顶部：工作区切换器
        self._switcher = WorkspaceSwitcher()
        self._switcher.workspace_changed.connect(self._on_workspace_changed)
        self.addToolBar(self._build_toolbar())

        # 底部 dock：任务卡中心
        self._hub = SessionHub()
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._hub)

        # 加载初始工作区
        ws_mod.ensure_default()
        self._reload_sidebar()
        self._hub.refresh()

    def _build_toolbar(self):
        from PyQt6.QtWidgets import QToolBar

        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.addWidget(self._switcher)
        return tb

    def _reload_sidebar(self) -> None:
        """根据工作区的 enabled_domains 重画侧边栏"""
        self._sidebar.clear()
        ws = ws_mod.current()
        if ws is None:
            return
        for domain_key in ws.enabled_domains():
            manifest = find_domain(domain_key)
            display = manifest.display_name if manifest else domain_key
            item = QTreeWidgetItem([display])
            item.setData(0, Qt.ItemDataRole.UserRole, domain_key)
            self._sidebar.addTopLevelItem(item)

    def _on_domain_click(self, item: QTreeWidgetItem, column: int) -> None:
        domain_key = item.data(0, Qt.ItemDataRole.UserRole)
        if not domain_key:
            return
        self.open_domain_tab(domain_key)

    def open_domain_tab(self, domain_key: str) -> None:
        # 已开 → 切到
        for i in range(self._tabs.count()):
            if self._tabs.tabData(i) == domain_key:
                self._tabs.setCurrentIndex(i)
                return
        manifest = find_domain(domain_key)
        if manifest is None or manifest.widget_factory is None:
            logger.warning("domain %s not registered or no widget_factory", domain_key)
            return
        try:
            widget = manifest.widget_factory()
        except Exception as e:  # noqa: BLE001
            logger.error("build widget for %s failed: %s", domain_key, e)
            return
        idx = self._tabs.addTab(widget, manifest.display_name)
        self._tabs.setTabData(idx, domain_key)
        self._tabs.setCurrentIndex(idx)

    def _on_tab_close(self, idx: int) -> None:
        self._tabs.removeTab(idx)

    def _on_workspace_changed(self, wid: str) -> None:
        # 关闭所有独立窗口 + 关闭所有 Tab + 重画侧边栏 + 刷新任务卡
        get_manager().close_all()
        while self._tabs.count():
            self._tabs.removeTab(0)
        self._reload_sidebar()
        self._hub.refresh()


def load_domains() -> None:
    """启动时自动 import 五个域的 __init__.py，触发 register_domain"""
    for name in ("chat", "novel", "studio", "warehouse", "analysis"):
        try:
            importlib.import_module(f"domains.{name}")
        except ImportError as e:
            # 域尚未实现，静默跳过（chat-warehouse 分支下其他域确实不存在）
            logger.info("domain %s not loaded: %s", name, e)


def main() -> int:
    import sys

    app = QApplication(sys.argv)
    app.setApplicationName("AIChatStudio")
    styles.apply_theme(app, "light")
    load_domains()
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
