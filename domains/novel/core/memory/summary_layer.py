"""
SummaryLayer - 摘要层：每章结构化摘要的存储与检索。

每章写完后自动生成结构化摘要（人物状态变化、情节进展、伏笔推进），
写作前注入最近 N 章摘要，保持主线脉络连贯。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from domains.novel.core.llm_client import LLMClient
from domains.novel.utils.logger import logger


@dataclass
class ChapterSummary:
    """结构化章节摘要。"""
    chapter_id: int = 0
    title: str = ""
    one_line_summary: str = ""               # 一句话摘要
    plot_progress: str = ""                  # 情节进展（200字以内）
    character_state_changes: list[dict] = field(default_factory=list)  # [{character, before, after, reason}]
    foreshadowing_progress: list[dict] = field(default_factory=list)   # [{action, detail}]
    timeline_events: list[dict] = field(default_factory=list)          # [{event, description}]
    key_facts: list[str] = field(default_factory=list)                 # 本章新增关键事实
    word_count: int = 0
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterSummary":
        return cls(
            chapter_id=d.get("chapter_id", 0),
            title=d.get("title", ""),
            one_line_summary=d.get("one_line_summary", ""),
            plot_progress=d.get("plot_progress", ""),
            character_state_changes=d.get("character_state_changes", []),
            foreshadowing_progress=d.get("foreshadowing_progress", []),
            timeline_events=d.get("timeline_events", []),
            key_facts=d.get("key_facts", []),
            word_count=d.get("word_count", 0),
            created_at=d.get("created_at", ""),
        )


class SummaryLayer:
    """摘要层：每章结构化摘要的存储与检索。"""

    def __init__(self, project_root: str, llm_client: LLMClient):
        self._summaries_dir = Path(project_root) / ".novelforge" / "summaries"
        self._summaries_dir.mkdir(parents=True, exist_ok=True)
        self._llm = llm_client

    def generate_summary(self, chapter_id: int, title: str, text: str) -> ChapterSummary:
        """
        调用 LLM 生成结构化摘要。

        Prompt 策略：
        - 要求 LLM 以 JSON 格式输出结构化摘要
        - 特别关注：人物状态变化（before -> after）、伏笔推进、时间线事件
        - 限制 one_line_summary 在 50 字以内，plot_progress 在 200 字以内
        """
        word_count = len(text)

        prompt = f"""请为以下小说章节生成结构化摘要，以 JSON 格式输出。

章节标题：{title}
章节正文（可能截断）：
---
{text[:6000]}
---

请输出以下 JSON 格式（只输出 JSON，不要其他内容）：
{{
  "one_line_summary": "一句话概括本章内容（50字以内）",
  "plot_progress": "本章情节进展（200字以内）",
  "character_state_changes": [
    {{
      "character": "人物名",
      "before": "本章前状态",
      "after": "本章后状态",
      "reason": "变化原因"
    }}
  ],
  "foreshadowing_progress": [
    {{
      "action": "planted|advanced|resolved",
      "detail": "伏笔内容描述"
    }}
  ],
  "timeline_events": [
    {{
      "event": "事件名",
      "description": "事件描述"
    }}
  ],
  "key_facts": ["本章新增的关键事实1", "关键事实2"]
}}

注意：
1. 只记录本章明确发生的事实，不要推测或虚构
2. 人物状态变化只记录有重大变化的，出场但无变化的不记
3. one_line_summary 要精炼有力"""

        # 长章节摘要 JSON 易超 1000 token 被截断。调大 max_tokens 并加重试。
        last_err = None
        for attempt in range(2):
            try:
                resp = self._llm.chat(
                    messages=[
                        {"role": "system", "content": "你是一个小说摘要助手，只输出 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=2500,
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp)
                summary = ChapterSummary(
                    chapter_id=chapter_id,
                    title=title,
                    one_line_summary=data.get("one_line_summary", ""),
                    plot_progress=data.get("plot_progress", ""),
                    character_state_changes=data.get("character_state_changes", []),
                    foreshadowing_progress=data.get("foreshadowing_progress", []),
                    timeline_events=data.get("timeline_events", []),
                    key_facts=data.get("key_facts", []),
                    word_count=word_count,
                    created_at=datetime.now().isoformat(),
                )
                self.save_summary(summary)
                logger.info(f"第{chapter_id}章摘要已生成(第{attempt+1}次): {summary.one_line_summary}")
                return summary
            except json.JSONDecodeError as e:
                last_err = e
                logger.warning(f"第{chapter_id}章摘要JSON解析失败(第{attempt+1}次): {e}")
                continue
            except Exception as e:
                last_err = e
                logger.error(f"第{chapter_id}章摘要生成失败: {e}")
                break
        # 重试仍失败：返回基础摘要（不抛异常，保证提交流程继续）
        logger.error(f"第{chapter_id}章摘要最终失败: {last_err}")
        summary = ChapterSummary(
            chapter_id=chapter_id,
            title=title,
            one_line_summary=title,
            plot_progress="（摘要生成失败）",
            word_count=word_count,
            created_at=datetime.now().isoformat(),
        )
        self.save_summary(summary)
        return summary

    def save_summary(self, summary: ChapterSummary) -> None:
        """保存摘要到 JSON 文件。"""
        path = self._summaries_dir / f"chapter_{summary.chapter_id:04d}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, ensure_ascii=False, indent=2)

    def get_summary(self, chapter_id: int) -> Optional[ChapterSummary]:
        """读取指定章节的摘要。"""
        path = self._summaries_dir / f"chapter_{chapter_id:04d}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ChapterSummary.from_dict(data)
        except Exception as e:
            logger.error(f"读取第{chapter_id}章摘要失败: {e}")
            return None

    def get_recent_summaries(
        self, current_chapter: int, count: int
    ) -> list[ChapterSummary]:
        """
        获取最近 N 章的摘要（按章节号倒序，不包含当前章）。

        如果某些章节没有摘要（跳章），自动跳过。
        """
        summaries = []
        chapter_id = current_chapter - 1
        while chapter_id >= 1 and len(summaries) < count:
            summary = self.get_summary(chapter_id)
            if summary:
                summaries.append(summary)
            chapter_id -= 1
        # 按章节号正序返回
        summaries.reverse()
        return summaries

    def get_all_summaries(self) -> list[ChapterSummary]:
        """获取所有章节摘要（用于全局检索）。"""
        summaries = []
        for path in sorted(self._summaries_dir.glob("chapter_*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                summaries.append(ChapterSummary.from_dict(data))
            except Exception:
                continue
        return summaries

    def delete_summary(self, chapter_id: int) -> bool:
        """删除指定章节的摘要文件。"""
        path = self._summaries_dir / f"chapter_{chapter_id:04d}.json"
        if path.exists():
            path.unlink()
            logger.info(f"已删除第{chapter_id}章摘要: {path}")
            return True
        return False

    def clear_all(self) -> None:
        """清空全部章节摘要文件（全书重写前重置派生记忆，防膨胀）。

        仅删除由提交自动生成的摘要，保留作者源文件（正文/设定/大纲）。
        重写随章节逐章重新生成，文件按章覆盖也幂等，清空可避免残留旧摘要。
        """
        try:
            count = 0
            for path in self._summaries_dir.glob("chapter_*.json"):
                try:
                    path.unlink()
                    count += 1
                except Exception:
                    continue
            logger.info(f"摘要派生记忆已清空（{count} 个文件）")
        except Exception as e:
            logger.error(f"清空摘要派生记忆失败: {e}")
            raise

    def compress_summaries(
        self, start_chapter: int, end_chapter: int
    ) -> ChapterSummary:
        """
        将多章摘要压缩为一份卷级摘要（当章节过多时）。
        例如：每 10 章压缩一次，保留关键转折。
        """
        summaries_to_compress = []
        for cid in range(start_chapter, end_chapter + 1):
            s = self.get_summary(cid)
            if s:
                summaries_to_compress.append(s)

        if not summaries_to_compress:
            return ChapterSummary(
                chapter_id=start_chapter,
                title=f"第{start_chapter}-{end_chapter}章卷级摘要",
                one_line_summary="（无摘要可压缩）",
                created_at=datetime.now().isoformat(),
            )

        # 构建压缩 prompt
        summaries_text = "\n\n".join(
            f"第{s.chapter_id}章《{s.title}》：{s.one_line_summary}\n情节：{s.plot_progress}"
            for s in summaries_to_compress
        )

        prompt = f"""请将以下多章摘要压缩为一份卷级摘要，保留关键转折和重要事件。

{summaries_text}

请输出 JSON 格式：
{{
  "one_line_summary": "卷级一句话概括（80字以内）",
  "plot_progress": "本卷情节主线（400字以内）",
  "key_facts": ["关键事件1", "关键事件2"]
}}"""

        try:
            resp = self._llm.chat(
                messages=[
                    {"role": "system", "content": "你是一个小说摘要助手，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp)
            return ChapterSummary(
                chapter_id=start_chapter,
                title=f"第{start_chapter}-{end_chapter}章卷级摘要",
                one_line_summary=data.get("one_line_summary", ""),
                plot_progress=data.get("plot_progress", ""),
                key_facts=data.get("key_facts", []),
                created_at=datetime.now().isoformat(),
            )
        except Exception as e:
            logger.error(f"卷级摘要压缩失败: {e}")
            return ChapterSummary(
                chapter_id=start_chapter,
                title=f"第{start_chapter}-{end_chapter}章卷级摘要",
                one_line_summary="（压缩失败）",
                plot_progress="; ".join(s.one_line_summary for s in summaries_to_compress),
                created_at=datetime.now().isoformat(),
            )

    def format_summaries_for_prompt(self, summaries: list[ChapterSummary]) -> str:
        """将摘要列表格式化为 prompt 文本。"""
        if not summaries:
            return ""
        parts = []
        for s in summaries:
            parts.append(f"【第{s.chapter_id}章《{s.title}》】")
            parts.append(f"摘要：{s.one_line_summary}")
            if s.plot_progress:
                parts.append(f"情节：{s.plot_progress}")
            if s.character_state_changes:
                for csc in s.character_state_changes:
                    parts.append(f"  人物变化：{csc.get('character', '?')}: {csc.get('before', '?')} → {csc.get('after', '?')}")
            if s.foreshadowing_progress:
                for fp in s.foreshadowing_progress:
                    parts.append(f"  伏笔({fp.get('action', '?')}): {fp.get('detail', '')}")
            parts.append("")
        return "\n".join(parts)
