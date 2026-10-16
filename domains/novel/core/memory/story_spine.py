"""
StorySpine - 故事脊柱账本：治「几百万/几千万字主线崩溃」的核心机制。

设计哲学
--------
长篇连载的主线崩溃，根因不是「记不住细节」，而是随字数增长，AI 逐渐失去对
「主线现在走到哪一步、下一步去哪」的整体感——摘要层只覆盖最近 N 章，档案层只存
静态事实，都无法承载「贯穿百万字的主线轨迹」。

StorySpine 是一份**极紧凑、始终最新、有界不膨胀**的主线状态卡：
- 固定项（premise/endgame/protagonist）：一次设定，永不漂移，作主线锚点。
- 动态项（current_stage/protagonist_state）：每章本地更新为最新状态（1-2 行）。
- 里程碑（milestones）：只记录「重大转折」，稀疏采样 → 15 条即可横跨数百章，
  给 AI 一条贯穿全书的主线骨架，而非逐章流水账。

关键：更新是**零额外 LLM 调用**——直接复用每章已生成的结构化摘要（ChapterSummary）
本地推导。始终注入但体量极小（几百 token 封顶），因此字数再多，主线注入成本恒定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def _trim(text: str, max_chars: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


@dataclass
class SpineState:
    """主线状态卡（持久化为 story_spine.json）。"""
    protagonist: str = ""            # 主角名（固定锚点）
    premise: str = ""                # 一句话核心设定/前提（固定锚点）
    endgame: str = ""                # 终局方向（固定锚点，用户可编辑）
    current_stage: str = ""          # 主线当前进行到（1-2 行，每章更新）
    protagonist_state: str = ""      # 主角当前目标/处境（每章更新）
    protagonist_weakness: str = ""   # 主角当前短板/限制（零 LLM，初始化/档案注入）
    female_arc: str = ""             # 女主当前支线进度（零 LLM，从档案库读取）
    milestones: list[dict] = field(default_factory=list)  # [{chapter_id, beat}] 稀疏重大节点
    milestone_total: int = 0         # 累计重大节点数（含已被裁掉的旧节点）
    updated_chapter: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SpineState":
        # 防御归一化：遗留/异常 JSON 里 milestones 可能是字符串/非 dict；
        # 统一转 [dict, ...] 形态，避免 dashboard / render 调 .get() 崩溃。
        raw_ms = d.get("milestones", []) or []
        norm_ms: list = []
        for m in raw_ms:
            if isinstance(m, dict):
                norm_ms.append(m)
            elif isinstance(m, str):
                norm_ms.append({"chapter_id": -1, "beat": m})
            # 其它类型（非 str/dict）丢弃，避免日志噪声
        return cls(
            protagonist=d.get("protagonist", ""),
            premise=d.get("premise", ""),
            endgame=d.get("endgame", ""),
            current_stage=d.get("current_stage", ""),
            protagonist_state=d.get("protagonist_state", ""),
            protagonist_weakness=d.get("protagonist_weakness", ""),
            female_arc=d.get("female_arc", ""),
            milestones=norm_ms,
            milestone_total=int(d.get("milestone_total", 0)),
            updated_chapter=int(d.get("updated_chapter", 0)),
        )


class StorySpine:
    """故事脊柱账本：持久化 + 本地零 LLM 更新 + 有界渲染。"""

    def __init__(self, project_root: str, milestone_max: int = 15):
        self._path = Path(project_root) / ".novelforge" / "story_spine.json"
        self._milestone_max = max(4, int(milestone_max))
        self._state = self._load()

    # ===== 持久化 =====

    def _load(self) -> SpineState:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return SpineState.from_dict(json.load(f))
            except Exception:
                pass
        return SpineState()

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._state.to_dict(), f, ensure_ascii=False, indent=2)

    @property
    def state(self) -> SpineState:
        return self._state

    # ===== 固定锚点设置 =====

    def seed(self, protagonist: str = "", premise: str = "", endgame: str = "",
             overwrite: bool = False) -> None:
        """设定固定锚点。默认只在字段为空时填充（不覆盖用户已编辑内容）。"""
        s = self._state
        if protagonist and (overwrite or not s.protagonist):
            s.protagonist = protagonist.strip()
        if premise and (overwrite or not s.premise):
            s.premise = _trim(premise, 200)
        if endgame and (overwrite or not s.endgame):
            s.endgame = _trim(endgame, 200)
        self.save()

    def set_fields(self, **kwargs) -> None:
        """UI 手动编辑用：直接设置任意字段。"""
        for k, v in kwargs.items():
            if hasattr(self._state, k):
                setattr(self._state, k, v)
        self.save()

    # ===== 核心：每章本地更新（零额外 LLM 调用） =====

    def update_from_summary(self, chapter_id: int, summary) -> None:
        """基于本章结构化摘要本地更新脊柱。summary 为 ChapterSummary 或等价 dict。

        - current_stage ← 本章情节进展（覆盖，始终为最新主线状态）
        - protagonist_state ← 若主角出现在人物状态变化中，取其 after
        - milestones ← 仅当本章构成「重大转折」时追加（稀疏），并按上限裁剪
        """
        get = (summary.get if isinstance(summary, dict) else lambda k, d=None: getattr(summary, k, d))
        one_line = get("one_line_summary", "") or ""
        plot = get("plot_progress", "") or ""
        char_changes = get("character_state_changes", []) or []
        fs_progress = get("foreshadowing_progress", []) or []

        s = self._state
        if plot:
            s.current_stage = _trim(plot, 160)

        # 主角当前处境：从人物状态变化中匹配主角
        if s.protagonist:
            for c in char_changes:
                if not isinstance(c, dict):
                    continue
                name = (c.get("character") or "").strip()
                if name and (name == s.protagonist or name in s.protagonist or s.protagonist in name):
                    after = (c.get("after") or "").strip()
                    if after:
                        s.protagonist_state = _trim(after, 120)
                    break

        # 是否构成重大转折（稀疏采样，避免逐章流水账）：
        #   有伏笔回收 / 或有 >=2 处人物状态质变
        resolved = any(
            isinstance(p, dict) and str(p.get("action", "")).lower() == "resolved"
            for p in fs_progress
        )
        significant = resolved or len([c for c in char_changes if isinstance(c, dict)]) >= 2
        if significant and one_line:
            beat = _trim(one_line, 60)
            # 去重：同章或与上一条完全相同则跳过
            if not s.milestones or (
                s.milestones[-1].get("chapter_id") != chapter_id
                and s.milestones[-1].get("beat") != beat
            ):
                s.milestones.append({"chapter_id": chapter_id, "beat": beat})
                s.milestone_total += 1
                if len(s.milestones) > self._milestone_max:
                    # 保留最新的 milestone_max 条（旧节点已被 current_stage/摘要覆盖）
                    s.milestones = s.milestones[-self._milestone_max:]

        s.updated_chapter = max(s.updated_chapter, chapter_id)
        self.save()

    # ===== 渲染（有界，供写作 prompt 注入） =====

    def render(self, next_direction: str = "", max_chars: int = 640,
               active_foreshadows: str = "", antagonist_info: str = "",
               female_arc: str = "", protagonist_weakness: str = "",
               reveal_endgame: bool = True) -> str:
        """渲染为紧凑主线账本文本。始终短小，字数再多注入成本恒定。

        Args:
            active_foreshadows: 活跃伏笔文本（零 LLM，由调用方从档案库读取）
            antagonist_info: 敌对势力/反派动态文本（零 LLM）
            reveal_endgame: 是否暴露终局方向。前期/中期写作时置 False，避免过早剧透结局。
        """
        # 防御：调用方可能传入 list（如 outline.core_events），强制转 str 避免整体渲染崩溃
        next_direction = next_direction if isinstance(next_direction, str) else str(next_direction or "")
        active_foreshadows = active_foreshadows if isinstance(active_foreshadows, str) else str(active_foreshadows or "")
        antagonist_info = antagonist_info if isinstance(antagonist_info, str) else str(antagonist_info or "")
        female_arc = female_arc if isinstance(female_arc, str) else str(female_arc or "")
        protagonist_weakness = protagonist_weakness if isinstance(protagonist_weakness, str) else str(protagonist_weakness or "")
        s = self._state
        if not (s.premise or s.current_stage or s.milestones or s.endgame):
            return ""
        parts = ["=== 主线进度账本（贯穿全书的主线骨架，必须严格沿此推进） ==="]
        if s.protagonist:
            line = f"主角：{s.protagonist}"
            if s.protagonist_state:
                line += f"（当前：{s.protagonist_state}）"
            parts.append(line)
        if s.premise:
            parts.append(f"核心设定：{s.premise}")
        if s.endgame and reveal_endgame:
            parts.append(f"终局方向：{s.endgame}")
        if s.milestones:
            parts.append("已发生的主线关键节点（时间顺序，稀疏采样）：")
            for m in s.milestones:
                parts.append(f"  · 第{m.get('chapter_id')}章：{m.get('beat')}")
            if s.milestone_total > len(s.milestones):
                parts.append(f"  （更早还有 {s.milestone_total - len(s.milestones)} 个节点，已并入前情摘要）")
        if s.current_stage:
            parts.append(f"主线现在走到：{s.current_stage}")
        # 活跃伏笔（零 LLM，由 engine.py 从档案库读取传入）
        if active_foreshadows:
            parts.append(f"活跃伏笔（未回收）：{active_foreshadows[:300]}")
        # 反派/敌对势力动态
        if antagonist_info:
            parts.append(f"敌对动态：{antagonist_info[:200]}")
        # 主角当前短板/限制（零 LLM，由初始化/档案注入）
        if protagonist_weakness:
            parts.append(f"主角当前短板/限制：{protagonist_weakness[:120]}")
        # 女主当前支线进度（零 LLM，从档案库读取）
        if female_arc:
            parts.append(f"女主当前支线进度：{female_arc[:200]}")
        if next_direction and next_direction.strip():
            parts.append(f"主线下一步应推进向：{_trim(next_direction, 120)}")
        parts.append("要求：本章须让主线目标向前推进一小步，不原地打转、不横生与主线无关的支线。")
        text = "\n".join(parts)
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        return text

    # ===== 重置 =====

    def clear(self, keep_anchors: bool = True) -> None:
        """清空动态记忆（全书重写前）。keep_anchors 保留固定锚点。"""
        anchors = (self._state.protagonist, self._state.premise, self._state.endgame)
        self._state = SpineState()
        if keep_anchors:
            self._state.protagonist, self._state.premise, self._state.endgame = anchors
        self.save()
