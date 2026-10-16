"""
InspirationManager - 灵感碎片管理器。

记录随时闪现的灵感（场景/设定/情节/台词/特质/伏笔），
支持：
1. 写作时自动检索相关碎片注入上下文
2. 一键扩展为完整正文段落
3. 融入大纲或设定集（AI建议放置位置）

存储方案：复用 archive.db，新增 inspirations 表。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from domains.novel.core.llm_client import LLMClient
from domains.novel.utils.logger import logger
from domains.novel.utils.text import extract_keywords


# 灵感类型
TYPE_SCENE = "scene"           # 场景碎片
TYPE_SETTING = "setting"       # 设定点子
TYPE_PLOT = "plot"             # 情节片段
TYPE_DIALOGUE = "dialogue"     # 台词
TYPE_TRAIT = "trait"           # 人物特质
TYPE_FORESHADOW = "foreshadow" # 伏笔点子

TYPE_LABELS = {
    TYPE_SCENE: "场景",
    TYPE_SETTING: "设定",
    TYPE_PLOT: "情节",
    TYPE_DIALOGUE: "台词",
    TYPE_TRAIT: "特质",
    TYPE_FORESHADOW: "伏笔",
}

# 状态
STATUS_DRAFT = "draft"         # 草稿（未处理）
STATUS_SCHEDULED = "scheduled"  # 已安排（关联到章节）
STATUS_USED = "used"           # 已使用（已写入正文）
STATUS_ARCHIVED = "archived"   # 已归档

STATUS_LABELS = {
    STATUS_DRAFT: "💭 草稿",
    STATUS_SCHEDULED: "📅 已安排",
    STATUS_USED: "✅ 已使用",
    STATUS_ARCHIVED: "📦 已归档",
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS inspirations (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    tags TEXT,
    status TEXT DEFAULT 'draft',
    related_chapter INTEGER,
    related_characters TEXT,
    priority INTEGER DEFAULT 3,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_insp_type ON inspirations(type);
CREATE INDEX IF NOT EXISTS idx_insp_status ON inspirations(status);
CREATE INDEX IF NOT EXISTS idx_insp_chapter ON inspirations(related_chapter);
"""


@dataclass
class Inspiration:
    """灵感碎片。"""
    id: str = ""
    type: str = TYPE_SCENE
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = STATUS_DRAFT
    related_chapter: Optional[int] = None
    related_characters: list[str] = field(default_factory=list)
    priority: int = 3
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Inspiration":
        return cls(
            id=row["id"],
            type=row["type"],
            title=row["title"] or "",
            content=row["content"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            status=row["status"],
            related_chapter=row["related_chapter"],
            related_characters=json.loads(row["related_characters"]) if row["related_characters"] else [],
            priority=row["priority"],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )


class InspirationManager:
    """灵感碎片管理器。"""

    def __init__(self, project_root: str, llm_client: LLMClient):
        self._project_root = Path(project_root)
        self._db_path = self._project_root / ".novelforge" / "archive.db"
        self._llm = llm_client
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """初始化数据库（复用 archive.db）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ===== CRUD =====

    def add(
        self,
        type: str,
        content: str,
        title: str = "",
        tags: list[str] | None = None,
        related_characters: list[str] | None = None,
        priority: int = 3,
    ) -> Inspiration:
        """添加灵感碎片。"""
        now = datetime.now().isoformat()
        insp = Inspiration(
            id=str(uuid.uuid4()),
            type=type,
            title=title or content[:30],
            content=content,
            tags=tags or [],
            related_characters=related_characters or [],
            priority=priority,
            created_at=now,
            updated_at=now,
        )
        self._conn.execute(
            "INSERT INTO inspirations (id, type, title, content, tags, status, "
            "related_chapter, related_characters, priority, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (insp.id, insp.type, insp.title, insp.content,
             json.dumps(insp.tags, ensure_ascii=False),
             insp.status, insp.related_chapter,
             json.dumps(insp.related_characters, ensure_ascii=False),
             insp.priority, insp.created_at, insp.updated_at)
        )
        self._conn.commit()
        logger.info(f"灵感碎片已添加: [{TYPE_LABELS.get(type, type)}] {insp.title}")
        return insp

    def update(self, insp: Inspiration) -> None:
        """更新灵感碎片。"""
        insp.updated_at = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE inspirations SET type=?, title=?, content=?, tags=?, status=?, "
            "related_chapter=?, related_characters=?, priority=?, updated_at=? WHERE id=?",
            (insp.type, insp.title, insp.content,
             json.dumps(insp.tags, ensure_ascii=False),
             insp.status, insp.related_chapter,
             json.dumps(insp.related_characters, ensure_ascii=False),
             insp.priority, insp.updated_at, insp.id)
        )
        self._conn.commit()

    def delete(self, insp_id: str) -> None:
        self._conn.execute("DELETE FROM inspirations WHERE id = ?", (insp_id,))
        self._conn.commit()

    def get(self, insp_id: str) -> Optional[Inspiration]:
        row = self._conn.execute(
            "SELECT * FROM inspirations WHERE id = ?", (insp_id,)
        ).fetchone()
        return Inspiration.from_row(row) if row else None

    def list_all(self, status: Optional[str] = None, type: Optional[str] = None) -> list[Inspiration]:
        """列出灵感碎片，可按状态/类型过滤。"""
        sql = "SELECT * FROM inspirations WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if type:
            sql += " AND type = ?"
            params.append(type)
        sql += " ORDER BY priority DESC, created_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [Inspiration.from_row(r) for r in rows]

    def list_active(self) -> list[Inspiration]:
        """获取所有未归档、未使用的碎片（可用于注入）。"""
        rows = self._conn.execute(
            "SELECT * FROM inspirations WHERE status IN ('draft', 'scheduled') "
            "ORDER BY priority DESC, created_at DESC"
        ).fetchall()
        return [Inspiration.from_row(r) for r in rows]

    # ===== 写作时检索 =====

    def search_for_writing(self, writing_instruction: str, top_k: int = 5) -> list[Inspiration]:
        """
        根据写作指令检索相关灵感碎片。

        策略：关键词匹配（类型+标签+内容+关联人物）
        """
        active = self.list_active()
        if not active:
            return []

        keywords = extract_keywords(writing_instruction, top_n=15)
        if not keywords:
            return active[:top_k]

        scored: list[tuple[Inspiration, float]] = []
        for insp in active:
            score = 0.0
            # 标题匹配
            for kw in keywords:
                if kw in insp.title:
                    score += 3.0
            # 内容匹配
            for kw in keywords:
                if kw in insp.content:
                    score += 2.0
            # 标签匹配
            for tag in insp.tags:
                for kw in keywords:
                    if kw in tag or tag in kw:
                        score += 4.0
            # 关联人物匹配
            for char in insp.related_characters:
                for kw in keywords:
                    if kw in char or char in kw:
                        score += 3.0
            # 优先级加成
            score += insp.priority * 0.5

            if score > 0:
                scored.append((insp, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [insp for insp, _ in scored[:top_k]]

    # ===== 一键扩展为正文 =====

    def expand_to_text(
        self,
        insp: Inspiration,
        context_chars: list[str] | None = None,
        worldview: str = "",
        on_stream: Optional[callable] = None,
    ) -> str:
        """
        将灵感碎片扩展为完整的正文段落。

        Args:
            insp: 灵感碎片
            context_chars: 相关人物档案
            worldview: 世界观设定
            on_stream: 流式回调
        Returns:
            扩展后的正文段落
        """
        type_label = TYPE_LABELS.get(insp.type, insp.type)
        char_info = ""
        if context_chars:
            char_info = "相关人物：\n" + "\n".join(
                f"- {c}" for c in context_chars[:5]
            )

        prompt = f"""请将以下灵感碎片扩展为完整的小说正文段落。

灵感类型：{type_label}
灵感标题：{insp.title}
灵感内容：{insp.content}
{char_info}
{f"世界观参考：{worldview[:500]}" if worldview else ""}

要求：
1. 将灵感碎片扩展为一个完整的场景/段落，有画面感、有细节
2. 保持与已有设定的一致性
3. 文字要有文学性，符合小说正文风格
4. 只输出正文内容，不要标题、注释或说明
5. 扩展后的内容应该可以直接插入到小说正文中"""

        if on_stream:
            full = ""
            for chunk in self._llm.chat_stream(
                messages=[
                    {"role": "system", "content": "你是一位专业的小说作家，擅长将灵感碎片扩展为生动的正文段落。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.85,
            ):
                full += chunk
                on_stream(chunk)
            return full
        else:
            return self._llm.chat(
                messages=[
                    {"role": "system", "content": "你是一位专业的小说作家，擅长将灵感碎片扩展为生动的正文段落。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.85,
            )

    # ===== 融入大纲/设定 =====

    def suggest_integration(self, insp: Inspiration, outline: str = "", settings: str = "") -> dict:
        """
        AI分析灵感碎片，建议融入位置。

        Returns:
        {
            "suggestion_type": "outline|setting|character",
            "target": "目标位置描述",
            "reason": "建议理由",
            "modified_content": "修改后的大纲/设定片段",
            "insert_position": "插入位置描述"
        }
        """
        type_label = TYPE_LABELS.get(insp.type, insp.type)

        outline_preview = outline[:2000] if outline else "（无大纲）"
        settings_preview = settings[:2000] if settings else "（无设定集）"

        prompt = f"""请分析以下灵感碎片，建议它最适合融入小说的哪个位置。

灵感类型：{type_label}
灵感标题：{insp.title}
灵感内容：{insp.content}

当前大纲：
{outline_preview}

当前设定集：
{settings_preview}

请以 JSON 格式输出建议（只输出 JSON）：
{{
  "suggestion_type": "outline|setting|character",
  "target": "具体目标位置（如：第一卷第三章、世界观-力量体系、人物-萧炎的特质）",
  "reason": "为什么建议放在这个位置（50字以内）",
  "modified_content": "将灵感融入后的目标片段（如果是outline，输出修改后的章节纲；如果是setting，输出修改后的设定段落）",
  "insert_position": "在大纲/设定中的具体插入位置描述"
}}

注意：
1. suggestion_type: outline=融入大纲，setting=融入设定集，character=融入人物档案
2. modified_content 要实际将灵感内容融入，不是简单拼接
3. 如果灵感不适合融入，suggestion_type 设为 "outline"，modified_content 为空"""

        try:
            resp = self._llm.chat(
                messages=[
                    {"role": "system", "content": "你是小说创作顾问，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )
            return json.loads(resp)
        except Exception as e:
            logger.error(f"融入建议生成失败: {e}")
            return {
                "suggestion_type": "outline",
                "target": "",
                "reason": f"生成失败: {e}",
                "modified_content": "",
                "insert_position": "",
            }

    # ===== 标记使用 =====

    def mark_used(self, insp_id: str, chapter_id: int) -> None:
        """标记碎片已在某章使用。"""
        insp = self.get(insp_id)
        if insp:
            insp.status = STATUS_USED
            insp.related_chapter = chapter_id
            self.update(insp)

    def mark_scheduled(self, insp_id: str, chapter_id: int) -> None:
        """标记碎片已安排到某章。"""
        insp = self.get(insp_id)
        if insp:
            insp.status = STATUS_SCHEDULED
            insp.related_chapter = chapter_id
            self.update(insp)

    # ===== 格式化输出 =====

    def format_for_prompt(self, inspirations: list[Inspiration]) -> str:
        """将灵感碎片列表格式化为 prompt 文本。"""
        if not inspirations:
            return ""
        parts = ["=== 灵感碎片（请在写作中适当融入） ==="]
        for insp in inspirations:
            type_label = TYPE_LABELS.get(insp.type, insp.type)
            parts.append(f"【{type_label}】{insp.title}")
            parts.append(f"  内容: {insp.content}")
            if insp.related_characters:
                parts.append(f"  关联人物: {', '.join(insp.related_characters)}")
            if insp.tags:
                parts.append(f"  标签: {', '.join(insp.tags)}")
            parts.append("")
        return "\n".join(parts)

    def get_stats(self) -> dict:
        """统计信息。"""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as c FROM inspirations GROUP BY status"
        ).fetchall()
        stats = {STATUS_DRAFT: 0, STATUS_SCHEDULED: 0, STATUS_USED: 0, STATUS_ARCHIVED: 0}
        for row in rows:
            stats[row["status"]] = row["c"]
        stats["total"] = sum(stats.values())
        return stats
