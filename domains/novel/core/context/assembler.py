"""
ContextAssembler - 上下文组装器。

将三层记忆检索结果组装进 prompt，按 token 预算管理各部分，
超限时按优先级裁剪（world/foreshadow 先裁，writing_instruction 最后裁）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from domains.novel.core.context.budget import TokenBudget
from domains.novel.core.llm_client import LLMClient
from domains.novel.core.memory.engine import MemoryRetrievalResult
from domains.novel.core.memory.summary_layer import SummaryLayer
from domains.novel.utils.logger import logger
from domains.novel.utils.text import truncate_text


@dataclass
class AssembledContext:
    """组装完成的上下文。"""
    messages: list[dict] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    truncated: dict[str, bool] = field(default_factory=dict)
    block_positions: dict[str, int] = field(default_factory=dict)
    total_tokens: int = 0
    budget: Optional[TokenBudget] = None


class ContextAssembler:
    """
    上下文组装器：将三层记忆检索结果组装进 prompt。

    组装顺序（对应 messages 结构）：
    1. [system] 写作系统提示（角色定义、写作规范）
    2. [system] 世界观设定 + 活跃伏笔（从档案库层）
    3. [system] 人物档案（从档案库层，仅相关人物）
    4. [system] 近期章节摘要（从摘要层）
    5. [system] 相关正文片段（从 RAG 层）
    6. [user] 上一章结尾正文（衔接上下文）
    7. [user] 当前章写作指令（章纲、要求）
    """

    def __init__(self, llm_client: LLMClient, config: dict):
        self._llm = llm_client
        self._config = config

    def assemble(
        self,
        chapter_id: int,
        writing_instruction: str,
        memory_result: MemoryRetrievalResult,
        previous_chapter_text: str = "",
        model_context_window: Optional[int] = None,
        genre: str = "",
        next_chapter_outline: str = "",
        problem_mode: bool = False,
        current_opening_hook: str = "",
        current_ending_hook: str = "",
        current_rhythm: str = "",
        tags: Optional[list] = None,
        process_tags: Optional[list] = None,
        chapter_spec: Optional[dict] = None,
    ) -> AssembledContext:
        """
        组装完整上下文。
        """
        context_window = model_context_window or self._config.get("context_window", 128000)
        max_tokens = self._config.get("max_tokens", 4096)
        budget = TokenBudget(
            total=context_window,
            reserved_for_completion=max_tokens,
        )

        assembled = AssembledContext(budget=budget)
        messages: list[dict] = []
        no_truncate: set = set()  # 方案A：硬记忆块不可被裁剪

        # 1. 系统提示（分层 + 预算门控）
        system_prompt = self._build_system_prompt(genre, chapter_type="", problem_mode=problem_mode, memory=memory_result, tags=tags, process_tags=process_tags)
        sys_tokens = self._llm.count_tokens(system_prompt)
        budget.set_actual("system_prompt", sys_tokens)
        messages.append({"role": "system", "content": system_prompt})
        assembled.token_usage["system_prompt"] = sys_tokens

        # ---- 方案 A/B：硬记忆钉顶 + 实体锚点触发召回（上下文最顶，对抗 lost-in-the-middle）----
        # 硬记忆（时间线/暗线/人设）钉到 system_prompt 之后最顶，标记 no_truncate；
        # 实体锚点按出场角色确定性触发（非相似度 RAG），同样置于顶部高注意力区。
        top_blocks = []  # (name, text, no_truncate_flag)
        if self._config.get("entity_anchor", True):
            ea = self._build_entity_anchors(
                memory_result, writing_instruction, previous_chapter_text,
                current_opening_hook, current_ending_hook, budget,
            )
            if ea:
                top_blocks.append(("entity_anchor", ea, False))
        if self._config.get("hard_tier_toppin", True):
            for _hn, _ht in self._build_hard_core(memory_result, budget):
                top_blocks.append((_hn, _ht, True))
        # 深大纲「本章拍摄脚本」：紧贴 system_prompt 最顶（高于硬记忆块），no_truncate。
        # AI 处理器按脚本展开，不发明情节——这是「大纲=记忆、生成=重建」的落实。
        if chapter_spec and (chapter_spec.get("event_progress") or chapter_spec.get("time_anchor")):
            _spec_text = self._build_chapter_spec(chapter_spec, budget)
            if _spec_text:
                top_blocks.insert(0, ("chapter_spec", _spec_text, True))
        # 本章简大纲（阶段1产物）已通过主记录字段注入：时间锚点见 _build_chapter_spec
        # （时间锚点·硬约束），临近章大纲见 _build_nearby_outlines。此处不再单独注入
        # brief_outline（旧冗余子字段，已移除），时间只保留 time_anchor 单一真源。
        # 倒序插入，使列表首位出现在 system_prompt 之后最顶
        for _bn, _bt, _bnt in reversed(top_blocks):
            _btok = self._llm.count_tokens(_bt)
            budget.parts.setdefault(_bn, {"pct": 0, "priority": 1, "actual": _btok})
            budget.set_actual(_bn, _btok)
            if _bnt:
                budget.parts[_bn]["no_truncate"] = True
                no_truncate.add(_bn)
            messages.insert(1, {"role": "system", "content": _bt})
            assembled.token_usage[_bn] = _btok
            assembled.truncated[_bn] = False

        # 2. 世界观 + 伏笔
        world_text = self._build_world_and_foreshadows(memory_result)
        if world_text:
            budget_wf = budget.get_budget("world_and_foreshadows")
            world_text, truncated = self._fit_to_budget(world_text, budget_wf, "tail")
            wf_tokens = self._llm.count_tokens(world_text)
            budget.set_actual("world_and_foreshadows", wf_tokens)
            messages.append({"role": "system", "content": world_text})
            assembled.token_usage["world_and_foreshadows"] = wf_tokens
            assembled.truncated["world_and_foreshadows"] = truncated

        # 2.5 全书总纲 + 简介（概览）—— 最高优先级设定，章节必须严格遵循
        overview_text = self._build_overview(memory_result)
        if overview_text:
            # 总纲可能较长，给足预算；overview 不在裁剪优先级表中，确保始终注入。
            # 但封顶不超过可用预算，避免 context_window 设得很小时 overview 单独撑爆上限。
            overview_cap = min(6000, budget.available)
            # 相位感知截断：前期/中期保留头部（前提+当前卷，避免砍掉核心设定）；
            # 后期/末期保留尾部（终局收束段）。总纲经 _phase_filter_master 已按相位裁剪，
            # 此处仅在超 6000 上限时兜底，方向须与相位一致。
            ov_phase = getattr(memory_result, "phase", "")
            ov_strategy = "head" if ov_phase in ("early", "mid") else "tail"
            overview_text, truncated = self._fit_to_budget(overview_text, overview_cap, ov_strategy)
            ov_tokens = self._llm.count_tokens(overview_text)
            messages.append({"role": "system", "content": overview_text})
            assembled.token_usage["overview"] = ov_tokens
            assembled.truncated["overview"] = truncated

        # 2.55 主线脊柱账本（贯穿全书的主线骨架，紧凑高优先级，治百万字主线崩溃）
        if getattr(memory_result, "story_spine", ""):
            spine_text = memory_result.story_spine
            spine_cap = min(1000, budget.available)
            spine_text, s_trunc = self._fit_to_budget(spine_text, spine_cap, "tail")
            sp_tokens = self._llm.count_tokens(spine_text)
            messages.append({"role": "system", "content": spine_text})
            assembled.token_usage["story_spine"] = sp_tokens
            assembled.truncated["story_spine"] = s_trunc

        # 2.55b 故事事件账本（本章须覆盖的计划事件，硬约束，治「分开生成做不好」）
        event_ledger_text = getattr(memory_result, "event_ledger", "")
        if event_ledger_text:
            el_cap = min(900, budget.available)
            event_ledger_text, el_trunc = self._fit_to_budget(event_ledger_text, el_cap, "tail")
            el_tokens = self._llm.count_tokens(event_ledger_text)
            messages.append({"role": "system", "content": event_ledger_text})
            assembled.token_usage["event_ledger"] = el_tokens
            assembled.truncated["event_ledger"] = el_trunc

        # 2.55c 连续性状态卡（治本：时间线/事件状态模型）
        # 给 LLM 写第 N 章前直接看「前一章结尾位置 / 已获物品 / 进行中 deadline / 时间线提示」，
        # 替代原本缺失的状态快照，消除「不全部传就吃书、全传就烧 token」的伪两难。
        # 高优先级、小预算、始终注入（与脊柱同对待）。
        # 方案A开启时，此块改由 _build_hard_core 钉顶注入，此处跳过避免重复。
        cont_state = getattr(memory_result, "continuity_state", "") or ""
        if not self._config.get("hard_tier_toppin", True) and cont_state:
            cs_cap = min(600, budget.available)
            cont_state, cs_trunc = self._fit_to_budget(cont_state, cs_cap, "tail")
            cs_tokens = self._llm.count_tokens(cont_state)
            messages.append({"role": "system", "content": cont_state})
            assembled.token_usage["continuity_state"] = cs_tokens
            assembled.truncated["continuity_state"] = cs_trunc

        # 2.55d 结构化弧 / 暗线 状态卡（治本：L2 弧归属 + 起承转合 + 本章暗线节点）
        # 让 LLM 写第 N 章前看到「本章属于哪个事件弧、什么阶段、今天要落哪个暗线节点」，
        # 与连续性状态卡同等待遇：高优先级、小预算、始终注入。防弧跨卷 / 暗线断裂于未然。
        arc_sp = getattr(memory_result, "arc_subplot", "") or ""
        if not self._config.get("hard_tier_toppin", True) and arc_sp:
            asp_cap = min(450, budget.available)
            arc_sp, asp_trunc = self._fit_to_budget(arc_sp, asp_cap, "tail")
            asp_tokens = self._llm.count_tokens(arc_sp)
            messages.append({"role": "system", "content": arc_sp})
            assembled.token_usage["arc_subplot"] = asp_tokens
            assembled.truncated["arc_subplot"] = asp_trunc

        # 2.6 卷上下文 + 近期节奏/钩子链（一致性增强，保持卷主题与全书节奏）
        continuity_text = self._build_book_continuity(memory_result, chapter_id)
        if continuity_text:
            budget_bc = budget.get_budget("book_continuity")
            continuity_text, truncated = self._fit_to_budget(continuity_text, budget_bc, "tail")
            bc_tokens = self._llm.count_tokens(continuity_text)
            budget.set_actual("book_continuity", bc_tokens)
            messages.append({"role": "system", "content": continuity_text})
            assembled.token_usage["book_continuity"] = bc_tokens
            assembled.truncated["book_continuity"] = truncated

        # 2.7 邻近章节大纲（前后窗口内完整大纲，防小范围吃书）
        # 方案C：批次窗口——仅当前批次内大纲，更早章节按需检索。
        batch_size = int(self._config.get("batch_size", 0) or 0)
        batch_start = None
        if batch_size > 0:
            batch_start = ((chapter_id - 1) // batch_size) * batch_size + 1
        adjacent_text = self._build_adjacent_outlines(memory_result, chapter_id, batch_start=batch_start)
        if adjacent_text:
            budget_ao = budget.get_budget("adjacent_outlines")
            adjacent_text, truncated = self._fit_to_budget(adjacent_text, budget_ao, "tail")
            ao_tokens = self._llm.count_tokens(adjacent_text)
            budget.set_actual("adjacent_outlines", ao_tokens)
            messages.append({"role": "system", "content": adjacent_text})
            assembled.token_usage["adjacent_outlines"] = ao_tokens
            assembled.truncated["adjacent_outlines"] = truncated

        # 3. 人物档案
        # 方案A开启时改由 _build_hard_core 钉顶注入（不可截断），此处跳过避免重复。
        char_text = self._build_character_profiles(memory_result)
        if not self._config.get("hard_tier_toppin", True) and char_text:
            budget_cp = budget.get_budget("character_profiles")
            char_text, truncated = self._fit_to_budget(char_text, budget_cp, "tail")
            cp_tokens = self._llm.count_tokens(char_text)
            budget.set_actual("character_profiles", cp_tokens)
            messages.append({"role": "system", "content": char_text})
            assembled.token_usage["character_profiles"] = cp_tokens
            assembled.truncated["character_profiles"] = truncated

        # 3.5 配角轮换花名册（近期在场人物，供本章顺手带配角戏份）
        cast_text = self._build_active_cast(memory_result)
        if cast_text:
            cast_cap = min(700, budget.available)
            cast_text, ct_trunc = self._fit_to_budget(cast_text, cast_cap, "tail")
            cast_tokens = self._llm.count_tokens(cast_text)
            messages.append({"role": "system", "content": cast_text})
            assembled.token_usage["active_cast"] = cast_tokens
            assembled.truncated["active_cast"] = ct_trunc

        # 4. 章节摘要
        summary_text = self._build_summaries(memory_result, chapter_id=chapter_id, batch_start=batch_start)
        if summary_text:
            budget_cs = budget.get_budget("chapter_summaries")
            # tail 策略：保留最近N章摘要（衔接更相关），丢弃最早摘要——比 head 更贴「要素齐全」
            summary_text, truncated = self._fit_to_budget(summary_text, budget_cs, "tail")
            cs_tokens = self._llm.count_tokens(summary_text)
            budget.set_actual("chapter_summaries", cs_tokens)
            messages.append({"role": "system", "content": summary_text})
            assembled.token_usage["chapter_summaries"] = cs_tokens
            assembled.truncated["chapter_summaries"] = truncated

        # 5. RAG片段（轻量模式 rag_inject_foreshadow_only：仅检索设定/伏笔，不注入正文散文）
        if not self._config.get("rag_inject_foreshadow_only", False):
            rag_text = self._build_rag_chunks(memory_result)
            if rag_text:
                budget_rag = budget.get_budget("rag_chunks")
                rag_text, truncated = self._fit_to_budget(rag_text, budget_rag, "tail")
                rag_tokens = self._llm.count_tokens(rag_text)
                budget.set_actual("rag_chunks", rag_tokens)
                messages.append({"role": "system", "content": rag_text})
                assembled.token_usage["rag_chunks"] = rag_tokens
                assembled.truncated["rag_chunks"] = truncated
        else:
            logger.info("轻量RAG模式：跳过正文散文片段注入（活跃伏笔/设定由档案与主线脊柱承载）")

        # 6. 上一章结尾（衔接）
        if previous_chapter_text:
            budget_rt = budget.get_budget("recent_text")
            # 保留结尾部分（衔接用）
            recent_text, truncated = self._fit_to_budget(previous_chapter_text, budget_rt, "head")
            rt_tokens = self._llm.count_tokens(recent_text)
            budget.set_actual("recent_text", rt_tokens)
            messages.append({"role": "user", "content": f"上一章结尾：\n\n{recent_text}"})
            assembled.token_usage["recent_text"] = rt_tokens
            assembled.truncated["recent_text"] = truncated

        # 6.5 灵感碎片（如果有相关碎片，注入到写作指令前）
        # 轻量RAG模式下同样跳过（灵感碎片本质是正文散文片段）
        insp_text = "" if self._config.get("rag_inject_foreshadow_only", False) else self._build_inspirations(memory_result)
        if insp_text:
            insp_tokens = self._llm.count_tokens(insp_text)
            # 灵感碎片从 RAG 预算中分配（优先级低于正文片段）
            insp_budget = budget.get_budget("rag_chunks")
            insp_text, insp_truncated = self._fit_to_budget(insp_text, insp_budget // 2, "tail")
            insp_tokens = self._llm.count_tokens(insp_text)
            messages.append({"role": "system", "content": insp_text})
            assembled.token_usage["inspirations"] = insp_tokens
            assembled.truncated["inspirations"] = insp_truncated

        # 6.6 下一章大纲（衔接用，让AI知道后续走向）
        if next_chapter_outline:
            nco_text = f"=== 下一章方向（本章结尾需为下一章铺垫） ===\n{next_chapter_outline}"
            nco_text, nco_truncated = self._fit_to_budget(nco_text, 800, "tail")
            nco_tokens = self._llm.count_tokens(nco_text)
            messages.append({"role": "system", "content": nco_text})
            assembled.token_usage["next_chapter_outline"] = nco_tokens
            assembled.truncated["next_chapter_outline"] = nco_truncated

        # 6.7 精确衔接锚点（强制）：本章开篇/结尾钩子的精确文字，确保衔接而非靠堆文本
        anchor_text = self._build_hook_anchors(current_opening_hook, current_ending_hook)
        if anchor_text:
            messages.append({"role": "system", "content": anchor_text})
            assembled.token_usage["hook_anchors"] = self._llm.count_tokens(anchor_text)

        # 7. 写作指令
        instruction_text = self._build_writing_instruction(
            writing_instruction, chapter_id, current_volume=memory_result.current_volume,
            current_rhythm=current_rhythm,
        )
        inst_tokens = self._llm.count_tokens(instruction_text)
        budget.set_actual("writing_instruction", inst_tokens)
        messages.append({"role": "user", "content": instruction_text})
        assembled.token_usage["writing_instruction"] = inst_tokens

        # 检查总 token 是否超限，超限则按优先级裁剪
        assembled.messages = messages
        # 记录各区块在 messages 中的真实位置（调试/验证用，对索引零依赖；
        # 在截断前采集，因截断只改内容不重排）。
        assembled.block_positions = self._locate_parts(messages, memory_result)
        assembled.total_tokens = self._llm.count_messages(messages)

        if assembled.total_tokens > budget.available:
            assembled = self._truncate_by_priority(assembled, budget, no_truncate)

        logger.info(f"上下文组装完成: {assembled.total_tokens} tokens "
                    f"(预算 {budget.available}), 裁剪: {assembled.truncated}")
        return assembled

    def _build_system_prompt(self, genre: str, chapter_type: str = "", problem_mode: bool = False, memory=None, tags=None, process_tags=None) -> str:
        """构建系统提示 — 分层 + 预算门控（见 prompt_registry）。

        不再是一个不断变长的 SYSTEM_WRITING 字符串：L0/L1 常驻，
        L2 质量增强模块按质量档位 + 预算装配，超预算自动丢弃低优先级项，
        写作系统提示长度始终有上限。problem_mode=True 时（修订/修复场景）只装 L0。
        """
        from domains.novel.core.pipeline.prompt_registry import build_writing_system_prompt
        tier = self._config.get("quality_tier", "standard")
        budget = int(self._config.get("system_prompt_budget", 1400))
        style = self._config.get("writing_style", "")
        phase = getattr(memory, "phase", "") if memory is not None else ""
        vol_label = getattr(memory, "vol_label", "") if memory is not None else ""
        return build_writing_system_prompt(
            genre=genre, chapter_type=chapter_type, tier=tier, budget_tokens=budget,
            problem_mode=problem_mode, style=style, phase=phase, vol_label=vol_label,
            tags=tags, process_tags=process_tags,
        )

    def _build_world_and_foreshadows(self, memory: MemoryRetrievalResult) -> str:
        """构建世界观+伏笔文本。

        顺序：活跃伏笔 → 近期时间线 → 世界观设定。
        理由：该块超预算时按 tail 截断（保留末尾），世界观设定作为最基础 anchor 放在最后，
        即使需要裁剪也优先保住世界观、丢弃溢出的最老伏笔，避免「基础设定被切掉导致内容崩」。
        """
        parts = []

        if memory.foreshadows:
            parts.append("=== 活跃伏笔 ===")
            for fs in memory.foreshadows:
                status_text = {"planted": "已埋下", "advanced": "推进中", "resolved": "已回收"}.get(fs.status, fs.status)
                parts.append(f"[{status_text}] 第{fs.planted_chapter}章埋下: {fs.description}")
                if fs.related_characters:
                    parts.append(f"  相关人物: {', '.join(fs.related_characters)}")
            parts.append("")

        if memory.timeline:
            parts.append("=== 近期时间线 ===")
            for tl in memory.timeline[-10:]:  # 最近10条
                parts.append(f"第{tl.chapter_id}章: {tl.event} - {tl.description}")
            parts.append("")

        if memory.world_settings:
            parts.append("=== 世界观设定（基础锚点，须始终保留） ===")
            for ws in memory.world_settings:
                parts.append(f"【{ws.category}】{ws.name}：{ws.description}")
            parts.append("")

        return "\n".join(parts) if parts else ""

    def _build_overview(self, memory: MemoryRetrievalResult) -> str:
        """构建全书总纲 + 简介（概览）文本 —— 章节写作必须遵循的整体框架。"""
        parts = []

        if memory.master_outline:
            parts.append("=== 全书总纲（必须遵循：本章节情节需服务于总体故事线，不得偏离核心设定） ===")
            parts.append(memory.master_outline.strip())
            parts.append("")

        if memory.synopsis:
            parts.append("=== 小说简介（故事基调与核心设定参考） ===")
            parts.append(memory.synopsis.strip())
            parts.append("")

        return "\n".join(parts) if parts else ""

    def _build_character_profiles(self, memory: MemoryRetrievalResult) -> str:
        """构建人物档案文本。"""
        if not memory.characters:
            return ""

        parts = ["=== 人物档案 ==="]
        for char in memory.characters:
            parts.append(f"【{char.name}】角色: {char.role or '未定'}")
            if char.aliases:
                parts.append(f"  别名: {', '.join(char.aliases)}")
            if char.description:
                parts.append(f"  外貌: {char.description}")
            if char.personality:
                parts.append(f"  性格: {char.personality}")
            if char.abilities:
                parts.append(f"  能力: {', '.join(char.abilities)}")
            if char.current_state:
                parts.append(f"  当前状态: {char.current_state}")
            if char.relationships:
                for rel in char.relationships[:5]:
                    parts.append(f"  关系: {rel.get('target', '?')} - {rel.get('relation', '')}")
            parts.append("")

        return "\n".join(parts)

    def _build_active_cast(self, memory: MemoryRetrievalResult) -> str:
        """构建配角轮换花名册（近期在场人物名单 + 带戏提示）。

        只给「谁还在场」的极简名单（名字/角色/一句当前状态），不重复完整档案，
        让 AI 在推进主角主线时能顺手给配角一点具体镜头，世界更有人气。
        """
        cast = getattr(memory, "active_cast", None) or []
        if not cast:
            return ""
        parts = ["=== 当前在场角色（配角轮换用：主角为绝对核心，本章可挑 1-2 位配角给具体镜头） ==="]
        for c in cast:
            name = getattr(c, "name", "") or ""
            if not name:
                continue
            role = getattr(c, "role", "") or "配角"
            state = getattr(c, "current_state", "") or ""
            line = f"· {name}（{role}）"
            if state:
                line += f"：{state}"
            parts.append(line)
        parts.append("提示：给配角的戏份须服务于主角成长或主线推进，不喧宾夺主、不另起无关支线。")
        return "\n".join(parts) if len(parts) > 2 else ""

    # ----------------------------------------------------------------
    # 方案 A：硬记忆钉顶（时间线/暗线/人设），不可截断，置于上下文最顶
    # ----------------------------------------------------------------
    def _build_hard_core(self, memory: MemoryRetrievalResult, budget) -> list:
        """返回 [(name, text)]：核心结构化台账（时间线/暗线/人设）钉顶用。

        每个卡有独立上限；整体再受聚合上限（可用预算 40%，封顶 3500），
        避免「硬记忆永远优先占 token」把正文上下文饿死——台账本身必须是紧凑精华，
        不是人设全文。

        健壮性：①拟合后为空（预算极小/负）的块直接丢弃，不注入空消息；
        ②聚合超上限时从最低优先级（末位=人设→次末=暗线）整块丢弃，
        绝不把块裁成「…」残块污染上下文（治本：异常紧窗口下优雅降级）。
        """
        blocks = []
        # 连续性状态卡（时间线）
        cont = getattr(memory, "continuity_state", "") or ""
        if cont:
            cap = min(600, budget.available)
            cont, _ = self._fit_to_budget(cont, cap, "tail")
            if cont.strip():
                blocks.append(("continuity_state", "=== 连续性状态卡（硬约束·不可截断） ===\n" + cont))
        # 弧/暗线状态卡
        arc = getattr(memory, "arc_subplot", "") or ""
        if arc:
            cap = min(450, budget.available)
            arc, _ = self._fit_to_budget(arc, cap, "tail")
            if arc.strip():
                blocks.append(("arc_subplot", "=== 弧/暗线状态卡（硬约束·不可截断） ===\n" + arc))
        # 人物档案（人设，紧凑）
        char = self._build_character_profiles(memory)
        if char:
            cap = min(1500, budget.available)
            char, _ = self._fit_to_budget(char, cap, "tail")
            if char.strip():
                blocks.append(("character_profiles", char))
        # 聚合上限：硬记忆块不超过可用预算的 40%，防饿死正文
        agg = min(3500, int(budget.available * 0.4))
        total = sum(self._llm.count_tokens(t) for _, t in blocks)
        # 从最低优先级起整块丢弃，直至满足上限（至少保留连续性状态卡）
        while total > agg and len(blocks) > 1:
            blocks.pop()
            total = sum(self._llm.count_tokens(t) for _, t in blocks)
        return blocks

    # ----------------------------------------------------------------
    # 方案 B：实体锚点触发召回（确定性触发，非相似度 RAG）
    # ----------------------------------------------------------------
    def _build_entity_anchors(self, memory: MemoryRetrievalResult, writing_instruction: str,
                              previous_text: str, opening_hook: str, ending_hook: str, budget) -> str:
        """解析待生成章信号（写作指令/上一章结尾/本章钩子），确定性触发出场角色的
        完整档案 + 相关过往剧情，强制注入顶部。

        与纯相似度 RAG 的区别：角色一出现就强制拉档，不赌模糊匹配漏召。
        范围受预算上限约束（精选字段 + 每条最多 3 条相关剧情），杜绝「全部关联记录」爆 token。
        """
        from framework_constraint import _extract_entities
        src = "\n".join([writing_instruction or "", previous_text or "",
                         opening_hook or "", ending_hook or ""])
        if not src.strip():
            return ""
        chars = getattr(memory, "characters", None) or []
        if not chars:
            return ""
        try:
            extracted = _extract_entities(src)
        except Exception:
            extracted = []
        triggered = []
        for c in chars:
            name = getattr(c, "name", "") or ""
            if not name:
                continue
            aliases = getattr(c, "aliases", None) or []
            hit = (name in src) or any(a and a in src for a in aliases)
            if not hit:
                for e in extracted:
                    if e and (e in name or name in e or any(a and e in a for a in aliases)):
                        hit = True
                        break
            if hit:
                triggered.append(c)
        if not triggered:
            return ""
        parts = ["=== 实体锚点（本章出场角色强制召回·确定性触发，非相似度匹配） ==="]
        cap = min(1200, int(budget.available * 0.15))
        for c in triggered:
            line = f"【{getattr(c, 'name', '')}】"
            if getattr(c, "role", ""):
                line += f" 角色:{c.role}"
            if getattr(c, "current_state", ""):
                line += f" 当前状态:{c.current_state}"
            rels = getattr(c, "relationships", None) or []
            if rels:
                rtxt = "; ".join(f"{r.get('target', '?')}-{r.get('relation', '')}" for r in rels[:3])
                if rtxt:
                    line += f" 关系:[{rtxt}]"
            # 相关过往剧情（从摘要召回，最多 3 条，摘要式）
            summaries = getattr(memory, "summaries", None) or []
            rel_plots = []
            for s in summaries:
                stxt = f"{getattr(s, 'one_line_summary', '')} {getattr(s, 'plot_progress', '') or ''}"
                if name in stxt or any(a and a in stxt for a in (getattr(c, "aliases", None) or [])):
                    rel_plots.append(f"第{getattr(s, 'chapter_id', '?')}章:{getattr(s, 'one_line_summary', '')}")
            if rel_plots:
                line += "\n  过往相关:" + "；".join(rel_plots[:3])
            parts.append(line)
        text = "\n".join(parts)
        text, _ = self._fit_to_budget(text, cap, "tail")
        return text

    # ----------------------------------------------------------------
    # 深大纲「本章拍摄脚本」：把 event_progress 渲染为严格规格块（顶部钉顶·no_truncate）
    # ----------------------------------------------------------------
    def _build_chapter_spec(self, outline: dict, budget) -> str:
        """把深大纲 event_progress / state_deltas / continuity_anchors 渲染成
        「本章拍摄脚本」严格规格块。AI 处理器只展开规格，不发明情节。"""
        eps = (outline or {}).get("event_progress") or []
        time_anchor = str((outline or {}).get("time_anchor") or "").strip()
        writing_focus = str((outline or {}).get("writing_focus") or "").strip()
        narrative_shots = str((outline or {}).get("narrative_shots") or "").strip()
        beat_sequence = str((outline or {}).get("beat_sequence") or "").strip()
        if not eps and not time_anchor and not writing_focus and not narrative_shots and not beat_sequence:
            return ""
        lines = ["=== 本章拍摄脚本（硬约束·不可截断）——正文须严格按以下事件进展展开，"
                 "只丰富过程、不发明情节 ==="]
        # 时间锚点钉在最顶部：写章时必须明确"距上章过了多久、现在是剧情第几天"，
        # 防止时间概念混乱与倒流（简大纲 time_anchor → 写章硬上下文）
        if time_anchor:
            lines.append(f"【时间锚点·硬约束】{time_anchor}"
                         "（正文所述时间推进须与此一致；若有时间跳跃必须显式过渡交代）")
        # 叙事镜头调度：把分镜意图直接交给写章处理器，避免写成事件流水账
        if narrative_shots:
            lines.append(f"【叙事镜头调度·按此顺序运镜】{narrative_shots}"
                         "（每条镜头须真实出现在正文，禁止只写事件不写镜头）")
        # 情节节拍顺序：明确起承转合递进，防止平铺罗列
        if beat_sequence:
            lines.append(f"【情节节拍顺序·须向前推进】{beat_sequence}")
        # 章级写作重心：深大纲回答"这一章要着重写什么"。放在事件节拍之前，
        # 让处理器先知道笔墨往哪儿使，再按节拍推进（beat 级 prose_focus 是它的细分）。
        if writing_focus:
            lines.append(f"【本章写作重心】{writing_focus}"
                         "（笔墨须向此处倾斜：重心场景写足写透，非重心内容简笔带过，"
                         "不得平均用力）")
        if eps:
            lines.append("【事件节拍·按编号顺序推进，不得调换先后、不得跳过】")
        prose_notes = []
        for idx, b in enumerate(eps, 1):
            summary = (b.get("summary") or "").strip()
            if not summary:
                continue
            ents = "、".join(b.get("entities") or [])
            ck = (b.get("checkpoint") or "").strip()
            # 显式编号 = 正文推进顺序（AI 据此知道先写哪拍、后写哪拍）
            seg = f"{idx}. {summary}"
            if ents:
                seg += f"（涉及：{ents}）"
            if ck:
                seg += f" [必须落地：{ck}]"
            lines.append(seg)
            pf = (b.get("prose_focus") or "").strip()
            if pf:
                prose_notes.append(f"  - 第{idx}拍：{pf}")
        sd = (outline or {}).get("state_deltas") or []
        if sd:
            lines.append("状态增量：")
            for d in sd:
                lines.append("  - " + (d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)))
        ca = (outline or {}).get("continuity_anchors") or {}
        if ca.get("from_prev"):
            lines.append("承接上章：" + str(ca["from_prev"]))
        if ca.get("to_next"):
            lines.append("埋下钩子：" + str(ca["to_next"]))
        # 文笔落点放最尾部：软性指引，预算紧张时优先被 tail 裁剪，
        # 不与上方 checkpoint / 状态增量等硬事实争抢配额
        if prose_notes:
            lines.append("文笔落点（软性建议，与硬事实冲突时以上方为准）：")
            lines.extend(prose_notes)
        text = "\n".join(lines)
        cap = min(1500, int(budget.available * 0.2))
        text, _ = self._fit_to_budget(text, cap, "tail")
        return text

    def _build_summaries(self, memory: MemoryRetrievalResult, chapter_id: int = None,
                         batch_start: int = None) -> str:
        """构建章节摘要文本。

        方案C：若开启批次窗口（batch_start 给定），仅载入当前批次 [batch_start, chapter_id]
        的摘要，更早章节改由实体锚点/检索按需召回，杜绝无限堆砌文本触发自动截断；
        但强制保留上一章（chapter_id-1，跨批次边界衔接用）。
        """
        if not memory.summaries:
            return ""

        parts = ["=== 前情摘要 ==="]
        for s in memory.summaries:
            _cid = s.chapter_id
            if batch_start is not None and _cid < batch_start and _cid != (chapter_id - 1 if chapter_id else None):
                continue
            parts.append(f"【第{s.chapter_id}章《{s.title}》】")
            parts.append(f"  {s.one_line_summary}")
            if s.plot_progress:
                parts.append(f"  情节: {s.plot_progress}")
            if s.character_state_changes:
                for csc in s.character_state_changes[:3]:
                    parts.append(f"  人物: {csc.get('character', '?')} {csc.get('before', '')} → {csc.get('after', '')}")
            parts.append("")

        return "\n".join(parts)

    def _build_rag_chunks(self, memory: MemoryRetrievalResult) -> str:
        """构建RAG检索片段文本。"""
        if not memory.rag_chunks:
            return ""

        parts = ["=== 相关正文片段 ==="]
        for chunk in memory.rag_chunks:
            ch_id = chunk.get("chapter_id", "?")
            score = chunk.get("score", 0)
            parts.append(f"（第{ch_id}章，相似度: {score:.2f}）")
            parts.append(chunk.get("text", ""))
            parts.append("")

        return "\n".join(parts)

    def _build_inspirations(self, memory: MemoryRetrievalResult) -> str:
        """构建灵感碎片文本（写前注入）。"""
        if not memory.inspirations:
            return ""
        parts = ["=== 灵感碎片（请在写作中适当融入以下灵感） ==="]
        for insp in memory.inspirations:
            type_label = getattr(insp, '_type_label', '') or insp.type
            # 尝试获取类型标签
            try:
                from domains.novel.core.inspiration.manager import TYPE_LABELS
                type_label = TYPE_LABELS.get(insp.type, insp.type)
            except Exception:
                type_label = insp.type
            parts.append(f"【{type_label}】{insp.title}")
            parts.append(f"  {insp.content}")
            if insp.related_characters:
                parts.append(f"  关联人物: {', '.join(insp.related_characters)}")
            parts.append("")
        parts.append("（以上灵感碎片为创作参考，请在写作中自然融入，不要生硬插入）")
        return "\n".join(parts)

    def _build_book_continuity(self, memory: MemoryRetrievalResult, chapter_id: int) -> str:
        """构建卷上下文 + 近期节奏/钩子链（一致性增强，保持全书节奏与衔接）。

        仅当项目定义了卷结构（volumes.json 存在）时注入卷上下文；
        近期节奏账本始终基于已生成的大纲提供（若有）。
        """
        parts = []

        # ---- 卷上下文 ----
        vols = memory.volumes or []
        cur = memory.current_volume
        if cur:
            parts.append("=== 当前卷上下文 ===")
            parts.append(
                f"你正在写作：【{cur.get('title', '')}】"
                f"（涵盖第{cur.get('start_chapter')}–{cur.get('end_chapter')}章）"
            )
            if cur.get("theme"):
                parts.append(f"本卷主题：{cur['theme']}")
            if cur.get("summary"):
                parts.append(f"本卷概要：{cur['summary']}")

        # ---- 当前卷规划段 ----
        # 注：本卷完整规划段（含情绪节奏配比）已由 2.5 概览块按相位注入
        # （_phase_filter_master 已切出当前卷完整段），此处不再重复注入，避免双份浪费 token。
        if vols:
            parts.append("全书卷结构：")
            for v in vols:
                parts.append(
                    f"  - 《{v.get('title', '')}》 第{v.get('start_chapter')}–{v.get('end_chapter')}章"
                )
            parts.append("（保持本卷主题与节奏，与前后卷自然过渡，不偏离全书总纲）")

        # ---- 近期节奏 / 钩子链账本 ----
        recent = memory.recent_outlines or []
        if recent:
            parts.append("=== 近期节奏与衔接 ===")
            parts.append("最近章节节奏（类型 / 钩子链），请据此保持节奏一致：")
            for ol in recent:
                cid = ol.get("chapter_id")
                ctype = ol.get("chapter_type_label") or ol.get("chapter_type") or ""
                oh = ol.get("opening_hook", "")
                eh = ol.get("ending_hook", "")
                entry = f"  第{cid}章 [{ctype}]"
                if oh:
                    entry += f" 开篇：{oh}"
                if eh:
                    entry += f" ｜ 结尾：{eh}"
                parts.append(entry)
            parts.append(
                "节奏提示：避免连续多章同一类型；保持张弛有度。"
                "本章须承接上一章结尾钩子，并为下一章方向铺垫，确保钩子链不断。"
            )

        if not parts:
            # 既无卷也无节奏数据，不注入空消息
            return ""
        return "\n".join(parts) + "\n"

    def _build_adjacent_outlines(self, memory: MemoryRetrievalResult, chapter_id: int,
                                 batch_start: int = None) -> str:
        """构建当前章前后窗口内章节的完整大纲文本（防小范围吃书）。

        注入前后各 N 章的标题/类型/核心事件/钩子/伏笔/情节概要，
        使 AI 在写第 N 章时能直接看到邻近章节到底写了什么，避免因���看到
        摘要与下一章方向而出现的局部（小范围）吃书/矛盾。
        方案C：若开启批次窗口，仅保留当前批次内大纲，更早章节按需检索。
        """
        outs = getattr(memory, "nearby_outlines", None) or []
        if not outs:
            return ""
        parts = ["=== 邻近章节大纲（写作前必读：避免与前后章节在人物/设定/钩子/伏笔上小范围吃书） ==="]
        for ol in outs:
            cid = ol.get("chapter_id", 0)
            if batch_start is not None and cid < batch_start and cid != (chapter_id - 1):
                continue
            rel = "前文" if cid < chapter_id else "后文"
            title = ol.get("title", "") or f"第{cid}章"
            label = f"【{rel} 第{cid}章《{title}》】"
            block = [label]
            if ol.get("chapter_type_label"):
                block.append(f"  类型: {ol['chapter_type_label']}")
            if ol.get("time_anchor"):
                block.append(f"  时间锚点: {ol['time_anchor']}")
            if ol.get("core_events"):
                block.append(f"  核心事件: {ol['core_events']}")
            if ol.get("plot_summary"):
                block.append(f"  情节概要: {ol['plot_summary']}")
            if ol.get("opening_hook"):
                block.append(f"  开篇钩子: {ol['opening_hook']}")
            if ol.get("ending_hook"):
                block.append(f"  结尾钩子: {ol['ending_hook']}")
            if ol.get("foreshadowing") and str(ol.get("foreshadowing")).strip().lower() not in ("none", "无", ""):
                block.append(f"  伏笔: {ol['foreshadowing']}")
            parts.append("\n".join(block))
            parts.append("")
        return "\n".join(parts) if len(parts) > 1 else ""

    def _build_hook_anchors(
        self, opening_hook: str, ending_hook: str
    ) -> str:
        """构建精确衔接锚点（强制约束）。

        只注入本章规划好的开篇/结尾钩子的「精确文字」，作为硬约束要求 AI 必须以
        其起笔/收束。文字极短（几十字），但衔接绝对精确——对应「开头结尾需要精确
        文字作为衔接、用量不大」的设计哲学。承接上一章的精确文字已通过 recent_text 注入。
        """
        parts = ["=== 精确衔接锚点（必须严格遵守，不可偏离） ==="]
        if opening_hook and opening_hook.strip():
            parts.append(
                f"【本章开篇锚点】本章必须以以下钩子起笔"
                f"（可直接使用或在其基调上展开，但须落实该悬念/冲突/意外）：\n"
                f"  {opening_hook.strip()}"
            )
        if ending_hook and ending_hook.strip():
            parts.append(
                f"【本章结尾锚点】本章必须以以下钩子收束"
                f"（抛出该悬念/疑问/反转/微妙变化，为下一章铺垫）：\n"
                f"  {ending_hook.strip()}"
            )
        if len(parts) == 1:
            return ""
        parts.append("（开篇/结尾锚点是硬性要求，未落实将破坏钩子链与全书衔接。）")
        return "\n".join(parts)

    def _build_writing_instruction(self, instruction: str, chapter_id: int,
                                   current_volume: Optional[dict] = None,
                                   current_rhythm: str = "") -> str:
        """构建故事走向指令 — 精简版，核心约束已在 system prompt 中。

        current_rhythm：本章节奏定位（爽点/危机感/情感互动/世界观揭秘/缓冲过渡），
        作为硬约束注入，使「情绪节奏配比」从软参考变为逐章可对照的指令。
        """
        volume_line = ""
        if current_volume:
            vt = current_volume.get("title", "")
            vtheme = current_volume.get("theme", "")
            volume_line = f"\n【当前卷】《{vt}》" + (f" — 主题：{vtheme}" if vtheme else "")
            volume_line += "：本章须服务于本卷主题与节奏。"

        rhythm_line = ""
        if current_rhythm:
            rhythm_line = (
                f"\n【本章节奏定位】{current_rhythm} —— 本章必须落实该定位："
                f"爽点=明确爽感/打脸/突破/收获；危机感=压迫/险境/悬念升级；"
                f"情感互动=人物关系或情感推进；世界观揭秘=设定或真相渐进揭示；"
                f"缓冲过渡=舒缓节奏、为后续铺垫。不得写成与节奏定位相悖的平铺章。"
            )

        return f"""=== 第{chapter_id}章写作任务 ===
{volume_line}{rhythm_line}
本章方向：
{instruction}

（指引较简略时，请根据前文情节和角色发展自主推进。）

【提醒】纯中文输出，禁止任何英文单词。直接输出正文。"""

    def _fit_to_budget(
        self,
        content: str,
        budget_tokens: int,
        strategy: str = "tail",
    ) -> tuple[str, bool]:
        """
        将内容裁剪到预算内。

        粗略换算：1 token ≈ 1.5 个中文字符（保守）
        """
        if not content:
            return "", False

        max_chars = int(budget_tokens * 1.3)  # 保守估算
        if max_chars <= 0:
            # 预算为 0/负（异常紧窗口）：直接丢弃，绝不产出「…」残块污染上下文
            return "", True
        if len(content) <= max_chars:
            return content, False

        truncated = truncate_text(content, max_chars, strategy)
        return truncated, True

    def _truncate_by_priority(self, assembled: AssembledContext, budget: TokenBudget,
                              no_truncate: Optional[set] = None) -> AssembledContext:
        """
        按优先级裁剪：当总 token 超预算时，从优先级最低（数字最大）的部分开始裁剪。

        裁剪顺序（先→后）：
          rag_chunks(7) → recent_text(6)/chapter_summaries(6) → book_continuity(5)
          → adjacent_outlines(4) → world_and_foreshadows(3)/character_profiles(3)
        世界观/人物档案为一致性核心锚点，优先级 3（最高保护），token 紧张时最后才裁；
        system_prompt(2) 和 writing_instruction(1) 基本不裁剪。
        """
        # 按优先级从低到高排序需要裁剪的部分
        parts_by_priority = sorted(
            budget.parts.items(),
            key=lambda x: x[1]["priority"],
            reverse=True  # 优先级数字大的先裁剪
        )

        for part_name, part_info in parts_by_priority:
            if assembled.total_tokens <= budget.available:
                break
            if part_info["priority"] <= 2:  # 系统提示和写作指令不裁剪
                continue
            if no_truncate and part_name in no_truncate:  # 方案A：硬记忆块不可裁剪
                continue

            # 找到对应的 message 并裁剪
            part_index = self._find_message_index(assembled.messages, part_name)
            if part_index is None:
                continue

            # 计算需要裁剪多少
            excess = assembled.total_tokens - budget.available
            current_tokens = part_info["actual"]
            target_tokens = max(0, current_tokens - excess - 200)  # 多裁一点留余量

            if target_tokens == 0:
                # 直接移除
                removed = assembled.messages.pop(part_index)
                assembled.token_usage[part_name] = 0
                assembled.truncated[part_name] = True
                logger.info(f"裁剪: 移除 {part_name}")
            else:
                # 裁剪到 target_tokens
                content = assembled.messages[part_index]["content"]
                max_chars = int(target_tokens * 1.3)
                strategy = "head" if part_name == "recent_text" else "tail"
                new_content = truncate_text(content, max_chars, strategy)
                assembled.messages[part_index]["content"] = new_content
                new_tokens = self._llm.count_tokens(new_content)
                assembled.token_usage[part_name] = new_tokens
                assembled.truncated[part_name] = True
                logger.info(f"裁剪: {part_name} {current_tokens} → {new_tokens} tokens")

            # 重新计算总 token
            assembled.total_tokens = self._llm.count_messages(assembled.messages)

        return assembled

    @staticmethod
    def _locate_parts(messages: list[dict], memory_result) -> dict[str, int]:
        """扫描 messages，返回各已知区块名 -> 其 message 索引。

        用于验证/调试（如方案 A/B/C 前后位置对比），对内容关键词零依赖误报：
        每个区块用其稳定 header 关键词定位；基线方案的连续性状态卡无 header，
        退化为用原始 continuity_state 文本前缀匹配（仅作定位，不进正文）。
        """
        kw = {
            "chapter_spec": ["本章拍摄脚本（硬约束"],
            "entity_anchor": ["实体锚点（本章出场角色强制召回"],
            "continuity_state": ["连续性状态卡（硬约束"],
            "arc_subplot": ["弧/暗线状态卡（硬约束"],
            "character_profiles": ["=== 人物档案 ==="],
            "world_and_foreshadows": ["世界观设定", "活跃伏笔", "近期时间线"],
            "overview": ["全书总纲", "小说简介"],
            "story_spine": ["主线脊柱"],
            "event_ledger": ["事件账本"],
            "chapter_summaries": ["前情摘要"],
            "adjacent_outlines": ["邻近章节大纲"],
            "recent_text": ["上一章结尾"],
            "next_chapter_outline": ["下一章方向"],
            "hook_anchors": ["精确衔接锚点"],
            "book_continuity": ["当前卷上下文", "近期节奏与衔接", "全书卷结构"],
            "active_cast": ["当前在场角色"],
        }
        positions: dict[str, int] = {}
        for name, keys in kw.items():
            for i, m in enumerate(messages):
                if any(k in (m.get("content") or "") for k in keys):
                    positions[name] = i
                    break
        # 基线 continuity_state 无 header：用原始文本前缀兜底定位
        if "continuity_state" not in positions:
            raw = (getattr(memory_result, "continuity_state", "") or "")[:30]
            if raw:
                for i, m in enumerate(messages):
                    if raw in (m.get("content") or ""):
                        positions["continuity_state"] = i
                        break
        return positions

    @staticmethod
    def _find_message_index(messages: list[dict], part_name: str) -> Optional[int]:
        """根据部分名称找到对应 message 的索引。"""
        content_map = {
            "world_and_foreshadows": ["世界观设定", "活跃伏笔", "近期时间线"],
            "character_profiles": ["人物档案"],
            "chapter_summaries": ["前情摘要"],
            "rag_chunks": ["相关正文片段"],
            "recent_text": ["上一章结尾"],
            "book_continuity": ["当前卷上下文", "近期节奏与衔接", "全书卷结构"],
            "adjacent_outlines": ["邻近章节大纲"],
        }
        keywords = content_map.get(part_name, [])
        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            if any(kw in content for kw in keywords):
                return i
        return None
