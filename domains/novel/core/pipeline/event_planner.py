"""
EventPlanner - 故事事件账本规划 pass。

职责：把「这一章应该发生什么」的共享计划建起来，解决「章节分开生成做不好」。

两步：
1. 解析 volumes.json / master.md 的卷规划建骨架（main/major 事件 + 章节窗口），零额外 token。
2. 可选跑一次 AI 规划 pass：基于骨架 + 总纲补全 minor 事件与精准窗口（每书/每卷一次，极省）。

产物：写入 archive.event_ledger。每章生成时由引擎读取窗口内事件作为硬约束。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from domains.novel.core.memory.archive_layer import ArchiveLayer, StoryEvent
from domains.novel.utils.logger import logger


def _load_volumes(project_root: str) -> list[dict]:
    try:
        from domains.novel.core.pipeline import volumes as vol_mod
        vols = vol_mod.load_volumes(Path(project_root))
        if vols:
            return vols
    except Exception:
        pass
    # 回退：直接读 volumes.json
    p = Path(project_root) / ".novelforge" / "volumes.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get("volumes", [])
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def build_skeleton(project_root: str) -> list[StoryEvent]:
    """解析卷规划建骨架：每卷一个 main(卷高潮) 事件 + 卷内若干 major 事件（按 summary 分句）。

    零 LLM，纯解析。窗口 = 卷的 [start_chapter, end_chapter]。
    """
    vols = _load_volumes(project_root)
    events: list[StoryEvent] = []
    now = datetime.now().isoformat()
    if not vols:
        # 没有卷结构：默认整本书一个 main 事件
        events.append(StoryEvent(
            type="main", title="全书主线推进", description="基于总纲推进全书核心主线",
            target_start=1, target_end=9999, status="planned", priority=5,
            created_at=now, updated_at=now,
        ))
        return events

    for v in vols:
        s = int(v.get("start_chapter", 1))
        e = int(v.get("end_chapter", s))
        title = v.get("title", f"第{v.get('id','?')}卷")
        theme = v.get("theme", "")
        summary = v.get("summary", "")
        # 卷高潮 = main 事件，窗口落在卷末 60% 段（卷末质变）
        climax_start = s + max(1, int((e - s) * 0.6))
        events.append(StoryEvent(
            type="main",
            title=f"{title}·卷末高潮",
            description=f"{theme}；{summary}",
            target_start=climax_start, target_end=e,
            status="planned", priority=5,
            linked_milestone=f"第{v.get('id')}卷收束",
            created_at=now, updated_at=now,
        ))
        # major 事件：把 summary 按句号切，每段一个 major（落在该卷前半段分布）
        sentences = [x.strip() for x in summary.replace("；", "。").split("。") if x.strip()]
        if len(sentences) > 1:
            span = max(1, (climax_start - s) // len(sentences))
            for i, sent in enumerate(sentences[:-1]):
                es = s + i * span
                ee = min(climax_start - 1, es + span)
                events.append(StoryEvent(
                    type="major",
                    title=f"{title}·阶段事件{i+1}",
                    description=sent,
                    target_start=es, target_end=max(ee, es),
                    status="planned", priority=3,
                    created_at=now, updated_at=now,
                ))
    return events


def _ai_plan_prompt(skeleton: list[StoryEvent], master_md: str, volumes_text: str) -> str:
    skel_json = json.dumps(
        [{"type": e.type, "title": e.title, "description": e.description,
          "target_start": e.target_start, "target_end": e.target_end,
          "priority": e.priority} for e in skeleton],
        ensure_ascii=False, indent=2,
    )
    return f"""你是一位专业长篇网文「故事事件规划师」。请基于全书总纲与卷规划，产出一份**贯穿全书的计划事件账本**。

已有骨架（由卷规划解析得出，你必须沿用其卷窗口边界，不要跳出各卷 start/end）：
{skel_json}

全书总纲（前 N 行）：
{master_md[:6000]}

卷结构：
{volumes_text}

请补全并细化，输出 JSON 数组，每条：
{{
  "type": "main|major|minor",
  "title": "事件标题（8-20字，有悬念/吸引力）",
  "description": "事件内容概述（一句话）",
  "background": "事件背景/前因（一句话，说明此事因何而起、与何相关）",
  "target_start": 整数（章节号，必须 >= 该卷 start_chapter）,
  "target_end": 整数（章节号，必须 <= 该卷 end_chapter；minor 可窄至 1 章）,
  "depends_on": ["前置事件标题（可选）"],
  "priority": 1-5,
  "progress": [{{"chapter": 整数, "title": "该章检查点标题", "summary": "这一章在事件中发生什么/铺垫什么（一句话）", "relative_time": {{"relative_to": "相对基准（必须显式写明，如『本事件起点』『上一检查点』『《事件标题》起点』）", "offset_days": 数字（相对基准的第几天，可小数，如 3 / 7.5）, "note": "相对说明（可选）"}}}}]
}}

要求：
1. main = 每卷卷末高潮/主线质变节点（每卷 1 个，与骨架对齐）；major = 卷内重要转折/中型冲突；minor = 日常/填充/小爽点（让章节不空，每 3-5 章至少一个 minor）。
2. 事件必须沿章节单调递增分布，覆盖每一章附近都有可推进的事件（避免某段 10+ 章没有任何计划事件）。
3. 后期卷（末卷）的事件可含收束/抉择；前期卷不要提前透支后期真相。
4. target_start/target_end 必须落在所属卷的 [start_chapter, end_chapter] 内。
5. 总数控制在 40-70 条（350 章量级）；事件标题不得重复。
6. 每条事件的 progress 字段请列出其窗口内**关键章节的检查点**（至少覆盖窗口起、窗口止，以及 2-4 个中间转折章），summary 说明该章在事件中的进展/铺垫，使「先想清楚怎么做、再把内容铺垫好、逐步推进不吃书」可被落实。
7. **时间维度（相对，必须 AI 输出）**：每个 progress 检查点的 relative_time 必须给出「相对本事件起点的第几天」，且 relative_to 必须显式写明相对基准（如「本事件起点」「上一检查点」），不得只给孤立天数——下游 AI 需要靠 relative_to 判断这是相对哪个事件/进展的时间维度，否则无法理解「第几天」相对谁。offset_days 可小数（如 3.5 表示三天半）。
8. **事件标准（务必遵守）**：每条事件须含「第几章 + 时间维度(relative_time) + 进展(progress) + 背景(background) + 对应章给对应内容」；progress 的 summary **全是大概思路、不给结果**——只点出该章铺垫什么方向/埋什么钩子，绝不写死具体对话、结局或尚未发生的后续。
9. 只输出 JSON 数组，不要其他内容。"""


def plan_story_events(
    project_root: str,
    archive: ArchiveLayer,
    llm=None,
    use_ai: bool = True,
    overwrite: bool = True,
) -> list[StoryEvent]:
    """规划并写入事件账本。返回写入的事件列表。

    - overwrite=True 时先清空旧账本再重建；否则在原有基础上追加。
    - use_ai=True 且 llm 可用时跑 AI 补完；否则仅用解析骨架。
    """
    if overwrite:
        try:
            archive.clear_event_ledger()
        except Exception:
            pass

    skeleton = build_skeleton(project_root)
    events = skeleton

    if use_ai and llm is not None:
        try:
            master_path = Path(project_root) / "outline" / "master.md"
            master_md = master_path.read_text(encoding="utf-8") if master_path.exists() else ""
            vols = _load_volumes(project_root)
            volumes_text = "\n".join(
                f"- 第{v.get('id')}卷《{v.get('title','')}》第{v.get('start_chapter')}~{v.get('end_chapter')}章：{v.get('theme','')}"
                for v in vols
            )
            prompt = _ai_plan_prompt(skeleton, master_md, volumes_text)
            # P4 防护：用 chat_json 替代裸 chat + json.loads —— 截断时自动翻倍
            # max_tokens 重试，避免 25 事件 JSON 被静默丢弃退回零 token skeleton。
            parsed = llm.chat_json(
                messages=[
                    {"role": "system", "content": "你是故事事件规划师，只输出 JSON 数组。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=8000,
                response_format={"type": "json_object"},
                retry=3,
                max_tokens_cap=16000,
            )
            ai_events = parsed if isinstance(parsed, list) else parsed.get("events", [])
            events = []
            for item in ai_events:
                if not isinstance(item, dict):
                    continue
                t = str(item.get("type", "minor"))
                if t not in ("main", "major", "minor"):
                    t = "minor"
                ts = int(item.get("target_start", 0) or 0)
                te = int(item.get("target_end", 0) or 0)
                if ts <= 0 or te <= 0 or ts > te:
                    continue
                events.append(StoryEvent(
                    type=t,
                    title=str(item.get("title", ""))[:60],
                    description=str(item.get("description", ""))[:300],
                    background=str(item.get("background", ""))[:300],
                    target_start=ts,
                    target_end=te,
                    status="planned",
                    depends_on=item.get("depends_on") or [],
                    priority=int(item.get("priority", 3) or 3),
                    progress=[p for p in (item.get("progress") or []) if isinstance(p, dict)],
                    created_at=datetime.now().isoformat(),
                    updated_at=datetime.now().isoformat(),
                ))
            # 若 AI 产出过空，退回骨架
            if not events:
                events = skeleton
                logger.warning("AI 事件规划返回空，退回解析骨架")
        except Exception as e:
            logger.warning(f"AI 事件规划失败，退回解析骨架: {e}")
            events = skeleton

    # 写入
    written: list[StoryEvent] = []
    for ev in events:
        archive.upsert_event(ev)
        written.append(ev)
    logger.info(f"事件账本规划完成：共 {len(written)} 条（use_ai={use_ai}）")
    return written


# 便捷入口：给定 MemoryEngine 直接规划
def plan_for_engine(engine, use_ai: bool = True) -> list[StoryEvent]:
    """供 engine / UI 调用：基于当前项目规划事件账本。"""
    return plan_story_events(
        project_root=str(engine._project_root),
        archive=engine._archive,
        llm=getattr(engine, "_llm", None),
        use_ai=use_ai,
    )
