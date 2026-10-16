"""NovelForge 适配器 — 把 NovelForge 的小说分析工作流程接入 AIChat。

NovelForge 是一个 AI 驱动的小说数据分析软件，核心工作流程：
  1. 加载小说项目数据（人物档案/伏笔/时间线/世界观/章节大纲/正文）
  2. 组装上下文（设定层 + 大纲 + 正文精准注入）
  3. AI 讨论 agent（可调用 read_chapter / search_novel / check_consistency）
  4. AI 生成结构化提案（task cards），用户确认后写入数据层
  5. 一致性守卫检查（防止幻觉/矛盾）

本适配器把这个工作流程桥接到 AIChat：
  - 项目数据从 NovelForge 的 .novelforge/archive.db + JSON 文件读取
  - AI 调用工具通过 AIChat 的 <|tool|> 协议（read_chapter / search_novel 等）
  - 提案通过 on_response() 检测 JSON 块
  - 讨论记忆存入 AIChat 四层记忆系统（L1→L2→L3→L4）
  - 隐藏记忆通过 <|memory|> 协议自动提取
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..protocol.types import Message, Session, MemoryResult, ChatRole
from ..protocol.adapter import ChatAdapter


class NovelForgeAdapter(ChatAdapter):
    """NovelForge 小说项目讨论适配器。

    用法：
        adapter = NovelForgeAdapter(project_root="/path/to/novel/project")
        # 或通过 kwargs
        adapter = NovelForgeAdapter(project_root="...", project_name="大苍界")
    """

    def __init__(self, **kwargs):
        self._project_root: str = kwargs.get("project_root", "")
        self._project_name: str = kwargs.get("project_name", "")
        self._project_data: dict = {}
        self._load_project_data()

    # ============================================================
    # ChatAdapter 接口
    # ============================================================

    @property
    def key(self) -> str:
        return "novelforge"

    @property
    def display_name(self) -> str:
        return f"小说分析 - {self._project_name}" if self._project_name else "小说分析"

    @property
    def description(self) -> str:
        return "NovelForge 小说项目分析：人物/伏笔/时间线/世界观/一致性检查"

    def build_system_prompt(self, session: Session) -> str:
        """构建 NovelForge 讨论系统提示词。"""
        from ..tools import TOOL_PROTOCOL_MD, MEMORY_PROTOCOL_MD

        parts = [
            "你是 NovelForge 小说项目的「架构顾问」agent，帮助用户分析剧情、人物、伏笔、节奏与大纲结构。",
            "你可以讨论剧情走向、角色发展、世界观设定、写作技巧等。",
            "当用户要求修改正文/设定时，生成结构化提案（JSON 格式），供用户确认后执行。",
            "",
            "工作原则：",
            "1. 回答骨架必须设定先行：按「①设定/体系 ②伏笔/暗线 ③背景/世界观 ④章节证据」组织",
            "2. 引用具体章节号时必须真实存在，只作为设定/伏笔的落地印证",
            "3. 证据不足必须明说「当前资料不足」，严禁编造",
            "4. 改写正文时 before 必须逐字匹配原文",
            "5. 你只提建议、不改数据；写入需用户点「采纳并写入」",
            "6. 回答精炼+结构化，分节用【小标题】起头",
            "7. 设定文件优先于聊天记录结论",
            "",
            "# NovelForge 写作技法参考（讨论写作质量时引用）",
            "NovelForge 写作系统提示采用 L0 硬约束 / L1 核心技法 / L2 可选模块三层 + 预算门控。以下技法已集成，讨论写作质量时可引用：",
            "- 灵魂场·行为铁律：每个主要人物 3-5 条不可违背的行为铁律（如朋友有难必救、承诺必兑），全书不得破例；违反则改情节不改角色",
            "- 3:1 连续流：叙事时间≠故事时间，平淡过渡压缩成一句、密集冲突展开成整段，约 3 份摘要对 1 份实写锚点；同一时空严禁切分，用环境流转/动作承接软缝",
            "- 感觉先于思考：身体先反应再进入推理；流畅叙述中可插入极短硬句（如「他很冷。」）制造节奏断点",
            "- 动作节拍：对话间插入动作/表情/环境替代「他说」式标签，连续 4 句以上纯对白仅用「他说/你说」是失败模式",
            "- 遮标签测试：遮住说话人名字，仅凭语气用词仍能分辨角色——若分不出说明声音不够鲜明",
            "- 感官锚点公式：[时间/频率]+[身体部位]+[非日常类比]+[可量化趋势]，将抽象概念绑定到具体生理感受",
            "- 通感与错位：跨感官描写（「她的笑是淡粉色的」）；同一章不重复同一种身体反应（「心一紧」后换「胃里发凉」「忘了呼吸」）",
            "- used-tropes 套路日志：archive.log_plot_beat(chapter_id, beat_type, note) 记录套路变体，避免重复腻烦",
        ]

        # 注入项目上下文
        ctx = self._build_project_context()
        if ctx:
            parts.append("")
            parts.append("# 当前小说项目上下文")
            parts.append(ctx)

        # 注入工具协议 + 记忆协议 + 流程蓝图协议
        parts.append("")
        parts.append(TOOL_PROTOCOL_MD)
        parts.append("")
        parts.append(MEMORY_PROTOCOL_MD)
        try:
            from ..flow import get_flow_protocol_md
            parts.append("")
            parts.append(get_flow_protocol_md())
        except Exception:
            pass

        return "\n".join(parts)

    def build_context_messages(self, session: Session,
                                memory_results: list[MemoryResult]) -> list[Message]:
        """注入记忆检索结果（来自 AIChat 四层记忆系统）。"""
        msgs: list[Message] = []
        if memory_results:
            # 按记忆类型分组
            facts: list[str] = []
            prefs: list[str] = []
            entities: list[str] = []
            others: list[str] = []

            for r in memory_results[:10]:
                mem_type = r.metadata.get("memory_type", "")
                line = f"- [{r.scope}] {r.content[:200]}"
                if mem_type == "fact":
                    facts.append(line)
                elif mem_type == "preference":
                    prefs.append(line)
                elif mem_type == "entity":
                    entities.append(line)
                else:
                    others.append(line)

            sections: list[str] = []
            if facts:
                sections.append("## 历史事实\n" + "\n".join(facts))
            if prefs:
                sections.append("## 用户偏好\n" + "\n".join(prefs))
            if entities:
                sections.append("## 已知实体\n" + "\n".join(entities))
            if others:
                sections.append("## 其他记忆\n" + "\n".join(others))

            if sections:
                msgs.append(Message(
                    role=ChatRole.SYSTEM,
                    content="以下是相关的历史讨论记忆：\n\n" + "\n\n".join(sections)
                ))
        return msgs

    def get_scope(self, session: Session) -> str:
        """返回 NovelForge 项目的记忆 scope。"""
        proj = self._project_name or "unknown"
        return f"novelforge:{proj}"

    def get_memory_filters(self, session: Session) -> dict[str, Any]:
        """返回记忆过滤条件。"""
        return {"adapter": "novelforge"}

    def on_response(self, session: Session, response: str) -> dict[str, Any] | None:
        """解析 AI 响应中的提案 JSON。

        检测格式：
          ```json
          { "tasks": [...] }
          ```
          或裸 JSON { "proposal": [...] }
        """
        # 先剥离记忆标签（提案检测不看记忆部分）
        try:
            from ..tools import extract_memory
            clean_response, _ = extract_memory(response)
        except Exception:
            clean_response = response

        # 检测提案 JSON
        proposal = None
        if "```json" in clean_response:
            try:
                start = clean_response.index("```json") + 7
                end = clean_response.index("```", start)
                json_str = clean_response[start:end].strip()
                proposal = json.loads(json_str)
            except (ValueError, json.JSONDecodeError):
                pass

        if proposal is None and '"proposal"' in clean_response:
            try:
                start = clean_response.index("{")
                end = clean_response.rindex("}") + 1
                json_str = clean_response[start:end]
                proposal = json.loads(json_str)
            except (ValueError, json.JSONDecodeError):
                pass

        if proposal and isinstance(proposal, dict):
            return {"type": "proposal", "data": proposal}

        return None

    # ============================================================
    # 项目数据加载
    # ============================================================

    def _load_project_data(self) -> None:
        """加载 NovelForge 项目数据。"""
        if not self._project_root:
            return

        root = Path(self._project_root)
        if not root.exists():
            return

        data: dict[str, Any] = {}

        # 1. 项目名
        if not self._project_name:
            proj_path = root / "project.json"
            if proj_path.exists():
                try:
                    proj = json.loads(proj_path.read_text(encoding="utf-8"))
                    self._project_name = proj.get("name", "") or proj.get("title", "")
                except Exception:
                    pass
            if not self._project_name:
                self._project_name = root.name

        # 2. 从 archive.db 读取结构化数据
        db_path = root / ".novelforge" / "archive.db"
        if db_path.exists():
            data["archive"] = self._load_archive_db(db_path)

        # 3. 加载章节大纲
        outlines = self._load_outlines(root)
        if outlines:
            data["outlines"] = outlines

        # 4. 加载讨论规则
        rules_path = root / ".novelforge" / "discussion_rules.json"
        if rules_path.exists():
            try:
                data["discussion_rules"] = json.loads(
                    rules_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 5. 加载讨论记忆
        mem_path = root / ".novelforge" / "discussion_memory.json"
        if mem_path.exists():
            try:
                data["discussion_memory"] = json.loads(
                    mem_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 6. 加载设定层（story_spine / volumes / structure_spec）
        settings_dir = root / ".novelforge" / "settings"
        if settings_dir.exists():
            data["settings"] = self._load_settings(settings_dir)

        # 7. 章节目录检测
        chapters_dir = root / "chapters"
        if chapters_dir.exists():
            chapters = sorted(chapters_dir.glob("chapter_*.md"))
            data["chapter_count"] = len(chapters)
            data["chapters_dir"] = str(chapters_dir)
            data["chapter_files"] = [f.name for f in chapters[:200]]

        self._project_data = data

    def _load_archive_db(self, db_path: Path) -> dict:
        """从 archive.db 读取人物/伏笔/时间线/世界观。"""
        result: dict[str, Any] = {}
        try:
            db = sqlite3.connect(str(db_path))
            db.row_factory = sqlite3.Row

            # 人物表
            try:
                rows = db.execute("SELECT * FROM characters ORDER BY last_updated_chapter DESC LIMIT 50").fetchall()
                result["characters"] = [dict(r) for r in rows]
            except Exception:
                result["characters"] = []

            # 伏笔表
            try:
                rows = db.execute("SELECT * FROM foreshadows ORDER BY planted_chapter DESC LIMIT 30").fetchall()
                result["foreshadows"] = [dict(r) for r in rows]
            except Exception:
                result["foreshadows"] = []

            # 时间线
            try:
                rows = db.execute("SELECT * FROM timeline ORDER BY chapter_id DESC LIMIT 20").fetchall()
                result["timeline"] = [dict(r) for r in rows]
            except Exception:
                result["timeline"] = []

            # 世界观设定
            try:
                rows = db.execute("SELECT * FROM world_settings LIMIT 30").fetchall()
                result["world_settings"] = [dict(r) for r in rows]
            except Exception:
                result["world_settings"] = []

            db.close()
        except Exception:
            pass

        return result

    def _load_outlines(self, root: Path) -> list[dict]:
        """加载章节大纲。"""
        # 尝试多个可能的路径
        for name in ["chapter_outlines.json", ".novelforge/chapter_outlines.json"]:
            p = root / name
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        return data[:200]
                    if isinstance(data, dict) and "chapters" in data:
                        return data["chapters"][:200]
                except Exception:
                    pass
        return []

    def _load_settings(self, settings_dir: Path) -> dict:
        """加载设定层文件。"""
        settings: dict[str, Any] = {}
        for json_file in settings_dir.glob("*.json"):
            try:
                settings[json_file.stem] = json.loads(
                    json_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return settings

    # ============================================================
    # 上下文组装
    # ============================================================

    def _build_project_context(self) -> str:
        """构建注入 system prompt 的项目上下文。"""
        if not self._project_data:
            return ""

        parts: list[str] = []

        # 项目概览
        parts.append(f"项目名称：{self._project_name}")
        chapter_count = self._project_data.get("chapter_count", 0)
        if chapter_count:
            parts.append(f"章节数：{chapter_count}")

        # 人物档案（精简）
        archive = self._project_data.get("archive", {})
        characters = archive.get("characters", [])
        if characters:
            char_lines: list[str] = []
            for c in characters[:20]:
                name = c.get("name", "?")
                role = c.get("role", "")
                desc = (c.get("description") or "")[:60]
                state = (c.get("current_state") or "")[:40]
                line = f"  - {name}（{role}）: {desc}"
                if state:
                    line += f" | 当前: {state}"
                char_lines.append(line)
            parts.append("## 人物档案\n" + "\n".join(char_lines))

        # 伏笔
        foreshadows = archive.get("foreshadows", [])
        if foreshadows:
            fs_lines: list[str] = []
            for f in foreshadows[:15]:
                desc = (f.get("description") or "")[:80]
                status = f.get("status", "planted")
                ch = f.get("planted_chapter", "?")
                fs_lines.append(f"  - [{status}] 第{ch}章: {desc}")
            parts.append("## 伏笔\n" + "\n".join(fs_lines))

        # 世界观设定
        world = archive.get("world_settings", [])
        if world:
            world_lines: list[str] = []
            for w in world[:10]:
                name = w.get("name") or w.get("category") or "?"
                desc = (w.get("description") or "")[:100]
                world_lines.append(f"  - {name}: {desc}")
            parts.append("## 世界观设定\n" + "\n".join(world_lines))

        # 时间线
        timeline = archive.get("timeline", [])
        if timeline:
            tl_lines: list[str] = []
            for t in timeline[:10]:
                ch = t.get("chapter_id", "?")
                event = (t.get("event") or "")[:60]
                tl_lines.append(f"  - 第{ch}章: {event}")
            parts.append("## 时间线（近10条）\n" + "\n".join(tl_lines))

        # 章节大纲（最近5章）
        outlines = self._project_data.get("outlines", [])
        if outlines:
            ol_lines: list[str] = []
            for o in outlines[-5:]:
                ch_id = o.get("chapter_id", o.get("id", "?"))
                summary = (o.get("plot_summary") or o.get("core_events") or "")[:80]
                ch_type = o.get("chapter_type_label", "")
                line = f"  - 第{ch_id}章"
                if ch_type:
                    line += f"（{ch_type}）"
                line += f": {summary}"
                ol_lines.append(line)
            parts.append("## 最近章节大纲\n" + "\n".join(ol_lines))

        # 设定层
        settings = self._project_data.get("settings", {})
        if settings:
            # story_spine 最重要
            spine = settings.get("story_spine", {})
            if isinstance(spine, dict):
                spine_text = spine.get("text", spine.get("spine", ""))
                if spine_text:
                    parts.append("## 故事脊柱\n" + str(spine_text)[:500])

            # 修炼体系
            structure = settings.get("structure_spec", {})
            if isinstance(structure, dict):
                struct_desc = structure.get("description", str(structure))[:300]
                parts.append("## 力量体系\n" + str(struct_desc))

        # 讨论规则
        rules = self._project_data.get("discussion_rules", {})
        if rules:
            rules_text = json.dumps(rules, ensure_ascii=False, indent=2)[:1000] if rules else ""
            parts.append("## 讨论规则参考\n" + rules_text)

        # 讨论记忆（最近5条）
        disc_mem = self._project_data.get("discussion_memory", [])
        if disc_mem and isinstance(disc_mem, list):
            mem_lines: list[str] = []
            for m in disc_mem[-5:]:
                scope = m.get("scope") or ""
                content = (m.get("content") or "")[:80]
                mem_lines.append(f"  - [{scope}] {content}")
            parts.append("## 历史讨论记忆\n" + "\n".join(mem_lines))

        # 限制总量（防 token 爆）
        result = "\n\n".join(parts)
        if len(result) > 6000:
            result = result[:6000] + "\n... [上下文截断，完整数据可通过工具读取]"
        return result

    # ============================================================
    # 工具支持：提供给 skill 调用的辅助方法
    # ============================================================

    def get_chapter_path(self, chapter_id: int) -> str:
        """获取章节文件路径。"""
        root = Path(self._project_root)
        # 尝试多种命名规则
        patterns = [
            f"chapter_{chapter_id:04d}.md",
            f"chapter_{chapter_id:03d}.md",
            f"chapter_{chapter_id}.md",
            f"第{chapter_id}章.md",
        ]
        for name in patterns:
            p = root / "chapters" / name
            if p.exists():
                return str(p)
        return ""

    def get_all_characters(self) -> list[dict]:
        """获取全部人物档案。"""
        archive = self._project_data.get("archive", {})
        return archive.get("characters", [])

    def get_all_foreshadows(self) -> list[dict]:
        """获取全部伏笔。"""
        archive = self._project_data.get("archive", {})
        return archive.get("foreshadows", [])

    def get_all_outlines(self) -> list[dict]:
        """获取全部大纲。"""
        return self._project_data.get("outlines", [])

    def get_world_settings(self) -> list[dict]:
        """获取世界观设定。"""
        archive = self._project_data.get("archive", {})
        return archive.get("world_settings", [])

    def get_settings(self) -> dict:
        """获取设定层。"""
        return self._project_data.get("settings", {})
