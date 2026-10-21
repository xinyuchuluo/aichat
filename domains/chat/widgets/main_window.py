"""AIChat 主窗口 — 三栏布局：会话侧栏 | 聊天视图 | 记忆面板。"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QPushButton, QLabel, QStatusBar, QMenuBar, QMenu, QMessageBox,
    QDockWidget, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

# ============================================================
# LLM 服务商官方预设 — 一键选，不用手动查链接和模型名
# ============================================================
LLM_PROVIDERS: list[dict] = [
    {
        "key": "deepseek",
        "name": "🟢 DeepSeek (深度求索)",
        "base_url": "https://api.deepseek.com/v1",
        "capabilities": {"web_search": True, "vision": False, "file_upload": True, "tool_use": True, "code_exec": True},
        "models": [
            ("deepseek-chat",         "V3 通用对话（主力，性价比高）", None),
            ("deepseek-reasoner",     "R1 推理模型（数学/代码深度思考）", None),
        ],
        "homepage": "https://platform.deepseek.com/",
        "tip": "控制台：platform.deepseek.com → API Keys 里申请",
    },
    {
        "key": "openai",
        "name": "🔵 OpenAI (GPT 官方)",
        "base_url": "",   # 留空 = 官方
        "capabilities": {"web_search": True, "vision": False, "file_upload": True, "tool_use": True, "code_exec": True},
        "models": [
            ("gpt-4o-mini",           "GPT-4o mini（便宜快，日常首选）", None),
            ("gpt-4o",                "GPT-4o（旗舰多模态）", {"vision": True}),
            ("gpt-4.1-mini",          "GPT-4.1 mini（新一代，更长上下文）", {"tool_use": True}),
            ("gpt-4.1",               "GPT-4.1（旗舰新一代）", {"vision": True, "tool_use": True}),
        ],
        "homepage": "https://platform.openai.com/api-keys",
        "tip": "platform.openai.com → API Keys 创建",
    },
    {
        "key": "zhipu",
        "name": "🔷 智谱 AI (GLM)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "capabilities": {"web_search": True, "vision": False, "file_upload": True, "tool_use": True, "code_exec": True},
        "models": [
            ("glm-4-flash",           "GLM-4 Flash（免费/超便宜，快速）", None),
            ("glm-4-plus",            "GLM-4 Plus（旗舰能力）", None),
            ("glm-4-long",            "GLM-4 Long（超长上下文 1M）", None),
            ("glm-4-0520",            "GLM-4 最新稳定版", None),
            ("glm-4.1",               "GLM-4.1（新一代通用）", None),
            ("glm-4.1-plus",          "GLM-4.1 Plus（旗舰）", {"vision": True}),
            ("glm-4v-plus",           "GLM-4V Plus（多模态看图）", {"vision": True}),
        ],
        "homepage": "https://open.bigmodel.cn/",
        "tip": "open.bigmodel.cn → API 密钥管理",
    },
    {
        "key": "moonshot",
        "name": "🌙 月之暗面 (Moonshot Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "capabilities": {"web_search": True, "vision": True, "file_upload": True, "tool_use": True, "code_exec": True},
        "models": [
            ("moonshot-v1-8k",        "Kimi V1 8K（短文本首选）", None),
            ("moonshot-v1-32k",       "Kimi V1 32K", None),
            ("moonshot-v1-128k",      "Kimi V1 128K（长文档）", None),
        ],
        "homepage": "https://platform.moonshot.cn/",
        "tip": "platform.moonshot.cn → API Key 创建",
    },
    {
        "key": "doubao",
        "name": "🟡 字节豆包 (火山引擎)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "capabilities": {"web_search": True, "vision": False, "file_upload": True, "tool_use": True, "code_exec": True},
        "models": [
            ("doubao-lite-4k",        "豆包 Lite 4K（轻量）", None),
            ("doubao-lite-32k",       "豆包 Lite 32K", None),
            ("doubao-pro-4k",         "豆包 Pro 4K（标准能力）", None),
            ("doubao-pro-32k",        "豆包 Pro 32K", None),
            ("doubao-seed-1-6-latest","Seed 1.6（新一代推理）", None),
            ("doubao-1-5-vision-pro", "豆包 1.5 Vision（多模态看图）", {"vision": True}),
        ],
        "homepage": "https://console.volcengine.com/ark/",
        "tip": "火山引擎控制台 → 方舟 Ark → 创建推理接入点(Endpoint ID)可填入模型名",
    },
    {
        "key": "qwen",
        "name": "🟠 阿里通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "capabilities": {"web_search": True, "vision": False, "file_upload": True, "tool_use": True, "code_exec": True},
        "models": [
            ("qwen-turbo",            "Qwen Turbo（快速便宜）", None),
            ("qwen-plus",             "Qwen Plus（标准能力）", None),
            ("qwen-max",              "Qwen Max（旗舰）", None),
            ("qwen-long",             "Qwen Long（超长上下文）", None),
            ("qwen3-coder-plus",      "Qwen3 Coder（代码模型）", None),
            ("qwen-vl-max",           "Qwen VL Max（多模态看图）", {"vision": True}),
            ("qwen-vl-plus",          "Qwen VL Plus（多模态看图）", {"vision": True}),
        ],
        "homepage": "https://dashscope.console.aliyun.com/",
        "tip": "阿里云百炼/DashScope 控制台 → API-KEY 管理",
    },
    {
        "key": "siliconflow",
        "name": "🟣 硅基流动 (SiliconFlow)",
        "base_url": "https://api.siliconflow.cn/v1",
        "capabilities": {"web_search": False, "vision": False, "file_upload": False, "tool_use": False, "code_exec": False},
        "models": [
            ("deepseek-ai/DeepSeek-V3", "DeepSeek V3（硅基代理）", None),
            ("Qwen/Qwen2.5-72B-Instruct","Qwen 2.5 72B", None),
            ("meta-llama/Llama-3.1-405B-Instruct", "Llama 3.1 405B", None),
            ("THUDM/glm-4-9b-chat",    "GLM-4 9B", None),
            ("01-ai/Yi-1.5-34B-Chat-16K", "Yi 34B", None),
            ("Qwen/Qwen2.5-VL-72B-Instruct","Qwen 2.5 VL（多模态看图）", {"vision": True}),
        ],
        "homepage": "https://cloud.siliconflow.cn/",
        "tip": "硅基流动国内代理多种开源模型，价格极低",
    },
    {
        "key": "ollama",
        "name": "⚪ 本地 Ollama（完全免费）",
        "base_url": "http://localhost:11434/v1",
        "capabilities": {"web_search": False, "vision": False, "file_upload": True, "tool_use": False, "code_exec": False},
        "models": [
            ("qwen2.5:7b",            "Qwen 2.5 7B（推荐中文，8G 显存）", None),
            ("qwen2.5:14b",           "Qwen 2.5 14B（中文更强）", None),
            ("deepseek-r1:7b",        "DeepSeek R1 7B（推理模型）", None),
            ("llama3.1:8b",           "Llama 3.1 8B（通用英文）", None),
            ("gemma2:9b",             "Gemma 2 9B（Google 轻量）", None),
            ("llava:7b",              "LLaVA 7B（多模态看图）", {"vision": True}),
        ],
        "homepage": "https://ollama.com/",
        "tip": "需先安装 Ollama 并运行 ollama pull <模型名>，API Key 随便填即可",
    },
]

from ..protocol.types import Message, ChatRole, Session
from ..session.agent import ChatAgent
from ..session.worker import ChatWorker
from ..session.session import SessionManager
from core.memory import MemoryEngine
from ..adapters import get_adapter, list_adapters
from .chat_view import ChatView
from .session_sidebar import SessionSidebar
from .memory_panel import MemoryPanel


class MainWindow(QMainWindow):
    """AIChat 主窗口。"""

    def __init__(self, config, memory: MemoryEngine, sessions: SessionManager):
        super().__init__()
        self._config = config
        self._memory = memory
        self._sessions = sessions
        self._agent: ChatAgent | None = None
        self._worker: ChatWorker | None = None
        self._adapters: list[dict] = []

        self.setWindowTitle("AIChat - 通用 AI 聊天软件")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)

        self._build_ui()
        self._connect_signals()

        # 自动选中或创建会话
        all_sessions = self._sessions.all_sessions()
        if all_sessions:
            self._on_session_switched(all_sessions[0].id)
        else:
            self._on_session_created("generic")

    def _build_ui(self):
        # 中央三栏 Splitter
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setFrameShape(QFrame.Shape.NoFrame)

        # 左栏：会话侧栏
        self._sidebar = SessionSidebar()
        splitter.addWidget(self._sidebar)

        # 中栏：聊天视图
        self._chat_view = ChatView()
        chat_frame = QFrame()
        chat_frame.setFrameShape(QFrame.Shape.NoFrame)
        chat_lay = QVBoxLayout(chat_frame)
        chat_lay.setContentsMargins(0, 0, 0, 0)
        # 顶部信息条（最关键的"上面的标题"，用派生色保证文字-背景对比）
        self._session_title = QLabel("未选择会话")
        self._apply_titlebar_style()
        chat_lay.addWidget(self._session_title)
        chat_lay.addWidget(self._chat_view, 1)
        splitter.addWidget(chat_frame)

        # 右栏：记忆面板
        self._memory_panel = MemoryPanel(self._memory)
        self._memory_panel.setFixedWidth(300)
        splitter.addWidget(self._memory_panel)

        splitter.setSizes([240, 660, 300])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(2, True)
        lay.addWidget(splitter)

        self.setCentralWidget(central)

        # 状态栏
        self._status = QStatusBar()
        self._status.showMessage("就绪")
        self.setStatusBar(self._status)

        # 菜单栏
        self._build_menu()

    def _build_menu(self):
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件")
        new_action = file_menu.addAction("新建会话")
        new_action.triggered.connect(lambda: self._on_session_created(self._current_adapter_key()))
        file_menu.addSeparator()
        quit_action = file_menu.addAction("退出")
        quit_action.triggered.connect(self.close)

        # 设置菜单
        settings_menu = menubar.addMenu("设置")
        llm_action = settings_menu.addAction("LLM 模型设置 (API Key / 模型 / 地址)")
        llm_action.triggered.connect(self._on_llm_settings)
        skin_action = settings_menu.addAction("皮肤设置")
        skin_action.triggered.connect(self._on_skin_settings)
        settings_menu.addSeparator()
        nf_action = settings_menu.addAction("📖 打开 NovelForge 小说项目…")
        nf_action.triggered.connect(self._on_open_novelforge)
        da_action = settings_menu.addAction("📊 打开数据分析文件…")
        da_action.triggered.connect(self._on_open_data_analysis)
        mode_action = settings_menu.addAction("🔄 切换模式 (通用/小说分析/数据分析)")
        mode_action.triggered.connect(self._on_switch_mode)

    def _on_open_novelforge(self):
        """打开 NovelForge 项目目录，切换到小说分析模式。"""
        from PyQt6.QtWidgets import QFileDialog
        last_dir = self._config.get("last_nf_project", str(Path.home()))
        project_root = QFileDialog.getExistingDirectory(
            self, "选择 NovelForge 项目目录", last_dir)
        if not project_root:
            return
        # 保存路径
        self._config["last_nf_project"] = project_root
        self._config.save()
        # 创建 NovelForge 适配器并注入
        from ..adapters.novelforge import NovelForgeAdapter
        from ..tools import set_novelforge_adapter
        adapter = NovelForgeAdapter(project_root=project_root)
        set_novelforge_adapter(adapter)
        # 创建新会话
        session = self._sessions.create(
            title=f"小说分析 - {adapter._project_name}",
            adapter_key="novelforge",
            adapter_config={"project_root": project_root},
        )
        self._setup_agent(session, adapter)
        try:
            self._memory.get_working_memory(session.id).clear()
        except Exception:
            pass
        self._sidebar.refresh_sessions(self._sessions.all_sessions(), session.id)
        self._chat_view.clear_messages()
        self._session_title.setText(session.title)
        # 显示项目摘要
        char_count = adapter._project_data.get("chapter_count", 0)
        char_n = len(adapter.get_all_characters())
        fs_n = len(adapter.get_all_foreshadows())
        self._status.showMessage(
            f"📖 已加载「{adapter._project_name}」: {char_count}章 / {char_n}角色 / {fs_n}伏笔")
        self._status_model_badge.setText(
            f"📖 {adapter._project_name} | 🧠 {self._config.get('llm_model', '?')}")

    def _on_open_data_analysis(self):
        """打开数据文件（Excel/CSV/Word），切换到数据分析模式。"""
        from PyQt6.QtWidgets import QFileDialog, QInputDialog
        last_dir = self._config.get("last_da_dir", str(Path.home()))
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择数据文件", last_dir,
            "数据文件 (*.xlsx *.xls *.csv *.docx);;Excel (*.xlsx *.xls);;CSV (*.csv);;Word (*.docx);;所有文件 (*)",
        )
        if not file_path:
            return
        # 保存路径
        self._config["last_da_dir"] = str(Path(file_path).parent)
        self._config.save()
        # 询问数据说明（可选）
        data_desc, ok = QInputDialog.getText(
            self, "数据说明（可选）",
            "填写数据时间范围/来源/单位等（可留空）：",
        )
        data_description = data_desc if ok and data_desc.strip() else ""
        # 询问分析重点（可选）
        focus, ok2 = QInputDialog.getText(
            self, "分析重点（可选）",
            "希望重点分析什么？（可留空，AI 自动规划）：",
        )
        analysis_focus = focus if ok2 and focus.strip() else ""
        # 创建数据分析适配器并注入
        from ..adapters.data_analysis import DataAnalysisAdapter
        from ..tools import set_data_analysis_adapter
        adapter = DataAnalysisAdapter(
            data_file=file_path,
            data_description=data_description,
            analysis_focus=analysis_focus,
        )
        set_data_analysis_adapter(adapter)
        # 创建新会话
        session = self._sessions.create(
            title=adapter.display_name,
            adapter_key="data_analysis",
            adapter_config={
                "data_file": file_path,
                "data_description": data_description,
                "analysis_focus": analysis_focus,
            },
        )
        self._setup_agent(session, adapter)
        try:
            self._memory.get_working_memory(session.id).clear()
        except Exception:
            pass
        self._sidebar.refresh_sessions(self._sessions.all_sessions(), session.id)
        self._chat_view.clear_messages()
        self._session_title.setText(session.title)
        # 显示数据文件摘要
        info = adapter.get_file_info()
        fname = info.get("file_name", Path(file_path).name)
        ftype = info.get("type", info.get("suffix", "?"))
        sheets = info.get("sheets", [])
        if sheets:
            row_n = sheets[0].get("row_count_estimate", "?")
            col_n = sheets[0].get("col_count", "?")
            sn = sheets[0].get("name", "")
            summary = f"{ftype} | Sheet「{sn}」 {row_n}行 × {col_n}列"
        else:
            row_n = info.get("row_count", "?")
            col_n = info.get("col_count", "?")
            summary = f"{ftype} | {row_n}行 × {col_n}列"
        self._status.showMessage(f"📊 已加载「{fname}」: {summary}")
        self._status_model_badge.setText(
            f"📊 {fname} | 🧠 {self._config.get('llm_model', '?')}")

    def _on_switch_mode(self):
        """在通用模式和小说分析模式之间切换。"""
        from PyQt6.QtWidgets import QInputDialog
        items = ["通用聊天", "📖 NovelForge 小说分析", "📊 数据分析"]
        choice, ok = QInputDialog.getItem(
            self, "切换模式", "选择工作模式：", items, 0, False)
        if not ok:
            return
        if "NovelForge" in choice:
            self._on_open_novelforge()
        elif "数据分析" in choice:
            self._on_open_data_analysis()
        else:
            # 切回通用模式，清理专用适配器引用
            from ..adapters import get_adapter
            adapter = get_adapter("generic")
            from ..tools import set_novelforge_adapter, set_data_analysis_adapter
            set_novelforge_adapter(None)
            set_data_analysis_adapter(None)
            session = self._sessions.create(
                title="通用聊天", adapter_key="generic")
            self._setup_agent(session, adapter)
            try:
                self._memory.get_working_memory(session.id).clear()
            except Exception:
                pass
            self._sidebar.refresh_sessions(self._sessions.all_sessions(), session.id)
            self._chat_view.clear_messages()
            self._session_title.setText(session.title)
            self._status.showMessage("已切换到通用聊天模式")
            self._refresh_model_statusbar()

    def _connect_signals(self):
        self._sidebar.session_created.connect(self._on_session_created)
        self._sidebar.session_switched.connect(self._on_session_switched)
        self._sidebar.session_deleted.connect(self._on_session_deleted)
        self._chat_view.message_sent.connect(self._on_message_sent)
        self._chat_view.user_requested_retry.connect(self._on_user_retry)
        self._cancel_stream_requested = False
        self._chat_view.message_edit_requested.connect(self._on_message_edit)
        self._chat_view.flow_confirmed.connect(self._on_flow_confirmed)
        # 状态栏：模型徽章（永久在左下）
        from PyQt6.QtWidgets import QLabel
        try:
            sb = self.statusBar()
            sb.setStyleSheet("QStatusBar { background: transparent; }")
            self._status_model_badge = QLabel("🧠 模型加载中…")
            self._status_model_badge.setContentsMargins(6, 2, 6, 2)
            sb.addPermanentWidget(self._status_model_badge, 0)
            self._refresh_model_statusbar()
        except Exception as exc:
            print("[状态栏徽章初始化失败]", exc)

    def set_available_adapters(self, adapters: list[dict]):
        """设置可用适配器列表。"""
        self._adapters = adapters
        self._sidebar.set_adapters(adapters)

    def _current_adapter_key(self) -> str:
        if self._adapters:
            return self._adapters[0].get("key", "generic")
        return "generic"

    # ===== 会话管理 =====

    def _on_session_created(self, adapter_key: str):
        adapter = get_adapter(adapter_key)
        session = self._sessions.create(
            title=f"新对话 - {adapter.display_name}",
            adapter_key=adapter_key,
        )
        self._setup_agent(session, adapter)
        # 新会话清空工作记忆，避免串到旧会话（按 session id 隔离）
        try:
            self._memory.get_working_memory(session.id).clear()
        except Exception: pass
        self._sidebar.refresh_sessions(self._sessions.all_sessions(), session.id)
        self._chat_view.clear_messages()
        self._session_title.setText(session.title)
        self._status.showMessage(f"已创建会话: {session.title}")

    def _on_session_switched(self, session_id: str):
        session = self._sessions.switch(session_id)
        if session:
            adapter = get_adapter(session.adapter_key, **session.adapter_config)
            self._setup_agent(session, adapter)
            self._inject_adapter_to_skills(adapter)
            # 记忆优化：把该会话持久化的消息全部回灌到 L1 工作记忆（按 session id 隔离）
            # （否则切会话后 WorkingMemory 空的，AI 不记得前面聊过啥）
            try:
                self._memory.get_working_memory(session.id).restore(session.messages)
            except Exception as e:
                import traceback; traceback.print_exc()
            self._sidebar.refresh_sessions(self._sessions.all_sessions(), session.id)
            self._chat_view.load_messages(session.messages)
            self._session_title.setText(session.title)
            self._status.showMessage(f"已切换到: {session.title}（已恢复工作记忆 {len(session.messages)} 条）")

    def _on_session_deleted(self, session_id: str):
        self._sessions.delete(session_id)
        remaining = self._sessions.all_sessions()
        self._sidebar.refresh_sessions(remaining)
        if remaining:
            self._on_session_switched(remaining[0].id)
        else:
            self._chat_view.clear_messages()
            self._session_title.setText("无会话")

    # ===== 编辑消息（重发） =====
    def _on_message_edit(self, msg_id: str, new_text: str):
        """用户对历史气泡做了『编辑并重新发送』。"""
        if not self._sessions.current or not self._agent:
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "AI 思考中", "AI 正在生成回复，请等一下再重发。")
            return
        session = self._sessions.current
        # 找到该消息索引
        idx = None
        for i, m in enumerate(session.messages):
            if m.id == msg_id:
                idx = i
                break
        if idx is None:
            self._status.showMessage("找不到该消息（可能已被截断）")
            return
        # 非 user 消息不许重发
        target = session.messages[idx]
        if target.role != ChatRole.USER:
            QMessageBox.information(self, "无法编辑", "目前只支持重发『用户』侧消息。AI 回复不能被直接编辑。")
            return
        # 会话层截断操作：找到 session 内部的 messages list，截到 idx 前，并改 idx 的内容
        # 会话 module 目前没有 truncate 接口，直接在 session 对象级操作 + save 持久化
        session.messages = session.messages[:idx]
        new_msg = Message(role=ChatRole.USER, content=new_text)
        session.messages.append(new_msg)
        # 覆盖保存
        self._sessions._save(session)
        # L1 工作记忆同步：按 session id 隔离，先清再 restore（截断）
        try:
            wm = self._memory.get_working_memory(session.id)
            wm.clear()
            wm.restore(session.messages)
        except Exception:
            pass
        # 重绘 UI
        self._chat_view.clear_messages()
        for m in session.messages:
            self._chat_view.add_message(m)
        self._session_title.setText(session.title)
        self._status.showMessage("已重写用户消息，重新生成中...")
        # 启动流式
        self._notify_llm_context_to_chat_view()
        self._cancel_stream_requested = False
        self._chat_view.start_streaming()
        # 异步重跑
        self._worker = _SendWorker(
            self._agent, session, new_text,
            summarize_callback=self._make_summarize_callback()
        )
        self._worker.chunk_ready.connect(self._on_chunk)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.status_changed.connect(self._status.showMessage)
        self._worker.start()


    # ========== 模型能力映射 / 状态栏模型徽章 ==========
    def _get_model_capabilities(self, provider, model_name):
        m = (model_name or "").lower()
        prov = (provider or "").lower()
        caps = {"web_search": False, "vision": False, "file_upload": False,
                "tool_use": False, "code_exec": False}
        if prov in {"deepseek", "openai", "zhipu", "moonshot", "siliconflow", "qwen", "doubao", "ollama"}:
            caps["tool_use"] = True; caps["code_exec"] = True; caps["file_upload"] = True
        vision_keywords = ("vision","vl","image","看图","多模态","qwen2.5-vl","qwen-vl",
                           "gpt-4o","gpt-4.1","gpt-4.5","claude-3","glm-4v","moonshot-v1-vision")
        if any(k in m for k in vision_keywords): caps["vision"] = True
        if prov == "zhipu" and "glm-4v" in m: caps["vision"] = True
        if prov == "qwen" and ("vl" in m or "vision" in m): caps["vision"] = True
        if prov == "moonshot" and "vision" in m: caps["vision"] = True
        web_keywords = ("r1","online","search","web","联网","plus","pro","all-tools","deepthink")
        if any(k in m for k in web_keywords): caps["web_search"] = True
        return caps

    def _current_provider_and_model(self):
        cfg = self._config
        prov_id = getattr(self, "_current_provider_id", None) or cfg.get("current_provider", "deepseek")
        prov_cfg = cfg.get("providers", {}).get(prov_id, {}) or {}
        model = prov_cfg.get("model", "") or ""
        name_map = {"deepseek":"DeepSeek","openai":"OpenAI","zhipu":"智谱",
                    "moonshot":"Moonshot","siliconflow":"硅基流动","qwen":"通义",
                    "doubao":"豆包","ollama":"Ollama"}
        disp = prov_cfg.get("name") or name_map.get(prov_id, prov_id)
        return prov_id, model, disp

    def _refresh_model_statusbar(self):
        try:
            from .styles import is_light_theme
            light = is_light_theme()
        except Exception:
            light = False
        prov_id, model, disp = self._current_provider_and_model()
        caps = self._get_model_capabilities(prov_id, model)
        badge = getattr(self, "_status_model_badge", None)
        if badge is None: return
        if not model:
            badge.setText("⚠️  未配置模型（设置 → 语言模型）")
            badge.setStyleSheet(self._status_badge_ss("#7c2d12", "#fef3c7", "#f59e0b"))
            return
        short = model if len(model) <= 24 else model[:22] + "…"
        bits = []
        if caps["web_search"]:  bits.append("🌐")
        if caps["vision"]:      bits.append("🖼")
        if caps["file_upload"]: bits.append("📁")
        if caps["tool_use"]:    bits.append("🛠")
        if caps["code_exec"]:   bits.append("💻")
        txt = "🧠 " + short + " · " + disp + ("   " + "".join(bits) if bits else "")
        badge.setText(txt)
        badge.setStyleSheet(self._status_badge_ss(
            "#4338ca" if light else "#d7bbff",
            "#ede9fe" if light else "#1d1640",
            "#c4b5fd" if light else "#3b2a66"))

    def _status_badge_ss(self, fg, bg, border) -> str:
        return ("QLabel { background:" + bg + "; color:" + fg + "; border:1px solid " + border +
                "; border-radius:999px; padding:2px 10px; font-size:11px; font-weight:800; }")

    def _notify_llm_context_to_chat_view(self):
        prov_id, model, disp = self._current_provider_and_model()
        caps = self._get_model_capabilities(prov_id, model)
        try:
            self._chat_view.set_llm_context(model, caps, disp)
        except Exception:
            pass
        self._refresh_model_statusbar()

    def _on_user_retry(self):
        self._cancel_stream_requested = True
        try:
            w = getattr(self, "_worker", None)
            if w and w.isRunning():
                try: w.requestInterruption()
                except Exception: pass
        except Exception: pass
        from PyQt6.QtCore import QTimer as _QT
        _QT.singleShot(160, self._resend_last_user_message)

    def _resend_last_user_message(self):
        sess = self._sessions.current
        if not sess or not sess.messages: return
        last_user_idx = None
        for i, mm in enumerate(sess.messages):
            if mm.role.name == "USER": last_user_idx = i
        if last_user_idx is None: return
        last = sess.messages[last_user_idx]
        sess.messages = sess.messages[:last_user_idx]
        try: self._sessions._save(sess)
        except Exception: pass
        try:
            wm = self._memory.get_working_memory(sess.id)
            wm.clear(); wm.restore(sess.messages)
        except Exception: pass
        self._chat_view.clear_messages()
        for mm in sess.messages:
            self._chat_view.add_message(mm)
        self._session_title.setText(sess.title)
        if last.metadata:
            try: self._chat_view.set_pending_attachments(last.metadata.get("files", []) or [])
            except Exception: pass
            try: self._chat_view.set_pending_images(last.metadata.get("images", []) or [])
            except Exception: pass
        self._on_message_sent(last.content)

    def _on_flow_confirmed(self, flow_dict: dict):
        """用户确认执行任务卡 → 后台线程执行 FlowRun。"""
        if self._agent is None or self._session is None:
            return
        worker = _FlowWorker(self._agent, self._session, flow_dict)
        flow_id = flow_dict.get("id", "")
        worker.step_progress.connect(self._on_flow_step)
        worker.flow_done.connect(self._on_flow_done)
        worker.start()

    def _on_flow_step(self, flow_id: str, step_label: str, status: str, summary: str):
        """任务卡步骤进度更新。"""
        self._chat_view.update_flow_card(flow_id, step_label, status, summary)

    def _on_flow_done(self, flow_id: str, flow_dict: dict):
        """任务卡执行完成。"""
        self._chat_view.finish_flow_card(flow_id, flow_dict)
        # 状态栏提示
        for s in flow_dict.get("steps", []):
            summ = s.get("summary", "")
            if "输出:" in summ or "report" in summ.lower():
                self._status.showMessage("📊 " + summ)
                break

    def _inject_adapter_to_skills(self, adapter):
        """把适配器实例注入到 skill 注册表，让工具能访问项目/数据上下文。

        会话切换/重建时调用，确保 NovelForge 和数据分析的 skill 可用。
        """
        key = getattr(adapter, "key", "")
        try:
            from ..tools import set_novelforge_adapter, set_data_analysis_adapter
            if key == "novelforge":
                set_novelforge_adapter(adapter)
            elif key == "data_analysis":
                set_data_analysis_adapter(adapter)
        except Exception:
            pass

    def _setup_agent(self, session: Session, adapter):
        """初始化 Agent（需要 LLM 客户端）。"""
        # 创建简易 LLM 客户端
        llm = _SimpleLLMClient(self._config)
        self._agent = ChatAgent(llm, self._memory, adapter, self._config)

    # ===== 消息发送 =====

    def _on_message_sent(self, text: str):
        if not self._sessions.current or not self._agent:
            self._status.showMessage("错误: 无活动会话")
            QMessageBox.warning(self, "无法发送", "请先创建或选择一个会话。")
            return
        if self._worker and self._worker.isRunning():
            self._status.showMessage("AI 正在思考中，请稍候...")
            return

        session = self._sessions.current

        # 用户消息入会话历史 + UI
        try:
            user_msg = Message(role=ChatRole.USER, content=text)
            self._sessions.add_message(session.id, user_msg)
            self._chat_view.add_message(user_msg)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "发送失败", f"保存消息出错: {e}")
            return

        # 开始流式占位（立即给用户反馈）
        self._notify_llm_context_to_chat_view()
        self._cancel_stream_requested = False
        self._chat_view.start_streaming()
        self._status.showMessage("正在检索记忆...")

        # 异步执行: 检索记忆 + 调 LLM
        self._worker = _SendWorker(
            self._agent, session, text,
            summarize_callback=self._make_summarize_callback()
        )
        self._worker.chunk_ready.connect(self._on_chunk)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.status_changed.connect(self._status.showMessage)
        self._worker.start()

    def _on_chunk(self, chunk: str):
        if getattr(self, "_cancel_stream_requested", False):
            return
        self._chat_view.append_stream_chunk(chunk)
        try:
            import time as _t
            if (not hasattr(self, "_last_st_refresh") or
                    _t.time() - self._last_st_refresh > 3.0):
                self._refresh_model_statusbar()
                self._last_st_refresh = _t.time()
        except Exception: pass


    def _on_done(self, full_text: str):
        self._chat_view.finish_streaming(full_text)
        self._cancel_stream_requested = False
        try: self._refresh_model_statusbar()
        except Exception: pass

        # 流程蓝图任务卡（如果 AI 回复携带了 <|flow|> 标签）
        pending_flow = getattr(self._worker, "_pending_flow_run", None)
        if pending_flow:
            self._chat_view.add_flow_card(pending_flow)

        # 入会话历史
        if self._sessions.current:
            ai_msg = Message(role=ChatRole.ASSISTANT, content=full_text)
            self._sessions.add_message(self._sessions.current.id, ai_msg)

        self._memory_panel.refresh()
        self._status.showMessage("回复完成")

        # 刷新侧边栏标题（可能已更新）
        if self._sessions.current:
            self._sidebar.refresh_sessions(
                self._sessions.all_sessions(), self._sessions.current.id
            )
            self._session_title.setText(self._sessions.current.title)

    def _on_error(self, err: str):
        self._chat_view.finish_streaming(f"[错误] {err}")
        self._cancel_stream_requested = False
        try: self._refresh_model_statusbar()
        except Exception: pass
        self._status.showMessage(f"错误: {err}")
        # 针对常见错误给出可操作的提示
        err_lower = err.lower()
        if "api_key" in err_lower or "api key" in err_lower or "authentication" in err_lower or "401" in err:
            tip = ("API Key 无效或未配置。\n\n请点击菜单：设置 → LLM 模型设置，"
                   "填入有效的 API Key、模型名称和 Base URL。")
        elif "model" in err_lower or "does not exist" in err_lower or "no access" in err_lower:
            tip = ("模型名称不正确或当前 Key 没有该模型权限。\n\n请点击菜单：设置 → LLM 模型设置，"
                   "确认模型名与服务商文档一致（如 deepseek-chat、gpt-4o-mini、glm-4-flash 等）。")
        elif "connection" in err_lower or "timeout" in err_lower or "network" in err_lower:
            tip = "网络连接失败 / 超时。请检查 Base URL 是否正确，以及网络是否可访问该服务。"
        else:
            tip = f"详细错误：{err}\n\n如持续出现，请检查 LLM 设置。"
        QMessageBox.warning(self, "AI 回复出错", tip)

    # ===== 设置 =====

    def _on_llm_settings(self):
        """LLM 模型配置对话框（带服务商一键配置 + 能力勾选 + 常用模型下拉 + 参数解释）。"""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QFormLayout, QLineEdit, QDoubleSpinBox,
            QSpinBox, QDialogButtonBox, QLabel, QMessageBox, QComboBox,
            QHBoxLayout, QPushButton, QGroupBox, QCheckBox, QToolButton,
            QSizePolicy, QWidget,
        )
        from PyQt6.QtCore import Qt
        from .styles import current_colors, is_light_theme

        col = current_colors()

        def _spin_style():
            if is_light_theme():
                return (
                    "QDoubleSpinBox, QSpinBox {"
                    f" background:#ffffff; color:{col.text};"
                    f" border: 2px solid {col.overlay}; border-radius: 8px;"
                    " padding: 6px 10px; font-size: 14px; font-weight: 700;"
                    " min-height: 34px; min-width: 116px; }"
                    " QDoubleSpinBox:focus, QSpinBox:focus {"
                    f" border: 2px solid {col.accent}; background:{col.surface0}; }}"
                    " QDoubleSpinBox::up-button, QSpinBox::up-button,"
                    " QDoubleSpinBox::down-button, QSpinBox::down-button { width: 20px; }"
                )
            return (
                "QDoubleSpinBox, QSpinBox {"
                " background:#0a1024; color:#ffffff;"
                f" border: 2px solid {col.accent}; border-radius: 8px;"
                " padding: 6px 10px; font-size: 14px; font-weight: 800;"
                " min-height: 34px; min-width: 116px; }"
                " QDoubleSpinBox:focus, QSpinBox:focus {"
                f" background:#0e1836; border: 2px solid {col.accent_hover}; }}"
                " QDoubleSpinBox::up-button, QSpinBox::up-button,"
                " QDoubleSpinBox::down-button, QSpinBox::down-button {"
                " width: 20px; background: rgba(255,255,255,0.06); border: none; }"
            )

        def _help_btn(tooltip_text):
            btn = QToolButton()
            btn.setText("❓")
            btn.setFixedSize(24, 24)
            btn.setToolTip(tooltip_text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QToolButton { background: transparent; border: 1px solid "
                + (col.overlay if is_light_theme() else col.surface2)
                + "; border-radius: 12px;"
                f" color: {col.accent}; font-size: 13px; font-weight: 900; }}"
                " QToolButton:hover { background: "
                + (col.surface0 if is_light_theme() else "#101835")
                + f"; color:{col.accent_hover}; }}"
            )
            def _show_detail():
                QMessageBox.information(self, "参数说明", tooltip_text)
            btn.clicked.connect(_show_detail)
            return btn

        dlg = QDialog(self)
        dlg.setWindowTitle("LLM 模型设置")
        dlg.setMinimumWidth(720)
        root = QVBoxLayout(dlg)
        root.setSpacing(10)

        title = QLabel("🤖  配置大模型接口")
        title.setStyleSheet(f"font-size: 19px; font-weight: 900; padding: 4px 0; color:{col.text};")
        root.addWidget(title)

        # ---------- 1. 服务商快速选择 ----------
        prov_box = QGroupBox("⚡ 官方服务商一键配置（选一个自动填好 Base URL + 常用模型 + 能力开关）")
        prov_lay_v = QVBoxLayout(prov_box)
        prov_row = QHBoxLayout()
        prov_combo = QComboBox()
        prov_combo.setStyleSheet("QComboBox { padding: 8px 10px; font-size: 13px; min-height: 30px; }")
        prov_combo.addItem("— 手动自定义 —", {
            "base_url": "", "models": [],
            "tip": "", "homepage": "",
            "capabilities": {"web_search": False, "vision": False, "file_upload": True,
                             "tool_use": False, "code_exec": False},
            "per_model_caps": {},
        })
        for p in LLM_PROVIDERS:
            per_model_caps = {}
            for m in p.get("models", []) or []:
                if len(m) >= 3 and m[2]:
                    per_model_caps[m[0]] = m[2]
            prov_combo.addItem(p["name"], {
                "base_url": p.get("base_url", ""),
                "models": [(x[0], x[1]) for x in p.get("models", []) or []],
                "tip": p.get("tip", ""),
                "homepage": p.get("homepage", ""),
                "capabilities": dict(p.get("capabilities", {})),
                "per_model_caps": per_model_caps,
            })
        prov_row.addWidget(QLabel("选择服务商："), 0)
        prov_row.addWidget(prov_combo, 1)
        prov_lay_v.addLayout(prov_row)
        prov_tip = QLabel("👈 选一个后会自动填好下方 Base URL / 模型 / 能力复选框。官方地址 & 控制台链接都已内置。")
        prov_tip.setWordWrap(True)
        prov_tip.setStyleSheet(f"color: {col.subtext}; font-size: 12px; padding: 2px 2px 6px 2px;")
        prov_lay_v.addWidget(prov_tip)
        root.addWidget(prov_box)

        # ---------- 2. 详细配置表单 ----------
        form_box = QGroupBox("🔧 详细参数")
        form = QFormLayout(form_box)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        # 🔑 API Key
        api_key_edit = QLineEdit(self._config.api_key)
        api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        api_key_edit.setPlaceholderText("sk-xxxxxxxxxxxxxxxxxxxxxxxx  （在服务商控制台申请）")
        api_key_edit.setMinimumHeight(40)
        api_key_edit.setStyleSheet(
            "QLineEdit {"
            f" background:{'#ffffff' if is_light_theme() else '#0a0f20'}; color:{col.text};"
            f" border: 2px solid {col.overlay}; border-radius: 10px; padding: 8px 96px 8px 14px;"
            " font-size: 14px; font-weight: 600; }"
            f" QLineEdit:focus {{ border: 2px solid {col.accent}; }}"
        )
        show_btn = QPushButton("👁 显示")
        show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        show_btn.setFixedHeight(30)
        show_btn.setCheckable(True)
        show_btn.setStyleSheet(
            "QPushButton {"
            f" background:{'#ffffff' if is_light_theme() else '#121a36'}; color:{col.accent};"
            f" border: 1px solid {col.overlay}; border-radius: 7px;"
            " padding: 4px 10px; font-size: 12px; font-weight: 800; min-width: 80px; }"
            f" QPushButton:hover {{ color:{col.text}; border-color:{col.accent}; background:{col.surface0}; }}"
        )
        def _toggle_key(checked):
            if checked:
                api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
                show_btn.setText("🔒 隐藏")
            else:
                api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
                show_btn.setText("👁 显示")
        show_btn.clicked.connect(_toggle_key)
        inner = QHBoxLayout(api_key_edit)
        inner.setContentsMargins(0, 0, 6, 0)
        inner.addStretch(1)
        inner.addWidget(show_btn)
        key_wrap = QWidget()
        key_wrap_l = QHBoxLayout(key_wrap)
        key_wrap_l.setContentsMargins(0, 0, 0, 0)
        key_wrap_l.addWidget(api_key_edit, 1)
        form.addRow("🔑 API Key：", key_wrap)

        # 🌐 Base URL
        base_url_edit = QLineEdit(self._config.base_url)
        base_url_edit.setPlaceholderText("留空 = 调用 OpenAI 官方")
        base_url_edit.setMinimumHeight(36)
        form.addRow("🌐 Base URL：", base_url_edit)

        # 🧠 模型
        model_row_w = QWidget()
        model_row = QHBoxLayout(model_row_w)
        model_row.setContentsMargins(0, 0, 0, 0); model_row.setSpacing(6)
        model_combo = QComboBox()
        model_combo.setEditable(True)
        model_combo.setStyleSheet(
            "QComboBox { padding: 6px 8px; font-size: 13px; min-height: 34px; }"
            " QComboBox QAbstractItemView { padding: 2px; }"
        )
        model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        model_combo.addItem(self._config.model or "（请选择或输入模型）")
        model_combo.setCurrentIndex(0)
        model_row.addWidget(QLabel("模型 ID："), 0)
        model_row.addWidget(model_combo, 1)
        form.addRow("🧠 模型名称：", model_row_w)

        # 🎚 参数
        temp_row = QHBoxLayout(); temp_row.setSpacing(8)
        temp_help = _help_btn(
            "🎚 【温度 Temperature】\n\n"
            "取值范围：0.00 ~ 2.00\n"
            "控制 AI 回答的『创造性 / 随机性 / 发散度』：\n"
            "  • 0.0 ~ 0.3：严谨保守 —— 适合代码、数学、翻译、事实问答\n"
            "  • 0.4 ~ 0.7：平衡自然 —— 日常聊天、总结、润色（推荐 0.70）\n"
            "  • 0.8 ~ 1.2：天马行空 —— 写小说、文案、头脑风暴、起名\n"
            "  • 1.3 ~ 2.0：高度发散 —— 容易出幻觉，仅适合纯创意任务"
        )
        temp_l = QLabel(f"温度 {self._config.temperature:.2f}")
        temp_l.setStyleSheet(f"font-size: 13px; font-weight: 800; color:{col.text}; min-width: 76px;")
        temp_spin = QDoubleSpinBox()
        temp_spin.setRange(0.0, 2.0); temp_spin.setSingleStep(0.05); temp_spin.setDecimals(2)
        temp_spin.setValue(self._config.temperature)
        temp_spin.setStyleSheet(_spin_style())
        temp_spin.setMinimumHeight(34)
        def _on_temp(v): temp_l.setText(f"温度 {v:.2f}")
        temp_spin.valueChanged.connect(_on_temp)
        temp_row.addWidget(temp_help)
        temp_row.addWidget(temp_l)
        temp_row.addWidget(temp_spin)
        temp_row.addSpacing(16)

        max_help = _help_btn(
            "🧮 【Max Tokens（最大生成长度）】\n\n"
            "单次请求中，AI 回复的『最大 token 数』上限：\n"
            "  • 512 ~ 1024：一句话 / 快速分类 / 短回答（省 token、响应快）\n"
            "  • 2048 ~ 4096：日常聊天、写一段代码（通用推荐 4096）\n"
            "  • 8192+：写长文档、完整章节、长代码\n\n"
            "⚠️ 注意：加上你输入的上下文 prompt，总长度不能超过模型支持的上下文窗口。"
        )
        max_l = QLabel("Max Tokens：")
        max_l.setStyleSheet(f"font-size: 13px; font-weight: 800; color:{col.text};")
        max_spin = QSpinBox()
        max_spin.setRange(256, 32768); max_spin.setSingleStep(256)
        max_spin.setValue(self._config.max_tokens)
        max_spin.setStyleSheet(_spin_style())
        max_spin.setMinimumHeight(34)
        temp_row.addWidget(max_help)
        temp_row.addWidget(max_l)
        temp_row.addWidget(max_spin, 1)
        params_wrap = QWidget(); params_wrap.setLayout(temp_row)
        form.addRow("🎚  采样参数：", params_wrap)
        root.addWidget(form_box)

        # ---------- 2.5 能力复选框区 ----------
        cap_box = QGroupBox("🛰  本模型支持范围（切服务商/模型会自动勾选，你也可以手动微调）")
        cap_lay = QHBoxLayout(cap_box); cap_lay.setSpacing(10)
        caps_spec = [
            ("web_search",  "🌐 联网搜索",         "模型能调用浏览器 / 搜索引擎查实时信息"),
            ("vision",      "🖼  多模态（看图）",   "支持上传图片，让 AI 识别 / 解释图片内容"),
            ("file_upload", "📁 文件上传",         "支持把 txt/md/code/pdf/xlsx 等文件发送给 AI"),
            ("tool_use",    "🛠  工具调用",         "兼容 Function Calling / Tool Use 协议"),
            ("code_exec",   "💻 运行程序 & 找文件", "允许执行 shell/python 命令、本地搜索文件"),
        ]
        caps_checks: dict[str, QCheckBox] = {}
        for key, label, desc in caps_spec:
            cb = QCheckBox(label)
            cb.setToolTip(desc)
            cb.setStyleSheet(
                "QCheckBox { spacing: 6px; padding: 5px 7px; border-radius: 6px; font-size: 12px;"
                f" font-weight: 700; color:{col.text}; }}"
                f" QCheckBox:hover {{ background:{col.surface0}; }}"
                " QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 2px solid "
                + col.overlay + "; background:"
                + ("#ffffff" if is_light_theme() else "#0a1024") + "; }"
                " QCheckBox::indicator:checked {"
                f" background: {col.accent}; border-color:{col.accent}; }}"
            )
            caps_checks[key] = cb
            cap_lay.addWidget(cb)
        root.addWidget(cap_box)

        # ---------- 3. 控制台链接 / 提示 ----------
        info_lbl = QLabel("")
        info_lbl.setWordWrap(True)
        info_bg = "rgba(46,204,113,0.08)" if is_light_theme() else "rgba(46,204,113,0.10)"
        info_lbl.setStyleSheet(
            "padding: 12px 14px; border-radius: 8px;"
            f" background: {info_bg}; color: {col.text};"
            " border-left: 3px solid #2ecc71;"
        )
        root.addWidget(info_lbl)

        state_label = QLabel(
            "✅ 修改后点『保存并生效』立即生效，无需重启。\n"
            "⚠️ 还没 Key？先选上面服务商 → 点下方控制台链接申请。"
        )
        state_label.setWordWrap(True)
        state_label.setStyleSheet(f"color: {col.subtext}; padding-top: 4px;")
        root.addWidget(state_label)

        # ---------- 4. 按钮 ----------
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("✅ 保存并生效")
        btns.button(QDialogButtonBox.StandardButton.Ok).setMinimumHeight(40)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setMinimumHeight(40)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        root.addWidget(btns)

        # ---------- 5. 信号：切服务商 / 切模型 ----------
        def _current_model_id() -> str:
            d = model_combo.currentData()
            if isinstance(d, str) and d:
                return d
            t = model_combo.currentText().strip()
            if "  —  " in t:
                return t.split("  —  ")[0].strip()
            return t

        def _recalc_caps():
            data = prov_combo.currentData() or {}
            base = dict(data.get("capabilities", {}))
            model_id = _current_model_id()
            per = (data.get("per_model_caps") or {}).get(model_id) or {}
            merged = {**base, **per}
            for k, cb in caps_checks.items():
                if k in merged:
                    cb.setChecked(bool(merged[k]))

        def _update_info():
            data = prov_combo.currentData() or {}
            tip = data.get("tip", "")
            homepage = data.get("homepage", "")
            html = ""
            if prov_combo.currentIndex() == 0:
                html = (
                    "💡 <b>手动自定义模式</b>：服务商不在列表？直接填 Base URL + 模型名即可"
                    "（兼容 OpenAI 协议的都能用）。"
                )
            else:
                html = f"📌 <b>{prov_combo.currentText()}</b><br>"
                if tip:
                    html += f"&nbsp;&nbsp;{tip}<br>"
                if homepage:
                    link_c = col.accent if is_light_theme() else "#7ec0ff"
                    html += f'🔗 <a href="{homepage}" style="color:{link_c};text-decoration:none;">{homepage}</a>'
            info_lbl.setText(html)
            info_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            info_lbl.setOpenExternalLinks(True)

        def _on_provider_changed(_idx):
            data = prov_combo.currentData() or {}
            base_url_edit.setText(data.get("base_url", "") or "")
            models = data.get("models", []) or []
            current_text = model_combo.currentText().strip()
            model_combo.blockSignals(True)
            model_combo.clear()
            added = set()
            for m_id, m_desc in models:
                model_combo.addItem(f"{m_id}  —  {m_desc}", m_id)
                added.add(m_id)
            if current_text and current_text not in added:
                model_combo.insertItem(0, current_text, current_text)
                model_combo.setCurrentIndex(0)
            elif models:
                model_combo.setCurrentIndex(0)
            model_combo.blockSignals(False)
            _recalc_caps()
            _update_info()

        def _on_model_changed(*_):
            _recalc_caps()

        prov_combo.currentIndexChanged.connect(_on_provider_changed)
        model_combo.currentIndexChanged.connect(_on_model_changed)
        model_combo.editTextChanged.connect(_on_model_changed)

        cur_bu = self._config.base_url or ""
        matched_idx = 0
        for i, p in enumerate(LLM_PROVIDERS, start=1):
            if p.get("base_url", "") and p["base_url"] in cur_bu:
                matched_idx = i; break
        prov_combo.setCurrentIndex(matched_idx)
        _on_provider_changed(matched_idx)

        # ---------- 6. 保存 ----------
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # 取模型：下拉可能带 "id  —  desc" 格式，取 data 优先
            m_data = model_combo.currentData()
            if isinstance(m_data, str) and m_data:
                model_name = m_data
            else:
                # 否则取输入框纯文本，去掉 "  —  desc" 后缀
                t = model_combo.currentText().strip()
                if "  —  " in t:
                    t = t.split("  —  ")[0].strip()
                model_name = t

            self._config.api_key = api_key_edit.text().strip()
            self._config.base_url = base_url_edit.text().strip()
            self._config.model = model_name
            self._config.temperature = temp_spin.value()
            self._config.max_tokens = max_spin.value()

            if not self._config.model:
                QMessageBox.warning(dlg, "模型未填写", "请选择或输入一个模型名称（如 deepseek-chat）。")
                return

            try:
                self._config.save()
            except Exception as e:
                QMessageBox.critical(self, "保存失败", f"写配置文件出错: {e}")
                return
            # 热更新：当前会话的 agent LLM 重建
            self._rebuild_all_llm_clients()
            QMessageBox.information(
                self, "✅ 设置已保存",
                f"LLM 配置已立即生效：\n\n"
                f"  服务商：{prov_combo.currentText()}\n"
                f"  模型：{self._config.model}\n"
                f"  地址：{self._config.base_url or '(OpenAI 官方)'}\n"
                f"  温度：{self._config.temperature:.2f}  Max Tokens：{self._config.max_tokens}\n\n"
                f"现在可以开始聊天了。"
            )
    def _rebuild_all_llm_clients(self) -> None:
        """所有已创建 agent 的 LLM client 重建（设置保存后调用）。"""
        # 最简单：如果有当前会话，重建它的 agent
        if self._sessions.current:
            try:
                from ..adapters import get_adapter
                s = self._sessions.current
                adapter = get_adapter(s.adapter_key, **s.adapter_config)
                self._setup_agent(s, adapter)
                self._inject_adapter_to_skills(adapter)
            except Exception:
                traceback.print_exc()

    def _make_summarize_callback(self):
        """构造记忆树形归纳的 LLM 摘要回调。"""
        config = self._config
        def cb(texts: list[str]) -> str:
            # 优先走 LLM；失败回退简单拼接
            if not config.api_key:
                combined = " ".join(texts)
                return combined[:200] + ("..." if len(combined) > 200 else "")
            try:
                from openai import OpenAI
                client = OpenAI(
                    api_key=config.api_key,
                    base_url=config.base_url or None,
                )
                resp = client.chat.completions.create(
                    model=config.model,
                    temperature=0.3,
                    max_tokens=512,
                    messages=[
                        {"role": "system", "content": "把以下对话内容压缩成不超过 200 字的中文摘要，保留关键事实："},
                        {"role": "user", "content": "\n".join(texts)},
                    ],
                )
                return resp.choices[0].message.content or ""
            except Exception:
                combined = " ".join(texts)
                return combined[:200] + ("..." if len(combined) > 200 else "")
        return cb

    def _on_skin_settings(self):
        from .styles import SKINS, get_skin, apply_skin
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QRadioButton, QButtonGroup, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("皮肤设置")
        dlg.setFixedWidth(400)
        lay = QVBoxLayout(dlg)

        group = QButtonGroup(dlg)
        buttons: dict[str, QRadioButton] = {}
        for key, skin in SKINS.items():
            radio = QRadioButton(f"{skin.name} — {skin.desc}")
            if key == self._config.skin:
                radio.setChecked(True)
            lay.addWidget(radio)
            group.addButton(radio)
            buttons[key] = radio

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            for key, radio in buttons.items():
                if radio.isChecked():
                    self._config.skin = key
                    self._config.save()
                    apply_skin(self._get_app(), key)
                    # 切皮肤后，重新构建所有内部硬编码样式（气泡/卡片/侧栏/标题条）
                    self._rebuild_all_theme_aware_styles()
                    break

    # ===== 主题相关：硬编码颜色的组件统一在这里刷新（保证文字/背景色差） =====
    def _apply_titlebar_style(self):
        """顶部会话标题条：用派生对比色，确保文字清晰（尤其切到浅皮肤时）。"""
        from .styles import titlebar_colors, current_colors
        bg_l, bg_r, text, _ = titlebar_colors()
        # 渐变 5%~8% 透明度的强调色叠在主基色上
        c = current_colors()
        ss = (
            "QLabel {"
            f"  font-size: 14px; font-weight: 900; letter-spacing: 1.5px; padding: 10px 20px;"
            f"  color: {text};"
            f"  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"    stop:0 {bg_l}, stop:1 {bg_r});"
            f"  border-bottom: 1px solid {c.overlay};"
            "}"
        )
        self._session_title.setStyleSheet(ss)

    def _rebuild_all_theme_aware_styles(self):
        """切皮肤后：重建所有内部样式并 repolish。"""
        from .styles import apply_skin_widget
        self._apply_titlebar_style()
        # ChatView / Sidebar / MemoryPanel 都有主题感知的样式（内部 setStyleSheet 硬编码颜色）
        for w in (self._chat_view, self._sidebar, self._memory_panel):
            if hasattr(w, "reapply_theme"):
                w.reapply_theme()
            else:
                apply_skin_widget(w)
        apply_skin_widget(self)

    def _get_app(self):
        from PyQt6.QtWidgets import QApplication
        return QApplication.instance()

    def closeEvent(self, event):
        self._sessions.save_all()
        self._config.save()
        super().closeEvent(event)


class _SendWorker(QThread):
    """把 记忆检索 + LLM 流式聊天 全部放到后台线程，彻底避免 UI 卡死。"""
    chunk_ready = pyqtSignal(str)
    done = pyqtSignal(str)
    error = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, agent, session, user_text,
                 memory_results=None, summarize_callback=None, parent=None,
                 user_message_ref=None):
        super().__init__(parent)
        self._agent = agent
        self._session = session
        self._user_text = user_text
        self._memory_results = memory_results
        self._summarize_cb = summarize_callback
        self._user_message_ref = user_message_ref  # 带 metadata.images 的用户消息
        self._pending_flow_run = None   # 流程蓝图任务卡（done 事件携带）

    def run(self):
        try:
            # Step 1: 检索记忆（FTS5 双路 + 可选向量，统一走后台线程）
            self.status_changed.emit("正在检索记忆...")
            if self._memory_results is None:
                self._memory_results = self._agent.retrieve_memory(
                    self._session, self._user_text
                )

            # 注入记忆摘要回调（树形归纳用）
            if self._summarize_cb is not None:
                try:
                    self._agent._memory.set_summarize_callback(self._summarize_cb)
                except Exception:
                    pass

            # Step 2: 流式 LLM 聊天
            self.status_changed.emit("AI 正在回复...")

            has_images = bool(
                self._user_message_ref is not None
                and (self._user_message_ref.metadata or {}).get("images")
            )
            if not has_images:
                # 普通链路（无图片）：走 agent.chat 封装的 commit + stream + 工具解析
                full = ""
                got_any = False
                for event in self._agent.chat(
                    self._session, self._user_text, self._memory_results
                ):
                    etype = event.get("type", "")
                    content = event.get("content", "")
                    if etype == "chunk":
                        got_any = True
                        full += content
                        self.chunk_ready.emit(content)
                    elif etype == "done":
                        full = content if content else full
                        fr = event.get("flow_run")
                        if fr:
                            self._pending_flow_run = fr
                    elif etype == "error":
                        # LLM 层直接返回的错误（如 401、模型不存在）
                        self.error.emit(content)
                        return
                if not got_any and not full:
                    self.error.emit("LLM 未返回任何内容（请检查模型名/API Key 是否正确）")
                    return
                self.done.emit(full)
            else:
                # 图片链路：手动组装 messages（最后一条 user = 含 images 的 to_llm_dict parts）
                # 先 commit 一次用户消息（含 images metadata）到 memory
                from ..protocol.types import ChatRole, Message
                scope = self._agent._adapter.get_scope(self._session)
                self._agent._memory.commit_message(
                    self._user_message_ref, self._session.id, scope=scope,
                    metadata={"adapter": self._agent._adapter.key},
                )
                msgs = self._agent.build_messages(self._session, self._memory_results)
                # 覆盖最后一条 USER：让 to_llm_dict 输出 content 为 list(含 image_url)
                # msgs 的尾一般来自 working_memory，里面很可能还没有我们刚 commit 的 images 版本，
                # 因此直接把最后一条 role==user 替换为 self._user_message_ref.to_llm_dict()
                user_llm = self._user_message_ref.to_llm_dict()
                replaced = False
                for i in range(len(msgs)-1, -1, -1):
                    if msgs[i].get("role") == "user":
                        msgs[i] = user_llm
                        replaced = True
                        break
                if not replaced:
                    msgs.append(user_llm)

                # 直接手动调 LLM.stream_chat
                llm = self._agent._llm
                kwargs = self._agent._llm_kwargs()
                full = ""
                got_any = False
                try:
                    if hasattr(llm, "stream_chat"):
                        for chunk in llm.stream_chat(msgs, **kwargs):
                            if isinstance(chunk, str):
                                txt = chunk
                            else:
                                txt = chunk.get("content", "") if isinstance(chunk, dict) else ""
                            if txt:
                                got_any = True
                                full += txt
                                self.chunk_ready.emit(txt)
                    else:
                        resp = llm.chat(msgs, **kwargs)
                        full = resp if isinstance(resp, str) else str(resp)
                        if full:
                            got_any = True
                            self.chunk_ready.emit(full)
                except Exception as e:
                    self.error.emit(str(e))
                    return

                # 工具解析 & 提交 assistant 消息
                try:
                    from ..tools import extract_and_run
                    processed, tool_results = extract_and_run(full)
                except Exception:
                    processed, tool_results = full, []
                final_text = processed
                ai_msg = Message(
                    role=ChatRole.ASSISTANT, content=final_text,
                    metadata={"adapter": self._agent._adapter.key,
                              "tool_calls": tool_results or []},
                )
                self._agent._memory.commit_message(
                    ai_msg, self._session.id, scope=scope,
                    metadata=ai_msg.metadata,
                )
                # on_response 回调
                try:
                    self._agent._adapter.on_response(self._session, final_text)
                except Exception:
                    pass
                if tool_results:
                    summary_lines = []
                    for tr in tool_results:
                        name = tr.get("name", "?")
                        r = tr.get("result", {})
                        if isinstance(r, dict):
                            ok = r.get("ok")
                            tail = r.get("stderr") or r.get("error") or (
                                f"matched {r.get('match_count')} files, scanned {r.get('scanned_files')}"
                                if name == "find_files" else f"exit={r.get('exit_code')}"
                            )
                        else:
                            ok = True; tail = ""
                        summary_lines.append("- tool:" + name + " ok=" + str(ok) + "  " + str(tail))
                    tm = Message(
                        role=ChatRole.TOOL,
                        content="\n".join(summary_lines) + "\n详细已拼在上面的 assistant 消息中。",
                        metadata={"adapter": self._agent._adapter.key},
                    )
                    self._agent._memory.commit_message(
                        tm, self._session.id, scope=scope,
                        metadata=tm.metadata,
                    )

                if not got_any and not final_text:
                    self.error.emit("LLM 未返回任何内容（请检查模型名/API Key 是否正确）")
                    return
                self.done.emit(final_text)
        except ImportError as e:
            self.error.emit(f"缺少依赖: {e}。请运行 pip install openai")
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            self.error.emit(f"{type(e).__name__}: {e}")


class _SimpleLLMClient:
    """简易 LLM 客户端 — 兼容 OpenAI 协议 SDK 调用方式（流式）。"""

    def __init__(self, config):
        self._config = config
        self._client = None
        self._last_error = None
        self._init_client()

    def _init_client(self):
        if not self._config.api_key:
            self._last_error = "未配置 API Key（模拟模式）"
            return
        try:
            from openai import OpenAI
        except ImportError as e:
            self._last_error = f"未安装 openai SDK: {e}"
            return
        try:
            self._client = OpenAI(
                api_key=self._config.api_key,
                base_url=self._config.base_url or None,
            )
            # 立即探活（不发请求，只保证参数合法）
            _ = self._client.api_key
        except Exception as e:
            self._client = None
            self._last_error = f"初始化 OpenAI 客户端失败: {e}"

    def stream_chat(self, messages: list[dict], **kwargs):
        """流式聊天（生成器）。未配置时给模拟响应，真实错误以异常抛出给上层。"""
        if not self._client:
            if not self._config.api_key:
                yield (
                    "（模拟回复）\n\n"
                    "✅ 软件已正常运行！你现在看到的是内置模拟回复。\n\n"
                    "👉 要连接真实 AI 模型，请点击顶部菜单：\n"
                    "    设置 → LLM 模型设置 (API Key / 模型 / 地址)\n\n"
                    "在弹窗中填写：\n"
                    "  • API Key — 服务商给你的密钥（sk-...）\n"
                    "  • Base URL — 服务商接口地址（如 https://api.deepseek.com/v1）\n"
                    "  • 模型名称 — 例如 deepseek-chat、gpt-4o-mini、glm-4-flash\n\n"
                    "保存后即可开始真实对话。"
                )
                return
            else:
                raise RuntimeError(self._last_error or "LLM 客户端未初始化")

        model = kwargs.get("model") or self._config.model or "deepseek-chat"
        temperature = kwargs.get("temperature", self._config.temperature)
        max_tokens = kwargs.get("max_tokens", self._config.max_tokens)
        if not model:
            raise RuntimeError("模型名称为空，请在 设置 → LLM 模型设置 中填写。")

        response = self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            timeout=120,
        )
        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content


# ============================================================
# _FlowWorker — 后台执行 FlowRun（任务卡），防止 UI 卡死
# ============================================================
class _FlowWorker(QThread):
    """后台执行 FlowRun，按步骤调用 skill / LLM。"""
    step_progress = pyqtSignal(str, str, str, str)  # flow_id, step_label, status, summary
    flow_done = pyqtSignal(str, dict)               # flow_id, flow_run_dict

    def __init__(self, agent, session, flow_run_dict, parent=None):
        super().__init__(parent)
        self._agent = agent
        self._session = session
        self._flow_run_dict = flow_run_dict
        self._flow_id = flow_run_dict.get("id", "")

    def run(self):
        try:
            from ..flow import (FlowRun, execute_flow_run,
                                 FlowMemoryManager, get_flow_scope)
            flow_run = FlowRun.from_dict(self._flow_run_dict)

            def progress_cb(step_label, status, summary):
                self.step_progress.emit(self._flow_id, step_label, status, summary)

            # 构造四层记忆写入器
            mgr = None
            memory_engine = getattr(self._agent, "_memory", None)
            adapter_key = getattr(self._agent._adapter, "key", "")
            if memory_engine and self._session:
                scope = get_flow_scope(flow_run, adapter_key)
                mgr = FlowMemoryManager(memory_engine, self._session.id, scope)

            result = execute_flow_run(
                flow_run,
                adapter=self._agent._adapter,
                llm_client=self._agent._llm,
                llm_kwargs=self._agent._llm_kwargs(),
                progress_callback=progress_cb,
            )

            # --- 四层记忆：每步写 L1+L2，完成后批量 L3 树 + L4 向量 ---
            if mgr is not None:
                try:
                    bp_key = result.blueprint_key
                    for run_step in result.steps:
                        mgr.commit_step(result.id, run_step, blueprint_key=bp_key)
                    tree_info = mgr.finalize_tree(result)
                    rd = result.to_dict()
                    rd.setdefault("memory", {})
                    rd["memory"]["scope"] = mgr.scope
                    rd["memory"]["tree_root_id"] = tree_info.get("root", "")
                    rd["memory"]["step_count"] = len(tree_info.get("steps", {}))
                    self.flow_done.emit(self._flow_id, rd)
                    return
                except Exception:
                    import traceback; traceback.print_exc()

            self.flow_done.emit(self._flow_id, result.to_dict())
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.flow_done.emit(self._flow_id, {
                **self._flow_run_dict,
                "status": "failed",
            })
