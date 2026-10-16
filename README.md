# AIChatStudio

> 一体化桌面 App，整合 5 个创作/工作域，共享平台内核 + 浅色主题壳。
> Mac + Win 跨平台，PyQt6 + SQLite，不依赖 ChromaDB。

## 快速启动

```bash
git clone git@github.com:xinyuchuluo/aichat.git
cd aichat
pip install -r requirements.txt
python3 main.py
```

首次启动自动创建工作区 `~/AichatStudio/default/`，侧边栏列 5 个域，点击即加载 Tab。

## 5 个域

| 域 | 侧边栏名 | 来源 | 重 LLM | 记忆 | 闸门 | DB 文件 |
|---|---|---|---|---|---|---|
| chat | 聊天记忆 | 原 AIChat | 中 | 四层（raw/tree/vector/working） | off | `~/AIChat_Data/` |
| novel | 小说创作 | NovelForge（清场后） | 重 | 实体档案 + RAG + 摘要 | medium | 每本书独立目录 |
| studio | 短片制片 | PRD 新写 | 无 | 无 | off | `<workspace>/studio.db` |
| warehouse | 数仓管控 | PRD 新写 | 中 | DAG/血缘 | strict | `<workspace>/warehouse.db` |
| analysis | 数据分析 | data-analyst-tool | 重 | 可选（对话历史） | off | `~/.data_analyst/` |

## 目录结构

```
aichat/
├── core/                    # 平台内核（8 服务，所有域共享）
│   ├── workspace.py         #   多工作区 + 声明启用域 + 数据物理隔离
│   ├── llm_client.py        #   OpenAI 兼容 + 步骤级 profile 路由
│   ├── config_vault.py      #   AES Fernet 加密配置存储
│   ├── proposal_gate.py     #   提案→闸门→权威落库（off/medium/strict）
│   ├── guard_registry.py    #   零 token 守卫注册框架
│   ├── graph_primitives.py  #   DAG/边集合/不变量校验
│   ├── vector_spi.py         #   向量 SPI（sqlite-vec→numpy→BM25 三级降级）
│   └── prompt_repo.py       #   提示词仓库（Jinja2 模板 + 版本 + 回滚）
├── shell/                   # 一体化壳
│   ├── main_window.py       #   侧边栏 + Tab + 域挂载 + 工作区切换
│   ├── styles.py            #   浅色主题 QSS（LIGHT_TOKENS 对齐 breeze）
│   ├── session_hub.py       #   会话中枢
│   ├── window_manager.py    #   多窗口管理
│   └── workspace_switcher.py#   工作区切换器
├── domains/
│   ├── chat/                # 聊天记忆域（原 AIChat，四层记忆）
│   ├── novel/               # 小说创作域（NovelForge，73 .py 清场后）
│   ├── studio/              # 短片制片域（5 表 + 4 Tab + 5 导出模板）
│   ├── warehouse/           # 数仓管控域（8 表 + 4 守卫 + sqlglot 解析）
│   └── analysis/            # 数据分析域（6 步流水线 + AES 加密 profile）
├── prompts/                 # 提示词仓库（数据文件，按域分子目录）
│   ├── chat/
│   ├── novel/
│   ├── studio/
│   ├── warehouse/
│   └── analysis/
├── pack/                    # 打包脚本
│   └── sync-branch.sh       #   chat-warehouse 分支同步脚本
├── docs/                    # 文档
├── main.py                  # 入口（启动 shell + 初始化内核 + 加载域）
├── requirements.txt         # 跨平台依赖
├── AGENTS.md                # 双机同步铁律 + AI 协作纪律
└── README.md
```

## 平台内核 core/（8 服务）

| 服务 | 职责 |
|---|---|
| `workspace` | 多工作区、声明启用域、数据物理隔离 |
| `llm_client` | OpenAI 兼容接口 + 步骤级 profile 路由 |
| `config_vault` | AES Fernet 加密配置存储 |
| `proposal_gate` | 提案→闸门→权威落库（off/medium/strict 三档） |
| `guard_registry` | 零 token 守卫注册框架（域注册守卫，写入前跑） |
| `graph_primitives` | DAG / 边集合 / 不变量校验（血缘、依赖、事件序） |
| `vector_spi` | 向量 SPI（sqlite-vec → numpy → BM25 三级降级，放弃 ChromaDB） |
| `prompt_repo` | 提示词仓库（Jinja2 模板 + 版本号 + 可回滚） |

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 域边界与依赖纪律

```
内核纪律（硬约束，第一天守住）：
  1. 内核不许 import 域
  2. 域只许 import 内核
  3. 域之间不能互相 import
  4. 提示词不许硬编码进 .py（放 prompts/<domain>/*.j2）
  5. LLM 产出必须走 proposal_gate → 闸门 → 权威落库
```

## 同步方案

| 场景 | 命令 | 何时用 |
|---|---|---|
| 日常双机同步 | `git pull && <改> && git add . && git commit && git push` | Mac↔Win 日常切换 |
| AI 改完等确认 | AI 改完不 push，告诉你状态，你说"push"才执行 | 与 AI 协作时 |
| chat-warehouse 分支 | `pack/sync-branch.sh` | 公司机最小部署（未来） |

详见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#同步方案)。

## 跨平台

- **Mac**：Python 3.9+，字体 fallback 链 PingFang SC 优先
- **Win**：Python 3.9+，字体 fallback 链 Microsoft YaHei 优先
- **放弃 ChromaDB**：C++ 依赖在 Win 不稳，改用 sqlite-vec + numpy + BM25 三级降级
- **sqlglot 可选**：数仓域 SQL 解析依赖，未装时 Tab 仍可打开但解析功能禁用
- **Python 3.9 兼容**：analysis 域全部文件加 `from __future__ import annotations`（避免 `X \| None` 语法）

## 备份

整合前快照保留在 `~/AIChat_backup_20261016/`（含 .git 完整历史 + 工作区状态）。

## 相关文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 架构说明（core 8 服务 + shell + 5 域 + 数据流）
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — 开发指南（加新域、打包、同步、常见坑）
- [AGENTS.md](AGENTS.md) — 双机同步铁律 + AI 协作纪律
- [docs/AICHAT_DOC.md](docs/AICHAT_DOC.md) — 原 AIChat 文档
- [docs/启动方式速查.md](docs/启动方式速查.md) — 启动方式速查
- [docs/使用指南.md](docs/使用指南.md) — 使用指南
