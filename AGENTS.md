# AGENTS.md — AIChatStudio 一体化 App（coding agent 上手指南）

> **本文件是项目的权威上手源，专供 coding agent（Claude Code / Cursor / Codex / WorkBuddy / 任意 AI 编码工具）阅读。**
> 任何 AI 工具进入本目录后，读这一份即可无缝开工。
>
> **🔴 维护铁律（硬约束）**：本文件记录项目结构、架构约定、工作流与已踩过的坑。
> **每次代码结构 / 约定 / 工作流变更，必须同步更新本文件。**
> 新增功能、改路径、改约定、修了某个坑，都要回写这一份——否则下个 agent 会踩你踩过的坑。
>
> **🔴🔴 同步铁律（AI 协作硬约束）**：AI 改完代码 **不许自动 git push**。
> 每次改完必须告诉用户：改了哪些文件 / 当前 git 状态 / 是否同步？
> **只有用户明确说"同步"/"push"才执行 `git push origin main`**。

---

## 标准工作流程（Agent 操作闭环）
> 任何 coding agent 接手本仓库，按此闭环操作，**不要跳步**。

0. **先读项目简报（MemSync，跨软件共享记忆）**：用户一提起某个项目，按「先看清单、再读简报」两步：
   - 看清单：MCP `mem_projects`（= CLI `python3 scripts/memsync.py projects`）→ `project_id / 显示名 / 别名 / 记忆条数`
   - 读简报：MCP `mem_project_brief(ref="显示名或别名")`（= CLI `memsync.py brief <别名> --refresh`）
   - **按名字查是正路**：`ref` 接受 project_id / 显示名 / 别名。**不要只拿 cwd 去 resolve**——目录不在项目内会返回
     `registered:false`，那只是"这个目录没注册"，**不代表项目不存在**（真实事故：某工具因此判定"学习项目没注册"）。
   - `mem_search` 只匹配记忆正文，**不匹配项目名/别名**；想按名字找项目用 `mem_projects` / `brief` / `resolve(ref=)`。
   - 别名全局唯一：注册时若名字/别名已被别的项目占用，`register` 会直接拒绝（别再抢名；2026-10-27 起强制）。
   - 收尾用 `mem_note(source=<工具名>)` 写回决策/坑，再刷新简报；否则别的软件看不见。
   - ⚠️ MCP 工具清单在**连接建立时**协商：改过 `core/memory/mcp_server.py` 后，客户端必须**重连 MCP**
     才看得到新工具（2026-10-27 新增 `mem_projects` 即属此情况）。

1. **开工前先读真实代码**：定位目标文件再改，**不靠记忆**。遵循「架构约定」(§3)；若改动涉及结构/路径/约定 → **必须同步改本文档**（见第 5 步）。
2. **改动中**：小步提交，每完成一个逻辑单元就 `commit`。
3. **改动后必做验证（不靠代码推断）**：
   - 语法：`find /Users/chenzhichao/AIChat -name "*.py" | xargs python3 -m py_compile`
   - 启动冒烟：`cd /Users/chenzhichao/AIChat && python3 main.py`，确认窗口能起来、不闪退。
   - 改 `tools.py` 后确认文件末尾有 `TOOL_PROTOCOL_MD = _gen_protocol_md()`（skill 追加后必须重新生成，否则新 skill 不进 system prompt）。
   - 改 `memory/*` 后确认四层链路：L1 内存→L2 SQLite 追加→L3 树形→L4 向量，且 `is_expired` 标记生效。
   - 临时验证脚本写项目根、跑完即删，不入库。
4. **提交**：`git add . && git commit -m "有意义描述"`。
5. **回写本文档（维护纪律）**：任何结构/约定/工作流变更 → 同步更新本文档；新增的坑/验证方法补进对应小节（§7）。

## 0. 一句话定位
一体化桌面 App（PyQt6 + 浅色主题），整合 5 个创作/工作域（chat 聊天记忆 / novel 小说 / studio 短片 / warehouse 数仓建设 / analysis 数据分析），共享平台内核 core/（工作区契约 / LLM 客户端 SPI / 加密配置 / 提案闸门 / 守卫注册 / DAG 原语 / 向量 SPI / 提示词仓库）。作家陈志超维护，Mac + Win 双系统。原 5 个独立软件（AIChat / NovelForge / data-analyst-tool / 短片 PRD / 数仓 PRD）合并到此仓库，原仓库继续保留作备份。

## 0.1 分支地图（硬约束）
| 分支 | 含 | 用途 |
|---|---|---|
| `main` | core + shell + 5 域 + prompts + pack + docs | 个人机全量部署 |
| `chat-warehouse` | core + shell + chat + warehouse | 公司机/工作机最小部署（无创作域） |

- 全量拉取：`git clone git@github.com:xinyuchuluo/AIChat.git`
- 工作机最小集：`git clone -b chat-warehouse git@github.com:xinyuchuluo/AIChat.git`
- 同步分支（main 有更新后）：`./pack/sync-branch.sh`（合并 main → 删 novel/studio/analysis → push）

## 1. 目录与数据位置（铁律）
- **软件目录**：`/Users/chenzhichao/AIChat/`（仓库根）。
- **工作区根**：`~/AichatStudio/`（Mac）/ `%USERPROFILE%\AichatStudio\`（Win）。
  - 首次启动自动创建 `~/AichatStudio/default/` 默认工作区
  - 多工作区：每个 workspace.json 声明启用哪些域
  - 公司机用 chat-warehouse 分支，workspace.json 的 enabled_domains 只列 `["chat", "warehouse"]`
- **历史坑**：旧数据目录 `~/AIChat_Data/` 已废弃（原样保留作冷备份，不迁移、不再写入）；旧 `~/.aichat/` 也废弃。**禁止再回到旧目录**。
- 工作区目录内容：`workspace.json`、`config.enc`（AES 加密 API Key）、`secret.key`、`chat/`、`novel/`、`studio/`、`warehouse/`、`analysis/`。
  - chat 域数据：`<workspace>/chat/sessions/`（会话 JSON）、`<workspace>/chat/config.json`。
  - **记忆库不再放工作区**：统一落共享档案 `~/AichatStudio/_memory/<profile>/`（personal / company 物理隔离），chat 域与 MemSync MCP/CLI 同库。首次升级会把旧 `<workspace>/chat/memory/` 的库原样接管进档案，旧目录不再写入。
- **不入库**：`~/AichatStudio/` 整个不入库（用户数据私有）；`config.enc` 和 `secret.key` 即使复制到仓库也不入库（`.gitignore` 排除）。

## 2. 目录与模块结构（精确路径）
```
AIChat/                          ← 仓库根（即新的一体化 App 根目录）
├─ main.py                       ← 【入口】python3 main.py
├─ requirements.txt              ← 跨平台依赖（不含 chromadb，含 sqlite-vec）
├─ AGENTS.md                     ← 本文件
├─ README.md                     ← 分支地图 + 同步纪律
├─ .gitignore                    ← 排除工作区数据 + config.enc + secret.key
├─ core/                         ← 【平台内核】写一次，五域共用
│  ├─ __init__.py
│  ├─ workspace.py               ← 工作区契约 + 多工作区 + 声明启用域
│  ├─ llm_client.py              ← LLM SPI + 步骤级 profile 路由 + 客户端缓存
│  ├─ config_vault.py            ← AES Fernet 加密配置存储
│  ├─ proposal_gate.py           ← 提案→闸门→权威（三档严格度）
│  ├─ guard_registry.py          ← 零 token 守卫注册框架
│  ├─ graph_primitives.py        ← 关系/事件/DAG 原语
│  ├─ vector_spi.py              ← 向量检索 SPI（sqlite-vec/numpy/BM25 三级降级）
│  ├─ memory/                    ← 【记忆内核】四层记忆后端，与域解耦、可被外部软件复用
│  │  ├─ types.py                ← MemoryRecord/Query/Result/TreeNode + observation_type
│  │  ├─ raw_store.py            ← L2 SQLite 只追加 + FTS5(trigram) + fact_slots 槽位
│  │  ├─ tree_store.py           ← L3 树节点 + 摘要 FTS5
│  │  ├─ vector.py              ← L4 向量索引（vector_spi；无 embedder 自动关闭）
│  │  ├─ retrieve.py            ← 检索链路（FTS5 双路 + 可选向量 → 槽位覆盖 → 回查 L2）
│  │  ├─ working.py             ← L1 工作记忆（纯内存，不参与检索排序）
│  │  ├─ engine.py              ← MemoryEngine 编排
│  │  ├─ paths.py               ← 档案路径（personal/company 物理隔离 + 旧库接管）
│  │  ├─ project_store.py       ← projects.db 项目注册表 + cwd 最长路径匹配
│  │  ├─ brief.py               ← PROJECT_MEMORY.md 人工段 + 自动段组装/刷新
│  │  └─ mcp_server.py          ← MemSync MCP server（stdio JSON-RPC，只读 + 可选写入）
│  └─ prompt_repo.py             ← 提示词仓库（Jinja2 + 版本号 + 可回滚）
├─ shell/                        ← 【一体化壳】
│  ├─ __init__.py
│  ├─ main_window.py             ← 主窗口 + 域面板挂载点
│  ├─ session_hub.py             ← 会话 + 任务卡中心（底部 dock）
│  ├─ window_manager.py          ← 多窗口/模块拉起
│  ├─ workspace_switcher.py       ← 工作区切换器（顶部）
│  └─ styles.py                  ← 浅色主题 QSS（全局唯一，各域不许改）
├─ domains/                      ← 五个域
│  ├─ chat/                      ← 聊天记忆域（来自原 aichat/ 子目录）
│  │  ├─ memory/                 ← 已删除：记忆实现在 core/memory/（域不再自带记忆层）
│  │  ├─ protocol/               ← ChatAdapter + types + events
│  │  ├─ session/                ← ChatAgent + SessionManager + Worker
│  │  ├─ adapters/               ← GenericAdapter（其他域通过 Facet 接管，不再走 adapter）
│  │  └─ widgets/                ← chat_view / session_sidebar / memory_panel / x_browser_panel
│  ├─ novel/                     ← 小说域（来自 NovelForge，先清场 attic/ 分支再 copy）
│  ├─ studio/                    ← 短片域（PRD 新写，PyQt，无 LLM，5 表 + 5 模板）
│  ├─ warehouse/                 ← 数仓建设域（PRD 新写，4 模块 + 4 守卫）
│  └─ analysis/                  ← 数据分析域（来自 data-analyst-tool）
├─ prompts/                      ← 提示词作为数据（用户可编辑，Jinja2 + 版本号）
│  ├─ chat/                       ├─ novel/   ├─ studio/   ├─ warehouse/   └─ analysis/
│  └─ .versions/                 ← 旧版本归档（可回滚）
├─ pack/                         ← 打包脚本
│  ├─ sync-branch.sh             ← chat-warehouse 分支同步脚本
│  ├─ mac.spec                    ← PyInstaller Mac（待写）
│  └─ win.spec                    ← PyInstaller Win（待写）
├─ scripts/                      ← 独立组件启动器（不依赖 PyQt）
│  ├─ memsync_mcp.py             ← MemSync MCP server（给 Trae / Codex / WorkBuddy 配）
│  └─ memsync.py                 ← MemSync CLI（resolve/register/brief/note/search/timeline）
└─ docs/                         ← 原 AIChat 文档（AICHAT_DOC.md / 启动方式速查 / 等）
```
- **import 根 = 项目根目录**：`from core.xxx import ...`、`from shell.xxx import ...`、`from domains.chat.xxx import ...`。运行 `python3 main.py` 时 cwd 必须等于 `/Users/chenzhichao/AIChat`（`main.py` 已 `sys.path.insert` 兜底）。
- **内核纪律（硬约束）**：
  1. 内核不许 import 任何域（`core/` 下任何文件不得 `from domains.*`）
  2. 域只许 import 内核（`from core.xxx import`）；域之间不许互相 import
  3. 提示词/背景/模板全部走 `prompts/` 数据文件，**不许硬编码进 .py**
  4. 任何 LLM 产出不得直接落库，必须经 `core/proposal_gate` 提案→闸门→权威通道
- 不强制 venv：依赖装到系统 Python（3.10+）。安装：`pip install -r requirements.txt`。

## 3. 核心架构约定（改代码前必读）

### 3.1 四层记忆架构（硬约束，不可违背）
> 实现位置：`core/memory/`（内核，不 import 任何域）。chat 域只调用，不自带记忆实现。
> 目标：可被其他软件复用；只读检索（search / timeline / get）对外开放，写入仅本 App、导入器或显式授权的 MemSync。

- **L1 工作记忆**：`core/memory/working.py`，每会话独立，**纯内存，无持久化**。只服务当轮上下文组装，**不参与检索排序**（近期消息不得压过长期事实）。
- **L2 SQLite 原始真相库**：`raw_memories.db`。**`content` 只追加，永不修改**。业务事实回查必须回 L2 取原文。
- **L3 树形记忆**：`tree_memories.db`。**异步批量生成**，由 N 条未归纳记录/话题切换触发（`summarize_threshold` 默认 10），**禁止每轮调 LLM**。当前只生成 `leaf`，mid/root 暂留 schema 不实现（用户决定：上线有问题再改）。
- **L4 向量索引**：`vector_index.db`，走 `core/vector_spi.py`（sqlite-vec → numpy → BM25）。**只存树节点摘要 + metadata，用于召回 UUID**，不存业务原文。**已移除 ChromaDB**；无 embedder 时本层自动关闭，检索退化为 FTS5 关键词。
- **检索链路（固定顺序，`core/memory/retrieve.py`）**：metadata 过滤 → L2 原文 FTS5 → L3 摘要 FTS5 展开 UUID → L4 向量展开 UUID（可选）→ 槽位覆盖 → 回查 L2 原文 → 按分数取 top_k。
- **观察类型**：`observation_type` 区分 `fact/preference/entity/reminder`（状态型，写 `fact_slots` 槽位表，检索只认当前值，旧记录仍留 L2）与 `decision/bugfix/event/flow_step/chat`（时间线型，只追加）。
- **过期机制**：用 `is_expired` 布尔标记，**不物理删除**。L2/L3/L4 三层都要同步标记。
- **中文检索**：FTS5 `tokenize='trigram'`；老版本 SQLite 自动降级 `unicode61` 或 LIKE。

### 3.1.1 MemSync：跨软件项目记忆（双轨）
> 目的：在 AIChat / Trae / Codex / WorkBuddy 之间切换时，agent 能直接知道项目定位、已定决策与进度；**拉取式，无守护进程，不打扰写代码节奏**。

- **双轨存储**：
  - 仓库内 `PROJECT_MEMORY.md`：人工段（永不被覆盖）+ 自动段（夹在 `<!-- memsync:auto:begin/end -->` 之间，由库里事实/决策生成，≤200 行），随 git 走，跨机同步。
  - 本地档案库：`~/AichatStudio/_memory/<profile>/`，历史与检索；同机即时共享。**不做实时协同**，跨机靠 git + 手动拷库。
- **档案隔离**：`personal`（默认）/ `company`。`work`、`company` 工作区的 `workspace.json` 写 `"memory_profile": "company"`，公司数据不进个人档案。
- **项目识别**：`projects.db` 注册表 + **cwd 最长路径匹配**（`project_store.resolve`）。**不在别人仓库里加识别文件**（除简报 PROJECT_MEMORY.md 外）。
- **scope 约定**：项目记忆统一 `project:<project_id>`；chat 域仍按 adapter 生成 scope（如 `novelforge:<项目>`），全库检索不受影响。
- **写入纪律**：只 `append`，带 `source`；同槽位（fact/preference/entity/reminder）新值覆盖旧值但旧记录留 L2。MCP 默认**只读**，启动加 `--allow-write` 才开放 `mem_note` / `mem_project_register`。
- **MCP 工具**：`mem_project_resolve` → `mem_project_brief`（先读这两步）→ `mem_search` / `mem_timeline` / `mem_get` / `mem_note` / `mem_project_register`。
- **客户端配置示例**（Trae / Codex / WorkBuddy 任选）：
  ```json
  {"mcpServers": {"memsync": {"command": "python3",
    "args": ["/Users/chenzhichao/AIChat/scripts/memsync_mcp.py", "--allow-write"]}}}
  ```
  只读部署去掉 `--allow-write`；公司档案加 `--profile company`。
- **豆包**：无 MCP / 会话接口，只走离线粘贴或导出导入（标 `import:doubao`），不做浏览器抓取。
- **AIChat 客户端行为**：读 `PROJECT_MEMORY.md` 人工段 + 读库；聊天写 L2；自动段由 CLI/MCP 刷新，**不擅自改人工段**。优先级：人工段 > 库中最新有效事实 > 历史。

### 3.2 聊天流水线（`chat/agent.py` ChatAgent.chat）
固定顺序，**不要乱序**：
1. 用户消息入 L1+L2（`commit_message`，metadata 带 adapter key）
2. 组装 messages = system_prompt + 适配器 context（注入记忆）+ 工作记忆
3. LLM 流式调用（`stream_chat`），逐 chunk yield
4. `extract_and_run`：解析 `<|tool|>` → 执行 skill → 拼回 `<|tool_result|>`
5. `extract_memory`：剥离 `<|memory|>` → 用户只见干净正文，记忆项逐条入记忆引擎
6. `parse_flow_tag`：剥离 `<|flow|>` → FlowRun 存入 message metadata，UI 渲染任务卡
7. AI 消息入 L1+L2（metadata 带 tool_calls/memory_items/flow_run）
8. 记忆项按 type(fact/preference/entity/tree_node/reminder) 构造 SYSTEM 消息写入记忆
9. 适配器 `on_response` 回调

### 3.3 Skill 功能池（`tools.py`）
- 注册模式：`@skill(name, description, params_schema, category)` 装饰器 → 进 `SKILL_REGISTRY`。
- 协议：LLM 用 `<|tool| name="xxx">JSON</|tool|>` 调用，宿主 `extract_and_run` 执行后返回 `<|tool_result| name="xxx"><![CDATA[...]]></|tool_result|>`。
- **铁律**：任何 skill 追加后，**必须在文件末尾重新生成** `TOOL_PROTOCOL_MD = _gen_protocol_md()`，否则新 skill 不进 system prompt（已踩坑：NovelForge skill 曾因追加在生成之后导致 LLM 不知道）。
- 安全沙箱：`_ALLOWED_ROOTS = [home, /tmp, cwd]`；危险命令正则 `_DANGEROUS_RE` 拦截 `rm -rf /`、`mkfs`、`dd of=/dev/`、`shutdown` 等；`python_eval` 禁 `import os/subprocess`、`open()`、`exec/eval`。

### 3.4 适配器（`adapters/`）
- 抽象基类 `ChatAdapter`：`build_system_prompt` / `build_context_messages` / `get_scope` / `get_memory_filters` / `on_response`。
- `GenericAdapter` 注入 `TOOL_PROTOCOL_MD` + `MEMORY_PROTOCOL_MD` 进 system prompt。
- `NovelForgeAdapter` / `DataAnalysisAdapter` 通过 `set_novelforge_adapter` / `set_data_analysis_adapter` 注入运行时实例，使 read_chapter / load_spreadsheet 等 skill 能访问项目数据。
- 注册：`adapters/__init__.py` 的 `_register_all()`，`register_adapters(window, config)` 向 UI 注入可选项。

### 3.5 FlowBlueprint / FlowRun（`flow.py`）
- **FlowBlueprint** = 静态模板，预定义步骤骨架（绑定 skill + 分支点位），AI 只能填参数，**不能自由生成流程**。
- **FlowRun** = 推演实例（任务卡）。AI 用 `<|flow|>JSON</|flow|>` 携带，用户确认后按步执行。
- `FlowMemoryManager`：FlowRun 每步结果写 L1+L2，完成后 `finalize_tree` 批量生成 L3 节点 + L4 向量。`mark_flow_expired` 三层同步标过期。

### 3.6 UI 约定（`ui/`）
- 赛博朋克皮肤：霓虹光晕、渐变滚动条、发光气泡、脉冲思考点、打字光标。
- 动态主题：`styles.py` 用 YIQ 亮度公式判断深浅，自动派生对比度达标的文字/背景/边框色。
- 气泡视觉层级：用户气泡 4px 青色渐变竖条 + `■ 你`(900)；AI 气泡 2px 紫青渐变竖条 + `● CORE AI`(700)；时间戳 9.5px 浅灰。
- 能力标签：`🌐 联网搜索` `🖼 支持看图` `📁 支持上传文件` `🛠 调用工具` `💻 执行代码`。
- 流式消息：`finish_streaming()` 提交时**强制注入** model badge + 能力标签 metadata（曾因未注入导致缺失，已修）。

## 4. LLM 调用约定
- OpenAI 兼容接口，参数从 `Config` 提取（model/temperature/max_tokens/api_key/base_url）。
- 模型设置窗口含 8 个官方服务商预设，自动填 Base URL + 模型列表 + 控制台超链接；能力勾选框（联网/多模态/文件/工具/代码执行）按服务商+模型自动勾。
- 记忆检索 + LLM 调用**必须**在后台线程 `_SendWorker` 跑，防 UI 冻结。

## 5. 内置 Skill 清单（`tools.py`）
| 类别 | Skill | 说明 |
|------|-------|------|
| shell | exec_shell | 跑 shell（白名单+超时+危险拦截+截断） |
| file | find_files | glob + 内容 grep |
| file | read_file | 读文件（行号范围+二进制检测） |
| file | write_file | 写文件（白名单+敏感目录拦截） |
| file | list_directory | 列目录（ls -la 风格） |
| web | web_fetch | 抓 URL（HTTP GET + 去 HTML） |
| code | python_eval | 执行 Python（沙箱+危险关键字拦截） |
| system | get_system_info | 系统信息（OS/CPU/内存/磁盘，只读） |
| novel | read_chapter | 读小说章节正文 |
| novel | search_novel | 全本关键词检索 |
| novel | list_characters | 列人物档案 |
| novel | list_foreshadows | 列伏笔 |
| novel | check_consistency | 一致性检查 |
| novel | get_outline | 取章节大纲 |
| data | load_spreadsheet | 加载 Excel/CSV |
| data | run_pandas | pandas 脚本执行 |
| data | data_summary | 统计摘要 |
| data | generate_report | 生成 HTML 报告 |

## 6. 验证方法
- 语法：`find /Users/chenzhichao/AIChat -name "*.py" | xargs python3 -m py_compile`
- 启动：`cd /Users/chenzhichao/AIChat && python3 main.py`
- 记忆链路：发一条消息 → 检查 `~/AichatStudio/_memory/<profile>/raw_memories.db` 有新记录 → 凑够 `summarize_threshold` 条 → 同目录 `tree_memories.db` 生成节点。旧 `<workspace>/chat/memory/` 只在首次升级时被接管，不再写入。
- MemSync 冒烟：`python3 scripts/memsync.py register <项目>` → `resolve` 嵌套目录命中、前缀相似目录不误匹配 → `brief --refresh` 自动段含最新决策且人工段不变 → `search 中文词` 命中 → MCP 只读模式 `mem_note` 返回 `isError`。
- 中文检索冒烟：`MemoryEngine(dir).search("中文关键词", scope="chat")` 应返回命中；状态型记忆同 `key` 只返回最新值。
- 工具：让 AI 调 `get_system_info`，确认返回 JSON 且气泡显示 `<|tool_result|>`。
- 记忆提取：让 AI 回复带 `<|memory| type="fact">{"content":"测试"}</|memory|>`，确认用户侧看不到该标签、且记忆库有 `[记忆·fact]` 记录。

## 7. 已踩过的坑（避免重复）
- QSS / `_DANGEROUS_RE` 中单 `}` 在 f-string 里报语法错 → QSS 用 `}}` 转义。
- Markdown 渲染器 `md_render.py` 正则闭括号不匹配 → 用 `r"\[([^\]]+)\]\((https?://[^\s)]+)\)"`。
- NovelForgeAdapter `c.get("description", "")[:60]` 当 description 为 None 切片报错 → 改 `(c.get("description") or "")`。
- NovelForge skill 追加在 `TOOL_PROTOCOL_MD` 生成之后 → LLM 不知道有 read_chapter。**任何 skill 追加后必须重新生成协议文档**。
- `SQLiteStore.mark_expired` 曾缺 `conn.commit()` → 过期标记不生效，已补。
- 流式消息缺 model badge/能力标签 → `finish_streaming()` 强制注入 metadata。
- macOS TCC 沙箱：从 IDE/Terminal 启动写入外部目录报 `[Errno 1] Operation not permitted` → 引导用户从 Finder 双击 `AIChat.command` 启动；写文件用原子写（`.tmp_{pid}` → fsync → `os.replace`）。
- **MemSync 别名被抢 → 项目"看起来没注册"**：新项目 StudyLab 注册时带了 `学习项目,学习实验室`，把 Study 的别名抢走，
  之后 `brief 学习实验室` 指向那个零记忆的新项目，客户端就报"没找到记录"。**别名是项目定位的唯一起点，必须全局唯一**：
  `ProjectStore.register()` 现已做冲突检测（`_name_conflicts`），CLI/MCP 都会拒绝并列出占用者。
- **MCP `mem_project_resolve` 曾只认 cwd**：调用方不在项目目录里就得到 `registered:false`，被误读成"没注册"。
  现已支持 `ref`（id/显示名/别名）；同时补了 `mem_projects` 列清单工具（此前只有 CLI 有 `projects`，MCP 无从发现名字）。

## 9. 密钥统一管理（凭证中心）

🔴 硬约束：API Key 全系统只存一份 —— AIChat 工作区 vault `<workspace>/config.enc`（AES Fernet）。

- 模块：`core/credential_hub.py`（`get/set_api_profiles`、`get/set_step_routing`、`ensure_migrated`）。
- 禁止：任何域/独立软件自建明文 key 或独立 enc。novel 旧明文 `domains/.novelforge/config.json` 已改为本地只留非敏感调参且 api_key 留空；analysis `~/.data_analyst` 本地 enc 不再含 api_profiles。
- 迁移：`ensure_migrated()` 幂等，首启自动跑；旧 key 并入 vault 后原文件备份 `.bak`。
- studio / warehouse 不调 LLM，不消费 key，但同属体系。
- 本次改动文件：`core/credential_hub.py`(新)、`domains/novel/config.py`(load 取 vault / save 写 vault+本地留空)、`domains/analysis/core/config_manager.py`(load 合并 / save 拆分)、`main.py`(启动触发迁移)、`.gitignore`(忽略 `.novelforge/`)。

## 8. 待办跟进
跨会话待办写进本文件 §8 或 memory。新增任务直接在此加 `- [ ] xxx`。
