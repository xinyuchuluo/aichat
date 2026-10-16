"""讨论功能：构建 agent 读取的小说上下文（精准正文读取）。

设计目标（用户要求"最大化模仿 agent"）：
- 精准：①注入「当前章节」完整正文（不再只靠 RAG 240 字片段）；
        ②解析用户指令里的「第X章 / 第X-Y章 / 关于XX的章」并直接读取对应正文；
        ③RAG 不可用时退回「关键词遍历检索」兜底，绝不静默丢正文。
- 去截断：RAG / 关键词命中放宽到段落级（数百字），不再硬截断 240 字。
- 高效：结构化记忆仍只取摘要；范围/当前章按需读取，限制总量防爆 token。
"""
from __future__ import annotations

import json
import os
import re
import glob
from typing import Optional

import jieba

from domains.novel.config import Config
from domains.novel.core.memory.engine import MemoryEngine
from domains.novel.core.discussion.power_canon import power_canon_block

# ---- 常量（控制 token 用量）----
_MAX_CHARS_CURRENT = 3500      # 当前章注入上限（绝大多数章不超）
_MAX_CHARS_RANGE = 1500        # 范围章每章上限
_MAX_CHARS_RAG = 700           # RAG 片段上限（原 240）
_MAX_CHARS_FALLBACK = 450      # 兜底命中段落上下文
_MAX_RANGE_CHAPTERS = 12       # 范围章最多注入数
_MAX_RAG_HITS = 10
_MAX_FALLBACK_HITS = 12
_MAX_CHARS_SCAN = 6000           # 全本遍历检索注入上限

_SCAN_TRIGGERS = [
    "全本", "整本", "所有章节", "全部章节", "全书", "每一章", "各章",
    "全书中", "全本里", "整个小说", "全部正文", "全篇",
]
_SCAN_CONSISTENCY = ["前后一致", "是否一致", "一致性", "核对", "逐章核对"]

_STOPWORDS = set(
    "的 了 是 在 我 你 他 她 它 们 这 那 有 和 与 对 到 也 就 都 还 把 被 给 让 "
    "从 向 于 以 为 之 其 此 个 些 上 下 中 里 外 前 后 内 间 会 能 要 想 说 看 "
    "做 用 去 来 出 入 过 又 再 很 太 更 最 不 没 别 请 吧 吗 呢 啊 呀 哦 嗯 哪 "
    "谁 何 怎 什 时 地 因 果 虽 但 而 若 如 故 即 且 并 或 将 已 正 刚 才 自 相 "
    "各 每 任 某 该 等 着 自己 帮 我 你 他 她 它 们 一 不 个 种 就 都 是 的 "
    # 提问性/指代性冗余（避免整句或泛指词进检索关键词）
    "分析 请问 看看 觉得 感觉 如何 怎样 怎么样 帮忙 解释 介绍 讲讲 讨论 "
    "希望 可以 需要 想要 知道 认为 能不能 可能 应该 关于 主角 角色 这个 这些 "
    "那些 一个 什么 为什么 怎么 什么 到底 究竟 大概 应该 最好 怎么 呢".split()
)

_CHAPTER_RE_RANGE = re.compile(r"第\s*(\d{1,3})\s*[-~～—–到至]\s*(\d{1,3})\s*章")
_CHAPTER_RE_SINGLE = re.compile(r"第\s*(\d{1,3})\s*章")
_ABOUT_RE = re.compile(
    r"(?:关于|提到|有关|涉及|讲[到述]?|围绕)\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9_]{1,12}?)\s*"
    r"(?:的?\s*(?:章|章节|内容|情节|相关|部分))"
)


def _chapter_path(project_root: str, cid: int) -> str:
    return os.path.join(project_root, "chapters", f"chapter_{cid:04d}.md")


def _load_outlines(project_root: str) -> list[dict]:
    """读取项目的章节大纲（.novelforge/chapter_outlines.json）。"""
    p = os.path.join(project_root, ".novelforge", "chapter_outlines.json")
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _load_json(project_root: str, name: str) -> object:
    """读取 .novelforge 下任意 JSON 设定文件，失败返回 None（防御性）。"""
    p = os.path.join(project_root, ".novelforge", name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _settings_text(project_root: str, max_chars: int = 5200) -> str:
    """组装「小说设定层」文本（L0–L2 规划骨架）——这是主角成长/主线/体系的权威来源，
    之前讨论 agent 完全没读，导致问『成长路线』只能从正文拼、答得飘。

    包含：故事脊柱(story_spine) · 主线弧(arcs) · 跨卷暗线(subplots) ·
          卷纲(volumes) · 修炼体系规格(structure_spec.md 关键段)。
    全部确定性注入（结构化 JSON，不走 LLM 抽），token 受 max_chars 控制。

    返回带小标题的分块文本；无项目/无文件返回空串。
    """
    if not project_root:
        return ""
    blocks: list[str] = []

    # 1) 故事脊柱（主角成长路线顶层设计，小文件全注入）
    sp = _load_json(project_root, "story_spine.json")
    if isinstance(sp, dict):
        lines: list[str] = []
        for k in ("protagonist", "premise", "protagonist_state", "protagonist_weakness",
                  "current_stage", "female_arc", "endgame"):
            v = sp.get(k)
            if isinstance(v, str) and v.strip():
                lines.append(f"- {k}：{v.strip()}")
            elif isinstance(v, (int, float)):
                lines.append(f"- {k}：{v}")
        ms = sp.get("milestones")
        if isinstance(ms, list) and ms:
            ml = "；".join(
                f"第{m.get('chapter_id')}章→{m.get('beat','')}"
                for m in ms if isinstance(m, dict) and m.get("chapter_id") is not None
            )
            if ml:
                lines.append(f"- 成长里程碑（卷末高潮）：{ml}")
        if lines:
            blocks.append("【故事脊柱 story_spine · 主角成长路线顶层设计】\n" + "\n".join(lines))

    # 2) 主线弧 arcs.json
    arcs = _load_json(project_root, "arcs.json")
    if isinstance(arcs, list) and arcs:
        lines = []
        for a in arcs:
            if not isinstance(a, dict):
                continue
            name = a.get("name") or a.get("id") or "?"
            summ = (a.get("summary") or "").strip().replace("\n", " ")
            typ = a.get("type") or ""
            vol = a.get("volume")
            vtag = f"（卷{vol}）" if vol else ""
            line = f"- [{typ}{vtag}] {name}"
            if summ:
                line += f"：{summ[:120]}"
            lines.append(line)
        if lines:
            blocks.append("【主线弧 arcs · 卷级主线故事】\n" + "\n".join(lines))
    elif isinstance(arcs, dict):
        # 兼容 {arcs:[...]} 包裹
        inner = arcs.get("arcs")
        if isinstance(inner, list) and inner:
            blocks.append("【主线弧 arcs · 卷级主线故事】\n" + "\n".join(
                f"- {a.get('name') or a.get('id')}：{(a.get('summary') or '')[:120]}"
                for a in inner if isinstance(a, dict)
            ))

    # 3) 跨卷暗线 subplots.json（含楚渊/混沌神体觉醒等）
    subs = _load_json(project_root, "subplots.json")
    if isinstance(subs, dict):
        subs = subs.get("subplots") or []
    if isinstance(subs, list) and subs:
        lines = []
        for s in subs:
            if not isinstance(s, dict):
                continue
            title = s.get("title") or s.get("id") or "?"
            kw = s.get("keyword") or ""
            spans = s.get("spans_volumes") or s.get("volumes")
            st = s.get("status") or ""
            tag = f"关键词『{kw}』" if kw else ""
            if spans:
                tag += f" · 跨卷{spans}" if tag else f"跨卷{spans}"
            if st:
                tag += f" · {st}"
            nodes = s.get("nodes") or []
            ninfo = ""
            if isinstance(nodes, list) and nodes:
                ninfo = "；节点：" + "，".join(
                    f"第{n.get('chapter')}章·{n.get('stage','')}"
                    for n in nodes[:8] if isinstance(n, dict) and n.get("chapter")
                )
            lines.append(f"- {title}（{tag}）{ninfo}")
        if lines:
            blocks.append("【跨卷暗线 subplots · 依附主线的隐性线索】\n" + "\n".join(lines))

    # 4) 卷纲 volumes.json（每卷 core_goal / 角色弧 / 转折点）
    vols = _load_json(project_root, "volumes.json")
    if isinstance(vols, dict):
        vols = vols.get("volumes") or []
    if isinstance(vols, list) and vols:
        lines = []
        for v in vols:
            if not isinstance(v, dict):
                continue
            vn = v.get("volume") or v.get("id") or "?"
            title = v.get("title") or v.get("name") or ""
            goal = (v.get("core_goal") or v.get("summary") or "").strip().replace("\n", " ")
            arc = (v.get("character_arc") or "").strip().replace("\n", " ")
            tps = v.get("key_turning_points") or v.get("turning_points") or []
            tp_txt = ""
            if isinstance(tps, list) and tps:
                tp_txt = "；转折：" + "，".join(str(t) for t in tps[:3])
            line = f"- 卷{vn} {title}".strip()
            if goal:
                line += f"：目标→{goal[:80]}"
            if arc:
                line += f"；角色弧→{arc[:80]}"
            line += tp_txt
            lines.append(line)
        if lines:
            blocks.append("【卷纲 volumes · 每卷目标/角色弧/转折点】\n" + "\n".join(lines))

    # 5) 修炼体系规格 structure_spec.md（抽关键段：境界/体系定义）
    spec_p = os.path.join(project_root, ".novelforge", "structure_spec.md")
    if os.path.isfile(spec_p):
        try:
            spec = open(spec_p, encoding="utf-8").read()
            # 只截取含「境界 / 体系 / 等级 / 修炼」等关键段，避免整篇灌入
            kept = []
            for para in re.split(r"\n\s*\n", spec):
                if re.search(r"(境界|体系|等级|修炼|功法|分层|突破|设定|规格|L0|L1|L2|维度)", para):
                    kept.append(para.strip())
            spec_txt = "\n\n".join(kept)[:1400]
            if spec_txt.strip():
                blocks.append("【修炼体系规格 structure_spec · 境界/体系定义】\n" + spec_txt)
        except Exception:
            pass

    # 6) 档案库 world_settings（本书真正落地的体系/线索/背景设定）
    #    这是「设定先出、正文后写」的权威来源；讨论 agent 必须优先据此回答。
    #    注入优先级：体系/线索/题材/套路 优先（核心设定骨架），其他/规则类次要设定降级截断。
    #    代码层强制（非提示词，泛化守卫）：每条设定注入前跑 world_settings_integrity
    #    单一定义校验——任意类别（不止力量体系）若把同一维度写成「两套/须明确区分」等
    #    分裂表述，注入时显式标红，从源头杜绝甩矛盾设定误导 agent。不再对"力量体系"
    #    做特殊压制/摘要分支（那是窄守卫时代的权宜），所有设定统一走"原文截断 + 通用校验"。
    try:
        import sqlite3 as _sqlite
        db_p = os.path.join(project_root, ".novelforge", "archive.db")
        if os.path.isfile(db_p):
            conn = _sqlite.connect(db_p)
            rows = conn.execute(
                "SELECT id, category, name, description FROM world_settings ORDER BY category, name"
            ).fetchall()
            # 设定版本戳：在 conn 关闭前读取当前版本号，让读者/agent 知道结论基于哪版设定
            _rev_row = None
            try:
                _rev_row = conn.execute(
                    "SELECT revision FROM settings_revision WHERE id=1"
                ).fetchone()
            except Exception:
                pass
            conn.close()
            from domains.novel.core.discussion.world_settings_integrity import (
                validate_world_setting,
            )
            _PRIORITY = {"体系": 0, "线索": 1, "题材": 2, "套路": 3}
            def _rank(cat: str) -> int:
                for k, v in _PRIORITY.items():
                    if str(cat).startswith(k):
                        return v
                return 9  # 其他/规则等降级
            rows_sorted = sorted(rows, key=lambda r: (_rank(r[0]), r[1]))
            ws_lines: list[str] = []
            has_power = False
            for _id, cat, name, desc in rows_sorted:
                raw = (desc or "").strip()
                if name and raw:
                    # 力量体系单真源锚定：探测是否存在力量/境界/阶梯类条目
                    if (str(cat).startswith("体系") or "阶梯" in (name or "")
                            or "境界" in (name or "") or "力量" in (name or "")
                            or "觉醒" in raw or "阶梯" in raw or "境界" in raw):
                        has_power = True
                    # 统一：原文截断注入（体系类给足 2000 字，其余 240 字），不再特殊压制
                    cap = 2000 if str(cat).startswith("体系") else 240
                    line = f"- [{cat}] {name}：{raw.replace(chr(10), ' ')[:cap]}"
                    # 通用单一定义校验：任意类别命中分裂表述则标红（泛化，不止力量体系）
                    _issues = validate_world_setting(raw)
                    if _issues:
                        line += "\n[⚠ 设定守卫] 该设定违反单一真源不变量（任意维度只应有一套定义）：" + "；".join(_issues)
                    ws_lines.append(line)
            if ws_lines:
                blocks.append("【设定库 world_settings · 本书体系/线索/背景（权威·优先引用）】\n"
                              + "\n".join(ws_lines))
            if _rev_row:
                _ver_blk = (
                    f"【设定版本】当前 world_settings 设定版本 = v{_rev_row[0]}。"
                    f"本书所有设定结论均基于该版本；若对话历史中出现更早版本的设定表述"
                    f"（如旧『双轨战力』『觉醒度%单独成体系』等），视为已失效，请以本版本为准。"
                )
                # 版本戳放最前，确保控长截断时仍优先注入（不被后面大块设定挤掉）
                blocks.insert(0, _ver_blk)
            # 力量体系唯一真源锚定：若库中含力量/境界设定，把 canonical 单轨对象
            # 作为权威块插在版本戳之后（索引1），让 LLM 第一眼读到的是单轨结构化真源，
            # 物理上读不到"两套体系"的歧义表述（结构性保证，非提示词/守卫）。
            if has_power:
                blocks.insert(1, power_canon_block(project_root))
    except Exception:
        pass

    # 合并并控长
    out: list[str] = []
    total = 0
    for b in blocks:
        if total + len(b) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                out.append(b[:remaining] + "\n…（设定层过长截断）")
            break
        out.append(b)
        total += len(b)
    return "\n\n".join(out)


def _outline_map_text(outlines: list[dict], max_chars: int = 2600) -> str:
    """生成『章节地图』：第X章【类型】标题：核心事件首句（供 AI 语义定位精读章）。

    大纲是小说规划的骨架（章节类型/核心事件/伏笔/节奏），让检索从
    「字面关键词」升级为「按规划意图定位」。
    """
    lines: list[str] = []
    for o in sorted(outlines, key=lambda x: x.get("chapter_id") or 0):
        cid = o.get("chapter_id")
        if cid is None:
            continue
        title = o.get("title") or ""
        typ = o.get("chapter_type_label") or o.get("chapter_type") or ""
        ev = (o.get("core_events") or o.get("plot_summary") or "").strip().replace("\n", " ")
        if len(ev) > 60:
            ev = ev[:60] + "…"
        lines.append(f"第{cid}章【{typ}】{title}：{ev}")
    out: list[str] = []
    total = 0
    for ln in lines:
        if total + len(ln) > max_chars:
            out.append("…（章节地图过长，其余省略）")
            break
        out.append(ln)
        total += len(ln)
    return "\n".join(out)


def _outline_full_text(project_root: str, max_chars: int = 6000) -> str:
    """生成『简大纲 + 深大纲』完整文本——规划/梳理类问题的权威依据。

    一条 chapter_outlines 记录已包含：
      - 简大纲层：core_events(核心事件) / plot_summary(情节摘要) / chapter_type_label(类型) /
                  title / foreshadowing(伏笔)
      - 深大纲层：state_deltas(人物状态变化) / continuity_anchors(承上启下) /
                  writing_focus(描写重点) / narrative_shots(镜头调度) /
                  beat_sequence(段落顺序) / event_progress(节拍) / body_snippet(首尾钩子)

    这些信息密度已足够回答『成长路线/主线/伏笔规划』等规划类问题，
    无需扫正文（正文只在一致性核查/改文时才碰）。
    """
    outs = _load_outlines(project_root)
    if not outs:
        return ""
    blocks: list[str] = []
    total = 0
    for o in sorted(outs, key=lambda x: x.get("chapter_id") or 0):
        cid = o.get("chapter_id")
        if cid is None:
            continue
        title = o.get("title") or ""
        typ = o.get("chapter_type_label") or o.get("chapter_type") or ""
        core = (o.get("core_events") or "").strip().replace("\n", " ")
        plot = (o.get("plot_summary") or "").strip().replace("\n", " ")
        fo_raw = o.get("foreshadowing") or ""
        fo = fo_raw if isinstance(fo_raw, str) else "；".join(str(x) for x in fo_raw[:6])
        fo = fo.strip().replace("\n", " ")
        sd = o.get("state_deltas")
        sd_txt = ""
        if isinstance(sd, str) and sd.strip():
            sd_txt = sd.strip().replace("\n", " ")
        elif isinstance(sd, list) and sd:
            sd_txt = "；".join(str(x) for x in sd[:6])
        anch_raw = o.get("continuity_anchors") or ""
        if isinstance(anch_raw, dict):
            anch_raw = "；".join(str(v) for v in anch_raw.values() if v)
        anchors = anch_raw.strip().replace("\n", " ") if isinstance(anch_raw, str) else ""
        focus = (o.get("writing_focus") or "").strip().replace("\n", " ")
        shots = (o.get("narrative_shots") or "").strip().replace("\n", " ")
        beats = (o.get("beat_sequence") or "").strip().replace("\n", " ")

        parts_c = [f"第{cid}章【{typ}】《{title}》"]
        if core:
            parts_c.append(f"  核心事件：{core[:200]}")
        if foreshort := fo[:120] if fo else "":
            parts_c.append(f"  伏笔：{foreshort}")
        if sd_txt:
            parts_c.append(f"  状态变化：{sd_txt[:160]}")
        if anchors:
            parts_c.append(f"  承上启下：{anchors[:120]}")
        if focus:
            parts_c.append(f"  描写重点：{focus[:100]}")
        if shots:
            parts_c.append(f"  镜头调度：{shots[:200]}")
        if beats:
            parts_c.append(f"  段落顺序：{beats[:200]}")
        block = "\n".join(parts_c)
        if total + len(block) > max_chars:
            blocks.append("…（大纲过长，其余省略）")
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)


def _read_chapter(project_root: str, cid: int, max_chars: int) -> Optional[str]:
    """读取某章 .md 正文，超长则截断并标注。"""
    p = _chapter_path(project_root, cid)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            txt = f.read().strip()
    except Exception:
        return None
    if not txt:
        return None
    if len(txt) > max_chars:
        return txt[:max_chars] + f"\n…（正文较长，已截断至前 {max_chars} 字，如需更多请明确指定章节）"
    return txt


def _parse_chapter_range(msg: str):
    """返回 (explicit_ranges: list[(a,b)], about_kw: str|None)。"""
    explicit: list[tuple[int, int]] = []
    for m in _CHAPTER_RE_RANGE.finditer(msg):
        a, b = int(m.group(1)), int(m.group(2))
        if a <= b:
            explicit.append((a, b))
    for m in _CHAPTER_RE_SINGLE.finditer(msg):
        n = int(m.group(1))
        # 避免与已解析的 range 起始重复
        if not any(a <= n <= b for a, b in explicit):
            explicit.append((n, n))
    about = None
    m = _ABOUT_RE.search(msg)
    if m:
        about = m.group(1).strip()
    return explicit, about


def _extract_keywords(msg: str) -> list[str]:
    """从消息提取中文/英文关键词（jieba 分词 + 去停用词 + 去重保序）。

    之前用正则按连续中文块提取，会把「帮我分析主角的养成路线」整句当关键词，
    正文里绝无字面匹配 → 检索恒 0 命中 → agent 无证据硬答。jieba 分词后
    「分析 / 主角 / 的 / 养成 / 路线」被拆分，过滤提问性停用词后留下实体词。
    """
    try:
        tokens = [t.strip() for t in jieba.lcut(msg) if t.strip()]
    except Exception:
        tokens = re.findall(r"[\u4e00-\u9fa5]{2,}|[A-Za-z0-9_]{2,}", msg)
    kws = [
        t for t in tokens
        if len(t) >= 2 and t not in _STOPWORDS and not re.fullmatch(r"[\W_]+", t)
    ]
    seen: set[str] = set()
    out: list[str] = []
    for t in kws:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:12]


# 强抽象体系词：命中即触发全本扫描（养成路线 / 修炼体系 / 势力格局这类问题需跨章证据）
_SCAN_TOPIC_TRIGGERS = [
    "养成", "修炼", "升级", "变强", "体系", "路线", "感情", "恋爱",
    "感情线", "势力", "伏笔", "线索",
]

# 抽象主题词 → 正文高频实体词（解决"养成路线"这类概括词在正文里无字面匹配的问题）
_TOPIC_EXPANSION: dict[str, list[str]] = {
    # —— 修炼 / 实力 / 养成 ——
    "养成": ["修为", "境界", "突破", "修炼", "实力", "战力", "功法", "灵气", "晋升", "炼体"],
    "修炼": ["修为", "境界", "突破", "功法", "灵气", "炼体", "灵力", "晋升"],
    "升级": ["突破", "晋升", "修为", "境界", "实力"],
    "变强": ["突破", "修为", "晋升", "实力", "境界"],
    "实力": ["修为", "战力", "境界", "突破", "功法"],
    "修为": ["境界", "突破", "灵气", "修炼"],
    "突破": ["境界", "修为", "晋升"],
    "体系": ["境界", "等级", "功法", "修为", "实力"],
    "路线": ["修为", "境界", "功法", "修炼", "突破"],
    "战力": ["实力", "修为", "境界", "战斗"],
    "功法": ["功法", "神通", "武技", "绝学"],
    # —— 感情 ——
    "感情": ["喜欢", "爱慕", "心动", "情愫", "婚约", "成亲", "红颜"],
    "恋爱": ["喜欢", "爱慕", "心动", "情愫", "婚约", "成亲"],
    "感情线": ["喜欢", "爱慕", "心动", "情愫", "婚约", "成亲"],
    # —— 势力 ——
    "势力": ["宗门", "家族", "门派", "王朝", "世家"],
    "宗门": ["门派", "长老", "弟子", "掌门"],
    # —— 剧情 / 线索 ——
    "线索": ["伏笔", "线索", "疑点", "真相"],
    "伏笔": ["伏笔", "铺垫", "暗示", "悬念"],
}


def _expand_keywords(keywords: list[str]) -> list[str]:
    """抽象主题词 → 正文高频词扩展（保留原词、去重保序）。"""
    out: list[str] = []
    seen: set[str] = set()
    for k in keywords:
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
        for ext in _TOPIC_EXPANSION.get(k, []):
            if ext not in seen:
                seen.add(ext)
                out.append(ext)
    return out


def _keyword_fallback(project_root: str, keywords: list[str], max_hits: int, ctx: int) -> list[str]:
    """遍历所有章节 .md，按关键词命中段落，返回片段文本列表（RAG 不可用兜底）。"""
    if not keywords or not project_root:
        return []
    ch_dir = os.path.join(project_root, "chapters")
    if not os.path.isdir(ch_dir):
        return []
    files = sorted(glob.glob(os.path.join(ch_dir, "chapter_*.md")))
    hits: list[str] = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        m = re.search(r"chapter_(\d+)\.md", fp)
        cid = int(m.group(1)) if m else 0
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        for p in paras:
            if any(kw in p for kw in keywords):
                snippet = p[:ctx]
                if len(p) > ctx:
                    snippet += "…"
                hits.append(f"[第{cid}章] {snippet}")
                if len(hits) >= max_hits:
                    return hits
    return hits


def _clean_kw(k: str) -> str:
    """清洗扫描关键词：去引号、去前后冗余词（的段落 / 关于 / 前后一致 …）。"""
    k = k.strip().strip("'\"“「『”」』")
    # 去前缀冗余（关于 / 检查 / 一下 …）
    k = re.sub(r"^(一下|检查|核对|关于|提到|涉及|有关|所有关于|所有提到)", "", k)
    # 去尾缀冗余（的段落 / 前后一致 / 是否 …）
    k = re.sub(
        r"(是否前后一致|前后一致|是否一致|一致性|的段落|的章节|相关|段落|章节|内容|情节|的设定|设定|是否|核对)$",
        "", k,
    )
    return k


def _parse_scan_intent(msg: str):
    """解析「全本遍历」意图：是否要扫全本 + 提取检索关键词。

    返回 (scan_all: bool, keywords: list[str])。
    触发：① 全本类词（全本 / 整本 / 所有章节 / 全书 …）；
        ② 一致性类词（前后一致 / 是否一致 / 一致性 / 核对 … 隐含要遍历全本）。
    关键词优先取引号内（中英文 / 单双引号）；否则取「关于X / 提到X / 检查X」的 X。
    """
    if not msg:
        return False, []
    scan_all = any(t in msg for t in _SCAN_TRIGGERS) or any(c in msg for c in _SCAN_CONSISTENCY)
    if not scan_all:
        return False, []
    kw: list[str] = []
    # 引号内优先（支持中英文 / 单双引号）
    q = re.findall(r"['\"“「『]([^'\"”」』]{1,20})['\"”」』]", msg)
    if q:
        kw = [_clean_kw(x) for x in q]
    else:
        m = re.search(
            r"(?:关于|提到|涉及|所有关于|所有提到|有关|检查|核对)\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})", msg
        )
        if m:
            kw.append(_clean_kw(m.group(1)))
    kw = [k for k in kw if k and k not in _STOPWORDS]
    return True, kw


def _scan_all_chapters_compact(
    project_root: str,
    keywords: Optional[list] = None,
    max_hits_per_chapter: int = 6,
    max_chars: int = 3000,
) -> str:
    """紧凑版全本检索：每章只保留命中句（去重、单句截断），供 search_all 工具快速定位关键章。

    返回格式：「第X章：句1 | 句2 …」（便于 agent 决定精读哪几章，而不是淹没在长段落里）。
    """
    ch_dir = os.path.join(project_root, "chapters")
    if not os.path.isdir(ch_dir):
        return ""
    files = sorted(
        glob.glob(os.path.join(ch_dir, "chapter_*.md")),
        key=lambda p: int(re.search(r"chapter_(\d+)\.md", p).group(1)),
    )
    kw = [k.strip() for k in (keywords or []) if k.strip()]
    out: list[str] = []
    total = 0
    for fp in files:
        m = re.search(r"chapter_(\d+)\.md", fp)
        cid = int(m.group(1)) if m else 0
        try:
            text = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        if not text:
            continue
        # 按句切分（中文句号/叹问号/分号），命中句去重保序
        sentences = [s.strip() for s in re.split(r"[。！？；\n]", text) if s.strip()]
        hit_sents: list[str] = []
        seen_s: set[str] = set()
        for s in sentences:
            if any(k in s for k in kw):
                s = s[:80] + ("…" if len(s) > 80 else "")
                if s not in seen_s:
                    seen_s.add(s)
                    hit_sents.append(s)
            if len(hit_sents) >= max_hits_per_chapter:
                break
        if not hit_sents:
            continue
        block = f"第{cid}章：{' | '.join(hit_sents)}"
        if total + len(block) > max_chars:
            out.append("…（已达检索字数上限）")
            break
        out.append(block)
        total += len(block)
    return "\n".join(out)


def _scan_all_chapters_with_stats(
    project_root: str,
    keywords: Optional[list] = None,
    max_hits_per_chapter: int = 6,
    max_chars: int = 3000,
):
    """全本检索（紧凑命中句）并统计每章命中数。

    返回 (compact_text: str, hits: dict[int, int])——hits 供三阶段流水线的
    「阶段2」挑选需要精读的关键章（命中最多者优先）。
    """
    ch_dir = os.path.join(project_root, "chapters")
    if not os.path.isdir(ch_dir):
        return "", {}
    files = sorted(
        glob.glob(os.path.join(ch_dir, "chapter_*.md")),
        key=lambda p: int(re.search(r"chapter_(\d+)\.md", p).group(1)),
    )
    kw = [k.strip() for k in (keywords or []) if k.strip()]
    out: list[str] = []
    hits: dict[int, int] = {}
    total = 0
    for fp in files:
        m = re.search(r"chapter_(\d+)\.md", fp)
        cid = int(m.group(1)) if m else 0
        try:
            text = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        if not text:
            continue
        sentences = [s.strip() for s in re.split(r"[。！？；\n]", text) if s.strip()]
        hit_sents: list[str] = []
        seen_s: set[str] = set()
        n_hit = 0
        for s in sentences:
            if any(k in s for k in kw):
                n_hit += 1
                s2 = s[:80] + ("…" if len(s) > 80 else "")
                if s2 not in seen_s:
                    seen_s.add(s2)
                    hit_sents.append(s2)
            if len(hit_sents) >= max_hits_per_chapter:
                break
        if n_hit:
            hits[cid] = n_hit
        if not hit_sents:
            continue
        block = f"第{cid}章：{' | '.join(hit_sents)}"
        if total + len(block) > max_chars:
            out.append("…（已达检索字数上限）")
            break
        out.append(block)
        total += len(block)
    return "\n".join(out), hits


def _scan_all_chapters(project_root: str, keywords: Optional[list] = None, max_chars: int = 6000) -> str:
    """遍历 chapters/ 下所有章节 .md，按关键词命中段落注入（带章号）。

    - 有关键词：收集每章含关键词的段落（每章最多 12 段，段内 600 字）
    - 无关键词：每章取前 120 字概览（看全本结构）
    - 受 max_chars 上限控制（约 6000 字），超出截断并提示
    """
    ch_dir = os.path.join(project_root, "chapters")
    if not os.path.isdir(ch_dir):
        return ""
    files = sorted(
        glob.glob(os.path.join(ch_dir, "chapter_*.md")),
        key=lambda p: int(re.search(r"chapter_(\d+)\.md", p).group(1)),
    )
    kw = [k.strip() for k in (keywords or []) if k.strip()]
    out: list[str] = []
    total = 0
    for fp in files:
        m = re.search(r"chapter_(\d+)\.md", fp)
        cid = int(m.group(1)) if m else 0
        try:
            text = open(fp, encoding="utf-8").read().strip()
        except Exception:
            continue
        if not text:
            continue
        if kw:
            paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            hits = [p for p in paras if any(k in p for k in kw)]
            if not hits:
                continue
            block = f"第{cid}章：\n" + "\n".join(h[:600] for h in hits[:12])
        else:
            block = f"第{cid}章（概览）：\n" + text[:120]
        if total + len(block) > max_chars:
            out.append("…（已达检索字数上限，后续章节未列出）")
            break
        out.append(block)
        total += len(block)
    return "\n\n".join(out)


def _detect_intent(msg: str) -> str:
    """识别用户问题类型，返回回答组织引导（结论先行 / 按时间线 / 引用章节）。"""
    if any(t in msg for t in ("养成", "修炼", "升级", "变强", "实力", "体系", "路线", "成长", "境界", "突破")):
        return (
            "这是成长/体系类问题：请按时间线（章节顺序）梳理主角实力、修炼的阶段性节点，"
            "逐节点引用章节号与原文证据，结论先行；若某些节点缺乏正文支撑，请明确说明，不要脑补。"
        )
    if any(t in msg for t in ("感情", "恋爱", "婚约", "心动", "情愫", "cp", "CP")):
        return (
            "这是感情线类问题：请按「相识 → 升温 → 确立」的时间线整理，逐点引用章节号与关键情节，"
            "结论先行；资料不足请明说。"
        )
    if any(t in msg for t in ("势力", "宗门", "家族", "门派", "格局")):
        return (
            "这是势力格局类问题：请按主要势力分别梳理（立场 / 实力 / 与主角关系），每条引用章节号；"
            "结论先行，资料不足请明说。"
        )
    if any(c in msg for c in _SCAN_CONSISTENCY):
        return (
            "这是一致性检查类问题：请逐处列出「一处说法 vs 另一处说法」并标注章节号，指出矛盾；"
            "若确认一致也请说明依据。"
        )
    return ""


# 规划/梳理类问题：只需设定层 + 简/深大纲，零正文扫描（正文是"被修正对象"不是"讨论依据"）
_PLAN_INTENT = [
    "路线", "规划", "体系", "主线", "弧", "伏笔", "线索", "人物弧", "成长",
    "养成", "修炼", "境界", "战力", "升级", "变强", "实力", "感情线", "势力格局",
    "世界观", "设定", "结构", "节奏", "大纲", "框架", "梳理", "总结", "概括",
    "整体", "架构", "设计",
]

# 核查/改文/具体情节类：才需要扫描正文（吃书检测 / 具体情节 / 改文）
_CHECK_INTENT = [
    "前后一致", "是否一致", "一致性", "核对", "逐章核对", "矛盾", "吃书",
    "哪章", "具体", "写得", "写得如何", "评价", "赏析", "这段文字",
    "改成", "改为", "改写", "修改", "改一下", "替换", "换一个", "删掉", "删除",
    "具体在哪", "哪一章", "哪一段",
]


def is_planning_intent(msg: str) -> bool:
    """判断是否为规划/梳理类问题——是则跳过正文扫描，只用设定层+大纲。

    规则：命中核查/改文类词 → 必为核查类（即使也命中规划词）；
          否则命中规划类词 → 规划类；
          都没有 → 默认 False（走通用路径，保守扫描）。
    """
    if any(t in msg for t in _CHECK_INTENT):
        return False
    if any(t in msg for t in _PLAN_INTENT):
        return True
    return False


def build_discussion_context(
    memory: Optional[MemoryEngine],
    user_message: str,
    config: Optional[Config] = None,
    project_root: Optional[str] = None,
    current_chapter_id: Optional[int] = None,
    top_k: Optional[int] = None,
) -> str:
    """拼出喂给 agent 系统提示的小说上下文块。

    读取层次（精准优先）：
      1) 结构化记忆：人物 / 活跃伏笔 / 事件账本（摘要）
      2) 用户明确指定「第X章 / 第X-Y章」→ 直接读取对应章完整正文
      3) 否则注入「当前章节」完整正文（锚点）
      4) RAG 检索跨章片段（伏笔/设定覆盖）；RAG 不可用 → 关键词遍历兜底
    返回字符串；无项目/无数据时返回通用讨论提示。
    """
    if memory is None:
        return "（未加载项目，无法读取小说上下文。仅作通用小说架构讨论。）"

    top_k = top_k or (config.rag_top_k if config else 8)
    top_k = min(top_k, _MAX_RAG_HITS)
    parts: list[str] = []

    # ---- 结构化记忆：人物 / 活跃伏笔 / 事件账本 ----
    try:
        chars = memory.archive.get_all_characters() or []
        if chars:
            lines = [
                f"- {c.name}（{getattr(c, 'role', '')}）：{getattr(c, 'current_state', '') or getattr(c, 'description', '')}"
                for c in chars[:20]
            ]
            parts.append("【人物档案】\n" + "\n".join(lines))
    except Exception:
        pass

    try:
        fss = memory.archive.get_active_foreshadows() or []
        if fss:
            lines = [
                f"- [{f.id}] {f.description}（状态：{f.status}，优先度：{f.priority}）"
                for f in fss[:30]
            ]
            parts.append("【活跃伏笔 / 线索】\n" + "\n".join(lines))
    except Exception:
        pass

    try:
        events = memory.get_story_events() or []
        if events:
            lines = [f"- [{e.id}] {e.title}（状态：{e.status}）" for e in events[:30]]
            parts.append("【事件账本】\n" + "\n".join(lines))
    except Exception:
        pass

    # ---- 精准正文读取 ----
    explicit, about = _parse_chapter_range(user_message)
    scan_all, scan_kw = _parse_scan_intent(user_message)
    raw_kws = _extract_keywords(about if about else user_message)
    keywords = _expand_keywords(raw_kws)

    # 体系性/主题性问题（养成路线、修炼体系、势力格局、感情线…）
    topic_hit = [t for t in _SCAN_TOPIC_TRIGGERS if t in user_message]
    # 设定层优先豁免：战力/修炼/升级/体系/成长类问题，设定层已覆盖权威顶层，
    # 不强制扫全本正文（避免 62 章正文淹没设定层），只留设定层 + 当前章锚点。
    setting_first = any(t in user_message for t in
                        ("战力", "修炼", "升级", "变强", "体系", "成长", "境界", "突破", "实力"))
    if topic_hit and project_root and not setting_first:
        scan_all = True
        # 原词里已有具体实体（星际/战舰/楚渊/玄铁剑…）→ 用原词精准扫描；
        # 全是抽象词（养成/路线/修炼…）→ 用扩展后的正文高频词扫描
        if not scan_kw:
            # 只保留「具体实体词」（剔除抽象词：路线/修炼/养成…本身是常见词，易误命中）
            concrete = [k for k in raw_kws if k not in _TOPIC_EXPANSION]
            if concrete:
                scan_kw = concrete[:6]
            else:
                scan_kw = keywords[:6]
        else:
            scan_kw = list(dict.fromkeys(scan_kw + raw_kws[:6]))[:6]

    # ① 明确章节范围：直接读对应章完整正文
    explicit_injected = False
    if explicit and project_root:
        flat: list[int] = []
        for a, b in explicit:
            for n in range(a, min(b, a + _MAX_RANGE_CHAPTERS) + 1):
                if n not in flat:
                    flat.append(n)
        flat = flat[:_MAX_RANGE_CHAPTERS]
        chunks = []
        for cid in flat:
            body = _read_chapter(project_root, cid, _MAX_CHARS_RANGE)
            if body:
                chunks.append(f"【指定章节正文 · 第{cid}章】\n{body}")
        if chunks:
            parts.append("\n\n".join(chunks))
            explicit_injected = True

    # ② 否则注入当前章完整正文（锚点）；全本扫描时不重复注入当前章以省 token
    if not explicit_injected and current_chapter_id and project_root and not scan_all:
        body = _read_chapter(project_root, current_chapter_id, _MAX_CHARS_CURRENT)
        if body:
            parts.append(f"【当前章节正文 · 第{current_chapter_id}章】\n{body}")

    # ③ RAG 检索（跨章伏笔/设定覆盖）；失败则关键词兜底（绝不静默丢正文）
    supplement: list[str] = []
    rag_ok = False
    try:
        q = about if about else user_message
        hits = memory.rag.search(q, top_k=top_k) or []
        if hits:
            rag_ok = True
            lines = []
            for h in hits[:top_k]:
                ch = h.get("chapter_id", "?")
                txt = (h.get("text") or "").strip().replace("\n", " ")
                if txt:
                    snippet = txt[:_MAX_CHARS_RAG]
                    if len(txt) > _MAX_CHARS_RAG:
                        snippet += "…"
                    lines.append(f"[第{ch}章] {snippet}")
            if lines:
                supplement.append("【相关片段（RAG 检索）】\n" + "\n".join(lines))
    except Exception:
        pass

    if not rag_ok:
        fb = _keyword_fallback(
            project_root or "", keywords, _MAX_FALLBACK_HITS, _MAX_CHARS_FALLBACK
        )
        if fb:
            supplement.append("【相关片段（关键词检索 · RAG 不可用兜底）】\n" + "\n".join(fb))

    if supplement:
        parts.append("\n\n".join(supplement))

    # ④ 全本遍历检索（用户要求「全本 / 整本 / 所有章节」或体系性主题时扫描全部正文）
    scan_text = ""
    if project_root and scan_all:
        scan_text = _scan_all_chapters(project_root, scan_kw, _MAX_CHARS_SCAN)
        if scan_text:
            label = "、".join(scan_kw) if scan_kw else "全本概览"
            parts.append(f"【全本检索结果 · 关键词：{label}】\n{scan_text}")
        elif scan_kw:
            # 实体词扫描无命中：即便 RAG 返回了相似片段也可能不相关，明确告知不得编造
            parts.append(
                f"【检索提示】按『{'、'.join(scan_kw)}』遍历全书未检索到直接相关段落。"
                "请如实告诉用户：资料不足，无法准确回答；建议指定章节范围或用更具体的关键词，"
                "严禁编造体系或情节。"
            )

    # 证据纪律兜底（非全本扫描路径）：完全没有正文证据时明确告知不得编造
    if not scan_all and not explicit_injected and not supplement:
        cur_has = False
        if current_chapter_id and project_root:
            cur_has = bool(_read_chapter(project_root, current_chapter_id, 1))
        if not cur_has:
            parts.append(
                "【检索提示】当前未检索到与该问题直接相关的正文证据。"
                "请如实告诉用户：资料不足，无法准确回答；建议指定章节范围或用更具体的关键词"
                "（具体人名 / 功法名 / 境界名 / 物品名），严禁编造体系或情节。"
            )

    if not parts:
        return "（当前项目暂无小说数据：无人物 / 伏笔 / 事件 / 正文。仅作通用小说架构讨论。）"

    header = (
        "以下是当前小说项目的真实上下文（含结构化记忆与正文）。"
        "讨论时请以这些为依据，引用具体章节号与段落；"
        "不要编造上下文里没有的设定、人物或伏笔。\n"
    )
    hint = _detect_intent(user_message)
    if hint:
        header += "\n回答组织要求：" + hint + "\n"

    # ⑤ 设定层 world_settings（体系/线索/背景的权威顶层）——无论走 chat 还是 agent_loop，
    #    体系/战力/成长路线类问题都必须优先据此展开。置于正文证据之后作锚定。
    if project_root:
        try:
            settings = _settings_text(project_root, 5200)
            if settings:
                parts.append(
                    "【小说设定层 · 体系/线索/背景（权威顶层，体系/战力/成长类问题须优先据此）】\n"
                    + settings
                )
        except Exception:
            pass

    return header + "\n\n" + "\n\n".join(parts)
