"""Token 预算管理。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenBudget:
    """Token 预算分配。

    百分比是初始默认值，会根据实际内容动态调整。
    优先级数字越小越重要（裁剪时最后裁剪）。
    """
    total: int = 128000
    reserved_for_completion: int = 4096
    available: int = 0  # = total - reserved_for_completion

    # 各部分预算（百分比）和优先级
    # 优先级 1 = 最重要（最后裁剪），7 = 最不重要（最先裁剪）
    # 【重要】世界观/人物是全书一致性锚点，设高保护优先级（优先数小=保护）；
    # RAG/摘要等辅助信息设低保护优先级（优先数大=优先丢弃）。
    parts: dict[str, dict] = None

    def __post_init__(self):
        if self.available == 0:
            self.available = self.total - self.reserved_for_completion
        if self.parts is None:
            self.parts = {
                "writing_instruction": {"pct": 5, "priority": 1, "actual": 0},
                "system_prompt": {"pct": 5, "priority": 2, "actual": 0},
                # 世界观+人物档案：一致性核心锚点，最高保护（绝不先裁）
                "world_and_foreshadows": {"pct": 15, "priority": 3, "actual": 0},
                "character_profiles": {"pct": 12, "priority": 3, "actual": 0},
                # 邻近大纲+卷节奏：重要但可压缩
                "adjacent_outlines": {"pct": 12, "priority": 4, "actual": 0},
                "book_continuity": {"pct": 8, "priority": 5, "actual": 0},
                # 上一章结尾+摘要：衔接用，可截断
                "recent_text": {"pct": 15, "priority": 6, "actual": 0},
                "chapter_summaries": {"pct": 12, "priority": 6, "actual": 0},
                # RAG：最不重要的辅助信息，最先丢
                "rag_chunks": {"pct": 16, "priority": 7, "actual": 0},
            }

    def get_budget(self, part_name: str) -> int:
        """获取某部分的 token 预算。"""
        part = self.parts.get(part_name)
        if not part:
            return 0
        return int(self.available * part["pct"] / 100)

    def get_total_used(self) -> int:
        return sum(p["actual"] for p in self.parts.values())

    def get_remaining(self) -> int:
        return self.available - self.get_total_used()

    def set_actual(self, part_name: str, tokens: int):
        if part_name in self.parts:
            self.parts[part_name]["actual"] = tokens
