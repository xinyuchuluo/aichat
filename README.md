# AIChatStudio

一体化桌面 App，整合 5 个创作/工作域，共享平台内核 + 浅色主题壳。

## 5 个域

| 域 | 来源 | 重 LLM | 记忆 | 闸门 |
|---|---|---|---|---|
| chat 聊天记忆 | AIChat | 中 | 四层（raw/tree/vector/working） | off |
| novel 小说 | NovelForge | 重 | 实体档案 + RAG + 摘要 | medium |
| studio 短片 | PRD 新写 | 无 | 无 | off |
| warehouse 数仓建设 | PRD 新写 | 中 | DAG/血缘 | strict |
| analysis 数据分析 | data-analyst-tool | 重 | 可选（对话历史） | off |

## 平台内核 core/

- `workspace` 多工作区、声明启用域、数据物理隔离
- `llm_client` OpenAI 兼容接口 + 步骤级 profile 路由
- `config_vault` AES Fernet 加密配置存储
- `proposal_gate` 提案→闸门→权威落库（三档严格度）
- `guard_registry` 零 token 守卫注册框架
- `graph_primitives` 关系/事件/DAG 原语
- `vector_spi` 向量检索 SPI（默认 sqlite-vec，降级 numpy/BM25）
- `prompt_repo` 提示词仓库（Jinja2 + 版本号 + 可回滚）

## 启动

```bash
pip install -r requirements.txt
python3 main.py
```

首次启动会在 `~/AichatStudio/default/` 创建默认工作区（Mac），Win 在 `%USERPROFILE%\AichatStudio\default\`。

## 分支地图

| 分支 | 含 | 用途 |
|---|---|---|
| `main` | core + shell + 5 域 + prompts + pack + docs | 个人机全量部署 |
| `chat-warehouse` | core + shell + chat + warehouse | 工作机/公司机最小部署 |

### 全量拉取（个人机）

```bash
git clone git@github.com:xinyuchuluo/AIChat.git
```

### 工作机最小集（chat + warehouse）

```bash
git clone -b chat-warehouse git@github.com:xinyuchuluo/AIChat.git
```

### 同步工作分支（main 有更新后跑）

```bash
./pack/sync-branch.sh
```

工作原理：
1. 拉 main 最新
2. 在 chat-warehouse 分支 `git merge main -X theirs`（接收 chat/warehouse 域更新）
3. 强制删除 `domains/{novel,studio,analysis}`（保持分支纯净）
4. commit + push

## 同步纪律（AI 协作时）

**AI 改完代码不自动 push。** 每次改完 AI 会告诉用户：
- 改了哪些文件 / 哪些目录
- 现在的 git 状态
- 要不要同步到远程？

**只有用户明确说"同步"/"push"才执行 `git push`**。

日常双机同步：
```bash
git pull && <改> && git add . && git commit -m "..." && git push origin main
```

开工前不盲信 `git status` 的 ahead/behind（跟踪引用会失真），用 `git ls-remote` 复核：
```bash
git ls-remote origin refs/heads/main   # 远程 hash
git rev-parse HEAD                      # 本地 hash
# 两者相等才安全
```

## 跨平台

- Mac + Win 双系统支持
- 字体 fallback：`"SF Pro Text", "PingFang SC", "Microsoft YaHei", system-ui, "Segoe UI"`
- 放弃 ChromaDB（C++ 在 Win 不稳），走 sqlite-vec + numpy + BM25 三级降级
- 打包：`pack/mac.spec` / `pack/win.spec`

## 风格

默认浅色主题（`#F7F7F8` 接近白），可切暗色。
品牌色一抹绿 `#0FDC78` 做 focus，不大面积铺。
各域不许改全局 QSS。

## 数据与程序分离

程序包不带数据。数据在 `~/AichatStudio/<workspace_id>/`，可单独 git/云盘同步。
首次启动选"新建空工作区"或"挂接我的工作区"。
别人下载到的是空工作区——拉不到你的记录。

## 内核纪律（硬约束）

1. 内核不许 import 任何域（`core/` 下任何文件不得 `from domains.*`）
2. 域只许 import 内核：`from core.xxx import ...`
3. 域之间不许互相 import
4. 提示词/背景/模板全部走 `prompts/` 数据文件，**不许硬编码进 .py**
5. 任何 LLM 产出不得直接落库，必须经"提案→闸门→权威"通道

## 文档

- [AGENTS.md](AGENTS.md) — 维护纪律（双机同步铁律）
- [docs/](docs/) — 各域迁移前的原软件文档
- [《内核规格说明》](file:///tmp/aichatstudio_spec.md) — 完整规格（待用户过目）

## 迁移来源（原仓库继续保留作备份）

| 域 | 原仓库 | 位置 |
|---|---|---|
| chat | `xinyuchuluo/AIChat`（原 aichat/ 子目录） | `~/AIChat_backup_20261016` 本地备份 |
| novel | NovelForge（本地工程） | `~/WorkBuddy/2026-07-04-18-03-19/novelforge/` |
| analysis | `xinyuchuluo/data-analyst-tool` | `/tmp/data-analyst-tool`（已 clone） |
| studio | PRD 定稿 | `~/Downloads/短片制片工作台_V1_需求文档.md` |
| warehouse | PRD 定稿 | `~/Downloads/数仓元数据知识库完整落地PRD*.md` |
