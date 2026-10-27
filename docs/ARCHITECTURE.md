# AIChatStudio 架构说明

> 三层架构：core（内核）→ shell（壳）→ domains（5 个域）。
> 内核不许 import 域，域只许 import 内核，域之间不能互相 import。

## 总览

```
┌─────────────────────────────────────────────────────────┐
│  shell/  一体化壳                                        │
│  ├── main_window.py   侧边栏 + Tab + 域挂载 + 工作区切换  │
│  ├── styles.py        浅色主题 QSS                       │
│  ├── session_hub.py   会话中枢                           │
│  ├── window_manager.py 多窗口管理                        │
│  └── workspace_switcher.py 工作区切换器                  │
├─────────────────────────────────────────────────────────┤
│  core/  平台内核（8 服务，所有域共享）                    │
│  workspace / llm_client / config_vault / proposal_gate  │
│  guard_registry / graph_primitives / vector_spi / prompt_repo │
├──────────────┬──────────────┬──────────────┬────────────┬──────────┤
│  chat        │  novel       │  studio      │  warehouse │ analysis │
│  聊天记忆    │  小说创作    │  短片制片    │  数仓管控   │ 数据分析 │
│              │              │              │             │          │
│  四层记忆    │  实体档案    │  5 表 CRUD   │  8 表 DAG  │ 6 步流水线│
│  ChatAdapter │  RAG+摘要    │  @资产正则   │  4 守卫     │ AES profile│
│  off 闸门    │  medium 闸门 │  off 闸门    │  strict 闸门│ off 闸门 │
└──────────────┴──────────────┴──────────────┴────────────┴──────────┘
```

## 1. core/ 平台内核（8 服务）

### 1.1 workspace — 工作区管理

**职责**：多工作区、声明启用域、数据物理隔离。

**关键概念**：
- 工作区 = 一个目录（`~/AichatStudio/<name>/`），包含该工作区的所有数据
- 工作区元数据声明启用哪些域（`enabled_domains`）
- 切工作区 = 切数据目录 + 切可见域
- 公司机装同一个 App，工作区指向数仓目录，小说/聊天的工作区不跟着走

**API**：
```python
from core.workspace import ensure_default, current, list_workspaces, switch
ws = ensure_default()        # 首次启动创建 default 工作区
ws = current()               # 获取当前工作区
ws.root                      # 工作区根目录 Path
ws.enabled_domains           # 启用的域列表 ["chat","novel",...]
ws.enabled_domains()         → list[str]
```

### 1.2 llm_client — LLM 客户端

**职责**：OpenAI 兼容接口 + 步骤级 profile 路由。

**关键概念**：
- Profile = 一组 LLM 配置（name, base_url, model, api_key）
- 步骤级路由 = 不同步骤（规划/生成/校验）可配不同模型
- API Key 走 config_vault 加密存储

**API**：
```python
from core.llm_client import init, LLMProfile, call
profiles = [LLMProfile(name="deepseek", base_url=..., model=..., api_key=...)]
init(profiles, routing_data)
resp = call(messages, profile_name="deepseek")
```

### 1.3 config_vault — 加密配置存储

**职责**：AES Fernet 加密配置（API Key 等）。

**关键概念**：
- 密钥文件 `secret.key` 存在工作区根目录
- 配置文件 `config.enc` AES 加密
- 首次启动自动生成密钥

**API**：
```python
from core.config_vault import for_workspace
vault = for_workspace(ws.root)
vault.set("api_profiles", [...])
vault.get("api_profiles", default=[])
```

### 1.4 proposal_gate — 提案→闸门→权威落库

**职责**：统一写入通道，防 AI 幻觉。

**三档严格度**（按域配置）：

| 档位 | 行为 | 适用域 |
|---|---|---|
| `off` | 人写的直接权威；AI 产出也直接入库 | chat / studio / analysis |
| `medium` | 人写的直接权威；AI 推断的实体/设定/正文变更进 pending 卡 | novel |
| `strict` | 全部草稿，人工审核才入库 | warehouse |

**流程**：
```
写入请求 → source 标签（human/ai/parser）
  ├─ human + 闸门通过 → 直接权威入库
  ├─ ai/parser + 闸门通过 → pending 任务卡 → 人点确认 → 升格权威
  └─ 闸门拦截 → 驳回
```

**蓝本**：NovelForge 已有的 proposal + power_canon 是同构实现，迁移时抽象到内核。

### 1.5 guard_registry — 零 token 守卫注册框架

**职责**：域注册守卫，写入前跑纯 Python 校验。

**关键概念**：
- 守卫 = 零 token 纯 Python 函数（不调 LLM）
- 域注册自己的守卫（如 warehouse 的循环依赖检测）
- proposal_gate 在入库前自动跑所有注册的守卫

**现有守卫**（warehouse 域注册）：
1. 循环依赖检测（DAG 成环驳回）
2. 反向分层检测（Source→ODS→DWD→DWS→ADS 单向）
3. 多对多强制说明（无业务原因/去重方案禁止审核）
4. 字段术语冲突检测（同名不同义、同义不同名告警）

### 1.6 graph_primitives — 知识图库原语

**职责**：通用图存储（节点 + 类型化边 + 多跳遍历），四域共用一套 `nodes` + `edges` 表，按 `domain` 字段物理隔离。原本只有边集合（DAG 原语），已升级为完整知识图库。

**核心能力**：
- `Node`（id/type/label/properties）+ `Edge`（src/dst/edge_type/properties），均支持 JSON 属性
- 节点 CRUD：`add_node`（可指定稳定 id，如 `char:张三`）/ `get_node` / `update_node` / `remove_node` / `list_nodes(type)` / `find_nodes(label)`
- 遍历：`neighbors(vertex, direction, edge_types)`（一层邻居，默认去重不含自身）、`traverse(start, max_depth, direction)`（**递归 CTE 多跳**，零依赖）、`subgraph(starts, max_depth)`（提取子图用于可视化/导出）
- 不变量校验：`has_cycle` / `topological_order`（向后兼容 DAG 用法）

**复用场景**：
- studio 域：`shot_asset_link`（分镜↔资产关联）
- novel 域：`depends_on`（章节依赖）
- warehouse 域：表血缘 DAG（Source→ODS→DWD→DWS→ADS）

**各域未来接入点（待实施）**：
- novel 域：把实体档案（人物/地点/物品/事件）从纯 RAG 向量升级为**实体关系图**，支撑关系问答与一致性校验（`neighbors` / `traverse` 直接可用）
- warehouse 域：血缘 DAG 升级为**带属性/类型的血缘图**（节点=表/字段，边=血缘）
- 跨域：用稳定节点 id 把各域实体连成一张**跨域知识图**，全局检索

### 1.7 vector_spi — 向量 SPI

**职责**：向量存储 SPI，三级降级。

**降级链**：
```
sqlite-vec（原生 C 扩展，快）→ numpy（纯 Python，中等）→ BM25（关键词，兜底）
```

**为什么放弃 ChromaDB**：
- C++ 依赖在 Windows 上不稳
- PyInstaller 打包体积大
- NovelForge 已有 sqlite-vec→numpy→BM25 三级降级，升级到内核即可

### 1.8 prompt_repo — 提示词仓库

**职责**：提示词作为数据文件，不硬编码进 .py。

**关键概念**：
- 域注册模板（`prompts/<domain>/*.j2`，Jinja2 占位符）
- 用户在 `prompts/` 目录编辑实际内容，覆盖模板
- 运行时按 domain + scene 取最终 prompt，带版本号，可 git 回滚
- 回应用户诉求"提示词会不断优化、不是软件生成的"

**AIChat 现有欠账**：`adapters/novelforge.py L60-109` 的 system prompt 硬编码在 .py 里 → 迁移时抽到 `prompts/chat/system.j2`。

## 2. shell/ 一体化壳

### 2.1 main_window.py — 主窗口

**职责**：侧边栏 + Tab + 域挂载 + 工作区切换。

**启动流程**：
```
main.py
  → styles.apply_theme(app, "light")      # 浅色主题
  → workspace.ensure_default()            # 创建工作区
  → llm_client.init(profiles, routing)    # 内核单例
  → prompt_repo.init(prompts_root)        # 提示词仓库
  → load_domains()                        # 加载域清单
  → MainWindow().show()                   # 显示壳
```

**域挂载机制**：
```python
# domains/<name>/__init__.py
from shell.main_window import DomainManifest, register_domain

register_domain(DomainManifest(
    key="chat",
    display_name="聊天记忆",
    widget_factory=_build_main_window,     # 无参函数，返回 QWidget
    supports_standalone_window=True,
))
```

**侧边栏**：列出当前工作区 `enabled_domains` 的所有域，点击 → `open_domain_tab` → 调 `widget_factory` → 加 Tab。

### 2.2 styles.py — 浅色主题

**色板**（对齐 chat 域 breeze 皮肤）：
- 背景 `#fbfdff`，卡片 `#FFFFFF`
- 主文字 `#0f172a`，次文字 `#475569`
- 品牌色 `#10b981`（focus，不大面积铺）

**字体 fallback 链**：
```css
"PingFang SC", "Helvetica Neue", "Microsoft YaHei",
system-ui, -apple-system, "Segoe UI", Roboto, sans-serif
```
Mac 命中 PingFang SC，Win 命中 Microsoft YaHei。

**纪律**：各域不许改全局 QSS，只能改自己 widget 内的局部样式。

## 3. domains/ 5 个域

### 3.1 chat — 聊天记忆域

| | |
|---|---|
| 来源 | 原 AIChat |
| 记忆 | 四层（raw_memories → tree_nodes → vector → working） |
| 闸门 | off（消息即真相） |
| 适配器 | ChatAdapter 协议（system prompt + tools 回调） |
| 已有欠账 | mid/root 树归纳从未实现、异步批量是同步内联、检索链树形筛选没接上、ChromaDB 硬依赖 → 迁移时补 |

### 3.2 novel — 小说创作域

| | |
|---|---|
| 来源 | NovelForge（清场后，73 .py） |
| 记忆 | 实体档案库（八张表）+ RAG + 摘要 |
| 闸门 | medium（AI 推断的实体/设定/正文变更进 pending 卡） |
| 守卫 | 六层零 token 守卫 + 写库闸门 + 假目标 id 过滤 |
| 清场 | 包根目录 13 个 regen/fix 一次性脚本 + 3 个 backup 目录未迁入 |

### 3.3 studio — 短片制片域

| | |
|---|---|
| 来源 | PRD 新写 |
| LLM | 无（PRD §六红线：不内置 LLM，不联网调 AI） |
| 闸门 | off |
| 表 | 5 张：projects / project_assets / asset_materials / storyboards / shot_asset_link |
| @资产 | 正则 `@([^\s，。；！？]+)` + 就地新建弹窗 |
| 导出 | 5 套模板：Markdown / DrawThings / Blender / Seedance / TTS |
| 状态 | Tab1 项目列表完整；Tab2/3/4 占位（后续迭代） |

### 3.4 warehouse — 数仓管控域

| | |
|---|---|
| 来源 | PRD 新写 |
| 闸门 | strict（全部草稿，人工审核才入库） |
| 表 | 8 张：terms / source_tables / source_columns / lineage_tables / lineage_columns / join_relations / draft_lineage / audit_log |
| 守卫 | 4 个零 token 守卫（循环依赖/反向分层/多对多/术语冲突） |
| SQL 解析 | sqlglot（可选，未装时 Tab 仍可打开） |
| UI | 双栏（权威 §7.1 / 草稿 §7.2 强制二分） |

### 3.5 analysis — 数据分析域

| | |
|---|---|
| 来源 | data-analyst-tool |
| 流水线 | 6 步：load_spreadsheet → intent → pandas_script → execute → stats → report |
| 加密 | AES 加密 API Key + 步骤级 profile 路由 |
| 闸门 | off（分析结果就是结果） |
| 报告 | HTML + ECharts |
| 兼容 | 全部文件加 `from __future__ import annotations`（Python 3.9） |

## 4. 数据流

```
用户启动 App
  → main.py
    → workspace.ensure_default()           # ~/AichatStudio/default/
    → config_vault.for_workspace(ws.root)  # 读加密配置
    → llm_client.init(profiles, routing)   # 初始化 LLM
    → prompt_repo.init(prompts_root)       # 加载提示词模板
    → load_domains()                       # import domains/<name>/__init__.py
      → 每个域 register_domain()          # 注册 DomainManifest
    → MainWindow().show()                  # 侧边栏列 enabled_domains

用户点击侧边栏某域
  → _on_domain_click(item)
    → open_domain_tab(domain_key)
      → manifest.widget_factory()          # 调域的 _build_main_window
        → 返回 QWidget（如 MainWindow）
      → tabs.addTab(widget, display_name) # 加 Tab

用户切工作区
  → workspace_switcher
    → switch(new_workspace)
      → close all tabs
      → reload sidebar（列新工作区的 enabled_domains）
```

## 5. 权威/草稿二分（PRD §0.1）

**统一机制**：proposal_gate + source 标签

| 来源 | 标签 | 行为 |
|---|---|---|
| 人写的 | `human` | 走闸门，通过即权威 |
| AI 推断 | `ai` | 走闸门，通过进 pending 卡，人点确认升格 |
| SQL 解析 | `parser` | 走闸门，通过进 pending 卡，人点确认升格 |

**闸门严格度按域配置**：
- warehouse：全拦（strict）
- novel：只拦实体/设定/正文变更（medium）
- chat/studio/analysis：不拦（off）

**蓝本**：NovelForge 已有的 proposal + power_canon + task_cards.json 是同构实现。

## 6. 内核纪律（硬约束）

```
1. 内核不许 import 域
2. 域只许 import 内核
3. 域之间不能互相 import
4. 提示词不许硬编码进 .py（放 prompts/<domain>/*.j2）
5. LLM 产出必须走 proposal_gate → 闸门 → 权威落库
6. 各域不许改全局 QSS（shell/styles.py），只能改自己 widget 内局部样式
```

违反任何一条 = 架构破产，必须立即修。
