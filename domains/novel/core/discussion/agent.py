"""讨论功能：小说架构顾问 agent（工具增强版）。

职责：
- 组装系统提示（角色 + 当前小说上下文）
- 普通聊天（流式）
- 生成结构化调整提案（chat_json）
- 【第4步新增】工具增强 agent loop：agent 可自主调用
  read_chapter / search_all / edit_chapter / run_guard，模仿真人编辑：
  读任意章、搜全本、改正文（带备份）、跑守卫。

不碰数据：写入由 proposal.py 负责，agent 只产出建议。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional, Generator

from domains.novel.config import Config
from domains.novel.core.llm_client import LLMClient
from domains.novel.core.memory.engine import MemoryEngine
from domains.novel.core.discussion.memory import DiscussionMemory
from domains.novel.core.discussion.power_canon import power_canon_block, is_power_intent
from domains.novel.core.discussion.world_settings_integrity import enforce_single_track

ROLE = (
    "你是 NovelForge 小说项目的「架构顾问」agent，帮助用户讨论剧情、人物、伏笔、节奏与大纲结构，"
    "给出专业、具体、可落地的建议。\n"
    "工作方式：你收到的是软件按『规划→检索→证据包』流程准备的**真实数据**"
    "（结构化记忆、全书检索命中、精读章节全文、跨会话记忆）。\n"
    "硬要求（软件会强制校验，不是可选项）：\n"
    "1. **回答骨架必须是设定先行的层级**：核心内容按「①设定/体系（权威顶层）②伏笔/暗线（贯穿线索）"
    "③背景/世界观（环境底色）④章节落地证据」组织。**章节号只是轻量锚点，不是主干**——"
    "禁止把回答写成『第X章…第Y章…』的章节罗列。规划/设定类问题严禁从正文反推，必须基于设定层展开。\n"
    "2. 引用具体章节号（如「第12章」）时，软件会逐条核验是否真实存在；只作为设定/伏笔的落地印证，不作主线骨架。\n"
    "3. 证据不足必须明说『当前资料不足』并反问澄清问题；严禁编造、严禁脑补。\n"
    "4. 改写正文时 before 必须逐字匹配原文（软件会拒绝不匹配 / 歧义的提案）。\n"
    "5. 用户要求「提出调整建议」时，输出结构化 JSON 提案（引用真实 id / 章节号）。\n"
    "   **严禁用英文占位符或概念词当 id**：不得写 id=\"protagonist\"/\"endgame\"/\"hero\" 之类，"
    "也不得凭空造 id。更新已存在实体时，必须用上方『可写对象清单』里的真实 name 或真 id"
    "（如主角写 name=\"楚渊\"、大结局相关写具体设定名）；清单里查不到的实体，用 add_xxx 创建新条目，"
    "不要硬编 id 去 update。软件侧虽会做概念→实体的确定性映射兜底，但你应直接输出真名以减少歧义。\n"
    "6. 你只提建议、不改数据；写入需用户点「采纳并写入」。\n"
    "7. **回答精炼 + 结构化**：默认给结论与要点，不主动铺开完整设定表/对照表/长清单（如九阶觉醒对照、全书检索明细）。"
    "需要细节时用户会问，届时再展开。严禁把注入的设定块原样复述给用户。\n"
    "8. **分节格式（UI 会按此做标题/内容字体分层与明细收起）**：核心结论放最前、不加标题；"
    "其余分节用 `【小标题】` 起头（如 `【成长路线总览】` `【伏笔呼应】`），小标题与正文要有明确层级；"
    "含对照表/逐章明细/完整数据的内容，单独用 `【明细：…】` 分节（UI 默认收起，用户点开才看）。\n"
    "9. **设定文件优先于聊天记录结论**：回答时以设定文档 / 大纲 / 正文等「文件真源」为最高权威；"
    "聊天记录中得出的任何结论都只是参考，**不得用对话结论推翻 / 覆盖文件真源**。两者冲突时一律以文件为准。"
)

# ---- 工具定义（OpenAI function-calling schema） ----
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_chapter",
            "description": "读取指定章节的完整正文（按章节号）。用于精确查看某章内容后再决定如何改。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "integer", "description": "章节号，如 12 表示第12章"}
                },
                "required": ["chapter_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_all",
            "description": "在全本所有章节中检索关键词，返回含该关键词的段落（带章节号）。用于定位某设定/人物/线索在全书的分布与前后一致性。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "要检索的关键词"}
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_chapter",
            "description": "改写某章正文的精确片段：把 before 替换为 after（必须逐字匹配该章原文）。注意：此工具只把改写方案暂存为「待确认提案」，不会直接写入文件；写入需用户点「采纳并写入」。请务必先用 read_chapter 确认原文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "integer", "description": "章节号"},
                    "before": {"type": "string", "description": "原文中需替换的精确片段（逐字）"},
                    "after": {"type": "string", "description": "替换后的新片段"},
                },
                "required": ["chapter_id", "before", "after"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_guard",
            "description": "对指定章节运行一致性守卫检查（规则级），返回发现的矛盾/破绽。改写正文后调用以验证。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "integer", "description": "章节号"}
                },
                "required": ["chapter_id"],
            },
        },
    },
]


class DiscussionAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        config: Optional[Config],
        memory: Optional[MemoryEngine],
        project_root: str = "",
    ):
        self._llm = llm_client
        self._config = config
        self._memory = memory
        self._project_root = project_root
        self._disc_mem = DiscussionMemory(
            project_root=project_root,
            config_dir=str(config.config_dir) if config else "",
        )

    def _current_settings_rev(self) -> int:
        """读取 world_settings 当前设定版本号（settings_revision 表）。读不到返回 0。"""
        if not self._project_root:
            return 0
        try:
            import sqlite3 as _sq
            db = _sq.connect(
                os.path.join(self._project_root, ".novelforge", "archive.db")
            )
            row = db.execute("SELECT revision FROM settings_revision WHERE id=1").fetchone()
            db.close()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    @staticmethod
    def _call_with_retry(fn, label: str, retries: int = 2):
        """LLM 调用重试（间隔退避）；连续失败则如实上报「可能不是网络而是软件/配置问题」。"""
        last: Optional[Exception] = None
        for i in range(retries + 1):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                last = e
                if i < retries:
                    time.sleep(1 + i)
        raise RuntimeError(
            f"{label}连续失败 {retries + 1} 次（最后错误：{last}）。"
            "若持续出现，可能不是网络波动，而是软件或 API 配置问题——请检查设置或查看日志。"
        )

    @staticmethod
    def _context_turns(history: list[dict]) -> list[dict]:
        """连续上下文：回灌断点之后的 user+assistant 轮次（保持对话连续性，便于延续调整）。

        架构红线（防旧错误自我繁殖）：
        - 回灌前对每条 assistant 历史回答过 enforce_single_track 清洗，擦掉双轨等旧措辞；
        - 模型推理仍以设定文件 / 证据包等「文件真源」为最高权威（见 ROLE 第9条），
          历史记录仅作连贯参考，绝不盖过文件真源。
        """
        out: list[dict] = []
        for m in history:
            role = m.get("role")
            content = m.get("content")
            if not content:
                continue
            if role == "assistant":
                content, _changed, _hits = enforce_single_track(content)
            out.append({"role": role, "content": content})
        return out

    def build_messages(self, history: list[dict], user_msg: str, context: str = "") -> list[dict]:
        """拼完整 messages：system(角色+上下文) + 断点后连续历史(user+assistant) + 当前 user。

        历史回灌 user+assistant 轮次（保持连续性，便于延续调整）；assistant 回答已在回灌前
        过 enforce_single_track 清洗（防旧双轨措辞自我繁殖）。推理仍以设定文件真源为权威。
        """
        sys = ROLE
        if context:
            sys += "\n\n# 当前小说上下文\n" + context
        msgs: list[dict] = [{"role": "system", "content": sys}]
        for m in self._context_turns(history):
            msgs.append({"role": m["role"], "content": m["content"]})
        msgs.append({"role": "user", "content": user_msg})
        return msgs

    def stream_chat(self, messages: list[dict]) -> Generator[str, None, None]:
        """流式普通回复。"""
        yield from self._llm.chat_stream(messages)

    def propose(self, messages: list[dict], catalog: str = "") -> dict:
        """生成结构化调整提案（dict）。降级：普通 chat 解析。

        输出以「任务卡」为单位：本轮回答里每一类可落到数据层的改动，各生成一张卡，
        供用户在右侧任务卡栏逐张确认。模型只填 title/chapters/proposal；
        「剧情时间」「字段级改前值」由软件确定性推导，不交给模型。
        """
        instruction = (
            "请判断本轮对话里产生了哪些【可以落到数据层】的改动，并为每一类改动产出一张任务卡。\n"
            "只返回一个 JSON 对象，不要包含任何其他文字。\n"
            '格式：{"thinking": "简要分析", "tasks": [ ... ]}\n'
            "tasks 中每张卡：\n"
            '- {"title":"改动主题（≤20字，如「新增登仙隐藏境界」「同步第12-18章大纲」）",'
            '"chapters":[受影响的章节号，没有则留空数组],'
            '"rationale":"为什么要改（一句话）",'
            '"proposal":[ ...操作对象... ]}\n'
            "【什么算可落地】用户明确拍板 / 点名要求修改的设定调整（新增/修改世界观条目）、"
            "大纲改动、人物档案变更、伏笔增删与状态推进、事件状态推进、正文片段改写 —— 出卡。\n"
            "【什么不算（必须返回空 tasks）】下列情况一律不要出卡，硬凑出的卡会被软件丢弃：\n"
            "  ① 纯讨论、提问、分析、还没有定论的设想（如聊到『卷五归隐结局』『大结局』"
            "但用户没说要改什么）；\n"
            "  ② 讨论中**提到**的概念，但本轮没有形成『要改它』的结论；\n"
            "  ③ 任何 update_* 目标 id/章节号在下方【可写对象真实清单】里找不到的操作（那是幻觉，软件会拒绝）。\n"
            "判断原则：拿不准『用户要不要改』时，宁可返回空 tasks，也不要出卡。\n"
            "【粒度】同一类改动合并成一张卡（如「同步受影响章节大纲」是一张卡，不要每章一张）；"
            "性质不同的改动分开成多张卡，方便用户逐张确认或驳回。\n"
            "proposal 中每条是一个操作对象，op 取值与字段：\n"
            '- {"op":"add_foreshadow","description":"...","planted_chapter":12,"status":"planted","priority":3,"related_characters":["角色名"],"notes":"..."}\n'
            '- {"op":"update_foreshadow","id":"已有伏笔id","status":"advanced","resolution_chapter":45,"notes":"..."}\n'
            '- {"op":"set_event_status","event_id":"已有事件id","status":"done"}\n'
            '- {"op":"add_character","name":"...","role":"...","description":"..."}\n'
            '- {"op":"update_character","id":"已有角色id","description":"..."}\n'
            '- {"op":"edit_chapter_text","chapter_id":12,"before":"原文需替换的精确片段（逐字复制上下文中该章正文，可含少量前后句以保证唯一）","after":"替换后的新片段","rationale":"为什么改"}\n'
            '- {"op":"update_world_setting","id":"已有设定id 或 name+category","description":"设定条目的【完整新描述】","cascade_keywords":["主角成长","觉醒"],"except_chapters":[12,13]}\n'
            '    改设定文档（world_settings）：description 写整条新内容（不是片段）；写入会过双轨守卫，若写出「另一套体系」会被拦截。改完自动绑版本号，使旧讨论结论失效。\n'
            '- {"op":"update_outline","chapter_id":12,"fields":{"core_events":"...","plot_summary":"...","state_deltas":"...","writing_focus":"...","narrative_shots":"...","beat_sequence":"..."}}\n'
            '    改章节大纲（简大纲+深大纲，存于 chapter_outlines.json）：fields 只接受已知字段（core_events/plot_summary/chapter_type_label/opening_hook/ending_hook/state_deltas/continuity_anchors/writing_focus/narrative_shots/beat_sequence/event_progress/time_anchor/body_snippet 等），写盘前自动备份。\n'
            '【级联铁律】当你调整「主角成长线路 / 力量体系 / 核心设定」等会影响整体的设定时，必须在同一条 proposal 里：\n'
            '  ① 先用 update_world_setting 改设定文档；\n'
            '  ② 再用若干条 update_outline 把受影响的章节大纲（简大纲 core_events/plot_summary + 深大纲 state_deltas/continuity_anchors/writing_focus/narrative_shots/beat_sequence 等）一并改为与新设定一致；\n'
            '  ③ 在 update_world_setting 里用 except_chapters 列出已在②覆盖的章，避免重复级联提示。\n'
            '这样用户在讨论里点一次「采纳并写入」，设定→大纲→深大纲→（必要时正文）就整链路同步，而不是只改一处。\n'
            '注意：update_* 必须引用上下文中真实存在的 id；add_* 才创建新数据；'
            'edit_chapter_text 的 chapter_id 必须是上下文中已注入正文的章节号，且 before 必须逐字匹配该章原文，否则软件会拒绝写入。'
            'edit_chapter_text / update_outline 可在一个提案中多次出现，以实现「跨章批量改写」。每条加 "rationale" 说明为什么。'
        )
        if catalog:
            instruction = (
                "【可写对象真实清单】update_* 的 id / 章节号必须取自此处，严禁自造。"
                "清单里没有的实体，不要生成对应操作（会直接被丢弃）。\n"
                + catalog + "\n\n"
                + instruction
            )
        msgs = messages + [{"role": "user", "content": instruction}]
        try:
            data = self._llm.chat_json(msgs, temperature=0.4)
        except Exception:
            raw = self._llm.chat(msgs, temperature=0.4)
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {"proposal": []}
        if not isinstance(data, dict):
            data = {"tasks": []}
        # 兼容旧格式（顶层 proposal 数组）→ 包成一张任务卡
        if not isinstance(data.get("tasks"), list):
            data["tasks"] = []
        if not isinstance(data.get("proposal"), list):
            data["proposal"] = []
        data["tasks"] = self._normalize_tasks(data)
        return data

    def _build_writable_catalog(self) -> str:
        """列出当前小说『可写对象』的真实清单，喂给模型生成 proposal 时引用。

        根因：模型在生成 proposal 时**看不到真实 id 清单**，只会凭讨论里提到的
        概念（卷五归隐结局、大结局）硬凑 id（如 id="endgame"、人物="protagonist"、
        第250章），导致『找错地方』。把真实可写对象列出来，模型才有正确依据，
        从源头减少假 id；剩余漏网之鱼由 proposal.filter_valid_ops 软件侧兜底丢弃。
        """
        lines: list[str] = []
        # 章节号范围（确定性从大纲读，不靠模型猜）
        try:
            from domains.novel.core.discussion.context_builder import _load_outlines
            data = _load_outlines(self._project_root)
            chs = data if isinstance(data, list) else (data.get("chapters") or [])
            ids = [int(c.get("chapter_id", 0) or 0) for c in chs
                   if str(c.get("chapter_id", "")).isdigit()]
            if ids:
                lines.append(
                    f"章节号范围：1 ~ {max(ids)}（共 {len(ids)} 章；"
                    f"edit_chapter_text / update_outline 的 chapter_id 必须在此范围内）"
                )
        except Exception:
            pass
        if self._memory is not None:
            try:
                ws = self._memory.archive.get_world_settings() or []
                names = [w.name for w in ws if getattr(w, "name", None)][:120]
                if names:
                    lines.append(
                        "已有设定名（update_world_setting 用 name 精确引用其一）：\n"
                        + "、".join(names)
                    )
            except Exception:
                pass
            try:
                chars = self._memory.archive.get_all_characters() or []
                cnames = [c.name for c in chars if getattr(c, "name", None)][:80]
                if cnames:
                    lines.append(
                        "已有角色名（update_character 用 name 引用）：\n" + "、".join(cnames)
                    )
            except Exception:
                pass
            try:
                fss = self._memory.archive.get_active_foreshadows() or []
                fids = [f.id for f in fss if getattr(f, "id", None)][:40]
                if fids:
                    lines.append(
                        "已有伏笔 id（update_foreshadow 用）：\n" + "、".join(fids)
                    )
            except Exception:
                pass
            try:
                evs = self._memory.get_story_events() or []
                eids = [e.id for e in evs if getattr(e, "id", None)][:40]
                if eids:
                    lines.append(
                        "已有事件 id（set_event_status 用）：\n" + "、".join(eids)
                    )
            except Exception:
                pass
        return "\n".join(lines)

    def _normalize_tasks(self, data: dict) -> list[dict]:
        """把 propose 输出规范化为任务卡列表（兼容旧 proposal 顶层格式）。

        模型只提供 title/chapters/rationale/proposal；剧情时间与字段级改前值
        由 build_task_cards 在软件侧确定性补齐。
        """
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            ops = data.get("proposal") or []
            if isinstance(ops, list) and ops:
                tasks = [{"title": "本轮改动", "proposal": ops}]
            else:
                return []
        out: list[dict] = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            ops = t.get("proposal") or []
            if not isinstance(ops, list) or not ops:
                continue
            chs: list[int] = []
            for c in (t.get("chapters") or []):
                try:
                    chs.append(int(c))
                except Exception:
                    pass
            out.append({
                "title": str(t.get("title") or "未命名改动")[:40],
                "chapters": chs,
                "rationale": str(t.get("rationale") or ""),
                "proposal": ops,
            })
        return out

    # ------------------------------------------------------------------
    # 第4步：工具增强 agent loop
    # ------------------------------------------------------------------
    def _tool_executor(self, name: str, args: dict, staged: Optional[list] = None) -> str:
        """执行 agent 发起的工具调用，返回结果文本（回灌给模型）。
        staged: 若提供，edit_chapter 的改写方案会被暂存进此列表（待确认，不落盘）。
        """
        from domains.novel.core.consistency.checker import ConsistencyChecker
        from domains.novel.core.discussion.context_builder import (
            _read_chapter, _scan_all_chapters_compact,
        )

        try:
            if name == "read_chapter":
                cid = int(args.get("chapter_id", 0) or 0)
                txt = _read_chapter(self._project_root, cid, 4000)
                return txt or f"第{cid}章正文不存在或为空"
            elif name == "search_all":
                kw = [str(args.get("keyword", "")).strip()]
                if not kw[0]:
                    return "未提供关键词"
                return (
                    _scan_all_chapters_compact(self._project_root, kw)
                    or f"全书未检索到关键词『{kw[0]}』——若无相关证据，请如实告知用户，不要编造"
                )
            elif name == "edit_chapter":
                cid = int(args.get("chapter_id", 0) or 0)
                if staged is not None:
                    staged.append({
                        "op": "edit_chapter_text",
                        "chapter_id": cid,
                        "before": str(args.get("before", "")),
                        "after": str(args.get("after", "")),
                        "rationale": "",
                    })
                    return f"已暂存第{cid}章正文改写（待用户确认后写入）"
                return "（暂存失败：缺少暂存容器）"
            elif name == "run_guard":
                cid = int(args.get("chapter_id", 0) or 0)
                chapter_text = _read_chapter(self._project_root, cid, 4000) or ""
                checker = ConsistencyChecker(self._memory, self._llm)
                rep = checker.check(chapter_id=cid, chapter_text=chapter_text, enable_llm_check=False)
                issues = getattr(rep, "issues", []) or []
                if not issues:
                    return f"第{cid}章一致性检查通过，未发现矛盾"
                return "\n".join(
                    f"[{getattr(i, 'severity', '')}] {getattr(i, 'category', '')}: {getattr(i, 'description', '')}"
                    for i in issues
                )
        except Exception as e:
            return f"工具执行异常：{e}"
        return "未知工具"

    # ------------------------------------------------------------------
    # 三阶段流水线（模仿 WorkBuddy 的流程）：规划 → 检索 → 思考
    # 阶段1 先让 LLM 决定「怎么读、读哪里」；阶段2 软件按计划确定性检索、
    #      读完把证据留成「证据包」；阶段3 LLM 只基于证据包组织回答。
    # ------------------------------------------------------------------
    def _planning_brief(self, current_chapter_id=None) -> str:
        """精简项目概况（仅用于阶段1规划，省 token）。"""
        lines: list[str] = []
        if self._project_root:
            ch_dir = os.path.join(self._project_root, "chapters")
            if os.path.isdir(ch_dir):
                import glob as _g
                n = len(_g.glob(os.path.join(ch_dir, "chapter_*.md")))
                lines.append(f"全书共 {n} 章")
        if current_chapter_id:
            lines.append(f"用户当前在看第{current_chapter_id}章")
        # 设定层（L0–L2 规划骨架：故事脊柱/主线弧/暗线/卷纲/体系规格）
        # —— 这是主角成长/主线/体系的权威来源，规划阶段就让 AI 知道顶层设计
        from domains.novel.core.discussion.context_builder import (
            _load_outlines, _outline_map_text, _settings_text,
        )
        settings = _settings_text(self._project_root, 2600)
        if settings:
            lines.append("小说设定层（规划骨架·权威来源）：\n" + settings)
        # 章节地图：大纲是小说规划的骨架，让规划器按「章节类型/核心事件」语义定位
        outs = _load_outlines(self._project_root)
        om = _outline_map_text(outs)
        if om:
            lines.append("章节地图（大纲）：\n" + om)
        if self._memory is not None:
            try:
                chars = self._memory.archive.get_all_characters() or []
                if chars:
                    lines.append("人物：" + "、".join(c.name for c in chars[:20]))
            except Exception:
                pass
            try:
                fss = self._memory.archive.get_active_foreshadows() or []
                if fss:
                    lines.append("活跃伏笔：" + "；".join(f.description[:36] for f in fss[:8]))
            except Exception:
                pass
            try:
                evs = self._memory.get_story_events() or []
                if evs:
                    lines.append("事件：" + "、".join(e.title for e in evs[:12]))
            except Exception:
                pass
        return "\n".join(lines) or "（无）"

    # 记忆触发词：用户说这些词时可能想长期记住某条规则/决策
    _MEMORY_TRIGGERS = (
        "记住", "以后", "记得", "规则", "约定", "要求", "别", "不要", "禁止",
        "统一", "定了", "就这样", "以后都",
    )

    def _extract_memory(self, user_msg: str) -> None:
        """检测用户消息里的记忆指令，LLM 分类后分层写入（软件级/本书规则/本书决策）。"""
        if not self._disc_mem or not any(t in user_msg for t in self._MEMORY_TRIGGERS):
            return
        try:
            data = self._call_with_retry(
                lambda: self._llm.chat_json([
                    {"role": "system", "content":
                     "你是小说工作台记忆记录器。用户消息里可能含有需要长期记住的规则/约定/决策。"
                     '只输出 JSON：{"items":[{"scope":"software|project_rule|project_decision","content":"一句话要点（陈述句，去掉“记住/以后”等词）"}]}\n'
                     "scope 含义：software=关于软件行为/回答方式的偏好（如“回答先给结论”）；"
                     "project_rule=针对当前这本小说的规则（如“主角的剑叫青云”）；"
                     "project_decision=本书讨论拍板的决策（如“确定第27章为全书转折点”）。"
                     "没有值得记的内容则 items 为空数组。不要在内容里混入文章情节复述。"},
                    {"role": "user", "content": user_msg},
                ], temperature=0.1),
                "记忆提取",
            )
        except Exception:
            return
        if not isinstance(data, dict):
            return
        for it in (data.get("items") or []):
            if not isinstance(it, dict):
                continue
            scope = it.get("scope")
            content = str(it.get("content", "")).strip()
            if not content or scope not in ("software", "project_rule", "project_decision"):
                continue
            self._disc_mem.add(scope, [content], rev=self._current_settings_rev())

    @staticmethod
    def _verify_answer(final: str, meta: dict) -> dict:
        """自检：抽取回答引用的章节号，核对真实存在、且属于证据包覆盖范围。"""
        cited = sorted({int(m) for m in re.findall(r"第\s*(\d{1,3})\s*章", final)})
        all_ids = set(meta.get("all_chapter_ids") or [])
        covered = set(meta.get("read_chapters") or []) | set(meta.get("scanned_ids") or [])
        missing = [c for c in cited if c not in all_ids]
        suspicious = [c for c in cited if c not in covered]
        return {
            "cited": cited,
            "missing": missing,
            "suspicious": suspicious,
            # 流程保障：有证据却一处章节都没引用 → 判定为"空谈"，UI 标 ⚠️
            "no_citations": not cited and bool(meta.get("read_chapters") or meta.get("scanned_ids")),
        }

    # 叙述性动词/高频词：内容自检时过滤，避免把"出手/相助/主角/登场"等当实体误报
    _NARRATION_STOPWORDS = set(
        "出手 相助 击败 打败 击杀 杀死 战斗 发生 进行 变成 出现 发现 看到 看着 知道 告诉 "
        "觉得 感觉 开始 结束 然后 其中 于是 最终 终于 突然 直接 再次 只能 可以 可能 应该 "
        "没有 就是 并且 或者 但是 因为 所以 如果 虽然 即使 表现 展现 透露 揭示 表明 意味着 "
        "暗示 铺垫 呼应 处理 面对 陷入 遭遇 来到 前往 离开 回到 获得 得到 失去 拥有 变强 "
        "突破 晋升 修炼 升级 实力 战力 主角 少年 少女 对方 二人 两人 三人 众人 他们 她们 "
        "此时 此刻 这里 那里 这个 那个 一下 起来 出来 过去 下来 上去 回来 回去 作为 位于 "
        "来自 属于 关于 对于 通过 借助 利用 依靠 登场 现身 出场 离去 归来 进入 走出 踏入 "
        "迎面 相遇 相见 遇到 望着 看着 低语 开口 说道 喊道".split()
    )

    # 否定/排除语境：这类词引导的实体不应被当成"正文确有此内容"去核验
    # （如"正文未找到『境界』""第X章没有提到运气的描写"——自我声明缺失，查不到≠编造）
    _NEG_CTX = set("未 没 没有 无 不存在 找不到 查无 缺乏 缺 暂未 尚未 并无 不含 不含 不含".split())

    @staticmethod
    def _verify_answer_content(final: str, project_root: str) -> list[dict]:
        """内容级自检：对回答中每处『第N章』引用，抽取其后实体词（引号内优先，
        否则 jieba 分词过滤停用词与叙述性动词），核对该实体是否真实出现在该章正文；
        分词切碎的人名/物品名（如 诸葛+青）会用相邻词拼接试配。

        关键防误报：抽取窗口内若实体词前面紧跟否定词（未/没有/找不到/缺乏…），
        或整句是"未找到X/没有提到X"的自我声明，则该词**跳过核验**——
        它被声明为缺失，查不到正文是正常的，不该标 ⚠️。

        返回 [{"chapter": N, "words": [...], "missing": [未命中的词]}...]。
        抽不出实体词的引用跳过（未断言具体内容则无需核验）。
        """
        import jieba
        from domains.novel.core.discussion.context_builder import _read_chapter, _STOPWORDS
        results: list[dict] = []
        for m in re.finditer(r"第\s*(\d{1,3})\s*章", final):
            cid = int(m.group(1))
            window = final[m.end(): m.end() + 30]
            words: list[str] = []
            quoted = re.findall(r"[“\"「『]([^”\"」』]{2,8})[”\"」』]", window)
            if quoted:
                # 引号内可能含顿号/逗号分隔的多个实体（『赵平、锻体、五重』）→ 拆词逐验
                words = []
                for q in quoted:
                    parts = re.split(r"[、，,]", q)
                    words.extend(p.strip() for p in parts if 2 <= len(p.strip()) <= 6)
                words = words[:3]
            else:
                tokens = [t for t in jieba.lcut(window) if len(t) >= 2 and t not in _STOPWORDS]
                words = [
                    t for t in tokens
                    if 2 <= len(t) <= 6 and t not in DiscussionAgent._NARRATION_STOPWORDS
                ][:3]
            # 否定语境排除：窗口内含"未找到/没有提到/缺乏X"式的自我声明 → 该实体跳过
            neg_claim = any(
                re.search(r"(未找到|没有(?:找到|提到|出现|描写)|缺乏|查无|暂未|尚未)[『\"「]?" + re.escape(w), window)
                for w in words
            )
            # 单实体前紧跟否定词（如"未找到『境界』"中 境界 前是 找到，但"第1章未找到境界"也覆盖）
            if neg_claim:
                words = []
            if not words:
                continue
            body = _read_chapter(project_root, cid, 9_999_999) or ""
            # 去标点版本：引号内短语常与正文差逗号/顿号（"每次觉醒，必失一物"）
            body_nopunct = re.sub(r"[，。！？、；：\s\u3000]", "", body)
            missing: list[str] = []
            for w in words:
                w_clean = re.sub(r"[，。！？、；：\s\u3000]", "", w)
                if not w_clean:
                    continue
                if w in body or w_clean in body_nopunct:
                    continue
                # 残词前缀试配（"以一敌" ← 正文"以一敌三"）
                if re.search(re.escape(w) + r"[\u4e00-\u9fa5]", body):
                    continue
                # 相邻词拼接试配（jieba 切碎的人名/物品名，如 诸葛+青）
                joined = any(
                    other != w and (w + other in body or other + w in body)
                    for other in words
                )
                if not joined:
                    missing.append(w)
            results.append({"chapter": cid, "words": words, "missing": missing})
        return results

    def _plan_retrieval(self, user_msg: str, brief: str) -> dict:
        """阶段1：LLM 分析用户问题，输出检索计划（关键词/档案/回答结构）。"""
        from domains.novel.core.discussion.context_builder import _expand_keywords
        instruction = (
            "先不要回答用户的问题。请先分析『用户问题』，输出一个 JSON 检索计划（只输出 JSON，不要任何其他文字）：\n"
            "{\n"
            '  "keywords": ["用于全书检索的具体实体词：人名/功法名/物品名/境界名，2-5 个；不要抽象概括词（如“养成路线”要拆成 修为/境界/突破）"],\n'
            '  "archive": ["需要从记忆档案查证的条目类型，character/foreshadow/event 的子集"],\n'
            '  "structure": "回答组织方式，取 timeline(按时间线) / by_topic(按主题分块) / compare(对比) / issue(列问题) 之一",\n'
            '  "steps": ["任务拆解：你要执行的步骤简述，1-4 条，如 梳理修炼节点 / 核对前后一致性"]\n'
            "}\n"
            "『项目概况』（只用于规划）：\n" + brief + "\n"
            "『用户问题』：" + user_msg
        )
        try:
            data = self._call_with_retry(
                lambda: self._llm.chat_json(
                    [{"role": "system", "content": "你是小说检索规划器，只输出 JSON。"},
                     {"role": "user", "content": instruction}],
                    temperature=0.2,
                ),
                "检索规划",
            )
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        kws = [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()]
        kws = _expand_keywords(kws)  # 抽象词 → 正文高频词
        arch = [a for a in (data.get("archive") or []) if a in ("character", "foreshadow", "event")]
        struct = data.get("structure") if data.get("structure") in ("timeline", "by_topic", "compare", "issue") else "timeline"
        steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()][:4]
        return {"keywords": kws[:8], "archive": arch, "structure": struct, "steps": steps}

    def _execute_plan(self, plan: dict, user_msg: str = "", current_chapter_id=None, max_chapters: int = 3) -> tuple[str, dict]:
        """阶段2：按计划确定性检索，读完把证据留成「证据包」。

        意图分流（核心改造）：
          - 规划/梳理类（路线/体系/主线/伏笔/成长…）→ 零正文扫描，
            证据包 = 设定层(L0–L2) + 简/深大纲(chapter_outlines 全字段) + 人物档案。
            正文是"被修正对象"，不是规划讨论的依据。
          - 核查/改文/具体情节类（前后一致/哪章/改文…）→ 才扫描正文 + AI 语义定位精读。
        """
        from domains.novel.core.discussion.context_builder import (
            _read_chapter, _scan_all_chapters_with_stats,
            _load_outlines, _outline_map_text, _settings_text,
            _outline_full_text, is_planning_intent,
        )
        parts: list[str] = []
        meta: dict = {"read_chapters": [], "scanned_chapters": 0, "scanned_ids": [], "all_chapter_ids": [], "mode": "check"}
        all_ids: set[int] = set()
        if self._project_root:
            import glob as _g
            all_ids = {
                int(re.search(r"chapter_(\d+)\.md", p).group(1))
                for p in _g.glob(os.path.join(self._project_root, "chapters", "chapter_*.md"))
            }
            meta["all_chapter_ids"] = sorted(all_ids)

        planning = is_planning_intent(user_msg)
        if planning:
            # ---- 规划类：只用设定层 + 简/深大纲，跳过正文扫描 ----
            meta["mode"] = "plan"
            settings = _settings_text(self._project_root, 5200)
            if settings:
                parts.append("【小说设定层 · 规划骨架（权威来源）】\n" + settings)
            outlines = _outline_full_text(self._project_root, 6000)
            if outlines:
                parts.append("【简大纲 + 深大纲（每章核心事件/状态变化/伏笔/承上启下）】\n" + outlines)
            # 人物档案（结构化结论，非正文）
            if self._memory is not None:
                try:
                    chars = self._memory.archive.get_all_characters() or []
                    if chars:
                        parts.append("【人物档案】\n" + "\n".join(
                            f"- {c.name}（{getattr(c, 'role', '')}）：{getattr(c, 'current_state', '') or getattr(c, 'description', '')}"
                            for c in chars[:20]))
                except Exception:
                    pass
            parts.append(
                "【讨论约束】此为规划类问题：回答须基于上述设定层与大纲，"
                "无需扫描正文；若大纲/设定未覆盖某细节，明确说明资料不足，严禁编造正文情节。"
            )
            return "\n\n".join(parts), meta

        # ---- 核查/改文类：扫描正文 + AI 语义定位精读 ----
        kws = plan.get("keywords") or []
        scan_text, hits = _scan_all_chapters_with_stats(self._project_root, kws) if (kws and self._project_root) else ("", {})
        meta["scanned_chapters"] = len(hits)
        meta["scanned_ids"] = list(hits.keys())

        # AI 语义定位 + 证据精简
        read_list: list[int] = []
        snippets: list[str] = []
        try:
            omap = _outline_map_text(_load_outlines(self._project_root), 2000)
            hit_marks = "\n".join(
                f"第{c}章：命中{n}句" for c, n in sorted(hits.items(), key=lambda x: -x[1])[:20]
            ) or "（无命中）"
            ai_prompt = (
                "你是小说检索助理。用户问题与关键词粗扫结果如下。请语义判断哪些章节最值得精读"
                "（命中多不一定相关，要结合章节类型与核心事件判断）。\n"
                '只输出 JSON：{"read_chapters":[章节号,最多3个,按相关性排序],"evidence_snippets":["第12章：精简后的关键证据，≤120字",最多8条]}\n'
                "用户问题：" + user_msg + "\n"
                "章节地图（大纲）：\n" + (omap or "（无）") + "\n"
                "关键词命中分布：\n" + hit_marks + "\n"
                "粗扫命中句：\n" + (scan_text[:2400] or "（无）")
            )
            data = self._call_with_retry(
                lambda: self._llm.chat_json(
                    [{"role": "system", "content": "你是小说检索助理，只输出 JSON。"},
                     {"role": "user", "content": ai_prompt}],
                    temperature=0.2,
                ),
                "AI 检索定位",
            )
            if isinstance(data, dict):
                reads = [int(c) for c in (data.get("read_chapters") or []) if isinstance(c, int) or str(c).isdigit()]
                read_list = list(dict.fromkeys(c for c in reads if c in all_ids))[:max_chapters]
                snippets = [str(s).strip() for s in (data.get("evidence_snippets") or []) if str(s).strip()][:8]
        except Exception:
            pass

        if not read_list:
            read_list = [c for c, _ in sorted(hits.items(), key=lambda x: -x[1])][:max_chapters]
        if current_chapter_id and current_chapter_id not in read_list and len(read_list) < max_chapters:
            read_list.append(current_chapter_id)

        # 证据包：设定层置顶（锚定规划意图）
        settings = _settings_text(self._project_root, 5200)
        if settings:
            parts.append("【小说设定层 · 规划骨架（权威来源）】\n" + settings)
        if snippets:
            parts.append("【AI 语义定位的关键证据】\n" + "\n".join(snippets))
        elif scan_text:
            parts.append(f"【全书检索 · 关键词：{'、'.join(kws[:6])}】\n{scan_text}")

        # 精读：所选章全文 + 该章大纲意图
        outline_map = {o.get("chapter_id"): o for o in (_load_outlines(self._project_root) or [])}
        for c in read_list[:max_chapters]:
            body = _read_chapter(self._project_root, c, 3500)
            if body:
                parts.append(f"【第{c}章全文】\n{body}")
                meta["read_chapters"].append(c)
            ol = outline_map.get(c)
            if ol:
                ev = (ol.get("core_events") or "").strip().replace("\n", " ")
                if len(ev) > 220:
                    ev = ev[:220] + "…"
                parts.append(
                    f"【第{c}章大纲意图】{ol.get('chapter_type_label', '') or ''}"
                    f"《{ol.get('title', '')}》：{ev}"
                )
        # 档案
        arch = plan.get("archive") or []
        if self._memory is not None:
            try:
                if "character" in arch or not arch:
                    chars = self._memory.archive.get_all_characters() or []
                    if chars:
                        parts.append("【人物档案】\n" + "\n".join(
                            f"- {c.name}（{getattr(c, 'role', '')}）：{getattr(c, 'current_state', '') or getattr(c, 'description', '')}"
                            for c in chars[:20]))
            except Exception:
                pass
            try:
                if "foreshadow" in arch:
                    fss = self._memory.archive.get_active_foreshadows() or []
                    if fss:
                        parts.append("【活跃伏笔】\n" + "\n".join(f"- [{f.id}] {f.description}（{f.status}）" for f in fss[:15]))
            except Exception:
                pass
            try:
                if "event" in arch:
                    evs = self._memory.get_story_events() or []
                    if evs:
                        parts.append("【事件账本】\n" + "\n".join(f"- [{e.id}] {e.title}（{e.status}）" for e in evs[:15]))
            except Exception:
                pass
        if not scan_text and not meta["read_chapters"]:
            parts.append(
                "【检索提示】全书未检索到与该问题直接相关的证据——请如实告知用户资料不足，"
                "建议指定章节或用更具体的关键词，严禁编造。"
            )
        return "\n\n".join(parts), meta

    def _generate_answer(self, user_msg: str, history: list[dict], plan: dict, evidence: str) -> str:
        """阶段3：LLM 只基于证据包组织回答（结论先行 / 按结构 / 引用章节）。"""
        struct_guide = {
            "timeline": "结论先行；按「设定/体系 → 伏笔/暗线 → 背景/世界观 → 章节落地印证」四层组织，"
                        "章节号只作轻锚点（如『（见第12章印证）』），禁止堆砌章节罗列。",
            "by_topic": "按主题分块组织，每块先讲设定/伏笔/背景，再附章节印证，结论先行。",
            "compare": "做逐项对比（A vs B），每项先比设定层差异，再标章节落地，最后给结论。",
            "issue": "逐条列出问题/矛盾，每条先指设定层缺口，再标章节与依据，最后给建议。",
        }.get(plan.get("structure"), "结论先行；按「设定/体系 → 伏笔/暗线 → 背景/世界观 → 章节落地印证」"
                                    "四层组织，章节号只作轻锚点，禁止章节罗列。")
        sys = ROLE
        mem_text = self._disc_mem.format_injection(self._current_settings_rev()) if self._disc_mem else ""
        if mem_text:
            sys += "\n\n# 跨会话记忆（用户定过的规则/决策，必须遵守）\n" + mem_text
        sys += "\n\n# 已检索到的证据包（真实数据，回答必须严格基于这些）\n" + evidence
        sys += "\n证据优先级：证据包中的「小说设定层（规划骨架）」是主角成长/主线/体系的权威顶层设计，" \
               "回答此类问题须优先据此展开，正文检索结果只作印证与落地（具体章节证据）。" \
               "两者冲突时以设定层为锚、指出正文缺失，严禁用正文碎片推翻规划设定。"
        # 力量体系结构保证：战力/修炼/境界/觉醒类问题，把 canonical 单轨对象作为
        # 【唯一权威】注入系统提示，并硬性要求答案只能据此推导——LLM 物理上只读到
        # 这一个单轨结构化真源，无法从"两套体系"的歧义自由文本现编第二套（结构性，非守卫）。
        if is_power_intent(user_msg):
            sys += "\n\n# 力量体系·唯一权威真源（以下为回答战力/修炼/境界/觉醒类问题的【唯一】依据）\n" \
                   + power_canon_block("")
        sys += "\n回答骨架铁律：设定/伏笔/背景是主干，章节号是轻锚点。规划类问题（成长路线/体系/伏笔规划）" \
               "必须完全基于设定层展开，绝不从正文反推；若设定层缺失某项核心（如战力阶梯），须直接指出缺口而非回避。"
        sys += "\n回答结构要求：" + struct_guide
        sys += " 若证据不足且用户问题模糊，先列出 1-3 个澄清问题反问，不要硬答。"
        msgs: list[dict] = [{"role": "system", "content": sys}]
        for m in self._context_turns(history):
            msgs.append({"role": m["role"], "content": m["content"]})
        msgs.append({"role": "user", "content": user_msg})
        return self._call_with_retry(
            lambda: self._llm.chat(msgs, temperature=0.7), "回答生成"
        )

    def run_three_stage(
        self,
        user_msg: str,
        history: list[dict],
        current_chapter_id=None,
        max_chapters: int = 3,
        on_stage=None,
    ) -> dict:
        """三阶段流水线：规划（怎么读/读哪里）→ 检索（读完留数据）→ 思考（基于证据回答）。

        on_stage(stage: str, message: str)：阶段回调，供 UI 实时显示思考进度。
        返回 {"content": 最终回答, "plan": 检索计划, "evidence": 检索元信息, "proposal": 改写提案}。
        """
        def emit(stage: str, msg: str):
            if on_stage:
                on_stage(stage, msg)

        emit("plan", "⏳ 阶段1/3 正在分析问题、制定检索计划…")
        self._extract_memory(user_msg)  # 记忆指令检测：分层写入软件级/本书规则/本书决策
        brief = self._planning_brief(current_chapter_id)
        mem_text = self._disc_mem.format_injection(self._current_settings_rev()) if self._disc_mem else ""
        if mem_text:
            brief += "\n\n【跨会话记忆】\n" + mem_text
        plan = self._plan_retrieval(user_msg, brief)
        kws = plan.get("keywords") or []
        emit(
            "retrieve",
            f"⏳ 阶段2/3 正在检索全书（关键词：{'、'.join(kws[:3])}{'…' if len(kws) > 3 else ''}）…",
        )
        evidence, meta = self._execute_plan(plan, user_msg, current_chapter_id, max_chapters)
        read = meta.get("read_chapters") or []
        mode = meta.get("mode", "check")
        if mode == "plan":
            emit("retrieve", "⏳ 阶段2/3 规划类问题：基于设定层 + 简/深大纲组织（不扫描正文）…")
            emit("answer", "⏳ 阶段3/3 正在基于设定层与大纲组织回答…")
        else:
            emit(
                "retrieve",
                f"⏳ 阶段2/3 正在检索全书（关键词：{'、'.join(kws[:3])}{'…' if len(kws) > 3 else ''}）…",
            )
            emit(
                "answer",
                f"⏳ 阶段3/3 正在基于证据组织回答（精读第{'/'.join(str(c) for c in read)}章）…",
            )
        final = self._generate_answer(user_msg, history, plan, evidence)
        # 自检：回答引用的章节是否真实存在、是否属于证据包覆盖范围
        verify = self._verify_answer(final, meta)
        # 内容级自检：每处『第N章』引用的实体词是否真在该章正文
        verify["content"] = self._verify_answer_content(final, self._project_root)
        # 任务卡：本轮回答里凡是能落到数据层的结论，都生成待确认任务卡。
        # 不再限于「改成/删掉」等改写动词——设定拍板、大纲调整、人物/伏笔变更同样出卡
        # （此前窄触发导致用户拍板的设定改动永远变不成可确认的卡片）。
        # 模型判断本轮无可落地改动时返回空 tasks，不产生卡片。
        tasks: list[dict] = []
        emit("propose", "⏳ 正在核对本轮可落地的改动…")
        try:
            msgs = self.build_messages(
                history, user_msg,
                context=evidence + "\n（上述为回答参考，提案的 before 必须逐字匹配对应章节正文）",
            )
            catalog = self._build_writable_catalog()
            tasks = (self.propose(msgs, catalog=catalog).get("tasks") or [])
        except Exception:
            tasks = []
        # 兼容旧 UI 路径：顶层 proposal 取首张卡的操作
        proposal = list(tasks[0]["proposal"]) if tasks else []
        return {
            "content": final, "plan": plan, "evidence": meta,
            "proposal": proposal, "tasks": tasks, "verify": verify,
        }

    def run_agent_loop(
        self,
        user_msg: str,
        history: list[dict],
        context: str = "",
        max_rounds: int = 8,
    ) -> dict:
        """工具增强 agent loop：模型可多轮调用工具，直到给出最终回答。

        返回 {"content": 最终文本, "trajectory": [步骤记录]}。
        trajectory 每一项：{"role":"assistant","content":...,"tool_calls":[...]}
        或 {"role":"tool","name":...,"result":...}。供 UI 透明展示。
        """
        msgs = self.build_messages(history, user_msg, context)
        trajectory: list[dict] = []
        staged: list[dict] = []
        for _ in range(max_rounds):
            resp = self._llm.chat_with_tools(msgs, TOOLS, temperature=0.3)
            msgs.append(resp["assistant_message"])
            trajectory.append({
                "role": "assistant",
                "content": resp["content"],
                "tool_calls": resp["tool_calls"],
            })
            if not resp["tool_calls"]:
                return {"content": resp["content"], "trajectory": trajectory, "proposal": staged}
            for tc in resp["tool_calls"]:
                result = self._tool_executor(tc["name"], tc["arguments"], staged)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                trajectory.append({
                    "role": "tool",
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                    "result": result,
                })
        return {
            "content": "（已达到最大工具轮次，未能在限定步数内完成，请拆分任务或重新说明）",
            "trajectory": trajectory,
            "proposal": staged,
        }
