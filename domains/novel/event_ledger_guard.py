"""事件账本一致性守卫（扁平事件账本，零 token）。

===== 职责边界（与 arcs / subplots 守卫严格隔离）=====
本模块【只】校验 event_ledger 表（事件对象自身）的：
  A. 窗口落点：事件的计划章节窗口必须落在所属卷边界 [start,end] 内；
  B. 单事件不跨卷：同一事件的窗口不得横跨两个卷（与「单弧不跨卷」对称的不变量）；
  C. 因果顺序：depends_on 的前置事件计划章节不得晚于本事件。

本模块【不】校验（那些是别的守卫的地盘，互不重叠）：
  - 叙事结构归属（arcs.json 的 卷→弧→章、弧不跨卷、弧覆盖无缺口）—— arc_subplot 守卫；
  - 跨卷暗线节点连续性（subplots.json）—— arc_subplot 守卫；
  - 物品/地点/时间线连续性（continuity_state / consistency_guard）—— 各自模块；
  - 事件「是否真在正文落地」：该职责由 assembler 注入事件硬约束 + 写后 advance 状态推进
    + 暗线节点落地守卫共同覆盖，本模块不重复，避免与暗线落地功能重叠、避免误报。

数据来源：仅 event_ledger 表 + volumes.json。
绝不读取 arcs.json / subplots.json / chapters 正文，保证文件隔离、数据隔离、互不影响。
"""

import os
import re
import json
import sqlite3


def _load_volumes(project_dir):
    p = os.path.join(project_dir, ".novelforge", "volumes.json")
    if not os.path.exists(p):
        return []
    try:
        d = json.load(open(p, encoding="utf-8"))
        if isinstance(d, dict):
            return d.get("volumes", [])
        if isinstance(d, list):
            return d
    except Exception:
        pass
    return []


def _chapter_to_volume(cid, vols):
    for v in vols:
        s = int(v.get("start_chapter", 0) or 0)
        e = int(v.get("end_chapter", 0) or 0)
        if s <= cid <= e:
            return v
    return None


def _load_events(project_dir):
    db = os.path.join(project_dir, ".novelforge", "archive.db")
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM event_ledger").fetchall()
    con.close()
    return [dict(r) for r in rows]


def _load_chapter_text(project_dir, cid):
    # 保留以兼容未来扩展；本模块当前不读取正文（落地校验已移至暗线守卫，避免重叠/误报）。
    p = os.path.join(project_dir, "chapters", "chapter_%04d.md" % cid)
    if not os.path.exists(p):
        return ""
    return open(p, encoding="utf-8").read()


def check_event_ledger_continuity(project_dir, use_cache=True):
    """校验扁平事件账本（event_ledger）的时机/因果/落地一致性。零 token。

    返回 issue 列表（与 arc_subplot / continuity_state 同格式）：
      {level, category, title, detail, evidence}
    """
    issues = []
    vols = _load_volumes(project_dir)
    events = _load_events(project_dir)
    if not events:
        return issues

    ev_by_id = {e["id"]: e for e in events}
    has_vol = bool(vols)

    for e in events:
        eid = e.get("id")
        ts = int(e.get("target_start") or 0)
        te = int(e.get("target_end") or 0)
        title = e.get("title") or eid

        # A + B. 窗口落点 & 单事件不跨卷
        if has_vol:
            v_start = _chapter_to_volume(ts, vols)
            if v_start is None:
                issues.append({
                    "level": "high",
                    "category": "事件窗口越界",
                    "title": f"事件「{title}」窗口起点第{ts}章不在任何卷内",
                    "detail": "事件计划窗口起点必须落在某一卷的 [start,end] 内。",
                    "evidence": f"target_start={ts}",
                })
                continue
            v_end = _chapter_to_volume(te, vols)
            if v_end is None or v_end.get("id") != v_start.get("id"):
                end_label = ("卷" + str(v_end.get("id"))) if v_end else "无卷"
                issues.append({
                    "level": "high",
                    "category": "事件跨卷(违反单事件不跨卷)",
                    "title": f"事件「{title}」窗口第{ts}~{te}章横跨不同卷",
                    "detail": "同一事件的计划窗口不得横跨两个卷（与「单弧不跨卷」对称的不变量）。",
                    "evidence": f"target_start={ts}(卷{v_start.get('id')}) target_end={te}({end_label})",
                })
                continue

        # C. depends_on 因果顺序
        dep_raw = e.get("depends_on")
        dep = json.loads(dep_raw) if dep_raw else []
        for d in dep:
            de = ev_by_id.get(d)
            if de and (int(de.get("target_start") or 0)) > ts:
                issues.append({
                    "level": "high",
                    "category": "事件因果逆序",
                    "title": f"事件「{title}」依赖的前置事件「{de.get('title')}」反而更晚",
                    "detail": "depends_on 的前置事件计划章节不得晚于本事件，否则因果链崩。",
                    "evidence": f"{eid} depends_on {d}（前置start={de.get('target_start')} > 本start={ts}）",
                })

    return issues


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    proj = sys.argv[1] if len(sys.argv) > 1 else "."
    iss = check_event_ledger_continuity(proj, use_cache=False)
    print(f"issues: {len(iss)}")
    for it in iss:
        print(f"  [{it['level']}] {it['category']} | {it['title']}")
