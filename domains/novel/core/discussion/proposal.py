"""讨论功能：把 agent 的结构化提案写入数据层，并跑 guard 防幻觉。

设计（用户要求"写入更大胆但防幻觉"）：
- 更大胆：允许跨章 / 结构性改动（伏笔 / 事件 / 人物）
- 防幻觉三道闸：
  1. 提案必须引用真实 id（更新类）或明确新内容（新增类）
  2. 走现有 memory 数据层写入（有格式校验，写不进错结构）
  3. 写完后跑现有 ConsistencyChecker（规则级）+ check_event_ledger_continuity（零 token）
     —— 幻觉出来的矛盾 / 破绽会被抓出来给用户看
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime
from typing import Optional

from domains.novel.core.consistency.checker import ConsistencyChecker
from domains.novel.core.llm_client import LLMClient
from domains.novel.core.memory.archive_layer import CharacterProfile, Foreshadow
from domains.novel.core.memory.engine import MemoryEngine
from domains.novel.event_ledger_guard import check_event_ledger_continuity


def _get_foreshadow(archive, fid):
    try:
        for f in (archive.get_foreshadows(None) or []):
            if f.id == fid:
                return f
    except Exception:
        pass
    return None


def _get_character(archive, cid):
    try:
        for c in (archive.get_all_characters() or []):
            if c.id == cid:
                return c
    except Exception:
        pass
    return None


def _chapter_path(project_root: str, cid: int) -> str:
    return os.path.join(project_root, "chapters", f"chapter_{int(cid):04d}.md")


def _read_full_chapter(project_root: str, cid: int, max_chars: int = 6000) -> str:
    """读取某章完整正文（用于改写后守卫校验真实文本）。"""
    p = _chapter_path(project_root, cid)
    if not os.path.isfile(p):
        return ""
    try:
        return open(p, encoding="utf-8").read().strip()[:max_chars]
    except Exception:
        return ""


def _backup_chapter(path: str) -> str:
    """写盘前备份原章正文到 .novelforge/guard/backup/，便于回退。"""
    backup_dir = os.path.join(
        os.path.dirname(os.path.dirname(path)), ".novelforge", "guard", "backup"
    )
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(backup_dir, f"{os.path.basename(path)}.bak_{ts}")
    shutil.copy2(path, bak)
    return bak


# ---- 设定 / 大纲 结构化写入（讨论级联改写的核心）----
_OUTLINE_PATH = os.path.join(".novelforge", "chapter_outlines.json")

# chapter_outlines.json 中允许讨论直接改写的字段（防止注入无关键）。
# 简大纲层：core_events / plot_summary / chapter_type_label / opening_hook / ending_hook
# 深大纲层：state_deltas / continuity_anchors / writing_focus / narrative_shots /
#          beat_sequence / event_progress / body_snippet / time_anchor
_ALLOWED_OUTLINE_FIELDS = {
    "chapter_type", "chapter_type_label", "title", "core_events", "opening_hook",
    "ending_hook", "foreshadowing", "plot_summary", "volume", "rhythm", "confirmed",
    "timeline", "time_point", "framework_controlled", "body_snippet", "event_progress",
    "state_deltas", "continuity_anchors", "writing_focus", "time_anchor",
    "narrative_shots", "beat_sequence",
}

# 级联扫描时用于匹配设定关键词的大纲文本字段
_OUTLINE_CASCADE_FIELDS = (
    "core_events", "plot_summary", "writing_focus", "state_deltas",
    "continuity_anchors", "narrative_shots", "beat_sequence", "event_progress",
    "opening_hook", "ending_hook",
)


def _load_outlines(project_root: str):
    p = os.path.join(project_root, _OUTLINE_PATH)
    if not os.path.isfile(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def _save_outlines(project_root: str, data) -> str:
    p = os.path.join(project_root, _OUTLINE_PATH)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p + f".bak_cascade_{ts}"
    if os.path.isfile(p):
        shutil.copy2(p, bak)
    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return bak


def _get_world_setting(archive, sid=None, name=None, category=None):
    """按 id 优先定位设定；否则按 name+category 模糊定位。"""
    if sid:
        for w in (archive.get_world_settings() or []):
            if w.id == sid:
                return w
    if name and category:
        try:
            return archive._find_world_setting(name, category)
        except Exception:
            return None
    return None


def _scan_outline_cascade(project_root: str, keywords: list, except_chapters: list) -> list:
    """设定变更后，确定性扫描章节大纲里引用了该设定关键词的章，给出级联同步提示。

    不做 AI 自动改写（避免惊喜式破坏），只产出信号，由用户在讨论里继续调整或被
    同一条提案里的 update_outline 显式覆盖。
    """
    hints: list[dict] = []
    data = _load_outlines(project_root)
    if not data:
        return hints
    chs = data if isinstance(data, list) else (data.get("chapters") or [])
    for c in chs:
        cid = int(c.get("chapter_id", 0) or 0)
        if cid in except_chapters:
            continue
        blob = " ".join(str(c.get(f, "") or "") for f in _OUTLINE_CASCADE_FIELDS)
        for kw in keywords:
            if kw and kw in blob:
                hints.append({
                    "chapter_id": cid, "keyword": kw,
                    "note": f"第{cid}章大纲引用了设定关键词「{kw}」，设定已变更，建议同步该章大纲",
                })
                break
    return hints


def _apply_chapter_edit(project_root: str, cid: int, before: str, after: str):
    """精确替换某章正文片段。返回 (backup_path, error)。

    - before 必须逐字匹配当前正文（去首尾空白容错），否则报错，绝不瞎改
    - before 在正文出现多次 → 报歧义、不替换，避免误改
    """
    path = _chapter_path(project_root, cid)
    if not os.path.exists(path):
        return None, f"第{cid}章正文文件不存在：{path}"
    text = open(path, encoding="utf-8").read()
    norm = (before or "").strip()
    if not norm:
        return None, "before 片段为空"
    # 优先精确匹配；失败再试去首尾空白
    count = text.count(before)
    src = before
    if count == 0:
        count = text.count(norm)
        src = norm
    if count == 0:
        return None, "正文片段未在第{cid}章原文中找到，无法精确替换（请确认片段逐字匹配当前正文）".replace("{cid}", str(cid))
    if count > 1:
        return None, f"正文片段在第{cid}章出现 {count} 次，存在歧义，已跳过以避免误改（请提供更长 / 更唯一的上下文）"
    bak = _backup_chapter(path)
    new_text = text.replace(src, after, 1)
    open(path, "w", encoding="utf-8").write(new_text)
    return bak, None


def apply_proposal(
    memory: MemoryEngine,
    project_root: str,
    proposal_dict: dict,
    llm_client: LLMClient,
    chapter_id: int = 1,
) -> dict:
    """执行提案里的每个操作，并返回 {applied, errors, guard_issues}。"""
    applied: list[str] = []
    errors: list[str] = []
    cascade_hints: list[dict] = []
    archive = memory.archive

    # 同提案 update_outline 覆盖的章：级联扫描时统一排除（这些章已在同提案里同步过）。
    # 注意：必须在 filter_valid_ops 之前从原始 proposal 收集，避免因过滤而漏判。
    outline_covered = {
        int(op.get("chapter_id", 0) or 0)
        for op in proposal_dict.get("proposal", [])
        if op.get("op") == "update_outline" and op.get("chapter_id")
    }

    # ---- 统一过滤（与 build_task_cards 同源，避免 apply 阶段重复 lookup 报「找不到」）----
    # 根因：用户讨论「改大结局/男主归隐」时，模型看不到真实 id 清单，会硬凑
    # id="endgame"/"protagonist" / 第250章这类占位。build_task_cards 已用 filter_valid_ops
    # 丢弃这些「假目标」并保留 add_xxx；现在 apply 必须走同一过滤路径，否则会再次
    # 走 _get_foreshadow / _get_character / _get_world_setting 各自查找并报「未找到」，
    # 错误日志里堆满噪音，让用户分不清「真失败」与「模型幻觉」。
    filtered_proposal, dropped_reasons = filter_valid_ops(memory, project_root, proposal_dict.get("proposal") or [])
    for d in dropped_reasons:
        errors.append(f"软件过滤（模型幻觉）：{d}")

    for op in filtered_proposal:
        try:
            kind = op.get("op")
            if kind == "add_foreshadow":
                fs = Foreshadow(
                    id="",
                    description=op.get("description", ""),
                    planted_chapter=int(op.get("planted_chapter", chapter_id) or chapter_id),
                    status=op.get("status", "planted"),
                    related_characters=op.get("related_characters") or [],
                    resolution_chapter=op.get("resolution_chapter"),
                    priority=int(op.get("priority", 3) or 3),
                    notes=op.get("notes", ""),
                )
                archive.save_foreshadow(fs)
                applied.append(f"新增伏笔：{fs.description}")
            elif kind == "update_foreshadow":
                fid = op.get("id")
                f = _get_foreshadow(archive, fid)
                if not f:
                    errors.append(f"伏笔 id={fid} 不存在，跳过")
                    continue
                if op.get("status"):
                    f.status = op["status"]
                if op.get("resolution_chapter") is not None:
                    f.resolution_chapter = op["resolution_chapter"]
                if op.get("notes"):
                    f.notes = op["notes"]
                if op.get("priority"):
                    f.priority = op["priority"]
                if op.get("related_characters"):
                    f.related_characters = op["related_characters"]
                if op.get("description"):
                    f.description = op["description"]
                archive.save_foreshadow(f)
                applied.append(f"更新伏笔 [{fid}] 状态→{f.status}")
            elif kind == "set_event_status":
                archive.set_event_status(op.get("event_id"), op.get("status"))
                applied.append(f"事件 [{op.get('event_id')}] 状态→{op.get('status')}")
            elif kind == "add_character":
                c = CharacterProfile(
                    id="",
                    name=op.get("name", ""),
                    role=op.get("role", ""),
                    description=op.get("description", ""),
                )
                archive.save_character(c)
                applied.append(f"新增人物：{c.name}")
            elif kind == "update_character":
                cid = op.get("id")
                c = _get_character(archive, cid)
                if not c:
                    errors.append(f"人物 id={cid} 不存在，跳过")
                    continue
                if op.get("description"):
                    c.description = op["description"]
                if op.get("role"):
                    c.role = op["role"]
                if op.get("name"):
                    c.name = op["name"]
                if op.get("current_state"):
                    c.current_state = op["current_state"]
                archive.save_character(c)
                applied.append(f"更新人物 [{cid}]")
            elif kind == "edit_chapter_text":
                cid = int(op.get("chapter_id", 0) or 0)
                before = op.get("before", "")
                after = op.get("after", "")
                bak, err = _apply_chapter_edit(project_root, cid, before, after)
                if err:
                    errors.append(f"正文改写失败（第{cid}章）：{err}")
                else:
                    applied.append(
                        f"改写第{cid}章正文（备份：{os.path.basename(bak)}）："
                        f"{before[:24]}… → {after[:24]}…"
                    )
            elif kind == "update_world_setting":
                # 改设定文档（world_settings）。写入走 save_world_setting 的双轨闸门；
                # 改完绑定版本号，使旧讨论结论可见失效。
                ws = _get_world_setting(
                    archive, op.get("id"), op.get("name"), op.get("category")
                )
                if not ws:
                    errors.append(
                        f"设定未找到（id={op.get('id')} / name={op.get('name')}），跳过"
                    )
                    continue
                if op.get("description"):
                    ws.description = op["description"]
                if op.get("name"):
                    ws.name = op["name"]
                if op.get("category"):
                    ws.category = op["category"]
                try:
                    archive.save_world_setting(ws)  # 过双轨闸门，违反则抛错拦截
                except Exception as e:
                    errors.append(f"设定写入被守卫拦截：{e}")
                    continue
                # 注：save_world_setting 内部已自动 bump settings_revision（archive_layer
                # _insert_or_update_world_setting 末尾），此处不再重复调用，避免版本号双跳。
                applied.append(f"更新设定 [{ws.id}]《{ws.name}》")
                # 级联信号：扫描受影响的章节大纲（同提案 update_outline 已覆盖的章 + agent 显式 except 的章除外）
                kws = [ws.name] + (op.get("cascade_keywords") or [])
                excepts = set(op.get("except_chapters") or []) | outline_covered
                hints = _scan_outline_cascade(project_root, kws, sorted(excepts))
                cascade_hints.extend(hints)
            elif kind == "update_outline":
                # 改章节大纲（简大纲 + 深大纲，均存于 chapter_outlines.json）。
                # 字段级更新，只接受 _ALLOWED_OUTLINE_FIELDS，写盘前自动备份。
                cid = int(op.get("chapter_id", 0) or 0)
                fields = op.get("fields") or {}
                bad = [k for k in fields if k not in _ALLOWED_OUTLINE_FIELDS]
                if bad:
                    errors.append(f"第{cid}章大纲含未知字段，跳过：{bad}")
                    continue
                if not fields:
                    errors.append(f"第{cid}章大纲无 fields，跳过")
                    continue
                data = _load_outlines(project_root)
                if data is None:
                    errors.append("chapter_outlines.json 不存在，跳过大纲更新")
                    continue
                chs = data if isinstance(data, list) else (data.get("chapters") or [])
                ch = next(
                    (c for c in chs if int(c.get("chapter_id", 0) or 0) == cid), None
                )
                if not ch:
                    errors.append(f"第{cid}章大纲不存在，跳过")
                    continue
                changed = []
                for k, v in fields.items():
                    if ch.get(k) != v:
                        ch[k] = v
                        changed.append(k)
                if not changed:
                    applied.append(f"第{cid}章大纲无变化（字段已是目标值）")
                else:
                    _save_outlines(project_root, data)
                    applied.append(f"更新第{cid}章大纲（字段：{','.join(changed)}）")
            else:
                errors.append(f"未知操作类型：{kind}")
        except Exception as e:
            errors.append(f"执行 {op.get('op')} 失败：{e}")

    # ---- guard 兜底（防幻觉）----
    guard_issues: list[dict] = []
    try:
        # 守卫应校验「被改写的正文」真实文本，而非空文本或默认章
        edited_cids = [
            int(op.get("chapter_id", 0) or 0)
            for op in proposal_dict.get("proposal", [])
            if op.get("op") == "edit_chapter_text" and op.get("chapter_id")
        ]
        guard_cid = edited_cids[0] if edited_cids else chapter_id
        guard_text = _read_full_chapter(project_root, guard_cid) if project_root else ""
        checker = ConsistencyChecker(memory, llm_client)
        report = checker.check(chapter_id=guard_cid, chapter_text=guard_text, enable_llm_check=False)
        for it in getattr(report, "issues", []) or []:
            guard_issues.append({
                "severity": getattr(it, "severity", ""),
                "category": getattr(it, "category", ""),
                "description": getattr(it, "description", ""),
                "suggestion": getattr(it, "suggestion", ""),
            })
    except Exception as e:
        guard_issues.append({
            "severity": "info",
            "category": "guard",
            "description": f"一致性校验未运行：{e}",
            "suggestion": "",
        })
    try:
        for d in check_event_ledger_continuity(project_root) or []:
            guard_issues.append({
                "severity": d.get("level", ""),
                "category": d.get("category", ""),
                "description": f"{d.get('title', '')}：{d.get('detail', '')}",
                "suggestion": d.get("evidence", ""),
            })
    except Exception:
        pass

    return {
        "applied": applied,
        "errors": errors,
        "guard_issues": guard_issues,
        "cascade_hints": cascade_hints,
    }


def _chapter_time_anchor(project_root: str, cid: int) -> str:
    """读某章大纲的剧情时间锚点（time_anchor）。确定性读取，不靠模型填。"""
    data = _load_outlines(project_root)
    if not data:
        return ""
    chs = data if isinstance(data, list) else (data.get("chapters") or [])
    ch = next((c for c in chs if int(c.get("chapter_id", 0) or 0) == int(cid)), None)
    return str((ch or {}).get("time_anchor", "") or "").strip()


def _max_chapter_id(project_root: str) -> int:
    """从章节大纲确定性读取当前最大章节号（软件真源，不靠模型）。"""
    data = _load_outlines(project_root)
    if not data:
        return 0
    chs = data if isinstance(data, list) else (data.get("chapters") or [])
    ids: list[int] = []
    for c in chs:
        try:
            ids.append(int(c.get("chapter_id", 0) or 0))
        except Exception:
            pass
    return max(ids) if ids else 0


# ---- 概念占位符 → 中文关键词（模糊回退用）----
# 模型常把「男主/主角」写成 id="protagonist"、把「大结局」写成 id="endgame" 这类
# 英文占位符。库里没有这些 id，但指向的实体（楚渊 / 终局线索设定）真实存在。
# 软件必须做确定性映射：唯一命中→修正保留；多命中→拒并列出候选，绝不瞎猜。
_HERO_CONCEPTS = {
    "protagonist", "男主", "男主角", "主角", "主人公", "main", "hero",
    "女主", "女主角", "heroine",
}
# 亲属/配角后缀：用于「主角」概念命中时排除（避免把「楚渊母亲」当成主角）
_KIN_SUFFIX = ("母亲", "父亲", "哥哥", "弟弟", "姐姐", "妹妹", "叔", "伯",
               "爷", "奶", "妻", "子", "女", "仆", "侍", "兄", "弟")


def _has_kin_suffix(name: str) -> bool:
    return any(name.endswith(s) or s in name for s in _KIN_SUFFIX)


def _fuzzy_resolve_character(chars: list, op: dict):
    """角色模糊回退。返回 (实体, None) 唯一命中；或 (None, 候选名列表) 拒/歧义。

    规则（确定性，不靠模型）：
      1. 主角概念词（protagonist/男主/主角…）→ 优先 role 含『主角』；否则取
         名字最短且非亲属后缀的角色（全书男主通常名字最短且为核心，如楚渊）。
      2. 中文名片段：op.name 是某角色名子串（角色名≤5字避免过宽）或反之，唯一命中。
      3. 多命中 → 返回候选列表（让 UI 提示用户指认），不猜。
    """
    name = str(op.get("name") or op.get("id") or "").strip()
    cid = str(op.get("id") or "").strip()
    concept = (name or cid).lower()
    if concept in _HERO_CONCEPTS:
        heroes = [c for c in chars if "主角" in (getattr(c, "role", "") or "")]
        if len(heroes) == 1:
            return heroes[0], None
        # 兜底（如主角 role 字段为空）：取名字含核心姓且非亲属后缀的角色
        # （大苍界：楚渊 role 空，但「楚」姓 + 非亲属 → 唯一命中楚渊，排除楚渊母亲）
        core = [c for c in chars
                if "楚" in (getattr(c, "name", "") or "")
                and not _has_kin_suffix(getattr(c, "name", "") or "")]
        if len(core) == 1:
            return core[0], None
        return None, [getattr(c, "name", "") for c in chars[:8]]
    if name:
        hits = [
            c for c in chars
            if (name in (getattr(c, "name", "") or "") or
                (getattr(c, "name", "") or "") in name)
            and 1 <= len(getattr(c, "name", "") or "") <= 5
        ]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, [getattr(c, "name", "") for c in hits[:8]]
    return None, [getattr(c, "name", "") for c in chars[:8]]


def _fuzzy_resolve_setting(ws_list: list, op: dict):
    """设定模糊回退。返回 (实体, None) 唯一命中；或 (None, 候选名列表)。

    规则：中文名片段唯一匹配（op.name 是设定名子串或反之，设定名≤12字）。
    通用概念（大结局/终局）通常多命中（破界真相/禁忌存在隐线/4相位推进…），
    必须拒并列出候选，绝不让软件替用户猜改哪条贯穿线。
    """
    name = str(op.get("name") or op.get("id") or "").strip()
    cid = str(op.get("id") or "").strip()
    if name:
        hits = [
            w for w in ws_list
            if (name in (getattr(w, "name", "") or "") or
                (getattr(w, "name", "") or "") in name)
            and 1 <= len(getattr(w, "name", "") or "") <= 12
        ]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, [getattr(w, "name", "") for w in hits[:8]]
    return None, [getattr(w, "name", "") for w in ws_list[:8]]


def filter_valid_ops(memory: MemoryEngine, project_root: str, ops: list) -> tuple[list, list]:
    """确定性过滤：只保留指向真实存在实体的操作，丢弃模型自造的假目标。

    返回 (kept, dropped_reasons)。这是『防线兜底』——即使模型编了假 id
    （如 id="endgame"、人物="protagonist"、第250章），也绝不生成任务卡。
    真值来源：archive（设定/人物/伏笔/事件）+ chapter_outlines.json（章节范围）。

    为什么必须软件侧做：模型在生成 proposal 时**看不到真实 id 清单**，
    只会凭讨论里提到的概念（卷五归隐结局、大结局）硬凑 id，导致『找错地方』。
    软件侧用真值库逐一核对——**精确匹配失败时做概念/中文名片段模糊回退**：
    唯一命中即修正保留（如 protagonist→楚渊），多命中则拒并提示候选（如
    『大结局』对应多条贯穿线，不让软件瞎猜），从源头消灭假卡又不错杀真实体。
    """
    if not memory:
        return list(ops or []), []  # 无 memory 时保守放行（不应发生）
    archive = memory.archive
    max_ch = _max_chapter_id(project_root)

    ws_list = list(archive.get_world_settings() or [])
    char_list = list(archive.get_all_characters() or [])
    fs_list = list(archive.get_foreshadows() or [])
    ws_by_id, ws_by_name = {}, {}
    try:
        for w in ws_list:
            wid = getattr(w, "id", None)
            wnm = getattr(w, "name", None)
            if wid:
                ws_by_id[wid] = w
            if wnm:
                ws_by_name[wnm] = w
    except Exception:
        pass
    char_by_id, char_by_name = {}, {}
    for c in char_list:
        cid = getattr(c, "id", None)
        cnm = getattr(c, "name", None)
        if cid:
            char_by_id[cid] = c
        if cnm:
            char_by_name[cnm] = c
    fs_by_id = {}
    for f in fs_list:
        fid = getattr(f, "id", None)
        if fid:
            fs_by_id[fid] = f
    ev_by_id = {}
    try:
        for e in (archive.get_events() or []):
            eid = getattr(e, "id", None)
            if eid:
                ev_by_id[eid] = e
    except Exception:
        pass

    kept: list[dict] = []
    dropped: list[str] = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        # 创建类操作：总是允许（新数据由写入时校验结构）
        if kind in ("add_foreshadow", "add_character", "add_world_setting", "add_event"):
            kept.append(op)
            continue
        if kind in ("edit_chapter_text", "update_outline"):
            try:
                cid = int(op.get("chapter_id"))
            except Exception:
                dropped.append(f"「{kind}」章节号无效")
                continue
            if cid < 1 or (max_ch and cid > max_ch):
                dropped.append(f"「{kind}」指向不存在的第{cid}章（当前共{max_ch}章）")
                continue
            kept.append(op)
            continue
        if kind == "update_world_setting":
            wid = op.get("id")
            wnm = op.get("name")
            ent = None
            if wid and wid in ws_by_id:
                ent = ws_by_id[wid]
            elif wid and wid in ws_by_name:        # 模型把真实设定名填进 id 字段
                ent = ws_by_name[wid]
            elif wnm and wnm in ws_by_name:
                ent = ws_by_name[wnm]
            if ent is not None:
                # name 命中但 id 写错 → 修正为真实 id，避免 apply 阶段 lookup 失败
                if (wid and wid not in ws_by_id) or (wnm and wnm not in ws_by_name):
                    op = dict(op)
                    op["id"] = getattr(ent, "id", None)
                    op["name"] = getattr(ent, "name", None)
                kept.append(op)
            else:
                # 模糊回退：中文名片段唯一匹配则修正保留
                ent, cands = _fuzzy_resolve_setting(ws_list, op)
                if ent is not None:
                    op = dict(op)
                    op["id"] = getattr(ent, "id", None)
                    op["name"] = getattr(ent, "name", None)
                    kept.append(op)
                else:
                    dropped.append(
                        f"「更新设定」目标不存在（id={wid} name={wnm}）"
                        + (f"；可能指：{', '.join(cands[:4])}" if cands else "")
                    )
            continue
        if kind == "update_character":
            cid = op.get("id")
            cnm = op.get("name")
            ent = None
            if cid and cid in char_by_id:
                ent = char_by_id[cid]
            elif cid and cid in char_by_name:      # 模型把真实角色名填进 id 字段
                ent = char_by_name[cid]
            elif cnm and cnm in char_by_name:
                ent = char_by_name[cnm]
            if ent is not None:
                if (cid and cid not in char_by_id) or (cnm and cnm not in char_by_name):
                    op = dict(op)
                    op["id"] = getattr(ent, "id", None)
                    op["name"] = getattr(ent, "name", None)
                kept.append(op)
            else:
                # 模糊回退：主角概念 / 中文名片段唯一匹配则修正保留
                ent, cands = _fuzzy_resolve_character(char_list, op)
                if ent is not None:
                    op = dict(op)
                    op["id"] = getattr(ent, "id", None)
                    op["name"] = getattr(ent, "name", None)
                    kept.append(op)
                else:
                    dropped.append(
                        f"「更新人物」目标不存在（id={cid} name={cnm}）"
                        + (f"；可能指：{', '.join(cands[:4])}" if cands else "")
                    )
            continue
        if kind == "update_foreshadow":
            fid = op.get("id")
            if fid and fid in fs_by_id:
                kept.append(op)
            else:
                # 模糊回退：id 片段唯一匹配则修正保留
                ent = None
                cands = [getattr(f, "description", "")[:12] for f in fs_list[:6]]
                if fid:
                    hits = [f for f in fs_list
                            if fid in (getattr(f, "id", "") or "")
                            and 4 <= len(getattr(f, "id", "") or "") <= 10]
                    if len(hits) == 1:
                        ent = hits[0]
                if ent is not None:
                    op = dict(op)
                    op["id"] = getattr(ent, "id", None)
                    kept.append(op)
                else:
                    dropped.append(
                        f"「更新伏笔」目标不存在（id={fid}）"
                        + (f"；可能指：{', '.join(cands[:4])}" if cands else "")
                    )
            continue
        if kind == "set_event_status":
            eid = op.get("event_id")
            if eid and eid in ev_by_id:
                kept.append(op)
            else:
                dropped.append(f"「更新事件」目标不存在（id={eid}）")
            continue
        kept.append(op)  # 未知 op 保守放行
    return kept, dropped


def build_task_cards(
    memory: MemoryEngine,
    tasks: list,
    project_root: str = "",
    project_name: str = "",
) -> list[dict]:
    """把 agent 产出的 tasks 转成 UI 可直接渲染的「任务卡」列表。

    每张卡包含：标题、创建时间、剧情时间、影响范围（章节区间 + 实体清单）、
    字段级改前→改后对比、原始操作。

    设计原则（与 P17 一脉相承）：
      - **剧情时间**：从 chapter_outlines.json 的 time_anchor **确定性读取**，
        不由模型填——模型填的时间不可信，且没必要占用 token。
      - **改前值**：从 memory 真值读取（复用 build_proposal_view），零幻觉。
      - 章节来源 = 模型给的 chapters ∪ 操作里带的 chapter_id（双路取并集，
        模型漏填时仍能算出正确范围）。
    """
    cards: list[dict] = []
    now = datetime.now()
    # 自动检测项目名（调用方未传时从 project.json 读取）
    if not project_name and project_root:
        try:
            import json as _json
            _meta_path = os.path.join(project_root, "project.json")
            if os.path.isfile(_meta_path):
                with open(_meta_path, "r", encoding="utf-8") as _f:
                    _meta = _json.load(_f)
                project_name = _meta.get("name", "") or _meta.get("title", "") or ""
        except Exception:
            pass
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        ops = t.get("proposal") or []
        if not isinstance(ops, list) or not ops:
            continue
        # ---- 确定性过滤：丢弃指向不存在实体的操作（模型编的假 id/章节号）----
        kept, dropped = filter_valid_ops(memory, project_root, ops)
        if not kept:
            # 整张卡无有效操作 → 不生成卡片，避免假卡跳出来（如 id=endgame 类占位）
            continue

        # ---- 章节范围：模型给的 ∪ 操作里带的 ----
        chs: set[int] = set()
        for c in (t.get("chapters") or []):
            try:
                chs.add(int(c))
            except Exception:
                pass
        for op in kept:
            if not isinstance(op, dict):
                continue
            cid = op.get("chapter_id")
            if cid in (None, "", 0):
                continue
            try:
                chs.add(int(cid))
            except Exception:
                pass
        chs_sorted = sorted(chs)

        # ---- 剧情时间：确定性从大纲 time_anchor 读 ----
        anchors: list[str] = []
        for c in chs_sorted:
            a = _chapter_time_anchor(project_root, c)
            if a and a not in anchors:
                anchors.append(a)
        if len(anchors) <= 1:
            story_time = anchors[0] if anchors else ""
        else:
            story_time = f"{anchors[0]} → {anchors[-1]}"

        if not chs_sorted:
            chapter_range = "全局（不涉具体章）"
        elif len(chs_sorted) == 1:
            chapter_range = f"第{chs_sorted[0]}章"
        else:
            chapter_range = f"第{chs_sorted[0]}–{chs_sorted[-1]}章（{len(chs_sorted)}章）"

        # ---- 范围 + 字段级对比（before 取真值，仅对过滤后的有效操作）----
        try:
            view = build_proposal_view(
                memory,
                {"thinking": t.get("rationale", ""), "proposal": kept},
                project_root,
            )
        except Exception:
            view = {"scope": [], "changes": [], "summary": ""}

        cards.append({
            "id": uuid.uuid4().hex[:8],
            "project_name": project_name,
            "title": str(t.get("title") or "未命名改动")[:40],
            "created_at": now.strftime("%m-%d %H:%M"),
            "story_time": story_time,
            "chapters": chs_sorted,
            "chapter_range": chapter_range,
            "scope": view.get("scope", []),
            "changes": view.get("changes", []),
            "summary": str(t.get("rationale") or ""),
            "proposal": kept,
            "filtered": dropped,    # 被确定性过滤掉的无效操作（模型编的假目标）
            "status": "pending",     # pending | done | rejected
            "result": "",            # 执行结果摘要
        })
    return cards


# ---- 提案可视化：改动范围 + 字段级 before/after 对比 ----
# 设计：before 值从 memory 真值读取（不依赖 LLM 编造），after 取提案目标值。
# 这样「改前」永远是真实数据，视图层零幻觉。
_FIELD_LABELS = {
    "status": "状态",
    "resolution_chapter": "回收章节",
    "notes": "备注",
    "priority": "优先度",
    "related_characters": "关联角色",
    "description": "描述",
    "role": "角色",
    "name": "名称",
    "current_state": "当前状态",
    "planted_chapter": "埋设章节",
    "core_events": "核心事件",
    "plot_summary": "情节摘要",
    "state_deltas": "状态变化",
    "continuity_anchors": "承上启下",
    "writing_focus": "描写重点",
    "narrative_shots": "镜头调度",
    "beat_sequence": "段落顺序",
    "event_progress": "节拍",
    "title": "标题",
    "chapter_type_label": "章节类型",
    "opening_hook": "开篇钩子",
    "ending_hook": "结尾钩子",
    "time_anchor": "时间锚点",
}


def _current_event(memory, eid):
    try:
        for e in (memory.get_story_events() or []):
            if getattr(e, "id", None) == eid:
                return e
    except Exception:
        pass
    return None


def _fmt_val(v) -> str:
    if isinstance(v, list):
        return "、".join(str(x) for x in v)
    return "" if v is None else str(v)


def build_proposal_view(memory, proposal_dict: dict, project_root: str = "") -> dict:
    """把结构化提案转成 UI 可用的「改动范围 + 调整对比」。

    返回：
      scope:   [{label, type, id}]            —— 提案涉及的实体
      changes: [{scope_label, field, before, after}] —— 字段级改前/改后
      summary: str                            —— agent 的分析说明
    before 从 memory 真实读取；after 取提案目标值。
    """
    scope: list[dict] = []
    changes: list[dict] = []
    archive = getattr(memory, "archive", None) if memory else None

    for op in proposal_dict.get("proposal", []) or []:
        kind = op.get("op")
        try:
            if kind == "update_foreshadow":
                fid = op.get("id")
                f = _get_foreshadow(archive, fid) if archive else None
                label = f"伏笔 [{fid}]" + (f" {f.description[:18]}" if f and f.description else "")
                scope.append({"label": label, "type": "foreshadow", "id": fid})
                if f:
                    for k in ("status", "resolution_chapter", "notes", "priority", "related_characters", "description"):
                        if k in op and op[k] is not None:
                            changes.append({
                                "scope_label": label,
                                "field": _FIELD_LABELS.get(k, k),
                                "before": _fmt_val(getattr(f, k, "")) or "（空）",
                                "after": _fmt_val(op[k]),
                            })
            elif kind == "set_event_status":
                eid = op.get("event_id")
                e = _current_event(memory, eid) if memory else None
                label = f"事件 [{eid}]" + (f" {e.title[:16]}" if e and getattr(e, "title", "") else "")
                scope.append({"label": label, "type": "event", "id": eid})
                before = getattr(e, "status", "") if e else ""
                changes.append({
                    "scope_label": label,
                    "field": "状态",
                    "before": _fmt_val(before) or "（空）",
                    "after": _fmt_val(op.get("status", "")),
                })
            elif kind == "update_character":
                cid = op.get("id")
                c = _get_character(archive, cid) if archive else None
                label = f"人物 [{cid}]" + (f" {c.name}" if c and c.name else "")
                scope.append({"label": label, "type": "character", "id": cid})
                if c:
                    for k in ("description", "role", "name", "current_state"):
                        if k in op and op[k] is not None:
                            changes.append({
                                "scope_label": label,
                                "field": _FIELD_LABELS.get(k, k),
                                "before": _fmt_val(getattr(c, k, "")) or "（空）",
                                "after": _fmt_val(op[k]),
                            })
            elif kind == "add_foreshadow":
                label = f"新伏笔 {op.get('description', '')[:18]}"
                scope.append({"label": label, "type": "foreshadow", "id": ""})
                changes.append({
                    "scope_label": label,
                    "field": "新增",
                    "before": "（无，新增）",
                    "after": f"{op.get('description', '')}（第{op.get('planted_chapter', '?')}章，优先度{op.get('priority', 3)}）",
                })
            elif kind == "add_character":
                label = f"新人物 {op.get('name', '')}"
                scope.append({"label": label, "type": "character", "id": ""})
                changes.append({
                    "scope_label": label,
                    "field": "新增",
                    "before": "（无，新增）",
                    "after": f"{op.get('name', '')}（{op.get('role', '')}）",
                })
            elif kind == "edit_chapter_text":
                cid = op.get("chapter_id")
                label = f"第{cid}章正文"
                scope.append({"label": label, "type": "chapter_text", "id": cid})
                b_raw, a_raw = op.get("before", ""), op.get("after", "")
                b_disp = b_raw[:200] + ("…" if len(b_raw) > 200 else "")
                a_disp = a_raw[:200] + ("…" if len(a_raw) > 200 else "")
                changes.append({
                    "scope_label": label,
                    "field": "正文改写",
                    "before": b_disp,
                    "after": a_disp,
                })
            elif kind == "update_world_setting":
                ws = _get_world_setting(
                    archive, op.get("id"), op.get("name"), op.get("category")
                )
                name = op.get("name") or (ws.name if ws else "?")
                sid = op.get("id") or (ws.id if ws else "")
                label = f"设定《{name}》" + (f" [{sid}]" if sid else "")
                scope.append({"label": label, "type": "world_setting", "id": sid})
                before = (ws.description if ws else "") or ""
                after = op.get("description", before) or ""
                changes.append({
                    "scope_label": label,
                    "field": "描述",
                    "before": (before or "（空）")[:400],
                    "after": (after or "（空）")[:400],
                })
            elif kind == "update_outline":
                cid = op.get("chapter_id")
                label = f"第{cid}章大纲"
                scope.append({"label": label, "type": "outline", "id": cid})
                ch = None
                if project_root:
                    data = _load_outlines(project_root)
                    if data is not None:
                        chs = data if isinstance(data, list) else (data.get("chapters") or [])
                        ch = next(
                            (c for c in chs if int(c.get("chapter_id", 0) or 0) == int(cid or 0)),
                            None,
                        )
                for k, v in (op.get("fields") or {}).items():
                    before = ch.get(k, "") if ch else "（未读取）"
                    changes.append({
                        "scope_label": label,
                        "field": _FIELD_LABELS.get(k, k),
                        "before": _fmt_val(before)[:300] or "（空）",
                        "after": _fmt_val(v)[:300],
                    })
        except Exception:
            continue

    return {
        "scope": scope,
        "changes": changes,
        "summary": proposal_dict.get("thinking", ""),
    }
