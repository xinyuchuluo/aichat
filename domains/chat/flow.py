"""流程蓝图 (FlowBlueprint) + 推演实例 (FlowRun) 系统。

核心理念：
  FlowBlueprint = 静态模板，预先定义工作骨架（步骤 + 绑定 skill + 分支点位）
  AI 只能填充参数，不能自由生成整套流程
  FlowRun = 推演实例（任务卡），用户确认后按步骤执行

协议：
  AI 响应中用 <|flow|>JSON</|flow|> 标签携带 FlowRun
  宿主解析后渲染为任务卡，用户确认后按步骤执行
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

# ============================================================
# 步骤类型
# ============================================================
STEP_SKILL = "skill"       # 调用 SKILL_REGISTRY 中的 skill
STEP_LLM = "llm"            # 调用 LLM（用 prompt_template + 前置步骤结果）
STEP_OUTPUT = "output"     # 只展示 AI 填充的内容（不执行）

# ============================================================
# FlowBlueprint — 静态模板
# ============================================================
@dataclass
class FlowBlueprintStep:
    """蓝图步骤定义。"""
    name: str                                              # 步骤标识
    label: str                                             # 显示名（如 "📊 加载数据"）
    description: str                                       # 步骤说明
    step_type: str = STEP_SKILL                            # skill / llm / output
    skill: str = ""                                        # 绑定的 skill 名
    prompt_template: str = ""                              # LLM 提示词模板
    depends_on: list[str] = field(default_factory=list)    # 前置步骤
    params_schema: dict = field(default_factory=dict)      # AI 可填充的参数描述
    branch_condition: str = ""                             # 分支条件（可选）


@dataclass
class FlowBlueprint:
    """流程蓝图（静态模板）。"""
    key: str                                               # 蓝图标识
    name: str                                              # 蓝图名
    description: str                                       # 蓝图说明
    steps: list[FlowBlueprintStep]                         # 步骤列表
    trigger_keywords: list[str] = field(default_factory=list)


# ============================================================
# FlowRun — 推演实例（任务卡）
# ============================================================
@dataclass
class FlowRunStep:
    """任务卡中的单步运行状态。"""
    name: str
    label: str
    status: str = "pending"                               # pending / running / done / failed
    params: dict = field(default_factory=dict)
    result: Any = None
    summary: str = ""
    error: str = ""


@dataclass
class FlowRun:
    """推演实例 = 任务卡。"""
    id: str
    blueprint_key: str
    title: str
    status: str = "pending"                                # pending / running / done / failed
    steps: list[FlowRunStep] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "blueprint_key": self.blueprint_key,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "steps": [
                {"name": s.name, "label": s.label, "status": s.status,
                 "params": s.params, "summary": s.summary, "error": s.error}
                for s in self.steps
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FlowRun":
        steps = [FlowRunStep(
            name=s.get("name", ""), label=s.get("label", ""),
            status=s.get("status", "pending"), params=s.get("params", {}),
            summary=s.get("summary", ""), error=s.get("error", ""),
        ) for s in d.get("steps", [])]
        return cls(
            id=d.get("id", str(uuid.uuid4())[:8]),
            blueprint_key=d.get("blueprint_key", ""),
            title=d.get("title", ""),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", ""),
            steps=steps,
        )


# ============================================================
# 蓝图注册表
# ============================================================
BLUEPRINTS: dict[str, FlowBlueprint] = {}


def register_blueprint(bp: FlowBlueprint) -> None:
    BLUEPRINTS[bp.key] = bp


def get_blueprint(key: str) -> FlowBlueprint | None:
    return BLUEPRINTS.get(key)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# 内置蓝图
# ============================================================

# ── 数据分析蓝图 ──────────────────────────────────
register_blueprint(FlowBlueprint(
    key="data_analysis",
    name="数据分析流程",
    description="加载 Excel/CSV 数据 → AI 规划分析维度 → 执行统计 → 深度分析 → 生成 HTML 报告",
    trigger_keywords=["数据分析", "分析数据", "统计", "报表", "excel分析", "csv分析", "数据洞察"],
    steps=[
        FlowBlueprintStep(
            name="load", label="📊 加载数据",
            description="加载数据文件，提取表头、行数、列数、样本",
            step_type=STEP_SKILL, skill="load_spreadsheet",
            params_schema={"sheet": "可选，Excel sheet 名", "max_rows": "样本行数，默认 5"},
        ),
        FlowBlueprintStep(
            name="plan", label="🧠 分析规划",
            description="AI 根据数据概况和分析重点，规划分析维度和指标",
            step_type=STEP_LLM,
            prompt_template=(
                "你是数据分析专家。以下是数据文件概况：\n{load_result}\n\n"
                "用户分析重点：{focus}\n\n"
                "请规划分析维度。输出 JSON：\n"
                '{"metrics": ["要统计的指标"], "dimensions": ["分组维度"], '
                '"comparisons": ["对比维度"], "anomalies": ["异常检测点"]}'
            ),
            params_schema={"focus": "用户指定的分析重点"},
            depends_on=["load"],
        ),
        FlowBlueprintStep(
            name="stats", label="📈 统计分析",
            description="执行 pandas 统计计算（数值列描述 + 分类列频次）",
            step_type=STEP_SKILL, skill="data_summary",
            params_schema={"sheet": "可选，指定 sheet"},
            depends_on=["plan"],
        ),
        FlowBlueprintStep(
            name="analyze", label="🔍 深度分析",
            description="AI 基于统计结果做深度分析，输出洞察和结论",
            step_type=STEP_LLM,
            prompt_template=(
                "你是数据分析专家。以下是统计结果：\n{stats_result}\n\n"
                "分析规划：\n{plan_result}\n\n"
                "请进行深度分析。输出 JSON：\n"
                '{"findings": ["关键发现"], "conclusions": ["核心结论"], '
                '"anomalies": ["异常"], "trends": ["趋势预判"]}'
            ),
            depends_on=["stats"],
        ),
        FlowBlueprintStep(
            name="report", label="📄 生成报告",
            description="生成 HTML 分析报告（含 KPI 卡片 + 图表）",
            step_type=STEP_SKILL, skill="generate_report",
            params_schema={"title": "报告标题"},
            depends_on=["analyze"],
        ),
    ],
))

# ── 小说大纲蓝图 ──────────────────────────────────
register_blueprint(FlowBlueprint(
    key="novel_outline",
    name="小说大纲生成",
    description="生成章节简大纲 → 深大纲 → 正文",
    trigger_keywords=["写大纲", "生成大纲", "简大纲", "深大纲", "写正文", "写章节"],
    steps=[
        FlowBlueprintStep(
            name="short_outline", label="📋 简大纲",
            description="根据框架生成本章简大纲",
            step_type=STEP_LLM,
            prompt_template="请为第 {chapter_id} 章生成简大纲。",
            params_schema={"chapter_id": "章节 ID"},
        ),
        FlowBlueprintStep(
            name="deep_outline", label="📖 深大纲",
            description="将简大纲展开为深大纲",
            step_type=STEP_LLM,
            prompt_template="请基于简大纲生成第 {chapter_id} 章的深大纲。\n简大纲：{short_outline_result}\n\n写作要求：\n- 叙事时间≠故事时间：平淡过渡压缩成一句、密集冲突展开成整段，约 3 份摘要对 1 份实写锚点\n- 同一时空严禁切分，不用分隔符/空行/「却说」；跨时空才分隔，用环境流转软缝\n- 每章双钩子：开篇前 3 行抛冲突/悬念，结尾留未解疑问\n- 人物须按自身立场利益行事，严禁替作者解说剧情目的",
            params_schema={"chapter_id": "章节 ID"},
            depends_on=["short_outline"],
        ),
        FlowBlueprintStep(
            name="write_body", label="✍️ 正文",
            description="根据大纲生成本章正文",
            step_type=STEP_LLM,
            prompt_template="请基于大纲生成第 {chapter_id} 章的正文。\n大纲：{deep_outline_result}\n\n写作技法要求：\n- 纯中文写作，禁止英文单词；禁止「不禁」「竟然」「嘴角微微上扬」等 AI 味套话\n- 感觉先于思考——身体先反应再推理；可插入极短硬句制造节奏断点\n- 动作节拍：对话间插入动作/表情/环境替代「他说」，4 句以上纯对白仅用「他说」是失败模式\n- 遮标签测试：遮住说话人名字仍能分辨角色\n- 感官锚点：[时间/频率]+[身体部位]+[非日常类比]+[可量化趋势]，绑定抽象到生理感受\n- 通感描写增画面感；同一章不重复同一种身体反应\n- 灵魂场铁律：角色 3-5 条不可违背的行为铁律，违反则改情节不改角色",
            params_schema={"chapter_id": "章节 ID"},
            depends_on=["short_outline"],
        ),
    ],
))


# ============================================================
# 协议文本（注入 system prompt）
# ============================================================
def _build_protocol_md() -> str:
    """动态生成协议文本，始终反映当前注册的蓝图。"""
    bp_lines = []
    for bp in BLUEPRINTS.values():
        bp_lines.append(f"### {bp.name} (`{bp.key}`)")
        bp_lines.append(bp.description)
        bp_lines.append("步骤：")
        for i, s in enumerate(bp.steps):
            params_desc = ", ".join(f"{k}: {v}" for k, v in s.params_schema.items()) if s.params_schema else "无"
            bp_lines.append(f"  {i+1}. {s.label} — {s.description}（参数：{params_desc}，依赖：{s.depends_on or '无'}）")
        bp_lines.append("")
    blueprints_list = "\n".join(bp_lines)

    return (
        "# 流程蓝图协议 (FlowBlueprint)\n\n"
        "你可以触发预定义的工作流程（蓝图）。蓝图是静态模板，你只能填充参数，不能自由生成流程。\n\n"
        "## 可用蓝图\n\n"
        + blueprints_list +
        "## 如何触发\n\n"
        "当用户的需求匹配某个蓝图时，在回复末尾用 `<|flow|>` 标签携带任务卡：\n\n"
        "```\n"
        "<|flow|>\n"
        '{"blueprint": "data_analysis", "title": "任务标题",\n'
        ' "steps": [\n'
        '   {"name": "load", "params": {"sheet": "Sheet1"}},\n'
        '   {"name": "plan", "params": {"focus": "季度趋势"}},\n'
        '   {"name": "stats", "params": {}},\n'
        '   {"name": "analyze", "params": {}},\n'
        '   {"name": "report", "params": {"title": "报告标题"}}\n'
        " ]}\n"
        "<|/flow|>\n"
        "```\n\n"
        "规则：\n"
        "1. 只能使用已注册的蓝图，不能自创新蓝图\n"
        "2. 只能填充 steps 中 params 的参数值\n"
        "3. 不能增减步骤、不能改变顺序\n"
        "4. title 用简短中文描述本次任务\n"
        "5. 用户确认后，步骤按顺序自动执行，结果会回传给你\n"

        "\n## 自动记忆写入\n\n"
        "当任务卡（FlowRun）被用户确认并执行完成后，宿主会自动把结果写入四层记忆池：\n\n"
        "1. **L1 工作记忆**（每步结果直接入会话工作记忆，AI 下一轮可直接看到）\n"
        "2. **L2 SQLite 原始库**（每步 raw_text 只追加不修改，含 step 状态/参数/摘要/结果）\n"
        "3. **L3 树形记忆**（批量生成：FlowRunRoot 作为 mid 节点，每步为 leaf 节点，形成树结构）\n"
        "4. **L4 向量索引**（只存树节点摘要 + UUID，检索时通过 UUID 回查 SQLite 原始文本）\n\n"
        "此外，你仍可用 `<|memory|>` 标签主动写入自定义记忆（fact/preference/entity/tree_node/reminder）。\n\n"
    )


# 模块加载时生成（后续注册的 skill 蓝图也能动态反映）
def get_flow_protocol_md() -> str:
    return _build_protocol_md()


# ============================================================
# 标签解析
# ============================================================
_FLOW_RE = re.compile(r"<\|flow\|>\s*(\{.*?\})\s*<\|/flow\|>", re.DOTALL)


def parse_flow_tag(text: str) -> FlowRun | None:
    """从 AI 响应中提取 FlowRun。"""
    m = _FLOW_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        bp_key = data.get("blueprint", "")
        bp = get_blueprint(bp_key)
        if not bp:
            return None
        step_params = {s["name"]: s.get("params", {}) for s in data.get("steps", [])}
        steps = []
        for bp_step in bp.steps:
            params = step_params.get(bp_step.name, {})
            steps.append(FlowRunStep(
                name=bp_step.name, label=bp_step.label,
                status="pending", params=params,
            ))
        return FlowRun(
            id=str(uuid.uuid4())[:8],
            blueprint_key=bp_key,
            title=data.get("title", bp.name),
            status="pending",
            steps=steps,
            created_at=_now_str(),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def strip_flow_tag(text: str) -> str:
    """移除 AI 响应中的 <|flow|> 标签（用户不需要看到原始 JSON）。"""
    return _FLOW_RE.sub("", text).rstrip()


# ============================================================
# 执行引擎
# ============================================================
def execute_flow_run(
    flow_run: FlowRun,
    adapter: Any = None,
    llm_client: Any = None,
    llm_kwargs: dict | None = None,
    progress_callback: Callable[[str, str, str], None] | None = None,
) -> FlowRun:
    """按顺序执行 FlowRun 的所有步骤。

    progress_callback(step_label, status, summary): 每步状态变化时调用

    对于 skill 步骤：调用 SKILL_REGISTRY[step.skill](**params)
    对于 llm 步骤：调用 LLM，用 prompt_template + 前置步骤结果
    """
    bp = get_blueprint(flow_run.blueprint_key)
    if not bp:
        flow_run.status = "failed"
        return flow_run

    from .tools import SKILL_REGISTRY

    flow_run.status = "running"
    step_results: dict[str, Any] = {}

    for i, bp_step in enumerate(bp.steps):
        run_step = flow_run.steps[i]
        run_step.status = "running"
        if progress_callback:
            progress_callback(run_step.label, "running", "")

        try:
            if bp_step.step_type == STEP_SKILL:
                skill_entry = SKILL_REGISTRY.get(bp_step.skill)
                if skill_entry:
                    func = skill_entry.get("func")
                    if func:
                        result = func(**run_step.params)
                        run_step.result = result
                        if isinstance(result, dict):
                            if result.get("ok", True):
                                run_step.summary = _summarize_result(result)
                                run_step.status = "done"
                            else:
                                run_step.error = str(result.get("error", "unknown"))
                                run_step.status = "failed"
                        else:
                            run_step.summary = str(result)[:500]
                            run_step.status = "done"
                    else:
                        run_step.error = f"skill {bp_step.skill} 无 func"
                        run_step.status = "failed"
                else:
                    run_step.error = f"skill {bp_step.skill} 未注册"
                    run_step.status = "failed"

            elif bp_step.step_type == STEP_LLM:
                prompt = bp_step.prompt_template
                # 填充 params
                for k, v in run_step.params.items():
                    prompt = prompt.replace("{" + k + "}", str(v))
                # 填充前置步骤结果
                for dep_name in bp_step.depends_on:
                    dep_result = step_results.get(dep_name, "")
                    if isinstance(dep_result, dict):
                        dep_str = json.dumps(dep_result, ensure_ascii=False, indent=2)[:2000]
                    else:
                        dep_str = str(dep_result)[:2000]
                    prompt = prompt.replace("{" + dep_name + "_result}", dep_str)

                if llm_client is None:
                    run_step.error = "LLM client 未注入"
                    run_step.status = "failed"
                else:
                    kwargs = llm_kwargs or {}
                    response = llm_client.chat(
                        [{"role": "user", "content": prompt}], **kwargs
                    )
                    if isinstance(response, str):
                        run_step.result = response
                        run_step.summary = response[:500]
                    else:
                        run_step.result = str(response)
                        run_step.summary = str(response)[:500]
                    run_step.status = "done"

            elif bp_step.step_type == STEP_OUTPUT:
                run_step.result = run_step.params.get("content", "")
                run_step.summary = str(run_step.result)[:500]
                run_step.status = "done"

            step_results[bp_step.name] = run_step.result

        except Exception as e:
            run_step.error = str(e)
            run_step.status = "failed"
            import traceback
            traceback.print_exc()

        if progress_callback:
            progress_callback(run_step.label, run_step.status, run_step.summary or run_step.error)

        if run_step.status == "failed":
            flow_run.status = "failed"
            break

    if flow_run.status != "failed":
        flow_run.status = "done"

    return flow_run




# ============================================================
# FlowMemoryManager — 把 FlowRun 写入四层记忆池
#
# 写入路径（严格遵守四层记忆架构规则）：
#  L1 工作记忆（纯内存，不持久化）— MemoryEngine.commit_message
#  L2 SQLite 原始真相库（raw_text 只追加不修改）— MemoryEngine → SQLite.append
#  L3 树形记忆 — FlowRun 完成后批量异步生成：
#     FlowRootNode (level=mid) 汇总整次推演
#     FlowStepNodes (level=leaf) 对应每步
#  L4 向量索引 — 只存树节点摘要 + UUID，业务事实回查 SQLite
#
# 过期：仅标记 is_expired，不物理删除
# ============================================================
class FlowMemoryManager:
    """FlowRun 的四层记忆写入器。

    用法：
        mgr = FlowMemoryManager(memory_engine, session_id, scope)
        for step in steps:
            mgr.commit_step(flow_run_id, step, blueprint_key)
        mgr.finalize_tree(flow_run)
    """

    def __init__(self, memory_engine, session_id: str, scope: str = ""):
        self._mem = memory_engine
        self._session_id = session_id
        self._scope = scope or f"flow_run:{uuid.uuid4().hex[:8]}"
        self._step_uuids: dict[str, str] = {}   # step.name → L2 raw uuid

    @property
    def scope(self) -> str:
        return self._scope

    def commit_step(self, flow_id: str, run_step: FlowRunStep,
                    blueprint_key: str = "") -> str:
        """把 FlowRun 单步结果写入 L1 工作记忆 + L2 SQLite 原始库。

        返回 L2 记录的 UUID（供 L3 树节点 raw_uuids 引用）。
        L2 raw_text 字段仅追加，不修改。
        """
        from .protocol.types import Message, ChatRole
        status_label = {"pending": "待执行", "running": "执行中",
                        "done": "已完成", "failed": "失败"}.get(run_step.status, run_step.status)
        params_str = ", ".join(f"{k}={v}" for k, v in run_step.params.items()) if run_step.params else "无参数"
        result_preview = ""
        if run_step.result:
            r = run_step.result
            if isinstance(r, dict):
                items = [f"{k}:{str(v)[:80]}" for k, v in list(r.items())[:8]]
                result_preview = " | ".join(items)
            else:
                result_preview = str(r)[:300]
        error_preview = f"\n错误详情: {run_step.error}" if run_step.error else ""

        raw_content = (
            f"[FlowRun Step] 蓝图={blueprint_key} | 任务卡ID={flow_id}\n"
            f"步骤: {run_step.label} ({run_step.name})\n"
            f"状态: {status_label}\n"
            f"参数: {params_str}\n"
            f"摘要: {run_step.summary or '无'}\n"
            f"结果预览: {result_preview or '无'}"
            + error_preview
        )
        msg = Message(
            role=ChatRole.SYSTEM,
            content=raw_content,
            metadata={
                "memory_type": "flow_step",
                "flow_id": flow_id,
                "flow_blueprint": blueprint_key,
                "flow_step_name": run_step.name,
                "flow_step_label": run_step.label,
                "flow_step_status": run_step.status,
                "is_expired": False,
            },
        )
        uuid_str = self._mem.commit_message(
            msg, session_id=self._session_id,
            scope=self._scope,
            metadata=msg.metadata,
        )
        self._step_uuids[run_step.name] = uuid_str
        return uuid_str

    def finalize_tree(self, flow_run: FlowRun) -> dict[str, str]:
        """FlowRun 完成时，批量生成 L3 树形记忆节点 + L4 向量索引。

        树结构：
            FlowRunRoot (level=mid)  — 本次推演的摘要（结构化拼接，不用LLM）
              ├── StepNode1 (leaf)
              ├── StepNode2 (leaf)
              └── ...
        """
        from .memory.tree_store import TreeNode

        tree = self._mem.tree
        vector = self._mem.vector
        step_node_ids: dict[str, str] = {}
        children_ids: list[str] = []
        all_raw_uuids: list[str] = []

        for run_step in flow_run.steps:
            raw_uuid = self._step_uuids.get(run_step.name, "")
            if raw_uuid:
                all_raw_uuids.append(raw_uuid)
            step_summary = (
                f"[{run_step.label}] 状态: {run_step.status} | "
                f"{run_step.summary or run_step.name}"
            )
            if run_step.status == "failed" and run_step.error:
                step_summary += f" | 错误: {run_step.error[:100]}"
            step_node = TreeNode(
                parent_id=None,
                summary=step_summary,
                scope=self._scope,
                level="leaf",
                raw_uuids=[raw_uuid] if raw_uuid else [],
                metadata={
                    "node_type": "flow_step",
                    "flow_id": flow_run.id,
                    "flow_blueprint": flow_run.blueprint_key,
                    "flow_step_name": run_step.name,
                    "flow_step_status": run_step.status,
                    "is_expired": False,
                },
            )
            nid = tree.add_node(step_node)
            step_node.node_id = nid
            step_node_ids[run_step.name] = nid
            children_ids.append(nid)
            try:
                vector.upsert_tree_node(step_node)
            except Exception:
                pass

        bp_key = flow_run.blueprint_key
        step_labels = "; ".join(f"{s.label}={s.status}" for s in flow_run.steps)
        root_summary = (
            f"[FlowRun] 蓝图={bp_key} | 标题={flow_run.title} | 状态={flow_run.status} | "
            f"步骤({len(flow_run.steps)}步): {step_labels}"
        )
        for s in reversed(flow_run.steps):
            summ = s.summary or ""
            if "输出:" in summ or "report" in summ.lower() or "报告" in summ:
                root_summary += f" | {summ[:120]}"
                break
        root_node = TreeNode(
            parent_id=None,
            summary=root_summary,
            scope=self._scope,
            level="mid",
            raw_uuids=all_raw_uuids,
            children_ids=children_ids,
            metadata={
                "node_type": "flow_run_root",
                "flow_id": flow_run.id,
                "flow_blueprint": bp_key,
                "flow_title": flow_run.title,
                "flow_status": flow_run.status,
                "step_count": len(flow_run.steps),
                "created_at": flow_run.created_at,
                "is_expired": False,
            },
        )
        root_id = tree.add_node(root_node)
        root_node.node_id = root_id
        for nid in children_ids:
            tree.update_parent(nid, root_id)
        try:
            vector.upsert_tree_node(root_node)
        except Exception:
            pass
        return {"root": root_id, "steps": step_node_ids}

    @staticmethod
    def mark_flow_expired(memory_engine, flow_id: str,
                          scope: str = "") -> int:
        """把指定 FlowRun 相关的记忆全部标记为过期（不物理删除）。"""
        count = 0
        uuids = memory_engine.sqlite.find_by_metadata_key("flow_id", flow_id, scope=scope)
        for u in uuids:
            memory_engine.sqlite.mark_expired(u)
            count += 1
        nodes = memory_engine.tree.find_by_metadata_key("flow_id", flow_id, scope=scope)
        for n in nodes:
            memory_engine.tree.mark_expired(n.node_id)
            count += 1
        try:
            memory_engine.vector.mark_expired_by_flow(flow_id, scope=scope)
        except Exception:
            pass
        return count


def get_flow_scope(flow_run: FlowRun, adapter_key: str = "") -> str:
    """生成 FlowRun 专属的记忆 scope。"""
    base = f"flow:{flow_run.blueprint_key}:{flow_run.id}"
    if adapter_key:
        base = f"{adapter_key}:{base}"
    return base
def _summarize_result(result: dict) -> str:
    """从 skill 结果中提取可读摘要。"""
    parts = []
    if "headers" in result:
        parts.append(f"表头: {', '.join(str(h) for h in result['headers'][:10])}")
    if "row_count" in result:
        parts.append(f"行数: {result['row_count']}")
    if "col_count" in result:
        parts.append(f"列数: {result['col_count']}")
    if "sheet_names" in result:
        parts.append(f"Sheets: {', '.join(result['sheet_names'])}")
    if "report_path" in result or "file_path" in result or "html_path" in result:
        p = result.get("report_path") or result.get("file_path") or result.get("html_path", "")
        parts.append(f"输出: {p}")
    if "numeric_stats" in result:
        parts.append(f"数值统计: {len(result['numeric_stats'])}列")
    if "categorical_stats" in result:
        parts.append(f"分类统计: {len(result['categorical_stats'])}列")
    if "error" in result:
        parts.append(f"错误: {result['error']}")
    return " | ".join(parts) if parts else str(result)[:200]
