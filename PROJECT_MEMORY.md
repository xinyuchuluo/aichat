# PROJECT_MEMORY — AIChat Studio

> 本文件供任何 AI 编码工具阅读：进入项目先读它，就知道定位、约束与当前进度。
> 「自动同步段」由 MemSync 从本地记忆库刷新，请勿手动编辑；其余内容人工维护、永不被覆盖。

## 人工备注

- 项目定位：一体化桌面 App（PyQt6 + SQLite，Mac/Win 双平台），5 个创作/工作域（chat 聊天记忆 / novel 小说 / studio 短片 / warehouse 数仓 / analysis 数据分析）共享同一套 `core/` 平台内核 + 浅色主题壳，各域独立 SQLite、数据物理隔离。
- 关键约束：
  - **域边界铁律**：内核不许 import 域；域只许 import 内核；**域之间禁止互相 import**。
  - 提示词不许硬编码进 `.py`，一律放 `prompts/<domain>/*.j2`（走 `core/prompt_repo.py`）。
  - LLM 产出必须走 `proposal_gate` → 闸门 → 权威落库（off/medium/strict 三档）。
  - 放弃 ChromaDB：向量用 `core/vector_spi.py` 三级降级（sqlite-vec → numpy → BM25）。
  - Python 3.9 兼容：需要 `X | None` 语法的文件必须带 `from __future__ import annotations`。
  - API Key 全系统只存一份：`<workspace>/config.enc`（AES Fernet，同目录 `secret.key`），统一入口 `core/credential_hub.py`；别处不再存明文。
  - 与 AI 协作时改完**不自动 push**，等用户说「push」才执行。
- 当前目标：记忆内核已收敛到 `core/memory/`（L1 working / L2 raw / L3 tree / vector 四层，域不再自带记忆实现），并统一到 MemSync 共享档案 `~/AichatStudio/_memory/<profile>/`（personal / company 物理隔离），AIChat 客户端、CLI、MCP 三方同库；下一步接 chat 域 brief 读取与 Neo4j 检索底座。
- 常用命令：
  ```bash
  python3 main.py                                            # 启动一体化壳
  python3 scripts/memsync.py resolve                         # 当前目录属于哪个项目
  python3 scripts/memsync.py brief --refresh                 # 刷新并打印项目简报
  python3 scripts/memsync.py note "..." --type decision --source workbuddy
  python3 scripts/memsync.py search 记忆内核 --limit 5
  git pull && git add . && git commit -m "..." && git push   # 改完等确认再 push
  ```

<!-- memsync:auto:begin -->
## 自动同步段（MemSync 生成）

- 项目：AIChat Studio（`aichat-studio-48d2e7`）
- 路径：`/Users/chenzhichao/AIChat`
- 最近更新：2026-10-27 15:50

### 当前有效事实
- [事实] 向量放弃 ChromaDB（Win 上 C++ 依赖不稳），改 core/vector_spi.py 三级降级 sqlite-vec → numpy → BM25（2026-10-27 11:53）
- [事实] API Key 全系统只存一份：<workspace>/config.enc（AES Fernet + 同目录 secret.key），统一入口 core/credential_hub.py；novel/analysis 不再存明文（2026-10-27 11:53）
- [事实] 域边界铁律：内核不许 import 域、域只许 import 内核、域之间禁止互相 import；提示词放 prompts/<domain>/*.j2 不进 .py；LLM 产出必须 proposal_gate → 闸门 → 权威落库（2026-10-27 11:53）

### 最近决策与变更
- 2026-10-27 15:50 [决策] AIChat 未提交改动按三组落地（仅本地、未 push）：97efc60 知识图库原语、3ad037c credential_hub、b42b436 MemSync（含 AGENTS.md + PROJECT_MEMORY.md + core/memory 四个新模块 + scripts）。.trae/ 仍未跟踪。（来源：trae）
- 2026-10-27 11:35 [决策] Trae 通过 MCP 写入的一条真实决策（来源：trae）
- 2026-10-27 11:32 [决策] 将记忆内核统一为 MemSync 档案模式 — 个人项目走 ~/AichatStudio/_memory/personal/，公司项目走 company/，AIChat/Trae/Codex/WorkBuddy 共享同一份 SQLite（来源：trae）
<!-- memsync:auto:end -->
