# AIChat 产品文档与代码索引

> 最后更新：2026-09-01
> 用途：产品说明 + 架构索引，方便后续 Agent 修改和迭代

---

## 一、产品概述

AIChat 是一款桌面端通用 AI 聊天软件，核心理念：**一个会记忆、会用工具、能编排流程的私人 AI 工作台**。

它把"聊天"升级为三层能力：
1. **对话**：赛博朋克风界面、流式输出、Markdown 富文本、多会话管理。
2. **能力**：内置 18 个 Agent Skill（跑命令/读文件/抓网页/跑 Python/读小说/跑数据…），AI 自主调用。
3. **记忆 + 流程**：四层记忆架构让 AI 跨会话记住事实与偏好；FlowBlueprint/FlowRun 把"数据分析/小说创作"这类多步工作流固化成任务卡，AI 填参数、用户确认、逐步执行。

用户只需选择适配器（通用闲聊 / NovelForge 小说 / 数据分析），剩余的上下文组装、记忆召回、工具调用、流程推演都由软件自动完成。

**核心设计原则**：
- 记忆分层：工作记忆(L1) → SQLite 原始库(L2) → 树形记忆(L3) → 向量索引(L4)，原始只追加、树形异步批量、向量只存摘要
- 检索链路固定：metadata 过滤 → 树形分支 → 向量召回 UUID → Rerank → 回查原文
- 过期不删除：用 `is_expired` 标记，三层同步
- 流程受控：FlowBlueprint 是静态骨架，AI 只填参数不生成流程
- 安全沙箱：路径白名单 + 危险命令/关键字拦截

---

## 二、功能清单

| 模块 | 功能 | 说明 |
|------|------|------|
| **会话管理** | 新建/切换/重命名/删除会话 | 左侧侧边栏，会话存 sessions.json |
| | 会话标题自动更新 | 首条消息摘要生成标题 |
| | 空会话/悬空会话修复 | session.py 已处理边界情况 |
| **聊天** | 流式输出 | 后台线程 _SendWorker，防 UI 冻结 |
| | Markdown 渲染 | 自定义 md_render.py，代码块可复制 |
| | 消息编辑重发 | 编辑后截断后续历史+清工作记忆+重生成 |
| | 图片上传 | 专属按钮+缩略图+base64 注入 LLM |
| | 文件上传 | 拖拽/选择+chip 预览+内容自动拼入消息 |
| | 气泡视觉层级 | 用户青色竖条 / AI 紫青竖条，角色名+时间戳 |
| | 流式心跳检测 | 卡死检测+重试按钮 |
| | 模型 badge+能力标签 | 流式消息强制注入 metadata |
| **LLM 设置** | 8 个官方服务商 | 自动填 Base URL+模型列表+控制台超链 |
| | 模型能力标注 | 联网/多模态/文件/工具/代码执行 勾选框 |
| | 参数面板 | 温度/Max Tokens 大输入框+帮助按钮 |
| | 动态主题 | YIQ 亮度公式派生对比色 |
| **记忆架构** | 四层记忆 | L1 内存 / L2 SQLite / L3 树形 / L4 ChromaDB |
| | 隐藏记忆指令 | AI 回复中 `<\|memory\|>` 标签自动剥离+写入 |
| | 记忆面板 | 查看/检索记忆 |
| | 过期标记 | is_expired 不物理删除 |
| **Agent Skill** | 文件操作 | read_file/write_file/list_directory/find_files |
| | Shell | exec_shell（白名单+危险拦截） |
| | 网络 | web_fetch |
| | 代码 | python_eval（沙箱） |
| | 系统 | get_system_info |
| **适配器** | GenericAdapter | 通用闲聊，注入工具+记忆协议 |
| | NovelForgeAdapter | 集成小说项目（read_chapter/search_novel 等 6 skill） |
| | DataAnalysisAdapter | 集成数据分析（load_spreadsheet/run_pandas 等 4 skill） |
| **Flow 流程** | FlowBlueprint | 静态流程蓝图模板 |
| | FlowRun | 任务卡，AI 用 `<\|flow\|>` 携带，确认后执行 |
| | FlowMemoryManager | FlowRun 写四层记忆+树形归纳+过期标记 |
| | FlowCardWidget | 任务卡 UI 渲染 |

---

## 三、架构图

```
┌──────────────────────────────────────────────────────────────┐
│                    MainWindow (UI 层)                         │
│  ┌──────────┐  ┌────────────────────────┐  ┌──────────────┐  │
│  │ 会话侧边栏 │  │    聊天视图              │  │  记忆面板     │  │
│  │          │  │  赛博朋克气泡/任务卡/MD   │  │  (可折叠)    │  │
│  └──────────┘  └────────────────────────┘  └──────────────┘  │
├──────────────────────────────────────────────────────────────┤
│              ChatAgent (聊天流水线层)                          │
│  用户消息入库 → 组装messages → LLM流式 → 工具提取 → 记忆提取    │
│  → Flow提取 → AI消息入库 → 记忆写入 → 适配器回调                │
├──────────────────────────────────────────────────────────────┤
│           Adapters (适配器层) + Tools (Skill池)                │
│  Generic / NovelForge / DataAnalysis   18 Skill + 协议         │
├──────────────────────────────────────────────────────────────┤
│              MemoryEngine (四层记忆层)                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │ L1 工作  │ │ L2 SQLite │ │ L3 树形  │ │ L4 向量  │        │
│  │ (内存)   │ │ (原文只追加)│ │(异步批量) │ │(ChromaDB)│        │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │
├──────────────────────────────────────────────────────────────┤
│              FlowBlueprint/FlowRun (流程层)                    │
│  静态蓝图 → AI 填参数 → 任务卡 → 确认执行 → 写记忆             │
├──────────────────────────────────────────────────────────────┤
│              LLMClient (API 层)                                │
│  OpenAI 兼容，8 服务商预设，流式调用，后台线程                  │
└──────────────────────────────────────────────────────────────┘
```

---

## 四、核心文件索引

### 4.1 入口与配置

| 文件 | 关键类/函数 | 说明 |
|------|------------|------|
| `main.py` | `main()` | 入口，加载 Config/Memory/Sessions，显示 MainWindow，注册适配器 |
| `aichat/config.py` | `Config` | 全局配置 dataclass，含 `~/.aichat`→`~/AIChat_Data` 自动迁移 |

### 4.2 协议层 `aichat/protocol/`

| 文件 | 关键类 | 说明 |
|------|--------|------|
| `types.py` | `Message`/`Session`/`ChatRole`/`MemoryQuery`/`MemoryResult` | 数据类，Message 带 metadata |
| `adapter.py` | `ChatAdapter` | 适配器抽象基类 |
| `events.py` | — | 事件类型定义 |

### 4.3 记忆层 `aichat/memory/`

| 文件 | 关键类/方法 | 说明 |
|------|------------|------|
| `engine.py` | `MemoryEngine` | 四层协调器：`commit_message`/`retrieve`/`_try_summarize` |
| `working.py` | `WorkingMemory` | L1，`get_llm_messages()` 按 token 裁剪 |
| `sqlite_store.py` | `SQLiteStore` | L2，`raw_text` 只追加；`find_by_metadata_key`/`mark_expired` |
| `tree_store.py` | `TreeMemoryStore`/`TreeNode` | L3，`update_parent`/`find_by_metadata_key`/`mark_expired` |
| `vector_store.py` | `VectorStore` | L4，`mark_expired_by_flow` best-effort 标记 |

### 4.4 聊天层 `aichat/chat/`

| 文件 | 关键类/方法 | 说明 |
|------|------------|------|
| `agent.py` | `ChatAgent.chat()` | 流水线主流程（见 §3.2 AGENTS.md） |
| `session.py` | `SessionManager` | 会话增删改查，修复空 ID/悬空/标题 |
| `worker.py` | `_SendWorker` | 后台线程，防 ChromaDB 下载卡 UI |

### 4.5 工具与流程 `aichat/`

| 文件 | 关键内容 | 说明 |
|------|---------|------|
| `tools.py` | `SKILL_REGISTRY`/`@skill`/`extract_and_run`/`extract_memory`/`TOOL_PROTOCOL_MD`/`MEMORY_PROTOCOL_MD` | 18 Skill + 工具/记忆协议 |
| `flow.py` | `FlowBlueprint`/`FlowRun`/`FlowMemoryManager`/`parse_flow_tag` | 流程蓝图+任务卡+记忆写入 |

### 4.6 适配器 `aichat/adapters/`

| 文件 | 类 | 说明 |
|------|----|------|
| `__init__.py` | `register_adapters()` | 适配器注册表 |
| `generic.py` | `GenericAdapter` | 注入工具+记忆协议 |
| `novelforge.py` | `NovelForgeAdapter` | 小说项目，注入 set_novelforge_adapter |
| `data_analysis.py` | `DataAnalysisAdapter` | 数据分析，注入 set_data_analysis_adapter |

### 4.7 UI 层 `aichat/ui/`

| 文件 | 关键内容 | 说明 |
|------|---------|------|
| `main_window.py` | `MainWindow` | 主窗+菜单+状态栏+_FlowWorker |
| `chat_view.py` | `MessageBubble`/`FlowCardWidget`/`finish_streaming` | 赛博朋克气泡+任务卡+流式元数据 |
| `md_render.py` | 自定义 Markdown→Qt | 代码块复制/表格/引用 |
| `styles.py` | `apply_skin`/`current_colors`/`is_light_theme` | cyberpunk 皮肤+YIQ 动态主题 |
| `memory_panel.py` | 记忆查看 | 记忆面板 |
| `session_sidebar.py` | 会话侧边栏 | 会话列表 |

---

## 五、数据流

### 5.1 一次对话的完整链路
```
用户输入 → SessionManager 加载会话
  → ChatAgent.retrieve_memory (metadata→树→向量→Rerank→回L2原文)
  → ChatAgent.chat:
      用户消息 commit_message (L1+L2)
      build_messages (system + 适配器context + 工作记忆)
      LLM stream_chat (后台线程)
      extract_and_run <|tool|> → 执行 skill → <|tool_result|>
      extract_memory <|memory|> → 干净正文 + 记忆项
      parse_flow_tag <|flow|> → FlowRun 存 metadata
      AI 消息 commit_message (L1+L2)
      记忆项逐条 commit_message (SYSTEM role)
  → UI 流式渲染 + 任务卡
```

### 5.2 FlowRun 记忆链路
```
用户确认任务卡 → _FlowWorker 逐步执行
  → FlowMemoryManager.commit_step (每步写 L1+L2)
  → finalize_tree (批量生成 L3 节点 + L4 向量)
  → mark_flow_expired (过期时三层同步标 is_expired)
```

---

## 六、扩展指南

### 6.1 新增 Skill
```python
@skill("my_tool", "描述", {"param": {"type": "string", "description": "..."}})
def my_tool(param: str, **_kw) -> dict:
    return {"ok": True, "result": ...}
# 末尾务必：TOOL_PROTOCOL_MD = _gen_protocol_md()
```

### 6.2 新增适配器
继承 `ChatAdapter`，实现 5 个方法，在 `adapters/__init__.py` 的 `_register_all()` 注册。

### 6.3 新增 FlowBlueprint
在 `flow.py` 注册蓝图，绑定 step+skill，AI 用 `<|flow|>` 填参，`FlowMemoryManager` 自动写记忆。
