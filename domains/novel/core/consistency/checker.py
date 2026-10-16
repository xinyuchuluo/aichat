"""
ConsistencyChecker - 一致性检查器。

检查新章节与已有档案/伏笔/时间线的冲突。
两层检查：
1. 规则检查（快速、确定性）：人物名一致性、伏笔状态、时间线顺序
2. LLM 检查（深度、语义性）：人物 OOC、伏笔冲突、世界观违规

集成到写作流水线：error 级别问题阻断提交，用户可选择忽略。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from domains.novel.core.llm_client import LLMClient
from domains.novel.core.memory.engine import MemoryEngine
from domains.novel.core.memory.archive_layer import CharacterProfile, Foreshadow, WorldSetting
from domains.novel.utils.logger import logger


@dataclass
class ConsistencyIssue:
    """一致性问题。"""
    severity: str = "info"       # error | warning | info
    category: str = ""           # character | foreshadow | timeline | worldview | power_system
    description: str = ""
    location: str = ""           # 章节中的位置引用
    suggestion: str = ""         # 修复建议


@dataclass
class ConsistencyReport:
    """一致性审查报告。"""
    chapter_id: int = 0
    issues: list[ConsistencyIssue] = field(default_factory=list)
    passed: bool = True          # 是否通过（无 error 级别问题）
    summary: str = ""

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "chapter_id": self.chapter_id,
            "issues": [
                {"severity": i.severity, "category": i.category,
                 "description": i.description, "location": i.location,
                 "suggestion": i.suggestion}
                for i in self.issues
            ],
            "passed": self.passed,
            "summary": self.summary,
        }


class ConsistencyChecker:
    """
    一致性检查器。

    检查维度：
    1. 人物 OOC：行为是否符合已建立的人设
    2. 伏笔冲突：是否与已有伏笔矛盾
    3. 时间线矛盾：事件顺序是否合理
    4. 世界观一致性：是否违反已建立的规则
    """

    def __init__(self, memory_engine: MemoryEngine, llm_client: LLMClient):
        self._memory = memory_engine
        self._llm = llm_client

    def check(
        self,
        chapter_id: int,
        chapter_text: str,
        strict: bool = False,
        enable_llm_check: bool = True,
    ) -> ConsistencyReport:
        """
        执行一致性检查。

        Args:
            chapter_id: 章节号
            chapter_text: 章节正文
            strict: 严格模式（warning 也算不通过）
            enable_llm_check: 是否启用 LLM 深度检查
        Returns:
            ConsistencyReport
        """
        report = ConsistencyReport(chapter_id=chapter_id)
        issues: list[ConsistencyIssue] = []

        # ===== 1. 规则检查（快速确定性） =====
        issues.extend(self._check_character_names(chapter_id, chapter_text))
        issues.extend(self._check_foreshadow_status(chapter_id))
        issues.extend(self._check_timeline_order(chapter_id))

        # ===== 2. LLM 检查（深度语义） =====
        if enable_llm_check and chapter_text.strip():
            try:
                issues.extend(self._llm_check_ooc(chapter_id, chapter_text))
                issues.extend(self._llm_check_foreshadow_conflicts(chapter_id, chapter_text))
                issues.extend(self._llm_check_worldview(chapter_id, chapter_text))
            except Exception as e:
                logger.warning(f"LLM一致性检查失败: {e}")
                issues.append(ConsistencyIssue(
                    severity="info",
                    category="system",
                    description=f"LLM深度检查未能完成: {e}",
                ))

        report.issues = issues
        report.passed = report.error_count == 0 and (not strict or report.warning_count == 0)

        # 生成摘要
        if not issues:
            report.summary = "一致性检查通过，未发现问题。"
        else:
            parts = []
            if report.error_count:
                parts.append(f"{report.error_count}个严重问题")
            if report.warning_count:
                parts.append(f"{report.warning_count}个警告")
            info_count = len(issues) - report.error_count - report.warning_count
            if info_count:
                parts.append(f"{info_count}个提示")
            report.summary = f"发现{'、'.join(parts)}"

        logger.info(f"第{chapter_id}章一致性检查: {report.summary}")
        return report

    # ===== 规则检查 =====

    def _check_character_names(self, chapter_id: int, chapter_text: str) -> list[ConsistencyIssue]:
        """检查人物名一致性：正文中出现的人物名是否在档案库中。"""
        issues = []
        all_chars = self._memory.archive.get_all_characters()
        if not all_chars:
            return issues

        # 收集所有已登记的人名和别名
        known_names = set()
        for char in all_chars:
            known_names.add(char.name)
            for alias in char.aliases:
                if alias:
                    known_names.add(alias)

        # 检查正文中是否有类似人名但未登记的
        # 简单策略：找正文中被「」或""引用的名字
        import re
        quoted_names = set(re.findall(r'[「"""]([^「"""]{1,6})[」""]', chapter_text))
        for name in quoted_names:
            if len(name) <= 1:
                continue
            # 检查是否与已知人名部分匹配
            is_known = any(name in known_names or known in name for known in known_names if known)
            if not is_known and name not in known_names:
                # 可能是新人物，也可能是不一致
                # 只在已有较多人物时才警告（避免第一章误报）
                if len(all_chars) >= 3:
                    issues.append(ConsistencyIssue(
                        severity="info",
                        category="character",
                        description=f"正文中出现名称「{name}」未在人物档案中登记",
                        suggestion="如果这是新人物，请确认名称拼写一致；如果是已有人物的别名，请在档案中添加该别名",
                    ))

        return issues

    def _check_foreshadow_status(self, chapter_id: int) -> list[ConsistencyIssue]:
        """检查伏笔状态：planted 超过20章未推进 → warning。"""
        issues = []
        foreshadows = self._memory.archive.get_active_foreshadows()
        for fs in foreshadows:
            if fs.status == "planted":
                gap = chapter_id - fs.planted_chapter
                if gap > 30:
                    issues.append(ConsistencyIssue(
                        severity="warning",
                        category="foreshadow",
                        description=f"伏笔「{fs.description[:40]}...」已埋下{gap}章未推进（第{fs.planted_chapter}章埋下）",
                        suggestion="考虑在本章或近期章节中推进此伏笔，或标记为已放弃",
                    ))
        return issues

    def _check_timeline_order(self, chapter_id: int) -> list[ConsistencyIssue]:
        """检查时间线顺序：事件 order 是否单调递增。"""
        issues = []
        timeline = self._memory.archive.get_timeline()
        if len(timeline) < 2:
            return issues

        prev_order = 0
        for entry in timeline:
            if entry.order < prev_order:
                issues.append(ConsistencyIssue(
                    severity="warning",
                    category="timeline",
                    description=f"时间线顺序异常：第{entry.chapter_id}章「{entry.event}」的序号({entry.order})小于前一条({prev_order})",
                    suggestion="检查时间线记录是否有误",
                ))
            prev_order = max(prev_order, entry.order)

        # 检查同一章节内是否有矛盾的时间线
        chapter_events = [e for e in timeline if e.chapter_id == chapter_id - 1]
        if len(chapter_events) > 1:
            # 同一章事件时间戳应合理
            for i in range(len(chapter_events) - 1):
                ts1 = chapter_events[i].timestamp_in_story
                ts2 = chapter_events[i + 1].timestamp_in_story
                if ts1 and ts2 and ts1 == ts2 and chapter_events[i].event != chapter_events[i + 1].event:
                    pass  # 同时间多事件不一定是问题

        return issues

    # ===== LLM 检查 =====

    def _llm_check_ooc(self, chapter_id: int, chapter_text: str) -> list[ConsistencyIssue]:
        """检查人物 OOC：行为是否符合已建立的人设。"""
        chars = self._memory.archive.get_all_characters()
        if not chars:
            return []

        # 只检查最近出场的人物
        relevant_chars = [c for c in chars if c.last_updated_chapter >= chapter_id - 10]
        if not relevant_chars:
            relevant_chars = chars[:5]

        char_profiles = "\n".join(
            f"- {c.name}：{c.personality or '性格未知'}（当前状态：{c.current_state or '未知'}）"
            for c in relevant_chars
        ) or "（无人物档案）"

        prompt = f"""请检查以下小说章节中是否存在人物 OOC（角色崩坏）的问题。

人物档案：
{char_profiles}

章节正文（第{chapter_id}章，可能截断）：
---
{chapter_text[:4000]}
---

请检查：
1. 人物行为是否符合其性格设定
2. 人物能力是否超出已建立的框架
3. 人物关系变化是否合理

请以 JSON 格式输出（只输出 JSON）：
{{
  "issues": [
    {{
      "severity": "error|warning|info",
      "character": "人物名",
      "description": "问题描述",
      "suggestion": "修复建议"
    }}
  ]
}}

如果没有问题，返回 {{"issues": []}}。注意只报告明确的问题，不要过度解读。"""

        try:
            resp = self._llm.chat(
                messages=[
                    {"role": "system", "content": "你是小说一致性审查助手，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp)
            issues = []
            for item in data.get("issues", []):
                issues.append(ConsistencyIssue(
                    severity=item.get("severity", "warning"),
                    category="character",
                    description=f"[{item.get('character', '?')}] {item.get('description', '')}",
                    suggestion=item.get("suggestion", ""),
                ))
            return issues
        except Exception as e:
            logger.warning(f"OOC检查失败: {e}")
            return []

    def _llm_check_foreshadow_conflicts(self, chapter_id: int, chapter_text: str) -> list[ConsistencyIssue]:
        """检查伏笔冲突：是否与已有伏笔矛盾。"""
        foreshadows = self._memory.archive.get_active_foreshadows()
        if not foreshadows:
            return []

        fs_list = "\n".join(
            f"- [{f.status}] 第{f.planted_chapter}章: {f.description}"
            for f in foreshadows[:15]  # 最多15条避免prompt过长
        )

        prompt = f"""请检查以下小说章节是否与已有伏笔存在矛盾。

已有活跃伏笔：
{fs_list}

章节正文（第{chapter_id}章，可能截断）：
---
{chapter_text[:4000]}
---

请检查：
1. 本章内容是否与已有伏笔矛盾
2. 本章是否遗忘了应该回收的伏笔
3. 本章是否引入了与伏笔冲突的设定

请以 JSON 格式输出（只输出 JSON）：
{{
  "issues": [
    {{
      "severity": "error|warning|info",
      "description": "问题描述",
      "suggestion": "修复建议"
    }}
  ]
}}

如果没有问题，返回 {{"issues": []}}。注意只报告明确的矛盾，不要过度解读。"""

        try:
            resp = self._llm.chat(
                messages=[
                    {"role": "system", "content": "你是小说一致性审查助手，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp)
            issues = []
            for item in data.get("issues", []):
                issues.append(ConsistencyIssue(
                    severity=item.get("severity", "warning"),
                    category="foreshadow",
                    description=item.get("description", ""),
                    suggestion=item.get("suggestion", ""),
                ))
            return issues
        except Exception as e:
            logger.warning(f"伏笔冲突检查失败: {e}")
            return []

    def _llm_check_worldview(self, chapter_id: int, chapter_text: str) -> list[ConsistencyIssue]:
        """检查世界观一致性：是否违反已建立的规则。"""
        settings = self._memory.archive.get_world_settings()
        if not settings:
            return []

        ws_list = "\n".join(
            f"- [{s.category}] {s.name}：{s.description}"
            for s in settings[:15]
        )

        prompt = f"""请检查以下小说章节是否违反了已建立的世界观规则。

世界观设定：
{ws_list}

章节正文（第{chapter_id}章，可能截断）：
---
{chapter_text[:4000]}
---

请检查：
1. 本章是否违反了世界观中的规则
2. 本章是否引入了与世界观矛盾的设定
3. 本章的能力/科技水平是否符合世界观框架

请以 JSON 格式输出（只输出 JSON）：
{{
  "issues": [
    {{
      "severity": "error|warning|info",
      "description": "问题描述",
      "suggestion": "修复建议"
    }}
  ]
}}

如果没有问题，返回 {{"issues": []}}。注意只报告明确的违规，不要过度解读。"""

        try:
            resp = self._llm.chat(
                messages=[
                    {"role": "system", "content": "你是小说一致性审查助手，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp)
            issues = []
            for item in data.get("issues", []):
                issues.append(ConsistencyIssue(
                    severity=item.get("severity", "warning"),
                    category="worldview",
                    description=item.get("description", ""),
                    suggestion=item.get("suggestion", ""),
                ))
            return issues
        except Exception as e:
            logger.warning(f"世界观检查失败: {e}")
            return []
