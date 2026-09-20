#!/usr/bin/env python3
"""AIChat — 通用 AI 聊天软件入口。"""
import sys
import os
import traceback

# 确保 aichat 包可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt
    except ImportError:
        print("错误：未安装 PyQt6，请运行 pip install PyQt6")
        sys.exit(1)

    try:
        from aichat.config import Config
        from aichat.memory.engine import MemoryEngine
        from aichat.chat.session import SessionManager
        from aichat.ui.main_window import MainWindow
        from aichat.ui.styles import apply_skin
        # 适配器按需通过 register_adapters 注册，无需单独导入
    except ImportError as e:
        print(f"导入错误：{e}")
        traceback.print_exc()
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("AIChat")

    # 加载配置
    config = Config.load()
    config.save()

    # 初始化核心组件
    memory = MemoryEngine(config.data_dir)
    memory.set_summarize_threshold(config.summarize_threshold)
    sessions = SessionManager(config.data_dir)

    # 应用皮肤
    apply_skin(app, config.skin)

    # 主窗口
    window = MainWindow(config, memory, sessions)
    window.show()

    # 注册适配器
    from aichat.adapters import register_adapters
    register_adapters(window, config)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
