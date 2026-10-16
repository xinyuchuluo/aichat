"""
MemoryEngine - 三层记忆引擎总调度。

这是整个工具的核心。三层记忆协同工作：
- 档案库层：确定性事实（人物/伏笔/时间线/世界观）
- RAG层：跨章节细节回忆（向量检索）
- 摘要层：主线脉络连贯（结构化章节摘要）

写前：retrieve_for_writing() 统一检索三层记忆
写后：commit_chapter() 分发更新三层记忆
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from domains.novel.core.llm_client import LLMClient
from domains.novel.core.memory.archive_layer import (
    ArchiveLayer, CharacterProfile, Foreshadow, TimelineEntry, WorldSetting
)
from domains.novel.core.memory.rag_layer import RAGLayer
from domains.novel.core.memory.summary_layer import SummaryLayer, ChapterSummary
from domains.novel.utils.logger import logger


@dataclass
class MemoryRetrievalResult:
    """三层记忆检索的合并结果。"""
    # 摘要层
    summaries: list[ChapterSummary] = field(default_factory=list)
    # RAG层
    rag_chunks: list[dict] = field(default_factory=list)  # [{text, chapter_id, score}]
    # 档案库层
    characters: list[CharacterProfile] = field(default_factory=list)
    foreshadows: list[Foreshadow] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    world_settings: list[WorldSetting] = field(default_factory=list)
    # 全书总纲 + 简介（概览）—— 写作时必须遵循，之前版本遗漏注入导致章节偏离设定
    master_outline: str = ""
    synopsis: str = ""
    # 灵感碎片（可选，由外部注入）
    inspirations: list = field(default_factory=list)  # list[Inspiration]
    # 卷结构（一致性增强）—— 写作时需保持卷主题与节奏
    volumes: list = field(default_factory=list)
    current_volume: Optional[dict] = None
    recent_outlines: list = field(default_factory=list)  # 最近若干章大纲（含类型与钩子，用于节奏账本）
    nearby_outlines: list = field(default_factory=list)  # 当前章前后窗口内章节的完整大纲（防小范围吃书）
    # 主线脊柱账本（治百万字主线崩溃）—— 已渲染的紧凑文本，始终注入且体量恒定
    story_spine: str = ""
    # 配角轮换花名册（治「只有主角太无聊」）—— 近期在场人物名单
    active_cast: list = field(default_factory=list)  # list[CharacterProfile]
    # 时期感知标记（由 _apply_phase_filter 设置，供 assemble 阶段读取）
    phase: str = ""          # "", "early", "mid", "late", "endgame"
    vol_label: str = ""      # 当前卷标题（如「第一卷：废脉觉醒」），供时期·设定贴合提示词使用
    reveal_endgame: bool = True  # 是否允许在 spine 中暴露终局方向
    current_volume_plan: str = ""  # 当前卷规划段（含情绪节奏配比），供写作提示词显式注入，强化「忠于本卷节奏」
    # 故事事件账本（前向规划：本章应覆盖的计划事件，硬约束，治「分开生成做不好」）
    event_ledger: str = ""
    # 连续性状态卡（治本：时间线/事件状态模型——主角在第几天、人在哪、东西拿到没）
    # 原 assembler 只有脊柱+前瞻待办+末10条流水，缺这张快照，LLM 只能靠猜→吃书。
    # 由 novelforge.continuity_state.get_state_card 在写前检索时注入。
    continuity_state: str = ""
    # 结构化弧 / 暗线 状态卡（L2 弧归属 + 起承转合 + 本章暗线节点，治「弧跨卷 / 暗线断裂」）
    # 由 novelforge.arc_subplot.get_arc_subplot_card 在写前检索时注入。
    arc_subplot: str = ""

    def to_dict(self) -> dict:
        return {
            "summaries": [s.to_dict() for s in self.summaries],
            "rag_chunks": self.rag_chunks,
            "characters": [c.__dict__ if hasattr(c, '__dict__') else c for c in self.characters],
            "foreshadows": [f.__dict__ if hasattr(f, '__dict__') else f for f in self.foreshadows],
            "timeline": [t.__dict__ if hasattr(t, '__dict__') else t for t in self.timeline],
            "world_settings": [w.__dict__ if hasattr(w, '__dict__') else w for w in self.world_settings],
            "master_outline": self.master_outline,
            "synopsis": self.synopsis,
            "volumes": self.volumes,
            "current_volume": self.current_volume,
        }


@dataclass
class CommitReport:
    """章节提交报告。"""
    chapter_id: int
    success: bool
    fact_report: dict = field(default_factory=dict)
    summary_created: bool = False
    rag_chunks_added: int = 0
    backup_created: bool = False
    error: str = ""


class MemoryEngine:
    """
    三层记忆引擎。统一调度摘要层、RAG层、档案库层。

    写前：根据当前写作指令检索三层记忆，返回组装所需的原始材料。
    写后：接收新章节正文，分别更新三层记忆。
    """

    def __init__(self, project_root: str, llm_client: LLMClient):
        self._project_root = Path(project_root)
        self._llm = llm_client
        self._archive = ArchiveLayer(project_root, llm_client)
        self._rag = RAGLayer(project_root, llm_client)
        self._summary = SummaryLayer(project_root, llm_client)

        # 主线脊柱账本（治百万字主线崩溃，本地零 LLM 更新）
        self._spine = None
        try:
            from domains.novel.core.memory.story_spine import StorySpine
            self._spine = StorySpine(project_root)
        except Exception as e:
            logger.warning(f"故事脊柱初始化失败: {e}")

        # 灵感碎片管理器（延迟初始化，复用 archive.db）
        self._inspiration = None
        try:
            from domains.novel.core.inspiration.manager import InspirationManager
            self._inspiration = InspirationManager(project_root, llm_client)
        except Exception as e:
            logger.warning(f"灵感碎片管理器初始化失败: {e}")

        # 运行时状态
        self._state_path = self._project_root / ".novelforge" / "state.json"
        self._commits_dir = self._project_root / ".novelforge" / "commits"
        self._backups_dir = self._project_root / ".novelforge" / "backups"
        self._commits_dir.mkdir(parents=True, exist_ok=True)
        self._backups_dir.mkdir(parents=True, exist_ok=True)

    @property
    def archive(self) -> ArchiveLayer:
        return self._archive

    @property
    def rag(self) -> RAGLayer:
        return self._rag

    @property
    def summary(self) -> SummaryLayer:
        return self._summary

    @property
    def inspiration(self):
        """灵感碎片管理器。"""
        return self._inspiration

    @property
    def spine(self):
        """故事脊柱账本。"""
        return self._spine

    # ===== 故事脊柱辅助 =====

    def _extract_endgame(self) -> str:
        """从总纲 master.md 扫描「终局方向」段落（本地，零 LLM）。

        寻找含 终局/结局/最终目标/最终走向 等关键词的标题，取其下非标题正文，
        退化情况下直接抓取含「终局」的句子。返回 ≤120 字的紧凑摘要，找不到返回空串。
        """
        try:
            master = self._project_root / "outline" / "master.md"
            if not master.exists():
                return ""
            text = master.read_text(encoding="utf-8", errors="ignore")
            lines = text.splitlines()
            keywords = ("终局", "结局", "最终目标", "最终走向", "大结局", "收尾")
            captured: list[str] = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                if not stripped.startswith("#"):
                    continue
                if any(k in stripped for k in keywords):
                    buf: list[str] = []
                    for nxt in lines[i + 1:]:
                        s2 = nxt.strip()
                        if s2.startswith("#") and s2:
                            break
                        if s2:
                            buf.append(s2)
                        if len(buf) >= 3:
                            break
                    if buf:
                        captured = buf
                        break
            if not captured:
                for ln in lines:
                    if "终局" in ln or "最终目标" in ln:
                        captured = [ln.strip()]
                        break
            if captured:
                return "；".join(captured)[:120]
        except Exception as e:
            logger.warning(f"扫描终局方向失败: {e}")
        return ""

    # ===== 时期感知（phase-aware）阶段过滤 =====

    def _derive_planned_total(self, volumes: list[dict], chapter_id: int) -> int:
        """推断全书「规划总章数」，用于时期划分（而非已写章数）。

        优先级：卷结构末卷 end_chapter > 总纲『分卷规划』里最大章号 > 已写最大章号（退化）。
        退化风险：若只按『已写章号』算 total_end，则全书永远被判定为 late，
        全量总纲照注——前期章节反而暴露后期内容且浪费 token。故尽量从卷结构/总纲解析规划总长。
        """
        # 1. 卷结构（最权威）
        if volumes:
            te = max(int(v.get("end_chapter", 0)) for v in volumes)
            if te > chapter_id:
                return te
        # 2. 总纲『分卷规划』里的最大章号（如「第281-350章」）
        try:
            master = (self._project_root / "outline" / "master.md").read_text(
                encoding="utf-8", errors="ignore"
            )
            import re
            ends = [int(m.group(1)) for m in re.finditer(r"第\s*\d+\s*[-~]\s*(\d+)\s*章", master)]
            if ends:
                te = max(ends)
                if te > chapter_id:
                    return te
        except Exception:
            pass
        return chapter_id  # 退化：仅已知到本章

    def _derive_phase(self, chapter_id: int, volumes: list[dict]) -> tuple[str, dict]:
        """根据 chapter_id + 卷结构推导当前时期，返回 (phase, info)。

        phase ∈ {"early", "mid", "late", "endgame"}（前 / 中 / 后 / 末）
        - early  （前期）: 进度 < 33%
        - mid    （中期）: 33% ~ 66%
        - late   （后期）: 66% ~ 85%
        - endgame（末期）: > 85%，或处于最后一卷（收束卷）
        info 含 total_end / progress / vol_index / vol_total 供调试与 UI 展示。
        """
        total_end = self._derive_planned_total(volumes, chapter_id)
        progress = chapter_id / total_end if total_end > 0 else 0.0

        vol_index = 0
        vol_total = 0
        try:
            from domains.novel.core.pipeline import volumes as vol_mod
            vols = volumes if volumes else vol_mod.get_volumes_or_default(self._project_root)
            vol_total = len(vols)
            for i, v in enumerate(vols):
                if v.get("start_chapter", 0) <= chapter_id <= v.get("end_chapter", 0):
                    vol_index = i + 1
                    break
        except Exception:
            pass

        is_last_vol = (vol_total > 0 and vol_index == vol_total)
        if is_last_vol:
            phase = "endgame"
        elif progress < 0.33:
            phase = "early"
        elif progress <= 0.66:
            phase = "mid"
        elif progress <= 0.85:
            phase = "late"
        else:
            phase = "endgame"

        info = {
            "phase": phase,
            "progress": round(progress, 3),
            "total_end": total_end,
            "vol_index": vol_index,
            "vol_total": vol_total,
        }
        return phase, info

    def _apply_phase_filter(self, result: "MemoryRetrievalResult", chapter_id: int):
        """写前检索完成后，按当前时期裁剪剧透内容（就地修改 result）。

        原则：前期不知道中后期大纲/伏笔；中期知道前期结果与本期伏笔；
        后期知道关键前期结果 + 本期进行中的线索，但不必塞全本总纲细节。
        """
        volumes = getattr(result, "volumes", None) or []
        phase, _ = self._derive_phase(chapter_id, volumes)

        # --- 1. 总纲裁剪：仅注入「前提段 + 当前卷规划段」，让本章忠于本时期设定 ---
        if result.master_outline:
            result.master_outline = self._phase_filter_master(
                result.master_outline, phase, chapter_id, volumes
            )

        # --- 2. 未来章节大纲：前期/中期剔除 cid > chapter_id 的后文条目 ---
        if result.nearby_outlines:
            if phase in ("early", "mid"):
                result.nearby_outlines = [
                    o for o in result.nearby_outlines
                    if o.get("chapter_id", 0) <= chapter_id
                ]
            # late 阶段保留全部（含后文铺垫）

        # --- 3. 卷列表：仅保留当前卷 ±1 相邻卷 ---
        if volumes:
            cur = next(
                (i for i, v in enumerate(volumes)
                 if v.get("start_chapter", 0) <= chapter_id <= v.get("end_chapter", 0)),
                None,
            )
            if cur is not None:
                lo_v = max(0, cur - 1)
                hi_v = min(len(volumes), cur + 2)
                result.volumes = volumes[lo_v:hi_v]

        # --- 4. story_spine：前期/中期淡化 endgame；后期/末期才逐步揭示终局方向 ---
        reveal_endgame = (phase in ("late", "endgame"))
        result.phase = phase
        result.reveal_endgame = reveal_endgame
        # 当前卷标签（供时期·设定贴合提示词使用）
        vol_label = ""
        if result.master_outline:
            try:
                cur_block, _ = self._extract_volume_sections(result.master_outline, chapter_id, volumes)
                if cur_block:
                    vol_label = cur_block.splitlines()[0].lstrip("#").strip()
            except Exception:
                pass
        result.vol_label = vol_label
        return result

    def _extract_volume_sections(self, master: str, chapter_id: int, volumes: list[dict]) -> tuple:
        """从总纲『分卷规划』段切出 (当前卷规划块, 下一卷规划块)。

        通过卷标题里的「第X-Y章」区间匹配 chapter_id 所属卷；匹配不到时退化为
        用传入的 volumes 区间顺序对应。找不到返回 ("", "")。
        """
        import re
        lines = master.splitlines()
        start = None
        for i, ln in enumerate(lines):
            if ln.strip().startswith("##") and "分卷" in ln:
                start = i
                break
        if start is None:
            return "", ""
        body = lines[start:]

        blocks: list[list[str]] = []
        cur: list[str] | None = None
        for ln in body:
            s = ln.strip()
            if s.startswith("###") and "卷" in s:
                if cur is not None:
                    blocks.append(cur)
                cur = [ln]
            elif cur is not None:
                cur.append(ln)
        if cur is not None:
            blocks.append(cur)
        if not blocks:
            return "", ""

        def _range(title: str):
            m = re.search(r"第\s*(\d+)\s*[-~]\s*(\d+)\s*章", title)
            if m:
                return int(m.group(1)), int(m.group(2))
            return None

        cur_idx = None
        for idx, blk in enumerate(blocks):
            r = _range(blk[0])
            if r and r[0] <= chapter_id <= r[1]:
                cur_idx = idx
                break
        if cur_idx is None:
            # 退化：按 volumes 顺序对应 blocks 顺序，取区间覆盖的块
            for idx, blk in enumerate(blocks):
                if idx < len(volumes or []):
                    v = volumes[idx]
                    vs, ve = int(v.get("start_chapter", 0)), int(v.get("end_chapter", 0))
                    if vs <= chapter_id <= ve:
                        cur_idx = idx
                        break
        if cur_idx is None:
            return "", ""
        cur_block = "\n".join(blocks[cur_idx]).strip()
        nxt_block = "\n".join(blocks[cur_idx + 1]).strip() if cur_idx + 1 < len(blocks) else ""
        return cur_block, nxt_block

    def _volume_hint(self, vol_block: str) -> str:
        """从某卷规划块抽取『走向提示』：仅保留标题 + 核心主题 + 情绪基调三行，
        丢弃『核心事件/成长线/扮猪吃虎/绝境/机缘/女性/冲突分层/情绪节奏配比』等
        易剧透的具体细节。用于中期/后期向 AI 透『下一卷走向』而不提前泄露真相。
        """
        keep: list[str] = []
        for ln in vol_block.splitlines():
            s = ln.strip()
            if s.startswith("###"):
                keep.append(s)
            elif s.startswith("**核心主题**") or s.startswith("**情绪基调**"):
                keep.append(s)
        return "\n".join(keep).strip() if keep else vol_block[:200].strip()

    def _phase_filter_master(self, master: str, phase: str, chapter_id: int, volumes: list[dict]) -> str:
        """按时期从总纲切出『前提段 + 当前卷规划段』，让本章忠于本时期设定。

        - early  （前期）：核心冲突前提段 + 当前卷【完整】规划段（不含下一卷）
        - mid    （中期）：前提段 + 当前卷完整段 + 下一卷【走向提示】（标题/主题/基调，
                            不泄露下一卷具体事件/真相/收尾，为转折铺垫但不剧透）
        - late   （后期）：同上（当前卷完整 + 下一卷走向提示），向终局汇聚
        - endgame（末期）：全量（收束卷无藏匿必要，对齐全量设定完成闭环）
        若切不到当前卷段则退化为前 40%，避免空注入。
        """
        if phase == "endgame":
            return master

        lines = master.splitlines()
        # 前提段只取「核心冲突」块（第一个 ## 段）。后续「故事结构/分卷规划」对各卷的
        # 概述含中后期核心事件，若作为前期前提注入即成剧透，故不纳入前提段——
        # 本卷该做什么由下方 _extract_volume_sections 切出的当前卷段精确提供。
        pre_end = len(lines)
        seen_first = False
        for i, ln in enumerate(lines):
            if ln.strip().startswith("##"):
                if not seen_first:
                    seen_first = True
                    continue
                pre_end = i
                break
        premise = "\n".join(lines[:pre_end]).strip()

        cur_vol, nxt_vol = self._extract_volume_sections(master, chapter_id, volumes)
        parts: list[str] = []
        if premise:
            parts.append(premise)
        if cur_vol:
            parts.append("\n\n" + cur_vol)
        # 中期/后期：仅透「下一卷走向提示」，不注入下一卷详细规划（避免提前暴露具体真相/收尾）
        if phase in ("mid", "late") and nxt_vol:
            parts.append("\n\n=== 下一卷走向提示（仅方向，不剧透细节） ===\n" + self._volume_hint(nxt_vol))
        out = "\n".join(parts).strip()
        if not out:
            out = master[: int(len(master) * 0.4)]
        return out

    def get_phase_context(self, chapter_id: int) -> dict:
        """供大纲生成路径使用：返回当前章的时期上下文。

        返回 {phase, phase_label, vol_label, current_volume_plan}，
        使 _generate_single_outline 能把『当前卷规划』与『时期·设定贴合』指令
        注入大纲 system 消息，做到前期大纲也严格忠于本卷设定。
        """
        vols: list[dict] = []
        try:
            from domains.novel.core.pipeline import volumes as vol_mod
            vols = vol_mod.get_volumes_or_default(self._project_root)
        except Exception:
            vols = []
        phase, _ = self._derive_phase(chapter_id, vols)
        phase_label = {"early": "前期", "mid": "中期", "late": "后期", "endgame": "末期"}.get(phase, "")
        vol_label = ""
        cur_plan = ""
        try:
            master_path = self._project_root / "outline" / "master.md"
            if master_path.exists():
                master = master_path.read_text(encoding="utf-8", errors="ignore")
                cur_block, _ = self._extract_volume_sections(master, chapter_id, vols)
                if cur_block:
                    vol_label = cur_block.splitlines()[0].lstrip("#").strip()
                    cur_plan = cur_block
        except Exception:
            pass
        return {
            "phase": phase,
            "phase_label": phase_label,
            "vol_label": vol_label,
            "current_volume_plan": cur_plan,
        }

    def rescan_spine_anchors(self) -> None:
        """手动重扫固定锚点（主角/核心设定/终局方向），供 UI 触发。

        - 主角、核心设定仅在为空时填充（不覆盖用户的手动编辑）
        - 终局方向强制从总纲重新拉取（overwrite=True），因为总纲更新后旧值可能过时
        """
        if self._spine is None:
            return
        st = self._spine.state
        if not st.protagonist:
            try:
                prot = next(
                    (c.name for c in self._archive.get_all_characters()
                     if "主角" in (c.role or "")),
                    "",
                )
                if prot:
                    self._spine.seed(protagonist=prot)
            except Exception as e:
                logger.warning(f"重扫主角失败: {e}")
        if not st.premise:
            try:
                meta_path = self._project_root / "project.json"
                if meta_path.exists():
                    import json
                    desc = json.loads(
                        meta_path.read_text(encoding="utf-8", errors="ignore")
                    ).get("description", "")
                    if desc:
                        self._spine.seed(premise=desc)
            except Exception:
                pass
        eg = self._extract_endgame()
        if eg:
            self._spine.seed(endgame=eg, overwrite=True)

    # ===== 写前检索 =====

    def retrieve_for_writing(
        self,
        chapter_id: int,
        writing_instruction: str,
        config: dict,
    ) -> MemoryRetrievalResult:
        """
        写前检索：根据写作指令从三层记忆中检索相关材料。

        流程：
        1. 档案库层：提取写作指令中的实体名，查询相关人物档案、活跃伏笔、近期时间线、相关世界观
        2. RAG层：用写作指令做语义检索，找到最相关的正文片段
        3. 摘要层：取最近 N 章的结构化摘要
        """
        result = MemoryRetrievalResult()

        # 1. 档案库层检索
        try:
            # 相关人物（窗口化：只注入与本章相关 + 近期在场者，封顶 character_inject_max，防长篇小说人物无限膨胀）
            result.characters = self._archive.search_characters_for_chapter(
                writing_instruction,
                current_chapter=chapter_id,
                window=int(config.get("character_window", 40)),
                limit=int(config.get("character_inject_max", 15)),
            )
            # 活跃伏笔（已过滤未回收；按优先级保留前 foreshadow_max 条，防长线作品伏笔无限膨胀）
            fs_max = int(config.get("foreshadow_max", 50))
            result.foreshadows = self._archive.get_active_foreshadows()[:fs_max] if fs_max > 0 else self._archive.get_active_foreshadows()
            # 近期时间线（最近5章）
            recent_start = max(1, chapter_id - 5)
            result.timeline = self._archive.get_timeline((recent_start, chapter_id - 1)) if chapter_id > 1 else []
            # 相关世界观（封顶：百万字后世界设定可达数百条，全量注入会随字数无限膨胀）
            ws_all = self._archive.get_world_settings()
            ws_max = int(config.get("world_settings_max", 40))
            result.world_settings = ws_all[:ws_max] if ws_max > 0 else ws_all
            # 配角轮换花名册（近窗口在场人物，供每章带一带配角戏份）
            cast_window = int(config.get("active_cast_window", 30))
            cast_max = int(config.get("active_cast_max", 8))
            result.active_cast = self._archive.get_active_cast(chapter_id, cast_window, cast_max)
        except Exception as e:
            logger.warning(f"档案库检索失败: {e}")

        # 2. RAG层检索（默认关闭：写作时不把已写正文散文再喂回模型，
        #    一致性由档案结构化事实 + 邻近大纲 + 上一章结尾精确文本承担）
        if config.get("rag_inject_into_writing", False):
            try:
                top_k = config.get("rag_top_k", 8)
                result.rag_chunks = self._rag.search(writing_instruction, top_k=top_k)
            except Exception as e:
                logger.warning(f"RAG检索失败: {e}")
        else:
            result.rag_chunks = []

        # 3. 摘要层检索
        try:
            recent_count = config.get("recent_summary_count", 5)
            result.summaries = self._summary.get_recent_summaries(chapter_id, recent_count)
        except Exception as e:
            logger.warning(f"摘要检索失败: {e}")

        # 4. 灵感碎片检索
        if self._inspiration:
            try:
                insp_top_k = config.get("inspiration_top_k", 5)
                result.inspirations = self._inspiration.search_for_writing(
                    writing_instruction, top_k=insp_top_k
                )
            except Exception as e:
                logger.warning(f"灵感碎片检索失败: {e}")

        # 5. 全书总纲 + 简介（概览）—— 章节写作必须遵循，之前遗漏导致偏离设定
        try:
            outline_path = self._project_root / "outline" / "master.md"
            if outline_path.exists():
                result.master_outline = outline_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"读取总纲失败: {e}")
        try:
            meta_path = self._project_root / "project.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                result.synopsis = meta.get("description", "")
        except Exception as e:
            logger.warning(f"读取简介失败: {e}")

        # 6. 卷结构 + 近期大纲（一致性增强：保持卷主题/节奏与钩子链）
        try:
            from domains.novel.core.pipeline import volumes as vol_mod
            vols = vol_mod.load_volumes(self._project_root)
            if vols:
                result.volumes = vols
                result.current_volume = vol_mod.get_volume_for_chapter(vols, chapter_id)
        except Exception as e:
            logger.warning(f"读取卷结构失败: {e}")

        # 6.5 当前卷规划段（干净块，含情绪节奏配比）—— 供写作提示词显式注入，
        # 让「本章须严格按本卷节奏（爽点/危机感/情感互动/世界观揭秘配比）执行」成为硬指令
        try:
            phase_ctx = self.get_phase_context(chapter_id)
            result.current_volume_plan = phase_ctx.get("current_volume_plan", "")
        except Exception:
            result.current_volume_plan = ""

        try:
            outlines_path = self._project_root / ".novelforge" / "chapter_outlines.json"
            if outlines_path.exists():
                with open(outlines_path, "r", encoding="utf-8") as f:
                    all_ol = json.load(f)
                if isinstance(all_ol, dict):
                    all_ol = all_ol.get("outlines", all_ol.get("volumes", []))
                before = [o for o in all_ol if o.get("chapter_id", 0) <= chapter_id]
                before.sort(key=lambda o: o.get("chapter_id", 0))
                result.recent_outlines = before[-6:]

                # 7. 邻近章节大纲（前后窗口内，防小范围吃书）：注入当前章前后各 W 章的完整大纲
                window = int(config.get("adjacent_outline_window", 5))
                lo = max(1, chapter_id - window)
                hi = chapter_id + window
                nearby = [
                    o for o in all_ol
                    if lo <= o.get("chapter_id", 0) <= hi and o.get("chapter_id", 0) != chapter_id
                ]
                nearby.sort(key=lambda o: o.get("chapter_id", 0))
                result.nearby_outlines = nearby
        except Exception as e:
            logger.warning(f"读取近期大纲失败: {e}")

        # 8. 主线脊柱账本（贯穿全书的主线骨架，紧凑且始终注入）
        if self._spine is not None and config.get("story_spine_enabled", True):
            try:
                # 惰性播种固定锚点：主角取档案中标注「主角」者，前提取简介首段
                st = self._spine.state
                if not st.protagonist:
                    prot = next(
                        (c.name for c in self._archive.get_all_characters()
                         if "主角" in (c.role or "")),
                        "",
                    )
                    if prot:
                        self._spine.seed(protagonist=prot)
                if not st.premise and result.synopsis:
                    self._spine.seed(premise=result.synopsis)
                # 终局方向：从总纲 master.md 扫描「终局/结局/最终目标」段落（零 LLM）
                if not st.endgame:
                    eg = self._extract_endgame()
                    if eg:
                        self._spine.seed(endgame=eg)
                # 下一步方向：取紧邻的下一章大纲核心事件
                next_dir = ""
                for o in (result.nearby_outlines or []):
                    if o.get("chapter_id", 0) == chapter_id + 1:
                        _ce = o.get("core_events")
                        if isinstance(_ce, list):
                            next_dir = "；".join(str(x) for x in _ce) if _ce else (o.get("plot_summary") or "")
                        else:
                            next_dir = _ce or o.get("plot_summary") or ""
                        break
                # 时期感知：先推导 phase，再决定 spine 是否暴露终局
                _phase, _ = self._derive_phase(chapter_id, result.volumes)
                _reveal = (_phase == "late")
                result.story_spine = self._spine.render(
                    next_direction=next_dir,
                    active_foreshadows="、".join(
                        [f"{fs.description} (第{fs.planted_chapter}章)"
                         for fs in (result.foreshadows or [])[:8]]
                    ) if result.foreshadows else "",
                    antagonist_info="、".join(
                        [f"{c.name}: {c.current_state or '活跃'}"
                         for c in (result.characters or [])
                         if c.role in ("反派", "敌人", "对手", "antagonist")][:4]
                    ) if result.characters else "",
                    female_arc=(
                        self._archive.get_female_arc_summary()
                        if getattr(self, "_archive", None) else ""
                    ),
                    protagonist_weakness=(self._spine.state.protagonist_weakness or ""),
                    reveal_endgame=_reveal,
                )
            except Exception as e:
                logger.warning(f"渲染主线脊柱失败: {e}")

        # 8.5 故事事件账本（本章应覆盖的计划事件，硬约束注入，治「分开生成做不好」）
        try:
            if getattr(self, "_archive", None):
                result.event_ledger = self._archive.format_due_events(chapter_id)
        except Exception as e:
            logger.warning(f"渲染事件账本失败: {e}")

        # 8.6 连续性状态卡（治本：时间线/事件状态模型）
        # 让 LLM 写第 N 章前直接看到「前一章结尾位置 / 已获物品 / 进行中 deadline」，
        # 不用猜、不靠全文，token 受控（~300-450 字）。零 LLM 抽取。
        try:
            from domains.novel.continuity_state import get_state_card
            card = get_state_card(str(self._project_root), chapter_id)
            if card:
                result.continuity_state = card
        except Exception as e:
            logger.warning(f"渲染连续性状态卡失败: {e}")

        # 8.7 结构化弧 / 暗线 状态卡（L2 弧归属 + 起承转合 + 本章暗线节点）
        # 让 LLM 写第 N 章前直接看到「本章属于哪个事件弧、什么阶段、今天要落哪个暗线节点」，
        # 不用猜、不靠全文，token 受控。零 LLM 抽取。
        try:
            from domains.novel.arc_subplot import get_arc_subplot_card
            card = get_arc_subplot_card(str(self._project_root), chapter_id)
            if card:
                result.arc_subplot = card
        except Exception as e:
            logger.warning(f"渲染弧/暗线状态卡失败: {e}")

        logger.info(f"写前检索完成: {len(result.characters)}人物, {len(result.foreshadows)}伏笔, "
                    f"{len(result.rag_chunks)}RAG片段, {len(result.summaries)}摘要, "
                    f"{len(result.inspirations)}灵感碎片")

        # 时期感知过滤：按当前章所处时期裁剪剧透内容（总纲/未来大纲/卷列表/spine终局）
        try:
            self._apply_phase_filter(result, chapter_id)
        except Exception as e:
            logger.warning(f"时期过滤失败（已降级为不过滤）: {e}")

        return result

    # ===== 写后更新 =====

    def commit_chapter(
        self,
        chapter_id: int,
        chapter_title: str,
        chapter_text: str,
        config: dict,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> CommitReport:
        """
        写后提交：更新三层记忆。

        流程：
        1. 从正文中提取结构化事实 → 更新档案库
        2. 生成章节结构化摘要 → 存入摘要层
        3. 正文分块 + embedding → 存入 RAG 层
        4. 生成 commit 记录
        5. 更新 state.json
        6. 自动备份

        每步独立容错：某步失败不影响其他步骤执行。
        """
        report = CommitReport(chapter_id=chapter_id, success=True)
        errors = []

        # 1. 事实提取 + 更新档案库（独立容错）
        if on_progress:
            on_progress("正在提取事实...")
        try:
            # 先清该章旧事实，保证幂等：覆盖/重写不会产生重复记录
            # （大纲阶段可能已写入「种子」事实，正文提交后以正文事实为权威覆盖之）
            try:
                self._archive.delete_chapter_facts(chapter_id)
            except Exception:
                pass
            facts = self._archive.extract_facts(chapter_id, chapter_text)
            report.fact_report = self._archive.apply_facts(chapter_id, facts)
            # 女性角色支线：从事实中检测女性角色出场并更新 female_arc
            try:
                self._update_female_arc_from_facts(chapter_id, facts)
            except Exception:
                pass
        except Exception as e:
            errors.append(f"事实提取失败: {e}")
            logger.error(f"第{chapter_id}章事实提取失败: {e}")

        # 2. 生成摘要（独立容错）
        if on_progress:
            on_progress("正在生成章节摘要...")
        try:
            summary = self._summary.generate_summary(chapter_id, chapter_title, chapter_text)
            report.summary_created = True
        except Exception as e:
            errors.append(f"摘要生成失败: {e}")
            logger.error(f"第{chapter_id}章摘要生成失败: {e}")
            # 创建基础摘要作为后备
            try:
                from domains.novel.core.memory.summary_layer import ChapterSummary
                from datetime import datetime
                summary = ChapterSummary(
                    chapter_id=chapter_id,
                    title=chapter_title,
                    one_line_summary=chapter_text[:50] + "...",
                    plot_progress="（摘要生成失败，使用截断正文）",
                    word_count=len(chapter_text),
                    created_at=datetime.now().isoformat(),
                )
                self._summary.save_summary(summary)
                report.summary_created = True
                logger.info(f"第{chapter_id}章使用后备摘要")
            except Exception as e2:
                errors.append(f"后备摘要也失败: {e2}")
                logger.error(f"第{chapter_id}章后备摘要失败: {e2}")

        # 2.5 更新主线脊柱账本（本地零 LLM 调用，复用本章摘要）
        if self._spine is not None and config.get("story_spine_enabled", True):
            try:
                self._spine._milestone_max = max(4, int(config.get("spine_milestone_max", 15)))
                self._spine.update_from_summary(chapter_id, summary)
            except Exception as e:
                logger.warning(f"第{chapter_id}章主线脊柱更新失败: {e}")

        # 2.6 推进故事事件账本（零 LLM：窗口覆盖本章的事件标 active，窗口闭合标 done）
        try:
            if getattr(self, "_archive", None):
                adv = self._archive.advance_events_for_chapter(chapter_id)
                if adv["activated"] or adv["completed"]:
                    logger.info(f"第{chapter_id}章事件账本推进: +{adv['activated']}进行中, +{adv['completed']}完成")
        except Exception as e:
            logger.warning(f"第{chapter_id}章事件账本推进失败: {e}")

        # 3. RAG入库（独立容错）
        if on_progress:
            on_progress("正在建立向量索引...")
        try:
            chunk_size = config.get("chunk_size", 500)
            chunk_overlap = config.get("chunk_overlap", 50)
            report.rag_chunks_added = self._rag.add_chapter_chunks(
                chapter_id, chapter_text, chunk_size=chunk_size, overlap=chunk_overlap
            )
        except Exception as e:
            errors.append(f"RAG入库失败: {e}")
            logger.error(f"第{chapter_id}章RAG入库失败: {e}")

        # 4. 生成 commit 记录
        if on_progress:
            on_progress("正在生成提交记录...")
        try:
            self._write_commit(chapter_id, chapter_title, report, summary)
        except Exception as e:
            errors.append(f"提交记录失败: {e}")
            logger.error(f"第{chapter_id}章提交记录失败: {e}")

        # 5. 更新 state.json
        try:
            self._update_state(chapter_id, chapter_title)
        except Exception as e:
            errors.append(f"状态更新失败: {e}")
            logger.error(f"第{chapter_id}章状态更新失败: {e}")

        # 6. 备份
        if on_progress:
            on_progress("正在备份...")
        try:
            self._backup_chapter(chapter_id, chapter_title, chapter_text)
            report.backup_created = True
        except Exception as e:
            errors.append(f"备份失败: {e}")
            logger.error(f"第{chapter_id}章备份失败: {e}")

        # 汇总结果
        if errors:
            report.error = "; ".join(errors)
            # 只有关键步骤（摘要+RAG）都失败才标记为失败
            if not report.summary_created and report.rag_chunks_added == 0:
                report.success = False
            else:
                report.success = True  # 部分成功
            logger.warning(f"第{chapter_id}章提交部分失败: {report.error}")
        else:
            logger.info(f"第{chapter_id}章提交完成: {report}")

        return report

    # ===== 查询接口 =====

    def reset_derived_memory(self) -> None:
        """清空三层派生记忆（档案库 / RAG / 摘要），用于全书重写前重置。

        仅清空由每章提交自动生成的派生事实，保留作者源文件：
        outline/master.md、settings/*、project.json、chapters/* 正文、灵感碎片。
        重写随章节逐章干净重建，避免百万字级重复提交导致记忆无限膨胀崩溃。
        各层独立容错：单层失败不影响其他层。
        """
        try:
            self._archive.clear_derived()
        except Exception as e:
            logger.error(f"重置档案派生记忆失败: {e}")
        try:
            self._rag.clear_all()
        except Exception as e:
            logger.error(f"重置 RAG 派生记忆失败: {e}")
        try:
            self._summary.clear_all()
        except Exception as e:
            logger.error(f"重置摘要派生记忆失败: {e}")
        # 清空主线脊柱动态记忆（保留固定锚点：主角/前提/终局，随重写逐章重建）
        if self._spine is not None:
            try:
                self._spine.clear(keep_anchors=True)
            except Exception as e:
                logger.error(f"重置主线脊柱失败: {e}")

    def query_character(self, name: str) -> Optional[CharacterProfile]:
        """按名称或别名查询人物档案。"""
        return self._archive.get_character(name)

    def query_foreshadows(self, status: Optional[str] = None) -> list[Foreshadow]:
        """查询伏笔。"""
        return self._archive.get_foreshadows(status)

    def query_timeline(self, chapter_range: Optional[tuple[int, int]] = None) -> list[TimelineEntry]:
        """查询时间线。"""
        return self._archive.get_timeline(chapter_range)

    # ===== 故事事件账本（前向规划） =====

    def get_story_events(self) -> list:
        """返回全部计划事件（StoryEvent 列表）。"""
        if getattr(self, "_archive", None):
            return self._archive.get_events()
        return []

    def plan_story_events(self, use_ai: bool = True) -> int:
        """规划并写入事件账本，返回事件条数。"""
        from domains.novel.core.pipeline.event_planner import plan_story_events
        evs = plan_story_events(
            str(self._project_root), self._archive,
            llm=getattr(self, "_llm", None), use_ai=use_ai,
        )
        return len(evs)

    def search_text(self, query: str, top_k: int = 5) -> list[dict]:
        """全文语义检索（RAG层）。"""
        return self._rag.search(query, top_k=top_k)

    def get_project_state(self) -> dict:
        """获取项目运行时状态。"""
        if self._state_path.exists():
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"current_chapter": 0, "chapters": [], "created_at": datetime.now().isoformat()}

    def get_project_tags(self) -> list:
        """读取项目内容标签（金手指/扮猪吃老虎/女主等）。

        返回原始 list（元素为 str 或 dict），由调用方用 tag_system.normalize_tag 规整。
        不再强制 str()——深度标签以 dict 形式携带 doctrine/anchors/rules。
        """
        meta_path = self._project_root / "project.json"
        if not meta_path.exists():
            return []
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return list(meta.get("tags", []) or [])
        except Exception:
            return []

    def get_project_process_tags(self) -> list:
        """读取项目流程约束标签（4相位/逐章顺序大纲/禁注入散文/事件账本等）。

        返回原始 list（元素为 str 或 dict），由调用方用 tag_system.normalize_tag 规整。
        """
        meta_path = self._project_root / "project.json"
        if not meta_path.exists():
            return []
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return list(meta.get("process_tags", []) or [])
        except Exception:
            return []

    def sync_project_tags(self) -> dict:
        """把项目深度标签落地为档案（「深度作用」的第二条通道）。

        - 每个标签（体系/线索/套路/流程/题材）的实质定义 upsert 为一条 world_setting
          （category=标签体系·<kind>），可被档案/RAG 检索召回，写作时模型能"想起"该体系。
        - 线索类（kind=线索）的每个贯穿锚点 upsert 为一条 foreshadow（确定性 id 幂等），
          由伏笔系统逐章提醒，真正"贯穿全文"。
        返回 {world_settings: 写入数, foreshadows: 写入数}。
        """
        if not getattr(self, "_archive", None):
            return {"world_settings": 0, "foreshadows": 0}
        try:
            from domains.novel.core.pipeline.tag_system import sync_to_archive_specs
            content = self.get_project_tags()
            process = self.get_project_process_tags()
            specs = sync_to_archive_specs(content, process)

            ws_count = 0
            for ws in specs["world_settings"]:
                existing = self._archive._find_world_setting(ws["name"], ws["category"])
                from domains.novel.core.memory.archive_layer import WorldSetting
                obj = WorldSetting(
                    id=existing.id if existing else "",
                    category=ws["category"],
                    name=ws["name"],
                    description=ws["description"],
                    related_entries=[],
                )
                self._archive.save_world_setting(obj)
                ws_count += 1

            fs_count = 0
            for fs in specs["foreshadows"]:
                from domains.novel.core.memory.archive_layer import Foreshadow
                obj = Foreshadow(
                    id=fs["id"],
                    description=fs["description"],
                    planted_chapter=fs["planted_chapter"],
                    status=fs["status"],
                    related_characters=fs["related_characters"],
                    priority=fs["priority"],
                    notes=fs["notes"],
                )
                self._archive.save_foreshadow(obj)
                fs_count += 1

            logger.info(f"标签同步到档案：world_settings={ws_count}, foreshadows={fs_count}")
            return {"world_settings": ws_count, "foreshadows": fs_count}
        except Exception as e:
            logger.warning(f"标签同步到档案失败（忽略）: {e}")
            return {"world_settings": 0, "foreshadows": 0}

    # ===== 内部方法 =====

    def _update_female_arc_from_facts(self, chapter_id: int, facts: dict) -> None:
        """从提交的事实中检测女性角色出场并更新 female_arc 表（零 LLM）。

        扫描 facts["characters"] 中 role 含「女」或 name 在已知女性角色列表中的记录，
        用其 state_change 或 new_info 拼接 arc_summary 并 upsert。
        """
        try:
            # 收集已知女性角色名
            known_females: set[str] = set()
            for c in self._archive.get_all_characters():
                if "女" in (c.role or ""):
                    if c.name:
                        known_females.add(c.name)
                    for a in (c.aliases or []):
                        if a:
                            known_females.add(a)

            if not known_females:
                return

            updated = 0
            for char_data in (facts.get("characters") or []):
                if not isinstance(char_data, dict):
                    continue
                name = char_data.get("name", "").strip()
                role = char_data.get("role", "")
                # 匹配：角色名在已知女性名单中，或 role 含「女」
                if name and (name in known_females or "女" in role):
                    state = char_data.get("state_change") or {}
                    after = state.get("after", "")
                    new_info = char_data.get("new_info") or {}
                    desc = new_info.get("description", "")
                    arc_summary = f"{after}。{desc}"[:200] if (after or desc) else f"第{chapter_id}章出场/互动"
                    self._archive.upsert_female_arc(
                        female_name=name,
                        arc_summary=arc_summary,
                        chapter_id=chapter_id,
                    )
                    updated += 1
            if updated:
                logger.info(f"第{chapter_id}章 commit 已更新 {updated} 条 female_arc")
        except Exception as e:
            logger.warning(f"第{chapter_id}章 female_arc 更新失败: {e}")

    def _write_commit(self, chapter_id: int, title: str, report: CommitReport, summary: ChapterSummary):
        """写入 commit 记录。"""
        commit = {
            "chapter_id": chapter_id,
            "title": title,
            "timestamp": datetime.now().isoformat(),
            "fact_report": report.fact_report,
            "summary": summary.to_dict(),
            "rag_chunks_added": report.rag_chunks_added,
        }
        path = self._commits_dir / f"chapter_{chapter_id:04d}.commit.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(commit, f, ensure_ascii=False, indent=2)

    def _update_state(self, chapter_id: int, title: str):
        """更新运行时状态。"""
        state = self.get_project_state()
        state["current_chapter"] = max(state.get("current_chapter", 0), chapter_id)
        state["updated_at"] = datetime.now().isoformat()

        chapters = state.get("chapters", [])
        # 移除同章节旧记录
        chapters = [c for c in chapters if c.get("chapter_id") != chapter_id]
        chapters.append({"chapter_id": chapter_id, "title": title, "committed_at": datetime.now().isoformat()})
        chapters.sort(key=lambda c: c["chapter_id"])
        state["chapters"] = chapters

        # 活跃伏笔数
        active_fs = self._archive.get_active_foreshadows()
        state["active_foreshadows"] = len(active_fs)

        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _backup_chapter(self, chapter_id: int, title: str, text: str):
        """章节级备份。"""
        backup = {
            "chapter_id": chapter_id,
            "title": title,
            "text": text,
            "backup_at": datetime.now().isoformat(),
        }
        path = self._backups_dir / f"chapter_{chapter_id:04d}.bak.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)

    def close(self):
        self._archive.close()
        self._rag.close()
