#!/usr/bin/env python3
"""AIChatStudio — 入口。

当前阶段：先让原 AIChat（domains/chat/）跑起来，验证迁移路径正确。
后续接入 shell/main_window 一体化壳 + 其他 4 个域。
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print("错误：未安装 PyQt6，请运行 pip install -r requirements.txt")
        return 1

    try:
        from domains.chat.config import Config
        from domains.chat.memory.engine import MemoryEngine
        from domains.chat.session.session import SessionManager
        from domains.chat.widgets.main_window import MainWindow
        from domains.chat.widgets.styles import apply_skin
    except ImportError as e:
        print(f"导入错误：{e}")
        traceback.print_exc()
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("AIChatStudio")

    # 加载配置（沿用原 AIChat 的 Config，不动）
    config = Config.load()
    config.save()

    # 初始化核心组件
    memory = MemoryEngine(config.data_dir)
    memory.set_summarize_threshold(config.summarize_threshold)
    sessions = SessionManager(config.data_dir)

    # 应用皮肤（暂用原 AIChat 皮肤，后续切到 shell/styles.py 浅色主题）
    apply_skin(app, config.skin)

    # 主窗口
    window = MainWindow(config, memory, sessions)
    window.show()

    # 注册适配器
    from domains.chat.adapters import register_adapters
    register_adapters(window, config)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
