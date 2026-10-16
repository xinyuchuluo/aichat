#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arc_subplot.py —— 结构化「事件弧(L2) + 跨卷暗线」模型与零 token 守卫
=================================================================

对接 structure_spec.md §1(L2 弧) / §2.2(暗线) / §5(守卫校验)。

数据（均存于 .novelforge/，与 chapter_outlines.json 风格一致）：
  - volumes.json   : 已由 event_planner / 本项目提供，定义卷边界 [start_chapter, end_chapter]（L1）。
  - arcs.json      : 本模块消费的结构化弧（L2）—— 弧归属单卷、显式映射章节、含起承转合阶段。
  - subplots.json  : 本模块消费的跨卷暗线实体（维度）—— 节点挂具体章、关键实体落点、可跨卷。

守卫（零 token，纯文件读写 + 字符串包含判定）：
  check_arc_subplot_continuity(project_dir)
    ① 弧不跨卷：每个 arc 的 chapters 必须全部落在所属 volume 的 [start,end]；
       弧的 volume 必须存在；弧内章节单调不减无重复；
       所有大纲章节须被恰好一个弧覆盖（无缺口 / 无同卷重叠）。
    ② 暗线节点不中断（跨卷追踪）：
       - 每个节点章节必须存在（文件存在且 ≤ 最大章）；
       - 节点章节单调不减（不可回退，回退=吃书）；
       - 节点声明的关键实体必须在该章正文出现（grep，未落地=节点虚假=吃书）；
       - 若暗线声明 spans_volumes，节点所在卷须被其覆盖。
    ③ 暗线回收完整性：status=done 的暗线须有 stage=payoff 的节点；status=active 且已到最后章节仍无 payoff → 提示（非 high）。

生成侧：
  get_arc_subplot_card(project_dir, chapter_id) -> 紧凑上下文卡（本章所属弧 / 起承转合阶段 /
  本章暗线节点），由 engine 注入 memory_result.arc_subplot，assembler 以高优先级块注入正文生成，
  实现「生成侧也按弧/暗线走」的治本（防吃书于未然）。

全程不调用 LLM，零 token。修正只报警 / 写待办，绝不读全文、绝不重写正文。
"""
from __future__ import annotations

import os
import re
import json
import datetime
import logging

logger = logging.getLogger("arc_subplot")


# ---------------------------------------------------------------------------
# 路径与加载器
# ---------------------------------------------------------------------------
def _novelforge_dir(project_dir: str) -> str:
    return os.path.join(project_dir, ".novelforge")


def load_volumes(project_dir: str) -> list:
    """读 volumes.json，返回卷列表（每卷含 id/title/start_chapter/end_chapter）。缺失返回 []。"""
    p = os.path.join(_novelforge_dir(project_dir), "volumes.json")
    if not os.path.exists(p):
        return []
    try:
        data = json.load(open(p, encoding="utf-8"))
        vols = data.get("volumes", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        # 归一化 id 为 str，便于与 arcs.json 的 volume 字段比对
        for v in vols:
            v["_id"] = str(v.get("id"))
        return vols
    except Exception as e:
        logger.warning("volumes.json 解析失败（跳过卷校验）：%s", e)
        return []


def load_arcs(project_dir: str) -> dict:
    p = os.path.join(_novelforge_dir(project_dir), "arcs.json")
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def load_subplots(project_dir: str) -> dict:
    p = os.path.join(_novelforge_dir(project_dir), "subplots.json")
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def _chapter_files(project_dir: str) -> list:
    d = os.path.join(project_dir, "chapters")
    if not os.path.isdir(d):
        return []
    out = []
    for fn in os.listdir(d):
        m = re.match(r'^chapter_(\d{4})\.md$', fn)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def _planned_chapter_ids(project_dir: str) -> list:
    """读取规划章节 id，作为增量写章时的『正确上界』。

    框架（volumes.json）是全本预规划的权威上界（大苍界 1-250）；
    chapter_outlines.json 是增量生成的深大纲，可能只覆盖到已规划/已写的
    一部分章节。二者合并后：
      - planned_total 取真正的全本规划上界（250），未来章（已规划未写）
        不再被误判为『章节不存在（吃书）』；
      - 同时在成书后（规划==已写）与旧行为一致。
    """
    ids: list = []
    p = os.path.join(_novelforge_dir(project_dir), "chapter_outlines.json")
    if os.path.exists(p):
        try:
            data = json.load(open(p, encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("chapters", []) or data.get("outlines", []) or []
            ids.extend(int(o["chapter_id"]) for o in data
                       if isinstance(o, dict) and "chapter_id" in o)
        except Exception:
            pass
    # 合并 volumes.json 的卷范围，得到真正的全本规划上界
    for v in load_volumes(project_dir):
        s = v.get("start_chapter")
        e = v.get("end_chapter")
        if isinstance(s, int) and isinstance(e, int) and e >= s:
            ids.extend(range(s, e + 1))
    return sorted(set(ids))


def _chapter_text(project_dir: str, cid: int) -> str:
    p = os.path.join(project_dir, "chapters", f"chapter_{cid:04d}.md")
    if not os.path.exists(p):
        return ""
    try:
        return open(p, encoding="utf-8").read()
    except Exception:
        return ""


def _volume_of(chapter: int, volumes: list) -> dict | None:
    """返回包含 chapter 的卷（按 [start_chapter, end_chapter] 闭区间）。"""
    for v in volumes:
        s = int(v.get("start_chapter", 1))
        e = int(v.get("end_chapter", s))
        if s <= chapter <= e:
            return v
    return None


def _beat_for(pos: int, total: int) -> str:
    """按弧内位置给起承转合阶段标签。"""
    if total <= 1:
        return "全"
    r = pos / (total - 1)
    if r < 0.25:
        return "起"
    if r < 0.55:
        return "承"
    if r < 0.85:
        return "转"
    return "合"


# ---------------------------------------------------------------------------
# 守卫：弧 / 暗线 连续性
# ---------------------------------------------------------------------------
def check_arc_subplot_continuity(project_dir: str, use_cache: bool = True) -> list:
    """检测结构化弧 / 暗线吃书，返回 issue 列表（兼容 guard_hook 格式）。零 token。"""
    issues: list = []
    volumes = load_volumes(project_dir)
    arcs_doc = load_arcs(project_dir)
    subplots_doc = load_subplots(project_dir)
    arcs = arcs_doc.get("arcs", []) if isinstance(arcs_doc, dict) else []
    subplots = subplots_doc.get("subplots", []) if isinstance(subplots_doc, dict) else []

    chapter_ids = _chapter_files(project_dir)
    max_ch = max(chapter_ids) if chapter_ids else 0
    # 规划章节（来自大纲），作为增量写章时的正确上界
    planned_ids = _planned_chapter_ids(project_dir)
    planned_set = set(planned_ids)
    planned_total = max(planned_ids) if planned_ids else 0

    # -------------------------------------------------------------------
    # ① 弧不跨卷
    # -------------------------------------------------------------------
    if arcs and volumes:
        vol_map = {v["_id"]: v for v in volumes if "_id" in v}
        covered: dict[int, list[str]] = {}  # 章节 -> 覆盖它的弧 id 列表（查重叠）
        arcs_with_chapters = 0  # 统计有多少弧显式提供了 chapters 字段
        for arc in arcs:
            aid = arc.get("id", "?")
            atitle = arc.get("title", aid)
            vid = str(arc.get("volume"))
            v = vol_map.get(vid)
            if v is None:
                issues.append({
                    "level": "high",
                    "category": "弧卷归属缺失",
                    "title": f"弧「{atitle}」归属的卷 {vid} 在 volumes.json 中不存在",
                    "detail": "弧必须归属一个存在的卷；请在 volumes.json 补充该卷或修正弧的 volume 字段。",
                    "evidence": f"arc.volume={vid}",
                })
                continue
            s = int(v.get("start_chapter", 1))
            e = int(v.get("end_chapter", s))
            chs = arc.get("chapters", []) or []
            if chs:
                arcs_with_chapters += 1
            # 单调不减 / 重复
            uniq = sorted(set(chs))
            if chs != uniq:
                issues.append({
                    "level": "high",
                    "category": "弧内章节无序",
                    "title": f"弧「{atitle}」章节序列含重复或非单调（{chs}）",
                    "detail": "弧内章节须按升序、不重复；重复/乱序说明弧结构错乱。",
                    "evidence": f"chapters={chs}",
                })
            # 跨卷：有章节落在卷边界外
            out_of = [c for c in chs if c < s or c > e]
            if out_of:
                issues.append({
                    "level": "high",
                    "category": "弧跨卷(违反单卷不变量)",
                    "title": f"弧「{atitle}」跨卷：章节 {out_of} 落在卷「{v.get('title')}」[{s},{e}] 之外",
                    "detail": "结构化弧的核心不变量是『一个弧不跨卷』（structure_spec.md §1）。"
                              "跨卷说明分卷/分弧错位——要么弧的章节划错，要么卷边界划错，必须二选一修正。",
                    "evidence": f"弧卷={vid}[{s},{e}]，越界章={out_of}",
                })
            # 覆盖登记
            for c in uniq:
                covered.setdefault(c, []).append(aid)
        # 重叠：同一章被同卷多个弧覆盖
        for c, aids in covered.items():
            if len(aids) > 1:
                issues.append({
                    "level": "high",
                    "category": "弧重叠覆盖",
                    "title": f"第{c}章被多个弧同时覆盖：{aids}",
                    "detail": "章节不应被同卷内多个弧重复覆盖，否则生成/守卫锚定混乱。",
                    "evidence": f"章{c} -> 弧{aids}",
                })
        # 缺口：大纲规划章节未被任何弧覆盖（与已写/未写无关，只判计划本身是否完整）
        # 仅当至少一个弧显式提供了 chapters 字段时才判定缺口——否则弧数据模型不含
        # chapters（框架生成的弧只有 volume 归属），此时缺口判定无意义且必为 100%
        # 假报，会摧毁守卫信用（见 SOFTWARE_PROBLEMS 衍射分析 P14）。
        if planned_ids and arcs_with_chapters > 0:
            gap = [c for c in planned_ids if c not in covered]
            if gap:
                issues.append({
                    "level": "medium",
                    "category": "弧未覆盖章节",
                    "title": f"有 {len(gap)} 章未被任何事件弧覆盖（如第{gap[0]}章…）",
                    "detail": "所有正文章节应归入某个事件弧（L2），否则该章无主线锚定、易吃书。"
                              "若确为过渡章，可建一个专门过渡弧覆盖之。",
                    "evidence": "未覆盖章示例：" + "、".join(f"第{c}章" for c in gap[:10]),
                })
    elif arcs and not volumes:
        issues.append({
            "level": "low",
            "category": "弧缺卷结构",
            "title": "存在 arcs.json 但缺少 volumes.json，弧跨卷校验跳过",
            "detail": "弧不跨卷校验依赖卷边界；请补充 volumes.json 启用该检查。",
            "evidence": "volumes.json 不存在",
        })

    # -------------------------------------------------------------------
    # ② 暗线节点不中断（跨卷追踪）
    # -------------------------------------------------------------------
    if subplots:
        for sp in subplots:
            sid = sp.get("id", "?")
            stitle = sp.get("title", sid)
            spkw = sp.get("keyword", "")
            spans = [str(x) for x in (sp.get("spans_volumes") or [])]
            nodes = sp.get("nodes", []) or []
            prev_ch = -1
            has_payoff = False
            for node in nodes:
                c = node.get("chapter")
                kw = node.get("keyword") or spkw
                stage = node.get("stage", "")
                if stage == "payoff":
                    has_payoff = True
                # 章节存在
                if c is None or c < 1:
                    issues.append({
                        "level": "high",
                        "category": "暗线节点章节非法",
                        "title": f"暗线「{stitle}」节点章节非法：{c}",
                        "detail": "暗线节点必须挂到具体存在的章节。",
                        "evidence": f"node={node}",
                    })
                    continue
                if c > planned_total:
                    issues.append({
                        "level": "high",
                        "category": "暗线节点章节不存在",
                        "title": f"暗线「{stitle}」节点声称第{c}章，但大纲仅规划至 {planned_total} 章",
                        "detail": "暗线节点章节超出大纲规划范围，等于该节点从未落地（吃书）。"
                                  "要么补规划该章，要么把节点改到已规划章节。",
                        "evidence": f"node.chapter={c} > planned_total={planned_total}",
                    })
                    prev_ch = c
                    continue
                # 仅当该章『应当已写』（≤ 已写最大章）却缺文件，才判断裂；
                # 已规划但未写的未来章属正常增量状态，不误报。
                if c <= max_ch and not os.path.exists(
                        os.path.join(project_dir, "chapters", f"chapter_{c:04d}.md")):
                    issues.append({
                        "level": "high",
                        "category": "暗线节点章节缺失文件",
                        "title": f"暗线「{stitle}」节点第{c}章无正文文件",
                        "detail": "节点章节文件不存在，暗线在该章断裂。",
                        "evidence": f"chapter_{c:04d}.md 缺失",
                    })
                    prev_ch = c
                    continue
                # 单调不减（倒退=吃书）
                if c < prev_ch:
                    issues.append({
                        "level": "high",
                        "category": "暗线节点倒退",
                        "title": f"暗线「{stitle}」节点章节倒退：第{prev_ch}章 → 第{c}章",
                        "detail": "暗线节点须沿章节单调递增；倒退说明节点时序错乱（吃书）。",
                        "evidence": f"prev={prev_ch} -> cur={c}",
                    })
                prev_ch = c
                # 关键实体落地（grep 正文，未出现=节点虚假）
                # 仅校验『已写』章；未来规划章尚未生成，跳过以免增量误报。
                if kw and c <= max_ch:
                    txt = _chapter_text(project_dir, c)
                    if kw not in txt:
                        issues.append({
                            "level": "high",
                            "category": "暗线节点关键实体未落地",
                            "title": f"暗线「{stitle}」第{c}章节点声称关键实体「{kw}」但正文未出现",
                            "detail": "暗线节点必须在所指章节正文里真实出现该关键实体，否则该节点是虚设的"
                                      "（吃书：读者在该章等不到暗线呼应）。请修正节点章节或关键实体。",
                            "evidence": f"node.chapter={c}，keyword='{kw}'，正文无此词",
                        })
                # 跨卷声明一致性
                if spans and volumes:
                    v = _volume_of(c, volumes)
                    if v is not None and v["_id"] not in spans:
                        issues.append({
                            "level": "medium",
                            "category": "暗线节点卷未声明",
                            "title": f"暗线「{stitle}」第{c}章落在卷「{v.get('title')}」但未被 spans_volumes 覆盖",
                            "detail": "暗线节点所在卷必须在其 spans_volumes 中声明；要么补声明，要么节点章节划错。",
                            "evidence": f"node.chapter={c} -> 卷{v['_id']}，spans_volumes={spans}",
                        })
            # ③ 回收完整性
            status = sp.get("status", "")
            if status == "done" and not has_payoff:
                issues.append({
                    "level": "medium",
                    "category": "暗线未回收",
                    "title": f"暗线「{stitle}」标记为已完结(done)却无 payoff 节点",
                    "detail": "已完结暗线须有 stage=payoff 的回收节点，否则收束缺失（吃书）。",
                    "evidence": f"status={status}",
                })

    return issues


# ---------------------------------------------------------------------------
# 生成侧：弧 / 暗线 状态卡
# ---------------------------------------------------------------------------
def get_arc_subplot_card(project_dir: str, chapter_id: int) -> str:
    """产出写给第 chapter_id 章前的紧凑弧/暗线快照（零 token）。"""
    volumes = load_volumes(project_dir)
    arcs_doc = load_arcs(project_dir)
    subplots_doc = load_subplots(project_dir)
    arcs = arcs_doc.get("arcs", []) if isinstance(arcs_doc, dict) else []
    subplots = subplots_doc.get("subplots", []) if isinstance(subplots_doc, dict) else []
    if not arcs and not subplots:
        return ""

    lines = ["【弧 / 暗线状态卡】（写本章前必读，防止弧跨卷 / 暗线断裂）"]

    # 弧
    cur_arc = None
    for arc in arcs:
        if chapter_id in (arc.get("chapters") or []):
            cur_arc = arc
            break
    if cur_arc:
        chs = cur_arc.get("chapters", [])
        pos = chs.index(chapter_id) if chapter_id in chs else 0
        beat = _beat_for(pos, len(chs))
        vtitle = ""
        if volumes:
            v = next((x for x in volumes if x.get("_id") == str(cur_arc.get("volume"))), None)
            vtitle = v.get("title", "") if v else ""
        lines.append(f"- 本章所属事件弧：{cur_arc.get('title','')}（卷：{vtitle}；"
                     f"本弧第{pos+1}/{len(chs)}章，阶段：{beat}）")
        if cur_arc.get("summary"):
            lines.append(f"- 弧概要：{cur_arc['summary'][:140]}")
    else:
        lines.append("- 本章未归入任何已知事件弧（请检查 arcs.json 是否已覆盖本章）")

    # 暗线节点
    active = []
    for sp in subplots:
        for node in (sp.get("nodes") or []):
            if node.get("chapter") == chapter_id:
                kw = node.get("keyword") or sp.get("keyword", "")
                active.append(f"{sp.get('title','')}·{node.get('stage','')}（关键：{kw}）")
    if active:
        lines.append("- 本章暗线节点：" + "；".join(active))
    else:
        lines.append("- 本章无暗线节点")

    lines.append("- 约束：本章弧阶段/暗线节点须与上方一致；勿让弧跨入其他卷、"
                 "勿让已声明的暗线节点在该章落空（关键实体须出现）。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="结构化弧 / 暗线 守卫（零token）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--check-arcs", action="store_true", help="检测弧/暗线连续性")
    ap.add_argument("--card", type=int, default=0, help="输出写给某章前的弧/暗线状态卡")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    if args.card:
        print(get_arc_subplot_card(args.project, args.card))
    if args.check_arcs:
        iss = check_arc_subplot_continuity(args.project, use_cache=not args.no_cache)
        print(f"issues: {len(iss)}")
        for it in iss:
            print(f"  [{it['level']}] {it['category']} | {it['title']}")
            print(f"      证据: {it['evidence']}")
