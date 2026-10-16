# FlowRun 记忆池与树形结构使用文档

> 适用于 AIChat 的流程蓝图（FlowBlueprint）+ 推演实例（FlowRun）架构。
> 本文档介绍 FlowRun 执行过程中如何自动写入四层记忆池、树形记忆节点如何生成、以及过期标记机制。

---

## 1. 总览

FlowRun 是「任务卡」的运行时实例：用户在聊天中触发流程蓝图，确认执行后，宿主按步骤运行，**每一步的输入/参数/状态/结果都会自动写入四层记忆系统**，完成后批量生成一棵树形记忆，供后续会话检索。

### 1.1 四层记忆与 FlowRun 的关系

| 层级 | 角色 | FlowRun 写入时机 | 存什么 | 规则约束 |
|------|------|----------------|--------|---------|
| **L1 工作记忆** | 纯内存，每会话独立 | 每步完成时 | 步骤消息原文 | 不持久化，进程退出即失 |
| **L2 SQLite 原始真相库** | 只追加的原始记录 | 每步完成时 | raw_text = 结构化步骤文本（蓝图ID/步骤名/状态/参数/摘要/结果预览） | **raw_text 只追加不修改** |
| **L3 树形记忆** | 结构化归纳 | FlowRun 完成后**批量异步**生成（不每步调 LLM） | FlowRunRoot(mid) + N 个 StepNode(leaf) | 异步批量触发 |
| **L4 向量索引（ChromaDB）** | 语义召回入口 | 与 L3 同步写入 | 只存树节点 `summary + metadata + UUID` | 检索时通过 UUID 回查 L2 原文 |

### 1.2 核心规则（不可违反）

- `raw_text` 字段在 L2 **只追加，永不修改**
- L3 树形记忆**异步批量生成**，不在每步调用 LLM
- L4 向量库**只存树节点摘要 + UUID**，业务事实必须回查 L2 SQLite 原文
- 检索链路固定：`metadata过滤 → 树分支筛选 → 向量召回UUID → Rerank → 回查SQLite`
- 过期仅打 `is_expired = True`，**不物理删除**

---

## 2. 涉及的代码模块

```
aichat/
├── flow.py                       # FlowBlueprint / FlowRun / FlowMemoryManager / execute_flow_run
├── chat/agent.py                 # 解析 <|flow|> 标签，把 FlowRun 放入 AI 消息 metadata
├── memory/
│   ├── engine.py                 # MemoryEngine（四层协调器）
│   ├── working.py                 # L1: WorkingMemory
│   ├── sqlite_store.py           # L2: SQLiteStore（append + find_by_metadata_key + mark_expired）
│   ├── tree_store.py             # L3: TreeMemoryStore（add_node + update_parent + find_by_metadata_key + mark_expired）
│   └── vector_store.py           # L4: VectorStore（upsert_tree_node + mark_expired_by_flow）
├── adapters/
│   ├── generic.py                # 通用适配器（system prompt 注入流程协议 + 记忆协议）
│   ├── data_analysis.py          # 数据分析适配器
│   └── novelforge.py             # NovelForge 适配器
└── ui/
    ├── chat_view.py              # FlowCardWidget 渲染任务卡
    └── main_window.py            # _FlowWorker 后台执行 FlowRun + 调用记忆写入
```

---

## 3. FlowMemoryManager API

`FlowMemoryManager` 是 FlowRun 专属的记忆写入器，位于 `aichat/flow.py`。

### 3.1 构造函数

```python
from aichat.flow import FlowMemoryManager, get_flow_scope

# scope 必须唯一标识本次 FlowRun，便于跨会话检索
scope = get_flow_scope(flow_run, adapter_key="data_analysis")
#  → "data_analysis:flow:data_analysis:fr_abc123"

mgr = FlowMemoryManager(
    memory_engine=mem,        # MemoryEngine 实例
    session_id="session_001", # 会话 ID
    scope=scope,              # 记忆 scope（不传会自动生成）
)
```

**scope 命名规则**：`{adapter_key}:flow:{blueprint_key}:{flow_run_id}`

- `adapter_key` — 适配器标识（generic / data_analysis / novelforge）
- `blueprint_key` — 蓝图标识
- `flow_run.id` — 本次推演实例 ID（UUID 前 8 位）

scope 决定后续检索范围，**同 scope 的记忆才会被召回**。

### 3.2 commit_step() — 写入单步到 L1 + L2

```python
def commit_step(
    self,
    flow_id: str,                # FlowRun.id
    run_step: FlowRunStep,       # 单步实例（含 label/status/params/summary/result）
    blueprint_key: str = "",     # 蓝图标识
) -> str:                        # 返回 L2 记录的 UUID
```

**调用时机**：`execute_flow_run()` 完成每一步后立即调用。

**写入的 raw_text 结构**（L2 SQLite 只追加，不修改）：

```
[FlowRun Step] 蓝图=data_analysis | 任务卡ID=fr_abc123
步骤: 📊 加载数据 (load)
状态: 已完成
参数: sheet=Sheet1, max_rows=5
摘要: 100行x5列 | 表头: 日期,产品,数量,单价,金额
结果预览: ok=True | headers=日期,产品,数量,单价,金额 | row_count=100
错误详情: <仅当 status=failed 时出现>
```

**metadata 同时写入**（供 metadata 过滤检索用）：

```json
{
  "memory_type": "flow_step",
  "flow_id": "fr_abc123",
  "flow_blueprint": "data_analysis",
  "flow_step_name": "load",
  "flow_step_label": "📊 加载数据",
  "flow_step_status": "done",
  "is_expired": false
}
```

### 3.3 finalize_tree() — 批量生成 L3 树 + L4 向量

```python
def finalize_tree(self, flow_run: FlowRun) -> dict[str, str]:
    # 返回: {"root": "<root_node_id>", "steps": {"load": "<node_id>", ...}}
```

**调用时机**：FlowRun 全部步骤执行完毕后调用一次（异步批量，不每步调 LLM）。

**生成的树结构**：

```
FlowRunRoot (level=mid)              ← 汇总一次推演
  ├── StepNode: load (level=leaf)
  ├── StepNode: plan (level=leaf)
  ├── StepNode: stats (level=leaf)
  ├── StepNode: analyze (level=leaf)
  └── StepNode: report (level=leaf)
```

**FlowRunRoot 节点 metadata**：

```json
{
  "node_type": "flow_run_root",
  "flow_id": "fr_abc123",
  "flow_blueprint": "data_analysis",
  "flow_title": "测试数据分析任务卡",
  "flow_status": "done",
  "step_count": 5,
  "created_at": "2026-09-01T12:00:00",
  "is_expired": false
}
```

**StepNode 节点 metadata**：

```json
{
  "node_type": "flow_step",
  "flow_id": "fr_abc123",
  "flow_blueprint": "data_analysis",
  "flow_step_name": "load",
  "flow_step_status": "done",
  "is_expired": false
}
```

**关键设计**：
- Root 节点的 `raw_uuids` 收集所有 StepNode 对应的 L2 UUID（供回查）
- Root 节点的 `children_ids` 列出所有 StepNode ID（建立父子关系）
- StepNode 的 `raw_uuids` 仅含本步的 L2 UUID
- `update_parent()` 回填每个 StepNode 的 `parent_id` 指向 Root

### 3.4 mark_flow_expired() — 过期标记（静态方法）

```python
@staticmethod
def mark_flow_expired(
    memory_engine,
    flow_id: str,            # 要过期的 FlowRun.id
    scope: str = "",         # 限定 scope（建议传入，加快查找）
) -> int:                    # 返回标记的条数
```

**调用时机**：用户主动让某次分析「过期」（例如数据已失效），宿主调用。

**作用范围**：同时标记 L2 / L3 / L4 三层
- L2 SQLite：按 `metadata.flow_id` 查找 UUID，调用 `SQLiteStore.mark_expired(uuid)`
- L3 树节点：按 `metadata.flow_id` 查找节点，调用 `TreeMemoryStore.mark_expired(node_id)`
- L4 向量：调用 `VectorStore.mark_expired_by_flow(flow_id, scope)`（best-effort，失败不阻塞）

**规则约束**：**只标记 `is_expired=True`，不物理删除**。
验证方法：`mark_flow_expired()` 前后 `SQLiteStore.count(scope)` 数值不变。

---

## 4. 数据结构参考

### 4.1 FlowRunStep（运行时单步状态）

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | str | 步骤标识（如 `load`） |
| `label` | str | 显示名（如 `📊 加载数据`） |
| `status` | str | pending / running / done / failed |
| `params` | dict | AI 填充的参数 |
| `result` | Any | skill / LLM 返回结果 |
| `summary` | str | 简短摘要（用于 L3 节点 summary） |
| `error` | str | 失败原因（status=failed 时） |

### 4.2 FlowRun（任务卡实例）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 8 位 UUID |
| `blueprint_key` | str | 使用的蓝图 key |
| `title` | str | 任务标题 |
| `status` | str | pending / running / done / failed |
| `steps` | list[FlowRunStep] | 步骤状态列表 |
| `created_at` | str | 创建时间 |

### 4.3 TreeNode（树形记忆节点）

| 字段 | 类型 | 说明 |
|------|------|------|
| `node_id` | str | 自动生成 UUID |
| `parent_id` | str / None | 父节点 ID（Root=None，StepNode=Root ID） |
| `summary` | str | 结构化摘要（不存原文） |
| `scope` | str | 记忆 scope |
| `level` | str | root / mid / leaf |
| `raw_uuids` | list[str] | 关联的 L2 原始记录 UUID |
| `children_ids` | list[str] | 子节点 ID 列表 |
| `metadata` | dict | 节点元数据（含 node_type / flow_id 等） |

---

## 5. 完整使用示例

### 5.1 编程式使用（后台 _FlowWorker 内部流程）

```python
from aichat.flow import (
    FlowRun, FlowRunStep, execute_flow_run,
    FlowMemoryManager, get_flow_scope,
)
from aichat.memory.engine import MemoryEngine

# 1. 初始化记忆引擎
mem = MemoryEngine(data_dir="~/.aichat/memory")

# 2. 构造 FlowRun（通常由 AI 通过 <|flow|> 标签生成）
flow_run = FlowRun(
    id="fr_demo_01",
    blueprint_key="data_analysis",
    title="8月销售数据分析",
    steps=[
        FlowRunStep(name="load",   label="📊 加载数据",  params={"sheet": "Sheet1"}),
        FlowRunStep(name="plan",   label="🧠 分析规划",  params={"focus": "月环比"}),
        FlowRunStep(name="stats",  label="📈 统计分析", params={}),
        FlowRunStep(name="analyze",label="🔍 深度分析", params={}),
        FlowRunStep(name="report", label="📄 生成报告", params={"title": "8月销售报告"}),
    ],
)

# 3. 准备记忆 scope + FlowMemoryManager
scope = get_flow_scope(flow_run, adapter_key="data_analysis")
mgr = FlowMemoryManager(mem, session_id="sess_001", scope=scope)

# 4. 执行 FlowRun（内部按步骤调用 skill / LLM）
result = execute_flow_run(
    flow_run,
    adapter=adapter,              # 适配器实例
    llm_client=llm,               # LLM 客户端
    llm_kwargs={"model": "..."},
    progress_callback=lambda label, status, summary: print(f"{label}: {status}"),
)

# 5. 把每步结果写入 L1 + L2
for step in result.steps:
    mgr.commit_step(result.id, step, blueprint_key=result.blueprint_key)

# 6. 批量生成 L3 树形记忆 + L4 向量索引
tree_info = mgr.finalize_tree(result)
print(f"Root 节点 ID: {tree_info['root']}")
print(f"叶节点数: {len(tree_info['steps'])}")
```

### 5.2 AI 自动触发（聊天链路）

用户在聊天中说：「分析一下这个 Excel 的销售数据」，AI 回复时自动在末尾追加：

```
<|flow|>
{"blueprint":"data_analysis", "title":"销售数据分析",
 "steps":[
   {"name":"load",   "params":{"sheet":"Sheet1"}},
   {"name":"plan",   "params":{"focus":"月环比"}},
   {"name":"stats",  "params":{}},
   {"name":"analyze","params":{}},
   {"name":"report", "params":{"title":"销售分析报告"}}
 ]}
<|/flow|>
```

宿主解析后：
1. `aichat/chat/agent.py` 调用 `parse_flow_tag()` 提取 FlowRun
2. `strip_flow_tag()` 清掉原始 JSON，用户只看到正文
3. FlowRun 放入 AI 消息 `metadata["flow_run"]`
4. `aichat/ui/chat_view.py` 的 `FlowCardWidget` 渲染任务卡（含步骤状态 + 确认按钮）
5. 用户点击「确认执行」→ `main_window._FlowWorker` 后台运行
6. 执行完毕自动调用 `FlowMemoryManager.commit_step()` + `finalize_tree()`

### 5.3 跨会话检索历史 FlowRun

```python
from aichat.protocol.types import MemoryQuery

# 检索某个 scope 下的 FlowRun 记忆
mq = MemoryQuery(
    query="月环比 销售额 报告",
    session_id="sess_002",              # 新会话也能检索
    scope="data_analysis:flow:data_analysis:fr_demo_01",
    top_k=5,
    filters={"flow_id": "fr_demo_01"},  # 按 flow_id 过滤
)
results = mem.retrieve(mq)
for r in results:
    print(f"score={r.score:.2f}  {r.content[:80]}")
    # content 即 L2 原始 raw_text，回查得到完整步骤记录
```

### 5.4 让某次 FlowRun 过期

```python
count = FlowMemoryManager.mark_flow_expired(
    mem,
    flow_id="fr_demo_01",
    scope="data_analysis:flow:data_analysis:fr_demo_01",
)
print(f"已标记 {count} 条记忆为过期（未物理删除）")

# 验证：记录数不变，但 is_expired=1
print(f"SQLite 记录数: {mem.sqlite.count(scope)}")  # 仍是原数量
```

---

## 6. 树形记忆可视化

FlowRun 完成后，L3 数据库中会形成如下结构：

```
mid  FlowRunRoot  [summary: 蓝图=data_analysis | 标题=... | 状态=done | 步骤(5): ...]
      ↑ raw_uuids: [u1, u2, u3, u4, u5]     ← 引用 L2 全部步骤记录
      ↑ children_ids: [n1, n2, n3, n4, n5]
      │
      ├── leaf  StepNode: load     [summary: 📊加载数据 状态:done | 100行x5列]
      │           ↑ raw_uuids: [u1]            ← 引用本步 L2 记录
      │           ↑ parent_id: <root>
      │
      ├── leaf  StepNode: plan     [summary: 🧠分析规划 状态:done | 指标: 销售额/毛利]
      │           ↑ raw_uuids: [u2]
      │
      ├── leaf  StepNode: stats    [summary: 📈统计分析 状态:done | 数值列: 2列]
      │           ↑ raw_uuids: [u3]
      │
      ├── leaf  StepNode: analyze [summary: 🔍深度分析 状态:done | Q3 同比上升 23%]
      │           ↑ raw_uuids: [u4]
      │
      └── leaf  StepNode: report  [summary: 📄生成报告 状态:done | 输出: /tmp/rpt.html]
                  ↑ raw_uuids: [u5]
```

**检索时**：
1. L4 向量库召回命中的树节点（通过 summary 语义匹配）
2. 从树节点的 `raw_uuids` 取出 UUID 列表
3. 回查 L2 SQLite 取原始 raw_text
4. Rerank 精排返回

---

## 7. 底层存储扩展方法

FlowRun 记忆功能为三个底层存储类各新增了方法：

### 7.1 SQLiteStore (L2)

| 方法 | 说明 |
|------|------|
| `find_by_metadata_key(key, value, scope="")` | 按 metadata 键值查找 UUID（不返回 raw_text，仅用于过期标记） |
| `mark_expired(uuid)` | 标记单条记录为过期（is_expired=1），**不物理删除** |

> **修复**：原先 `mark_expired` 缺少 `conn.commit()`，已补充提交。

### 7.2 TreeMemoryStore (L3)

| 方法 | 说明 |
|------|------|
| `update_parent(node_id, parent_id)` | 回填节点的 parent_id（建立 FlowRun 父子关系） |
| `find_by_metadata_key(key, value, scope="")` | 按 metadata 键值查找节点（返回 TreeNode 列表） |
| `mark_expired(node_id)` | 把树节点 metadata 中 `is_expired` 置为 True |

### 7.3 VectorStore (L4)

| 方法 | 说明 |
|------|------|
| `mark_expired_by_flow(flow_id, scope="")` | best-effort 把指定 flow_id 的向量条目标记过期。底层不支持时返回 0，不阻塞主流程 |

---

## 8. 内置蓝图与记忆行为

### 8.1 数据分析蓝图（`data_analysis`）

| 步骤 | 名称 | 类型 | 写入 L2 的内容 |
|------|------|------|--------------|
| 1 | 📊 加载数据 | skill: `load_spreadsheet` | sheet 名、行数、列数、表头、样本 |
| 2 | 🧠 分析规划 | llm | AI 规划的指标/维度/对比/异常点 |
| 3 | 📈 统计分析 | skill: `data_summary` | 数值列统计 + 分类列频次 |
| 4 | 🔍 深度分析 | llm | AI 输出的发现/结论/异常/趋势 |
| 5 | 📄 生成报告 | skill: `generate_report` | HTML 报告路径 |

### 8.2 小说大纲蓝图（`novel_outline`）

| 步骤 | 名称 | 类型 | 写入 L2 的内容 |
|------|------|------|--------------|
| 1 | 📋 简大纲 | llm | 章节简大纲文本 |
| 2 | 📖 深大纲 | llm（依赖简大纲） | 章节深大纲文本 |
| 3 | ✍️ 正文 | llm（依赖简大纲） | 章节正文文本 |

---

## 9. 检索链路详解

FlowRun 记忆严格遵循固定检索链路：

```
┌────────────────────────────────────────────────────────────────┐
│  用户提问：上次分析的 Q3 销售怎么样？                            │
└────────────────────────────┬───────────────────────────────────┘
                             ↓
   Step 1: metadata 过滤
   ─ scope = "data_analysis:flow:..."  限定到本次 FlowRun
   ─ filters = {"flow_id": "fr_demo_01"}  精确到具体任务卡
                             ↓
   Step 2: 树形分支筛选
   ─ TreeMemoryStore.get_by_scope(scope) 取本 scope 所有节点
                             ↓
   Step 3: 向量召回 UUID
   ─ VectorStore.query(query, scope, top_k=10)
   ─ 从结果节点的 raw_uuids 收集 UUID
                             ↓
   Step 4: Rerank 精排
   ─ 关键词重叠度评分（无 LLM 调用）
                             ↓
   Step 5: 回查 SQLite 原文
   ─ SQLiteStore.get_by_uuids(uuids)
   ─ 返回 MemoryResult（content 为完整 raw_text）
                             ↓
   ┌──────────────────────────────────────────────────────────┐
   │  AI 拿到记忆：[FlowRun Step] 步骤: 🔍深度分析 | 摘要: Q3 同比上升 23% │
   └──────────────────────────────────────────────────────────┘
```

---

## 10. 验证测试

集成测试脚本（可用 `python3` 直接运行）：

```python
import tempfile
from aichat.flow import FlowRun, FlowRunStep, FlowMemoryManager, get_flow_scope
from aichat.memory.engine import MemoryEngine

tmp = tempfile.mkdtemp(prefix="aichat_mem_")
mem = MemoryEngine(tmp)

fr = FlowRun(
    id="fr_test_01",
    blueprint_key="data_analysis",
    title="测试任务卡",
    steps=[
        FlowRunStep(name="load",   label="📊 加载数据",  status="done", summary="100行x5列"),
        FlowRunStep(name="plan",   label="🧠 分析规划", status="done", summary="指标: 销售额"),
        FlowRunStep(name="stats",  label="📈 统计分析",status="done", summary="2列数值统计"),
        FlowRunStep(name="analyze",label="🔍 深度分析",status="done", summary="Q3 同比 +23%"),
        FlowRunStep(name="report", label="📄 生成报告",status="done",
                    result={"report_path": "/tmp/rpt.html"}, summary="输出: /tmp/rpt.html"),
    ],
)

scope = get_flow_scope(fr, "data_analysis")
mgr = FlowMemoryManager(mem, "session_test_1", scope)

# L1 + L2
for step in fr.steps:
    mgr.commit_step(fr.id, step, blueprint_key=fr.blueprint_key)
assert mem.get_stats(scope)["sqlite_records"] == 5

# L3 + L4
tree_info = mgr.finalize_tree(fr)
nodes = mem.tree.get_by_scope(scope)
assert len([n for n in nodes if n.level == "mid"]) == 1   # 1 个 Root
assert len([n for n in nodes if n.level == "leaf"]) == 5  # 5 个 StepNode
root = [n for n in nodes if n.level == "mid"][0]
assert len(root.children_ids) == 5
assert len(root.raw_uuids) == 5

# 过期不删除
before = mem.sqlite.count(scope)
marked = FlowMemoryManager.mark_flow_expired(mem, fr.id, scope)
after = mem.sqlite.count(scope)
assert before == after  # 只标记不物理删除

print("All tests passed")
```

---

## 11. 常见问题

**Q1：FlowRun 的记忆和普通聊天记忆会混淆吗？**

不会。FlowRun 使用独立 scope（`{adapter_key}:flow:{blueprint}:{id}`），与普通聊天记忆的 scope（如 `data_analysis:session_xxx`）天然隔离。检索时只在指定 scope 内召回。

**Q2：失败步骤也会写入记忆吗？**

会。失败的步骤 `status=failed`，raw_text 会包含 `错误详情: <异常信息>`，便于后续排查。树节点的 summary 也会标注 `状态: failed | 错误: ...`。

**Q3：L3 树节点用 LLM 摘要吗？**

不用。FlowRunRoot 的 summary 是结构化拼接（蓝图+标题+状态+步骤标签列表+报告路径），不调用 LLM。这符合「异步批量、不每步调 LLM」的约束。

**Q4：向量库索引失败会阻塞主流程吗？**

不会。`finalize_tree()` 中的 `vector.upsert_tree_node()` 调用都包在 `try/except` 中，索引失败只影响语义检索能力，不影响 L2/L3 数据完整性。

**Q5：如何清理旧的 FlowRun 记忆？**

调用 `FlowMemoryManager.mark_flow_expired(mem, flow_id, scope)`，三层存储同步标记 `is_expired=True`。**不会物理删除**，但后续检索会跳过 `is_expired=True` 的记录。如需彻底清理，需手动操作数据库（不推荐）。
