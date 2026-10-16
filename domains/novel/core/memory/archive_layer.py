"""
ArchiveLayer - 档案库层：结构化的人物表、伏笔表、时间线、世界观设定。

存储方案：SQLite (archive.db)，纯标准库 sqlite3，零依赖。
这是三层记忆的"确定性事实"层，负责确保人物/伏笔/时间线不漂移。
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
from domains.novel.core.memory.similarity import similarity, normalize as _sim_normalize, best_match

# P21 二级危害防护：阻止残词经 apply_facts 写入 characters 表。
# 上游 _extract_entities 已修复（P21），但 LLM 事实提取仍可能独立产生残词，
# 此处做 defense-in-depth——已知非人名词表（金融/任何/周围…）直接拦截。
try:
    from framework_constraint import _NAME_GENERIC as _FRAG_NAMES
except Exception:
    _FRAG_NAMES = set()


# ============ 数据结构定义 ============

@dataclass
class CharacterProfile:
    """人物档案。"""
    id: str = ""
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    role: str = ""               # 主角/配角/反派/路人
    description: str = ""        # 外貌描述
    personality: str = ""        # 性格特征
    background: str = ""         # 背景故事
    current_state: str = ""      # 当前状态（随章节更新）
    abilities: list[str] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)  # [{target, relation, description}]
    first_appearance: int = 0
    last_updated_chapter: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Foreshadow:
    """伏笔记录。"""
    id: str = ""
    description: str = ""
    planted_chapter: int = 0
    status: str = "planted"      # planted | advanced | resolved | abandoned
    related_characters: list[str] = field(default_factory=list)
    resolution_chapter: Optional[int] = None
    priority: int = 3            # 1-5
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class TimelineEntry:
    """时间线条目。"""
    id: str = ""
    chapter_id: int = 0
    event: str = ""
    timestamp_in_story: str = ""
    description: str = ""
    characters_involved: list[str] = field(default_factory=list)
    order: int = 0
    created_at: str = ""


@dataclass
class WorldSetting:
    """世界观设定。"""
    id: str = ""
    category: str = ""           # 地理/势力/规则/物品/其他
    name: str = ""
    description: str = ""
    related_entries: list[str] = field(default_factory=list)
    revision: int = 0            # 设定版本：每次被改/增/删自动 +1（见 _bump_settings_revision）
    created_at: str = ""
    updated_at: str = ""


@dataclass
class FemaleArc:
    """女性角色独立支线（P3-12：跨卷不丢女主剧情）。"""
    id: str = ""
    female_name: str = ""
    arc_summary: str = ""        # 当前支线进展（1-2 行）
    status: str = "active"       # active | resolved | abandoned
    started_chapter: int = 0
    last_chapter: int = 0
    related_protagonist: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class PlotBeatLog:
    """近 20 章已用爽点类型日志（P3-12：写作自动检索规避重复套路）。"""
    id: str = ""
    chapter_id: int = 0
    beat_type: str = ""          # growth_upgrade | face_slap_hide | opportunity_luck | crisis_survival ...
    note: str = ""
    created_at: str = ""


@dataclass
class StoryEvent:
    """故事事件账本（前向规划，治「章节分开生成做不好」）。

    与被动的 timeline（已发生日志）不同，这是「计划要发生的事」：
    - type: main(主线节点/大事件) | major(大事件) | minor(小事件/填充)
    - target_start/target_end: 计划发生的章节窗口
    - status: planned | active | done | skipped
    - depends_on: 前置事件 id 列表（保证主线因果链不崩）
    - linked_milestone: 关联的 Spine 里程碑描述
    每章生成时读取窗口内事件作为硬约束，保证跨章统一推进。
    """
    id: str = ""
    type: str = "minor"          # main | major | minor
    title: str = ""
    description: str = ""
    background: str = ""        # 事件背景/前因（AI 规划时铺填，一句话）
    target_start: int = 0
    target_end: int = 0
    status: str = "planned"      # planned | active | done | skipped
    depends_on: list[str] = field(default_factory=list)
    linked_milestone: str = ""
    priority: int = 3            # 1-5
    # 各章进展：事件在窗口内每章的检查点（AI 规划时铺设的「内容铺垫」）。
    # 每项: {"chapter": int, "title": str, "summary": str}
    # 写章时只取窗口内、且 chapter<=当前章 的「已铺垫」+ 首个 chapter>当前章 的「即将节点」。
    progress: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases TEXT,
    role TEXT,
    description TEXT,
    personality TEXT,
    background TEXT,
    current_state TEXT,
    abilities TEXT,
    relationships TEXT,
    first_appearance INTEGER,
    last_updated_chapter INTEGER,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS foreshadows (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    planted_chapter INTEGER NOT NULL,
    status TEXT DEFAULT 'planted',
    related_characters TEXT,
    resolution_chapter INTEGER,
    priority INTEGER DEFAULT 3,
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS timeline (
    id TEXT PRIMARY KEY,
    chapter_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    timestamp_in_story TEXT,
    description TEXT,
    characters_involved TEXT,
    "order" INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS world_settings (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    related_entries TEXT,
    revision INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS character_aliases (
    alias TEXT PRIMARY KEY,
    character_id TEXT NOT NULL,
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

CREATE TABLE IF NOT EXISTS female_arc (
    id TEXT PRIMARY KEY,
    female_name TEXT NOT NULL,
    arc_summary TEXT,
    status TEXT DEFAULT 'active',
    started_chapter INTEGER,
    last_chapter INTEGER,
    related_protagonist TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS plot_beat_log (
    id TEXT PRIMARY KEY,
    chapter_id INTEGER NOT NULL,
    beat_type TEXT NOT NULL,
    note TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS event_ledger (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT,
    description TEXT,
    background TEXT,
    target_start INTEGER,
    target_end INTEGER,
    status TEXT DEFAULT 'planned',
    depends_on TEXT,
    linked_milestone TEXT,
    priority INTEGER DEFAULT 3,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_timeline_chapter ON timeline(chapter_id);
CREATE INDEX IF NOT EXISTS idx_foreshadow_status ON foreshadows(status);
CREATE INDEX IF NOT EXISTS idx_world_category ON world_settings(category);
CREATE INDEX IF NOT EXISTS idx_female_arc_status ON female_arc(status);
CREATE INDEX IF NOT EXISTS idx_plot_beat_chapter ON plot_beat_log(chapter_id);
CREATE INDEX IF NOT EXISTS idx_event_status ON event_ledger(status);
CREATE INDEX IF NOT EXISTS idx_event_window ON event_ledger(target_start, target_end);

-- 设定全局版本号（设定版本绑定讨论结论）：讨论/写入任何 world_settings 增改删后 +1
CREATE TABLE IF NOT EXISTS settings_revision (
    id INTEGER PRIMARY KEY,
    revision INTEGER DEFAULT 1,
    updated_at TEXT,
    note TEXT
);
"""


class ArchiveLayer:
    """
    档案库层：结构化事实存储。

    所有表都有完整 schema，支持复杂查询。
    写后通过 extract_facts → apply_facts 自动更新。
    """

    def __init__(self, project_root: str, llm_client: LLMClient):
        self._project_root = Path(project_root)
        self._db_path = self._project_root / ".novelforge" / "archive.db"
        self._llm = llm_client
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """初始化数据库。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        # 迁移：事件账本补充 progress 列（各章进展）。幂等。
        try:
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(event_ledger)").fetchall()]
            if "progress" not in cols:
                self._conn.execute("ALTER TABLE event_ledger ADD COLUMN progress TEXT")
            if "background" not in cols:
                self._conn.execute("ALTER TABLE event_ledger ADD COLUMN background TEXT")
        except Exception:
            pass
        # 迁移：world_settings 补充 revision 列（设定版本绑定讨论结论）。
        # SCHEMA 已有该列；老库可能缺失，缺则补（幂等，保护旧项目库）。
        try:
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(world_settings)").fetchall()]
            if "revision" not in cols:
                self._conn.execute("ALTER TABLE world_settings ADD COLUMN revision INTEGER DEFAULT 0")
        except Exception:
            pass
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ===== 事实提取 =====

    def extract_facts(self, chapter_id: int, chapter_text: str) -> dict:
        """
        调用 LLM 从正文中提取结构化事实。

        返回格式：
        {
          "characters": [...],
          "foreshadows": [...],
          "timeline_events": [...],
          "world_settings": [...]
        }
        """
        # 获取已有人物名（帮助 LLM 消歧）
        existing_chars = self.get_all_characters()
        char_list = "\n".join(
            f"- {c.name}（别名: {', '.join(c.aliases)}）" for c in existing_chars
        ) if existing_chars else "（暂无已登记人物）"

        prompt = f"""请从以下小说章节正文中提取结构化事实，以 JSON 格式输出。

已有人物列表（用于消歧，不要重复创建）：
{char_list}

章节正文：
---
{chapter_text[:8000]}
---

请输出以下 JSON 格式（只输出 JSON，不要其他内容）：
{{
  "characters": [
    {{
      "name": "人物名",
      "action": "appeared|updated|new",
      "state_change": {{"before": "之前状态", "after": "之后状态", "reason": "变化原因"}},
      "new_info": {{"description": "外貌", "personality": "性格", "ability": "能力", "background": "背景"}},
      "relationships": [{{"target": "对方人物名", "relation": "关系描述"}}]
    }}
  ],
  "foreshadows": [
    {{
      "action": "planted|advanced|resolved",
      "description": "伏笔内容",
      "related_characters": ["相关人物名"],
      "existing_description": "如果是推进或回收已有伏笔，填写该伏笔的描述以便匹配"
    }}
  ],
  "timeline_events": [
    {{
      "event": "事件名",
      "timestamp_in_story": "故事内时间",
      "description": "事件描述",
      "characters_involved": ["参与人物"]
    }}
  ],
  "world_settings": [
    {{
      "category": "地理|势力|规则|物品|其他",
      "name": "设定名",
      "description": "设定描述"
    }}
  ]
}}

注意：
1. action="new" 表示新出场人物，"updated" 表示已有人物状态变化，"appeared" 表示已有人物出场但无重大变化
2. 只提取本章明确发生的事实，不要推测或虚构
3. 伏笔的 action="planted" 表示新埋下，"advanced" 表示推进，"resolved" 表示回收
4. 如果某个类别没有提取到内容，返回空数组"""

        # 长章节事实 JSON 易超 max_tokens 被截断（Unterminated string）。
        # 改用 chat_json：自动检测截断并翻倍 max_tokens 重试（封顶 16000），
        # 避免档案事实大面积静默缺失（这是 P4 修复的核心场景）。
        try:
            facts = self._llm.chat_json(
                messages=[
                    {"role": "system", "content": "你是一个小说事实提取助手，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=4000,
                response_format={"type": "json_object"},
                retry=3,
                max_tokens_cap=16000,
            )
            logger.info(f"第{chapter_id}章事实提取: {len(facts.get('characters', []))}人物, "
                        f"{len(facts.get('foreshadows', []))}伏笔, "
                        f"{len(facts.get('timeline_events', []))}时间线, "
                        f"{len(facts.get('world_settings', []))}世界观")
            return facts
        except Exception as e:
            logger.error(f"第{chapter_id}章事实提取最终失败: {e}")
            return {"characters": [], "foreshadows": [], "timeline_events": [], "world_settings": []}

    def apply_facts(self, chapter_id: int, facts: dict) -> dict:
        """
        将提取的事实应用到档案库（事务性更新）。

        返回更新报告。
        """
        report = {
            "characters_new": 0,
            "characters_updated": 0,
            "foreshadows_new": 0,
            "foreshadows_advanced": 0,
            "foreshadows_resolved": 0,
            "timeline_added": 0,
            "world_settings_new": 0,
            "world_settings_updated": 0,
        }

        now = datetime.now().isoformat()

        try:
            self._conn.execute("BEGIN")

            # 处理人物
            for char_data in facts.get("characters", []):
                if not isinstance(char_data, dict):
                    continue
                name = char_data.get("name", "").strip()
                if not name:
                    continue
                if name in _FRAG_NAMES:
                    logger.warning(f"apply_facts: 跳过疑似残词人物名 '{name}'（P21 防护）")
                    continue
                action = char_data.get("action", "appeared")
                existing = self._find_character_by_name(name)

                if existing is None:
                    # 新人物
                    new_info = char_data.get("new_info") or {}
                    state_change = char_data.get("state_change") or {}
                    char = CharacterProfile(
                        id=str(uuid.uuid4()),
                        name=name,
                        aliases=char_data.get("aliases") or [],
                        role=new_info.get("role", ""),
                        description=new_info.get("description", ""),
                        personality=new_info.get("personality", ""),
                        background=new_info.get("background", ""),
                        current_state=state_change.get("after", ""),
                        abilities=[new_info.get("ability")] if new_info.get("ability") else [],
                        relationships=char_data.get("relationships") or [],
                        first_appearance=chapter_id,
                        last_updated_chapter=chapter_id,
                        created_at=now,
                        updated_at=now,
                    )
                    self._insert_character(char)
                    report["characters_new"] += 1
                elif action == "updated":
                    # 更新已有人物
                    state_change = char_data.get("state_change") or {}
                    if state_change.get("after"):
                        existing.current_state = state_change["after"]
                    new_info = char_data.get("new_info") or {}
                    if new_info.get("description") and not existing.description:
                        existing.description = new_info["description"]
                    if new_info.get("personality") and not existing.personality:
                        existing.personality = new_info["personality"]
                    if new_info.get("ability") and new_info["ability"] not in existing.abilities:
                        existing.abilities.append(new_info["ability"])
                    # 合并关系
                    for rel in (char_data.get("relationships") or []):
                        existing.relationships.append(rel)
                    existing.last_updated_chapter = chapter_id
                    existing.updated_at = now
                    self._update_character(existing)
                    report["characters_updated"] += 1

            # 处理伏笔
            for fs_data in facts.get("foreshadows", []):
                if not isinstance(fs_data, dict):
                    continue
                action = fs_data.get("action", "planted")
                desc = fs_data.get("description", "").strip()
                if not desc:
                    continue

                if action == "planted":
                    # 治本：尝试合并到已有同线（相似度），避免「同一伏笔反复插新行」膨胀
                    existing_fs = self._find_foreshadow_by_desc(desc, threshold=0.6)
                    if existing_fs is not None:
                        if len(desc) > len(existing_fs.description or ""):
                            existing_fs.description = desc
                        for rc in (fs_data.get("related_characters") or []):
                            if rc and rc not in existing_fs.related_characters:
                                existing_fs.related_characters.append(rc)
                        if existing_fs.status == "planted":
                            existing_fs.status = "advanced"
                        existing_fs.updated_at = now
                        self._update_foreshadow(existing_fs)
                        report["foreshadows_advanced"] += 1
                    else:
                        fs = Foreshadow(
                            id=str(uuid.uuid4()),
                            description=desc,
                            planted_chapter=chapter_id,
                            status="planted",
                            related_characters=fs_data.get("related_characters") or [],
                            priority=3,
                            created_at=now,
                            updated_at=now,
                        )
                        self._insert_foreshadow(fs)
                        report["foreshadows_new"] += 1
                else:
                    # 推进或回收已有伏笔
                    existing_desc = fs_data.get("existing_description", desc)
                    existing_fs = self._find_foreshadow_by_desc(existing_desc)
                    if existing_fs:
                        if action == "advanced":
                            existing_fs.status = "advanced"
                            report["foreshadows_advanced"] += 1
                        elif action == "resolved":
                            existing_fs.status = "resolved"
                            existing_fs.resolution_chapter = chapter_id
                            report["foreshadows_resolved"] += 1
                        existing_fs.updated_at = now
                        self._update_foreshadow(existing_fs)

            # 处理时间线（治本：本章内相似事件去重，避免同一事件反复插行）
            current_order = self._get_max_timeline_order()
            existing_tl = self.get_timeline((chapter_id, chapter_id))
            inserted_tl_norms = set()
            inserted_tl_texts = []
            for i, tl_data in enumerate(facts.get("timeline_events", [])):
                if not isinstance(tl_data, dict):
                    continue
                event = tl_data.get("event", "").strip()
                if not event:
                    continue
                nev = _sim_normalize(event)
                dup = nev in inserted_tl_norms
                if not dup:
                    for ev in existing_tl + inserted_tl_texts:
                        if similarity(getattr(ev, "event", ev), event) >= 0.8:
                            dup = True
                            break
                if dup:
                    continue
                inserted_tl_norms.add(nev)
                inserted_tl_texts.append(event)
                tl = TimelineEntry(
                    id=str(uuid.uuid4()),
                    chapter_id=chapter_id,
                    event=event,
                    timestamp_in_story=tl_data.get("timestamp_in_story", ""),
                    description=tl_data.get("description", ""),
                    characters_involved=tl_data.get("characters_involved") or [],
                    order=current_order + i + 1,
                    created_at=now,
                )
                self._insert_timeline(tl)
                report["timeline_added"] += 1

            # 处理世界观
            for ws_data in facts.get("world_settings", []):
                if not isinstance(ws_data, dict):
                    continue
                name = ws_data.get("name", "").strip()
                if not name:
                    continue
                category = ws_data.get("category", "其他")
                existing_ws = self._find_world_setting(name, category)
                if existing_ws is None:
                    ws = WorldSetting(
                        id=str(uuid.uuid4()),
                        category=category,
                        name=name,
                        description=ws_data.get("description", ""),
                        created_at=now,
                        updated_at=now,
                    )
                    self._insert_world_setting(ws)
                    report["world_settings_new"] += 1
                else:
                    existing_ws.description = ws_data.get("description", existing_ws.description)
                    existing_ws.updated_at = now
                    self._update_world_setting(existing_ws)
                    report["world_settings_updated"] += 1

            self._conn.commit()
            logger.info(f"第{chapter_id}章事实已提交: {report}")
        except Exception as e:
            self._conn.rollback()
            logger.error(f"事实提交失败: {e}")
            raise

        return report

    # ===== 清空 / 幂等 =====

    def clear_derived(self):
        """清空全部派生事实表（全书重写前重置记忆用），保留 source 设定文件。

        派生表：characters / character_aliases / foreshadows / timeline / world_settings。
        这些是每章提交时由 extract_facts 重建的，清空后随重写逐章干净重建，
        避免百万字级重复提交导致记忆无限膨胀。
        """
        try:
            self._conn.execute("DELETE FROM character_aliases")
            self._conn.execute("DELETE FROM characters")
            self._conn.execute("DELETE FROM foreshadows")
            self._conn.execute("DELETE FROM timeline")
            self._conn.execute("DELETE FROM world_settings")
            self._conn.execute("DELETE FROM event_ledger")
            self._conn.commit()
            self._bump_settings_revision("全书重写：清空并重建全部设定")
            logger.info("档案派生记忆已清空")
        except Exception as e:
            self._conn.rollback()
            logger.error(f"清空档案派生记忆失败: {e}")
            raise

    def delete_chapter_facts(self, chapter_id: int):
        """删除指定章节已提交的派生事实（时间线/伏笔），使重复提交幂等。

        人物与世界设定按名称 upsert（不会重复）；时间线与伏笔按章删除后重建，
        防止同一章被多次提交时记录翻倍（百万字重写崩溃的根因之一）。
        """
        try:
            self._conn.execute("DELETE FROM timeline WHERE chapter_id = ?", (chapter_id,))
            self._conn.execute("DELETE FROM foreshadows WHERE planted_chapter = ?", (chapter_id,))
            self._conn.commit()
            logger.info(f"已清除第{chapter_id}章旧派生事实（时间线/伏笔）")
        except Exception as e:
            self._conn.rollback()
            logger.error(f"清除第{chapter_id}章派生事实失败: {e}")
            raise

    # ===== 查询接口 =====

    def get_character(self, name: str) -> Optional[CharacterProfile]:
        """按名称或别名查询人物。"""
        return self._find_character_by_name(name)

    def get_all_characters(self) -> list[CharacterProfile]:
        """获取所有人物档案。"""
        rows = self._conn.execute("SELECT * FROM characters ORDER BY first_appearance").fetchall()
        return [self._row_to_character(r) for r in rows]

    def get_active_foreshadows(self) -> list[Foreshadow]:
        """获取所有未回收的伏笔。"""
        rows = self._conn.execute(
            "SELECT * FROM foreshadows WHERE status IN ('planted', 'advanced') ORDER BY priority DESC, planted_chapter"
        ).fetchall()
        return [self._row_to_foreshadow(r) for r in rows]

    def get_foreshadows(self, status: Optional[str] = None) -> list[Foreshadow]:
        """查询伏笔，可按状态过滤。"""
        if status:
            rows = self._conn.execute(
                "SELECT * FROM foreshadows WHERE status = ? ORDER BY planted_chapter", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM foreshadows ORDER BY planted_chapter").fetchall()
        return [self._row_to_foreshadow(r) for r in rows]

    def get_timeline(self, chapter_range: Optional[tuple[int, int]] = None) -> list[TimelineEntry]:
        """查询时间线。"""
        if chapter_range:
            rows = self._conn.execute(
                "SELECT * FROM timeline WHERE chapter_id BETWEEN ? AND ? ORDER BY \"order\"",
                chapter_range
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM timeline ORDER BY \"order\"").fetchall()
        return [self._row_to_timeline(r) for r in rows]

    def get_world_settings(self, category: Optional[str] = None) -> list[WorldSetting]:
        """查询世界观设定。"""
        if category:
            rows = self._conn.execute(
                "SELECT * FROM world_settings WHERE category = ? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM world_settings ORDER BY category, name").fetchall()
        return [self._row_to_world_setting(r) for r in rows]

    # ===== 女性角色支线（P3-12：跨卷不丢女主剧情） =====

    def upsert_female_arc(self, female_name: str, arc_summary: str = "", chapter_id: int = 0,
                          status: str = "active", related_protagonist: str = "") -> None:
        now = datetime.now().isoformat()
        row = self._conn.execute(
            "SELECT * FROM female_arc WHERE female_name = ?", (female_name,)
        ).fetchone()
        if row:
            self._conn.execute(
                "UPDATE female_arc SET arc_summary=?, status=?, last_chapter=?, updated_at=? "
                "WHERE female_name=?",
                (arc_summary, status, chapter_id or row["last_chapter"], now, female_name),
            )
        else:
            self._conn.execute(
                "INSERT INTO female_arc (id, female_name, arc_summary, status, started_chapter, "
                "last_chapter, related_protagonist, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), female_name, arc_summary, status,
                 chapter_id, chapter_id, related_protagonist, now, now),
            )
        self._conn.commit()

    def get_active_female_arcs(self) -> list[FemaleArc]:
        rows = self._conn.execute(
            "SELECT * FROM female_arc WHERE status='active' ORDER BY last_chapter DESC"
        ).fetchall()
        return [self._row_to_female_arc(r) for r in rows]

    def get_female_arc_summary(self) -> str:
        """拼接所有活跃女主支线为单行文本（供主线脊柱注入，零 LLM）。"""
        arcs = self.get_active_female_arcs()
        if not arcs:
            return ""
        return "；".join(f"{a.female_name}：{a.arc_summary}" for a in arcs if a.arc_summary)

    def finish_female_arc(self, female_name: str) -> None:
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE female_arc SET status='resolved', updated_at=? WHERE female_name=?",
            (now, female_name),
        )
        self._conn.commit()

    def _row_to_female_arc(self, r) -> FemaleArc:
        return FemaleArc(
            id=r["id"], female_name=r["female_name"], arc_summary=r["arc_summary"] or "",
            status=r["status"] or "active", started_chapter=r["started_chapter"] or 0,
            last_chapter=r["last_chapter"] or 0, related_protagonist=r["related_protagonist"] or "",
            created_at=r["created_at"] or "", updated_at=r["updated_at"] or "",
        )

    # ===== 爽点类型日志（P3-12：写作自动检索规避重复套路） =====

    def log_plot_beat(self, chapter_id: int, beat_type: str, note: str = "") -> None:
        now = datetime.now().isoformat()
        self._conn.execute(
            "INSERT INTO plot_beat_log (id, chapter_id, beat_type, note, created_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), chapter_id, beat_type, note, now),
        )
        self._conn.commit()

    def get_recent_plot_beats(self, limit: int = 20) -> list[PlotBeatLog]:
        rows = self._conn.execute(
            "SELECT * FROM plot_beat_log ORDER BY chapter_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_plot_beat(r) for r in rows]

    def _row_to_plot_beat(self, r) -> PlotBeatLog:
        return PlotBeatLog(
            id=r["id"], chapter_id=r["chapter_id"], beat_type=r["beat_type"],
            note=r["note"] or "", created_at=r["created_at"] or "",
        )

    # ===== 故事事件账本（前向规划：本章应发生什么） =====

    def upsert_event(self, ev: StoryEvent) -> None:
        now = datetime.now().isoformat()
        if not ev.id:
            ev.id = str(uuid.uuid4())
        if not ev.created_at:
            ev.created_at = now
        ev.updated_at = now
        exists = self._conn.execute("SELECT 1 FROM event_ledger WHERE id = ?", (ev.id,)).fetchone()
        prog_json = json.dumps(ev.progress, ensure_ascii=False) if ev.progress else "[]"
        if exists:
            self._conn.execute(
                "UPDATE event_ledger SET type=?, title=?, description=?, background=?, target_start=?, "
                "target_end=?, status=?, depends_on=?, linked_milestone=?, priority=?, progress=?, updated_at=? "
                "WHERE id=?",
                (ev.type, ev.title, ev.description, ev.background, ev.target_start, ev.target_end, ev.status,
                 json.dumps(ev.depends_on, ensure_ascii=False), ev.linked_milestone, ev.priority,
                 prog_json, now, ev.id),
            )
        else:
            self._conn.execute(
                "INSERT INTO event_ledger (id, type, title, description, background, target_start, target_end, "
                "status, depends_on, linked_milestone, priority, progress, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev.id, ev.type, ev.title, ev.description, ev.background, ev.target_start, ev.target_end, ev.status,
                 json.dumps(ev.depends_on, ensure_ascii=False), ev.linked_milestone, ev.priority,
                 prog_json, ev.created_at, now),
            )
        self._conn.commit()

    def get_events(
        self,
        status: Optional[str] = None,
        etype: Optional[str] = None,
        chapter_id: Optional[int] = None,
    ) -> list[StoryEvent]:
        """查询事件账本。可按状态/类型过滤；chapter_id 给定时只返回窗口覆盖该章的事件。"""
        sql = "SELECT * FROM event_ledger WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if etype:
            sql += " AND type = ?"
            params.append(etype)
        if chapter_id is not None:
            sql += " AND target_start <= ? AND target_end >= ?"
            params.append(chapter_id)
            params.append(chapter_id)
        sql += " ORDER BY target_start, priority DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_story_event(r) for r in rows]

    def get_due_events(self, chapter_id: int) -> list[StoryEvent]:
        """窗口覆盖本章的全部事件（含 planned/active/done；skipped 由上层按需过滤）。

        注意：这里返回「所有状态」，是因为 get_chapter_event_checkpoints 需要 done 事件作为深大纲 event_progress 的「拍摄脚本节点」。
        若只想拿「未完成」事件（如注入「须覆盖」清单），请在调用方自行过滤 status!='done'，或改用 get_overdue_events。
        """
        return self.get_events(status=None, chapter_id=chapter_id)

    def get_overdue_events(self, chapter_id: int) -> list[StoryEvent]:
        """窗口已结束（target_end < chapter_id）但仍未完成的事件（planned/active）。

        这些是「本该发生却没发生」的事件，必须在后续章节强制补上 —— 治主线崩溃。
        """
        rows = self._conn.execute(
            "SELECT * FROM event_ledger WHERE target_end < ? AND status IN ('planned','active') "
            "ORDER BY target_end, priority DESC",
            (chapter_id,),
        ).fetchall()
        return [self._row_to_story_event(r) for r in rows]

    def mark_event_done(self, event_id: str, chapter_id: int) -> None:
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE event_ledger SET status='done', updated_at=? WHERE id=?",
            (now, event_id),
        )
        self._conn.commit()

    def set_event_status(self, event_id: str, status: str) -> None:
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE event_ledger SET status=?, updated_at=? WHERE id=?",
            (status, now, event_id),
        )
        self._conn.commit()

    def delete_event(self, event_id: str) -> None:
        self._conn.execute("DELETE FROM event_ledger WHERE id = ?", (event_id,))
        self._conn.commit()

    def advance_events_for_chapter(self, chapter_id: int) -> dict:
        """提交章节后推进事件状态（零 LLM）：

        - 窗口覆盖本章且仍为 planned → active（进入执行）
        - 窗口已闭合（target_end <= chapter_id）且仍为 planned/active → done（视为本章已覆盖）
        返回 {activated, completed} 计数。
        """
        activated = 0
        completed = 0
        rows = self._conn.execute(
            "SELECT * FROM event_ledger WHERE status IN ('planned','active')"
        ).fetchall()
        now = datetime.now().isoformat()
        for r in rows:
            ev = self._row_to_story_event(r)
            if ev.target_start <= chapter_id <= ev.target_end:
                if ev.status == "planned":
                    self._conn.execute(
                        "UPDATE event_ledger SET status='active', updated_at=? WHERE id=?",
                        (now, ev.id),
                    )
                    activated += 1
            elif ev.target_end is not None and ev.target_end <= chapter_id:
                self._conn.execute(
                    "UPDATE event_ledger SET status='done', updated_at=? WHERE id=?",
                    (now, ev.id),
                )
                completed += 1
        if activated or completed:
            self._conn.commit()
        return {"activated": activated, "completed": completed}

    def format_due_events(self, chapter_id: int) -> str:
        """渲染本章「应覆盖的计划事件」紧凑文本（供大纲/正文 prompt 注入）。无则空串。

        防膨胀：窗口内「未完成」due 事件封顶 12 条（按优先级），逾期事件封顶最近 6 条。
        已标 done 的事件其检查点已在前序章节落地，不再列入（防跨章重复推进同一事件）；
        逾期列表本身只含 planned/active，天然不含 done。正常顺序写作时事件会被 commit 标 done，
        逾期列表本就很小；此封顶仅作安全网。
        """
        def _rt(p):
            """相对时间维度渲染：优先『相对X第N天』，无则空（兼容旧数据无此字段）。"""
            rt = p.get("relative_time")
            if not isinstance(rt, dict):
                return ""
            rel = rt.get("relative_to") or "本事件起点"
            off = rt.get("offset_days")
            if off is None:
                return f"(相对{rel})"
            try:
                off = float(off)
                off_s = str(int(off)) if off == int(off) else str(off)
            except (TypeError, ValueError):
                off_s = str(off)
            return f"(相对{rel}第{off_s}天)"

        due = [e for e in self.get_due_events(chapter_id) if e.status != "done"]
        overdue = self.get_overdue_events(chapter_id)
        if not due and not overdue:
            return ""
        # due 按优先级降序，封顶 12；已「done」的事件其检查点已落地，不再列入「须覆盖」（防跨章重复推进）
        due_sorted = sorted(due, key=lambda e: (-(e.priority or 0), e.target_start))
        due_top = due_sorted[:12]
        # overdue 按 target_end 降序（最近错过的最优先补），封顶 6
        overdue_top = sorted(overdue, key=lambda e: -(e.target_end or 0))[:6]
        label = {"main": "主线", "major": "大事件", "minor": "小事件"}
        parts = ["=== 故事事件账本（本章须覆盖的计划事件，来自共享故事计划） ==="]
        for ev in due_top:
            tag = {"done": "已完成", "active": "进行中", "skipped": "已跳过"}.get(ev.status, "待启动")
            parts.append(
                f"· [{label.get(ev.type, ev.type)}/{tag}] 计划第{ev.target_start}~{ev.target_end}章："
                f"{ev.title} — {ev.description}"
            )
            # 各章进展：只给「已铺垫（截至本章）」+「即将节点（首个未到章）」，
            # 不 dumping 全窗口，避免重复且让 AI 明确本步该做什么。
            prog = [p for p in (ev.progress or []) if isinstance(p, dict)]
            laid = [p for p in prog if (p.get("chapter") or 0) <= chapter_id]
            coming = [p for p in prog if (p.get("chapter") or 0) > chapter_id]
            coming.sort(key=lambda p: p.get("chapter", 0))
            if laid:
                laid.sort(key=lambda p: p.get("chapter", 0))
                laid_txt = "；".join(
                    f"第{p['chapter']}章"
                    f"{_rt(p) if p.get('relative_time') else (('(第'+str(p['story_day'])+'天)') if p.get('story_day') else '')}"
                    f"·{p.get('title','')}：{p.get('summary','')}" for p in laid
                )
                parts.append(f"    已铺垫（截至第{chapter_id}章）：{laid_txt}")
            if coming:
                nxt = coming[0]
                parts.append(
                    f"    本章/即将节点（第{nxt.get('chapter')}章"
                    f"{_rt(nxt) if nxt.get('relative_time') else (('·第'+str(nxt.get('story_day'))+'天') if nxt.get('story_day') else '')}"
                    f"）：{nxt.get('title','')} — {nxt.get('summary','')}"
                )
        if len(due_sorted) > len(due_top):
            parts.append(f"· …（另有 {len(due_sorted) - len(due_top)} 条窗口内事件略去）")
        if overdue_top:
            parts.append("【逾期未完成，必须在后续章节（含本章）补上】：")
            for ev in overdue_top:
                parts.append(
                    f"· [{label.get(ev.type, ev.type)}/逾期] 原计划第{ev.target_start}~{ev.target_end}章："
                    f"{ev.title} — {ev.description}"
                )
        if len(overdue) > len(overdue_top):
            parts.append(f"· …（另有 {len(overdue) - len(overdue_top)} 条更早逾期事件，随章节推进已陆续吸收）")
        parts.append("要求：本章核心事件必须向前推进上述计划事件（按「已铺垫→即将节点」逐步落地），"
                     "不得无视或跳过错过的节点，也不得凭空发明未规划情节（不吃书）。")
        return "\n".join(parts)

    def get_chapter_event_checkpoints(self, chapter_id: int) -> list[dict]:
        """返回本章须执行的事件检查点（结构化），供深大纲 event_progress 对齐。

        仅排除 skipped（已作废）事件；planned/active/done 均纳入——
        因为规划描述章节「应含内容」，与写章进度无关（done 事件在其窗口章仍是无可争议的拍摄脚本节点）。
        每事件取：严格落在本章的节点（chapter==ch）。即将节点（chapter>ch）不属于本章拍摄脚本，
        那是写章 pass（format_due_events）的推进提示，避免跨章污染（吃书）。
        这样章节层「拍摄脚本」与事件层「账本节点」是同一份数据（双重铺设对齐，不吃书）。
        上限 16 个，超出按事件优先级截断。"""
        due = [e for e in self.get_due_events(chapter_id)
               if e.status != "skipped"]
        due.sort(key=lambda e: (-(e.priority or 0), e.target_start))
        out: list[dict] = []
        for ev in due:
            prog = [p for p in (ev.progress or []) if isinstance(p, dict)]
            if not prog:
                continue
            this_ch = [p for p in prog if (p.get("chapter") or 0) == chapter_id]
            # 仅取严格落在本章的节点，杜绝跨章污染（把第N章进展点塞进第M章=吃书）。
            # 『首个 chapter>ch 的即将节点』不再作为本章 beat：那是写章 pass（format_due_events）
            # 的推进提示，且已明确标注为“即将节点（第N章）”，不属于本章施工图的拍摄脚本。
            sel = list(this_ch)
            for p in sel:
                out.append({
                    "event_id": ev.id,
                    "event_title": ev.title,
                    "event_type": ev.type,
                    "chapter": p.get("chapter"),
                    "title": p.get("title", ""),
                    "summary": p.get("summary", ""),
                    "relative_time": p.get("relative_time"),
                })
            if len(out) >= 16:
                break
        return out

    def clear_event_ledger(self) -> None:
        """清空事件账本（全书重写前重置，随后重新规划生成）。"""
        try:
            self._conn.execute("DELETE FROM event_ledger")
            self._conn.commit()
            logger.info("事件账本已清空")
        except Exception as e:
            self._conn.rollback()
            logger.error(f"清空事件账本失败: {e}")
            raise

    def _row_to_story_event(self, r) -> StoryEvent:
        return StoryEvent(
            id=r["id"],
            type=r["type"] or "minor",
            title=r["title"] or "",
            description=r["description"] or "",
            background=r["background"] or "",
            target_start=r["target_start"] or 0,
            target_end=r["target_end"] or 0,
            status=r["status"] or "planned",
            depends_on=json.loads(r["depends_on"]) if r["depends_on"] else [],
            linked_milestone=r["linked_milestone"] or "",
            priority=r["priority"] or 3,
            progress=json.loads(r["progress"]) if r["progress"] else [],
            created_at=r["created_at"] or "",
            updated_at=r["updated_at"] or "",
        )

    def search_by_keywords(self, keywords: list[str]) -> dict:
        """
        关键词触发的多表检索（类似 SillyTavern 的 World Info）。
        在人物名/别名、伏笔描述、世界观名称中匹配关键词。
        """
        results = {"characters": [], "foreshadows": [], "world_settings": []}
        if not keywords:
            return results

        # 人物匹配
        all_chars = self.get_all_characters()
        for char in all_chars:
            names = [char.name] + char.aliases
            for kw in keywords:
                if any(kw in n for n in names):
                    results["characters"].append(char)
                    break

        # 伏笔匹配
        all_fs = self.get_foreshadows()
        for fs in all_fs:
            for kw in keywords:
                if kw in fs.description or any(kw in c for c in fs.related_characters):
                    results["foreshadows"].append(fs)
                    break

        # 世界观匹配
        all_ws = self.get_world_settings()
        for ws in all_ws:
            for kw in keywords:
                if kw in ws.name or kw in ws.description:
                    results["world_settings"].append(ws)
                    break

        return results

    def get_active_cast(
        self, current_chapter: int, window: int = 30, limit: int = 8
    ) -> list[CharacterProfile]:
        """配角轮换花名册：返回「近期在场」的人物，供每章顺手带一带配角戏份。

        治「只有主角太无聊」：写作时给 AI 一份当前世界里还活跃的配角名单
        （近 window 章内出现/更新过的人物，按最近出场排序，封顶 limit）。
        主角（role 含「主角」）优先置顶且不计入裁剪，其余配角轮换出镜。
        字数再多也只取近窗口 + 封顶，注入成本恒定。
        """
        all_chars = self.get_all_characters()
        if not all_chars:
            return []
        lo = max(0, current_chapter - max(1, window))
        recent = [c for c in all_chars if c.last_updated_chapter >= lo]
        # 近窗口内没人（如刚开书），退回最近更新的若干人
        if not recent:
            recent = sorted(all_chars, key=lambda c: c.last_updated_chapter, reverse=True)[:limit]

        protagonists = [c for c in recent if "主角" in (c.role or "")]
        others = [c for c in recent if "主角" not in (c.role or "")]
        others.sort(key=lambda c: c.last_updated_chapter, reverse=True)

        roster = protagonists + others
        # 主角全留，配角按上限裁剪
        capped = protagonists + others[: max(0, limit - len(protagonists))]
        return capped if capped else roster[:limit]

    def search_characters_for_chapter(
        self,
        writing_instruction: str,
        current_chapter: int = 0,
        window: int = 40,
        limit: int = 15,
    ) -> list[CharacterProfile]:
        """根据写作指令检索可能相关的人物（窗口化 + 上限，防长篇小说人物无限膨胀）。

        优先级：主角(role含「主角」) 永远保底 > 本章程关键词命中者(相关性) > 近期在场者(最近 window 章更新过)。
        最终封顶 limit 个。无命中时退回最近更新的人物，保证始终有人物上下文。

        这样既控 token，又提质：只给与本章相关 + 近期活跃的人物，避免上百号人淹没主角、
        劣化一致性（这正是「内容不崩、要素齐全」的第一优先级考量）。
        """
        keywords = extract_keywords(writing_instruction, top_n=15)
        all_chars = self.get_all_characters()
        if not all_chars:
            return []

        # 相关性评分：名字/别名命中=3，描述/状态命中=1
        scored: list[tuple[int, CharacterProfile]] = []
        for char in all_chars:
            score = 0
            names = [char.name] + char.aliases
            for kw in keywords:
                if any(kw in n or n in kw for n in names if n):
                    score = max(score, 3)
                    break
            if score == 0:
                for kw in keywords:
                    if kw in (char.current_state or "") or kw in (char.description or ""):
                        score = max(score, 1)
                        break
            if score > 0:
                scored.append((score, char))

        # 主角永远保底
        protagonists = [c for c in all_chars if "主角" in (c.role or "")]

        selected: list[CharacterProfile] = []
        seen: set[str] = set()

        def _add(c: CharacterProfile):
            if c.name not in seen:
                selected.append(c)
                seen.add(c.name)

        for c in protagonists:
            _add(c)

        # 关键词相关者：按相关性降序、再近期更新降序
        scored.sort(key=lambda x: (x[0], x[1].last_updated_chapter), reverse=True)
        for _, c in scored:
            _add(c)

        # 若相关人物偏少（< _MIN_CONTEXT），且已知当前章，用「近期在场」窗口补齐到 _MIN_CONTEXT，
        # 保持世界有人气但不污染聚焦章节；超过则不再回填（聚焦章节保持精简）。
        _MIN_CONTEXT = 8
        if len(selected) < _MIN_CONTEXT and current_chapter > 0:
            lo = max(0, current_chapter - max(1, window))
            recent = sorted(
                [c for c in all_chars if c.last_updated_chapter >= lo and c.name not in seen],
                key=lambda c: c.last_updated_chapter, reverse=True,
            )
            for c in recent:
                if len(selected) >= _MIN_CONTEXT:
                    break
                _add(c)

        # 封顶
        if len(selected) > limit:
            selected = selected[:limit]

        # 兜底：完全无命中时退回最近更新的若干人
        if not selected:
            selected = sorted(all_chars, key=lambda c: c.last_updated_chapter, reverse=True)[:limit]

        return selected

    # ===== 手动编辑接口 =====

    def save_character(self, char: CharacterProfile) -> None:
        if not char.id:
            char.id = str(uuid.uuid4())
            char.created_at = datetime.now().isoformat()
        char.updated_at = datetime.now().isoformat()
        self._insert_or_update_character(char)

    def save_foreshadow(self, fs: Foreshadow) -> None:
        if not fs.id:
            fs.id = str(uuid.uuid4())
            fs.created_at = datetime.now().isoformat()
        fs.updated_at = datetime.now().isoformat()
        self._insert_or_update_foreshadow(fs)

    def save_timeline_entry(self, tl: TimelineEntry) -> None:
        if not tl.id:
            tl.id = str(uuid.uuid4())
            tl.created_at = datetime.now().isoformat()
        self._insert_or_update_timeline(tl)

    def save_world_setting(self, ws: WorldSetting) -> None:
        # 代码层强制（非提示词）：任意类别的 world_settings 必须维持「单一定义不变量」
        # （泛化守卫，不止力量体系）。任何把同一维度写成「两套/须明确区分/不可混为一谈/
        # 另一把尺」等分裂表述的写入，无论它是力量/地理/物品/规则/势力哪一类，都直接拦截。
        if ws and ws.description:
            try:
                from domains.novel.core.discussion.world_settings_integrity import (
                    validate_world_setting, WorldSettingIntegrityError,
                )
                _issues = validate_world_setting(ws.description)
                if _issues:
                    raise WorldSettingIntegrityError(
                        "设定违反单一真源不变量，已拦截写入：\n" + "\n".join(_issues)
                    )
            except WorldSettingIntegrityError:
                raise
            except Exception:
                pass  # 守卫模块不可用时降级，不阻断正常存档
        if not ws.id:
            ws.id = str(uuid.uuid4())
            ws.created_at = datetime.now().isoformat()
        ws.updated_at = datetime.now().isoformat()
        self._insert_or_update_world_setting(ws)

    def delete_character(self, character_id: str) -> None:
        self._conn.execute("DELETE FROM characters WHERE id = ?", (character_id,))
        self._conn.execute("DELETE FROM character_aliases WHERE character_id = ?", (character_id,))
        self._conn.commit()

    def delete_foreshadow(self, foreshadow_id: str) -> None:
        self._conn.execute("DELETE FROM foreshadows WHERE id = ?", (foreshadow_id,))
        self._conn.commit()

    def delete_timeline_entry(self, entry_id: str) -> None:
        self._conn.execute("DELETE FROM timeline WHERE id = ?", (entry_id,))
        self._conn.commit()

    def delete_world_setting(self, setting_id: str) -> None:
        self._conn.execute("DELETE FROM world_settings WHERE id = ?", (setting_id,))
        self._conn.commit()
        self._bump_settings_revision(f"删除了设定条目（id={setting_id}）")

    def _bump_settings_revision(self, note: str = "") -> int:
        """设定写库后的自动副作用：world_settings 任何增/改/删后，全局版本号 +1。

        这是「设定版本绑定讨论结论」机制的自动触发器——无需人工跑脚本。
        同时把「设定已更新」提示文本写入 .novelforge/settings_update_note.txt，
        供 UI 在对话流顶部插入，使旧结论可见地失效。返回新版本号。
        """
        try:
            row = self._conn.execute(
                "SELECT revision FROM settings_revision WHERE id=1"
            ).fetchone()
            new = (row[0] + 1) if row else 1
            self._conn.execute(
                "INSERT INTO settings_revision(id, revision, updated_at, note) "
                "VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "revision=excluded.revision, updated_at=excluded.updated_at, note=excluded.note",
                (new, datetime.now().isoformat(), note or "设定已变更"),
            )
            self._conn.commit()
            # 同步 world_settings 各条目 revision，便于「本书决策」比对
            self._conn.execute(
                "UPDATE world_settings SET revision=? WHERE revision<? OR revision IS NULL OR revision=0",
                (new, new),
            )
            self._conn.commit()
            # 产出 UI 可用的「设定已更新」提示
            try:
                import os as _os
                note_path = _os.path.join(
                    _os.path.dirname(_os.path.dirname(self._db_path))
                    if hasattr(self, "_db_path") else "", "settings_update_note.txt"
                )
                # 优先用 .novelforge 目录（archive.db 的父目录）
                db_dir = _os.path.dirname(self._conn.execute(
                    "PRAGMA database_list"
                ).fetchall() and "x") if False else ""
                # archive.db 路径由 self 持有，这里用最稳的方式：从 connection 文件推断
                note_path = self._settings_note_path()
                if note_path:
                    with open(note_path, "w", encoding="utf-8") as _f:
                        _f.write(
                            f"[系统提示] 本书设定已于 {datetime.now().strftime('%H:%M')} 更新至 v{new}。"
                            f"此前对话中基于旧版设定的结论（如旧的战力/地图/人物关系表述）可能已失效，"
                            f"请以当前设定为准，必要时重新确认。"
                        )
            except Exception:
                pass
            return new
        except Exception as e:
            logger.error(f"bump settings_revision 失败: {e}")
            return 0

    def _settings_note_path(self) -> str:
        """推断 .novelforge/settings_update_note.txt 路径（archive.db 的父目录）。"""
        try:
            import os as _os
            # self 持有 _conn；用 PRAGMA 拿 file 路径
            _rows = self._conn.execute("PRAGMA database_list").fetchall()
            for _r in _rows:
                if _r[1] == "main" and _r[2]:
                    _db = _r[2]
                    return _os.path.join(_os.path.dirname(_db), "settings_update_note.txt")
        except Exception:
            pass
        return ""


    # ===== 内部方法 =====

    def _find_character_by_name(self, name: str) -> Optional[CharacterProfile]:
        # 先精确匹配名字
        row = self._conn.execute("SELECT * FROM characters WHERE name = ?", (name,)).fetchone()
        if row is None:
            # 再匹配别名
            row = self._conn.execute(
                "SELECT c.* FROM characters c JOIN character_aliases a ON c.id = a.character_id WHERE a.alias = ?",
                (name,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_character(row)

    def _find_foreshadow_by_desc(self, desc: str, threshold: float = 0.5) -> Optional[Foreshadow]:
        """相似度匹配已有伏笔（只在未回收的线里找）；threshold 控制合并松紧。"""
        rows = self._conn.execute(
            "SELECT * FROM foreshadows WHERE status IN ('planted', 'advanced')"
        ).fetchall()
        fss = [self._row_to_foreshadow(r) for r in rows]
        m = best_match(desc, fss, lambda fs: fs.description, threshold)
        return m[0] if m else None

    def _find_world_setting(self, name: str, category: str) -> Optional[WorldSetting]:
        row = self._conn.execute(
            "SELECT * FROM world_settings WHERE name = ? AND category = ?", (name, category)
        ).fetchone()
        if row:
            return self._row_to_world_setting(row)
        # 治本：同 category 下 name 相似视为同一设定，避免同物异名重复插入
        rows = self._conn.execute(
            "SELECT * FROM world_settings WHERE category = ?", (category,)
        ).fetchall()
        fss = [self._row_to_world_setting(r) for r in rows]
        m = best_match(name, fss, lambda ws: ws.name, threshold=0.7)
        return m[0] if m else None

    def _get_max_timeline_order(self) -> int:
        row = self._conn.execute("SELECT MAX(\"order\") as m FROM timeline").fetchone()
        return row["m"] or 0 if row else 0

    def _insert_character(self, char: CharacterProfile):
        self._conn.execute(
            "INSERT INTO characters (id, name, aliases, role, description, personality, background, "
            "current_state, abilities, relationships, first_appearance, last_updated_chapter, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (char.id, char.name, json.dumps(char.aliases, ensure_ascii=False), char.role,
             char.description, char.personality, char.background, char.current_state,
             json.dumps(char.abilities, ensure_ascii=False),
             json.dumps(char.relationships, ensure_ascii=False),
             char.first_appearance, char.last_updated_chapter, char.created_at, char.updated_at)
        )
        for alias in char.aliases:
            self._conn.execute(
                "INSERT OR IGNORE INTO character_aliases (alias, character_id) VALUES (?, ?)",
                (alias, char.id)
            )

    def _update_character(self, char: CharacterProfile):
        self._conn.execute(
            "UPDATE characters SET name=?, aliases=?, role=?, description=?, personality=?, "
            "background=?, current_state=?, abilities=?, relationships=?, last_updated_chapter=?, "
            "updated_at=? WHERE id=?",
            (char.name, json.dumps(char.aliases, ensure_ascii=False), char.role,
             char.description, char.personality, char.background, char.current_state,
             json.dumps(char.abilities, ensure_ascii=False),
             json.dumps(char.relationships, ensure_ascii=False),
             char.last_updated_chapter, char.updated_at, char.id)
        )
        # 更新别名表
        self._conn.execute("DELETE FROM character_aliases WHERE character_id = ?", (char.id,))
        for alias in char.aliases:
            self._conn.execute(
                "INSERT OR IGNORE INTO character_aliases (alias, character_id) VALUES (?, ?)",
                (alias, char.id)
            )

    def _insert_or_update_character(self, char: CharacterProfile):
        exists = self._conn.execute("SELECT 1 FROM characters WHERE id = ?", (char.id,)).fetchone()
        if exists:
            self._update_character(char)
        else:
            self._insert_character(char)
        self._conn.commit()

    def _insert_foreshadow(self, fs: Foreshadow):
        self._conn.execute(
            "INSERT INTO foreshadows (id, description, planted_chapter, status, related_characters, "
            "resolution_chapter, priority, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fs.id, fs.description, fs.planted_chapter, fs.status,
             json.dumps(fs.related_characters, ensure_ascii=False),
             fs.resolution_chapter, fs.priority, fs.notes, fs.created_at, fs.updated_at)
        )

    def _update_foreshadow(self, fs: Foreshadow):
        self._conn.execute(
            "UPDATE foreshadows SET description=?, planted_chapter=?, status=?, related_characters=?, "
            "resolution_chapter=?, priority=?, notes=?, updated_at=? WHERE id=?",
            (fs.description, fs.planted_chapter, fs.status,
             json.dumps(fs.related_characters, ensure_ascii=False),
             fs.resolution_chapter, fs.priority, fs.notes, fs.updated_at, fs.id)
        )

    def _insert_or_update_foreshadow(self, fs: Foreshadow):
        exists = self._conn.execute("SELECT 1 FROM foreshadows WHERE id = ?", (fs.id,)).fetchone()
        if exists:
            self._update_foreshadow(fs)
        else:
            self._insert_foreshadow(fs)
        self._conn.commit()

    def _insert_timeline(self, tl: TimelineEntry):
        self._conn.execute(
            "INSERT INTO timeline (id, chapter_id, event, timestamp_in_story, description, "
            "characters_involved, \"order\", created_at) VALUES (?,?,?,?,?,?,?,?)",
            (tl.id, tl.chapter_id, tl.event, tl.timestamp_in_story, tl.description,
             json.dumps(tl.characters_involved, ensure_ascii=False), tl.order, tl.created_at)
        )

    def _insert_or_update_timeline(self, tl: TimelineEntry):
        self._conn.execute(
            "INSERT OR REPLACE INTO timeline (id, chapter_id, event, timestamp_in_story, description, "
            "characters_involved, \"order\", created_at) VALUES (?,?,?,?,?,?,?,?)",
            (tl.id, tl.chapter_id, tl.event, tl.timestamp_in_story, tl.description,
             json.dumps(tl.characters_involved, ensure_ascii=False), tl.order, tl.created_at)
        )
        self._conn.commit()

    def _insert_world_setting(self, ws: WorldSetting):
        self._conn.execute(
            "INSERT INTO world_settings (id, category, name, description, related_entries, "
            "revision, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (ws.id, ws.category, ws.name, ws.description,
             json.dumps(ws.related_entries, ensure_ascii=False),
             ws.revision or 1, ws.created_at, ws.updated_at)
        )

    def _update_world_setting(self, ws: WorldSetting):
        self._conn.execute(
            "UPDATE world_settings SET category=?, name=?, description=?, related_entries=?, "
            "revision=?, updated_at=? WHERE id=?",
            (ws.category, ws.name, ws.description,
             json.dumps(ws.related_entries, ensure_ascii=False),
             ws.revision or 1, ws.updated_at, ws.id)
        )

    def _insert_or_update_world_setting(self, ws: WorldSetting):
        exists = self._conn.execute("SELECT 1 FROM world_settings WHERE id = ?", (ws.id,)).fetchone()
        if exists:
            self._update_world_setting(ws)
        else:
            self._insert_world_setting(ws)
        self._conn.commit()
        self._bump_settings_revision(f"{'更新' if exists else '新增'}设定条目：{ws.name}")


    # ===== Row 转换 =====

    @staticmethod
    def _row_to_character(row: sqlite3.Row) -> CharacterProfile:
        return CharacterProfile(
            id=row["id"],
            name=row["name"],
            aliases=json.loads(row["aliases"]) if row["aliases"] else [],
            role=row["role"] or "",
            description=row["description"] or "",
            personality=row["personality"] or "",
            background=row["background"] or "",
            current_state=row["current_state"] or "",
            abilities=json.loads(row["abilities"]) if row["abilities"] else [],
            relationships=json.loads(row["relationships"]) if row["relationships"] else [],
            first_appearance=row["first_appearance"] or 0,
            last_updated_chapter=row["last_updated_chapter"] or 0,
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    @staticmethod
    def _row_to_foreshadow(row: sqlite3.Row) -> Foreshadow:
        return Foreshadow(
            id=row["id"],
            description=row["description"],
            planted_chapter=row["planted_chapter"],
            status=row["status"],
            related_characters=json.loads(row["related_characters"]) if row["related_characters"] else [],
            resolution_chapter=row["resolution_chapter"],
            priority=row["priority"],
            notes=row["notes"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    @staticmethod
    def _row_to_timeline(row: sqlite3.Row) -> TimelineEntry:
        return TimelineEntry(
            id=row["id"],
            chapter_id=row["chapter_id"],
            event=row["event"],
            timestamp_in_story=row["timestamp_in_story"] or "",
            description=row["description"] or "",
            characters_involved=json.loads(row["characters_involved"]) if row["characters_involved"] else [],
            order=row["order"] or 0,
            created_at=row["created_at"] or "",
        )

    @staticmethod
    def _row_to_world_setting(row: sqlite3.Row) -> WorldSetting:
        _raw = row["related_entries"]
        _rel: list = []
        if _raw:
            try:
                _rel = json.loads(_raw)
            except Exception:
                # 容错：历史数据可能存为分号/逗号分隔字符串而非 JSON 数组
                _rel = [x.strip() for x in str(_raw).replace(";", ",").split(",") if x.strip()]
        return WorldSetting(
            id=row["id"],
            category=row["category"],
            name=row["name"],
            description=row["description"] or "",
            related_entries=_rel,
            revision=row["revision"] if "revision" in row.keys() else 0,
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

