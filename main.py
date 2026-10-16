#!/usr/bin/env python3
"""AIChatStudio — 一体化 App 入口。

启动流程：
1. 加载工作区（首次启动自动创建 ~/AichatStudio/default）
2. 初始化内核单例（config_vault / llm_client / prompt_repo）
3. 加载域清单（domains/<name>/__init__.py 里 register_domain）
4. 显示 shell.main_window.MainWindow（侧边栏列启用域，浅色主题）

当前阶段：chat 域已接入新壳；novel/studio/warehouse/analysis 待迁
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
        from core import workspace as ws_mod
        from core import config_vault
        from core import llm_client
        from core import prompt_repo
        from shell import styles
        from shell.main_window import MainWindow, load_domains
    except ImportError as e:
        print(f"内核加载错误：{e}")
        traceback.print_exc()
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("AIChatStudio")
    app.setOrganizationName("AichatStudio")

    # 浅色主题（默认）
    styles.apply_theme(app, "light")

    # 工作区：首次启动创建 default
    try:
        ws = ws_mod.ensure_default()
    except Exception as e:  # noqa: BLE001
        print(f"工作区初始化失败：{e}")
        traceback.print_exc()
        return 1

    # 内核单例初始化
    try:
        vault = config_vault.for_workspace(ws.root)
        profiles_data = vault.get("api_profiles", [])
        routing_data = vault.get("step_routing", {})
        from core.llm_client import LLMProfile
        profiles = [
            LLMProfile(
                name=p["name"],
                base_url=p["base_url"],
                model=p["model"],
                api_key=p.get("api_key", ""),
                extra=p.get("extra", {}),
            )
            for p in profiles_data
        ]
        llm_client.init(profiles, routing_data)
    except Exception:  # noqa: BLE001  -- 首次启动无配置文件，跳过；用户进 UI 后再配
        pass

    # 提示词仓库
    prompts_root = os.path.join(os.path.dirname(__file__), "prompts")
    prompt_repo.init(prompts_root)

    # 加载域清单（chat/novel/studio/warehouse/analysis，按 workspace.enabled_domains 显示）
    load_domains()

    # 主窗口（一体化壳）
    win = MainWindow()
    win.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
