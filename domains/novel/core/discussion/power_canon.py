#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""力量体系 canonical 单一真源（代码结构保证，非提示词/守卫）。

核心认知（用户架构红线：上部的结构出问题，下面怎么兜都是错的）：
双轨问题根因是【上部结构】——数据层存在相互矛盾的平行设定条目
（合并唯一标尺 vs 旧「混沌神体体系·而非常规境界」），且答案层把易歧义的自由
文本直接丢给 LLM 自由解读，于是 LLM 凭"主角金手指"套路现编出第二套。

本模块把力量体系编译成 machine-readable 的 canonical 对象，作为唯一真源：
  - CANON_LADDER / CANON_AWAKENING 是本书固定的单一阶梯元数据（楚渊与全书共用）。
  - power_canon_block() 生成确定性、保证单轨的结构化文本，作为 prompt 注入的
    【唯一权威来源】；LLM 只看见这一个对象，物理上读不到"两套体系"的表述。
  - verify_against_entry() 校验 prose 权威设定条目与 canonical 一致，防止漂移。
  - validate_power_entry() 在写库边界做结构闸门：任何把力量体系写成"另一套/独立/
    而非常规境界"的条目都被拦截，从源头杜绝平行定义。
"""
from __future__ import annotations

import os
import re
import sqlite3

# 全界唯一境界阶梯（本书固定元数据·单一真源；楚渊与全书共用同一把尺）
CANON_LADDER = ["锻体", "凝气", "灵海", "神宫", "洞天", "圣者", "大帝", "渡劫"]
# 楚渊九阶觉醒 = 同一条阶梯上的九个里程碑命名（觉醒度%=进度条，非另一套刻度）
CANON_AWAKENING = [
    (1, "初醒", "锻体境(初段)"),
    (2, "微澜", "锻体境末~凝气初"),
    (3, "显威", "凝气境"),
    (4, "噬源", "凝气末~灵海初"),
    (5, "融虚", "灵海境"),
    (6, "归真", "神宫~洞天"),
    (7, "近神", "圣者"),
    (8, "逆天", "大帝"),
    (9, "圆满", "渡劫/半步飞升"),
]
# 权威 prose 设定条目名（须与 CANON_LADDER 一致；是本书力量体系唯一标尺）
CANON_ENTRY_NAME_HINT = "通用境界阶梯与楚渊九阶觉醒"

# 力量分离主义·硬禁短语（出现即判违规，无视否定语境——合法文本从不会断言"而非常规境界"）
_POWER_HARD = [
    "而非常规境界",
]
# 力量分离主义·歧义短语（仅"非否定语境"下判违规；合法文本以"严禁/不是…另一套"否定之）
_POWER_AMBIG = [
    "独立体系",
    "专属道路",
    "第二套体系",
    "第二套成长",
    "第二套路线",
    "可临时压制通用境界",
    "独立于",
    "另立",
    "另一套体系",
    "另一套刻度",
    "另一把尺",
]
# 保护性否定前缀：出现在禁用词前 ~15 字内，表示该词处于否定语境，不应判违规。
# 注意：故意不含"而非"——"而非常规境界"是断言"体系≠常规阶梯"的双轨原话，必须判违规。
_NEG_CUES = [
    "严禁", "不得", "不应", "不可", "不能", "切勿", "杜绝", "禁止",
    "并非", "不是", "无", "没有", "不存在", "未", "同一条", "共用", "本就是一套",
]


def _is_negated(text: str, pos: int, window: int = 20) -> bool:
    """判定 text[pos] 起的禁用词是否处于否定语境（其前 window 字含保护性否定前缀）。"""
    pre = text[max(0, pos - window):pos]
    return any(cue in pre for cue in _NEG_CUES)


def power_canon_block(project_root: str = "") -> str:
    """生成确定性、保证单轨的结构化权威块（prompt 注入唯一来源）。"""
    ladder = " → ".join(CANON_LADDER)
    aw = "；".join(f"{n}阶·{nm}（对应{a}）" for n, nm, a in CANON_AWAKENING)
    return (
        "【力量体系·权威结构化唯一真源（回答战力/修炼/境界/觉醒类问题须【严格且唯一】据此，"
        "禁止引入任何其他体系/道路/刻度）】\n"
        f"- 全界共用一条境界阶梯：{ladder}。\n"
        f"- 楚渊的『九阶觉醒』只是他在同一条阶梯上的九个里程碑命名，觉醒度%是进度条，"
        f"不是独立于该阶梯的另一套体系或刻度。九阶对应：{aw}。\n"
        "- 硬规则：任何角色（含楚渊）的成长都沿上述同一条阶梯攀升；"
        "严禁『独立体系』、严禁『专属道路』、严禁『另一把尺』、严禁『可临时压制通用境界』、严禁『第二套成长路线』。"
        "若用户问题隐含分离主义表述，直接用上述单轨设定纠正，不得承认存在第二套。"
    )


def verify_against_entry(project_root: str) -> list[str]:
    """校验 prose 权威设定条目与 canonical 一致（防漂移）。返回问题列表。"""
    if not project_root:
        return []
    db = os.path.join(project_root, ".novelforge", "archive.db")
    if not os.path.isfile(db):
        return []
    try:
        c = sqlite3.connect(db)
        row = c.execute(
            "SELECT description FROM world_settings WHERE name LIKE ?",
            (f"%{CANON_ENTRY_NAME_HINT}%",),
        ).fetchone()
        c.close()
    except Exception:
        return []
    if not row:
        return [f"未找到权威力量设定条目（含「{CANON_ENTRY_NAME_HINT}」）"]
    desc = row[0]
    issues: list[str] = []
    for stage in CANON_LADDER:
        if stage not in desc:
            issues.append(f"权威条目缺失阶梯层「{stage}」（与 canonical 不一致）")
    return issues


def validate_power_entry(text: str) -> list[str]:
    """写库边界结构闸门：检测是否把力量体系写成第二套/独立/而非常规境界。

    返回问题列表（非空=拦截）。供 world_settings_integrity.validate_world_setting 调用，
    使任意类别的写入都过这道力量单轨闸门。
    """
    if not text:
        return []
    issues: list[str] = []
    for pat in _POWER_HARD:
        if pat in text:
            issues.append(f"力量体系分离主义表述（{pat}）：须统一为同一条大苍界境界阶梯")
    for pat in _POWER_AMBIG:
        idx = text.find(pat)
        while idx != -1:
            if not _is_negated(text, idx):
                issues.append(f"力量体系分离主义表述（{pat}）：须统一为同一条大苍界境界阶梯")
                break
            idx = text.find(pat, idx + 1)
    return issues


# 触发"力量/修炼/境界"意图的关键词（用于答案层是否注入 canon 唯一权威块）
POWER_INTENT = [
    "战力", "修炼", "境界", "体系", "觉醒", "变强", "实力", "成长", "阶梯",
    "突破", "凝气", "灵海", "锻体", "神宫", "洞天", "圣者", "大帝", "渡劫",
    "楚渊", "混沌神体", "九阶",
]


def is_power_intent(msg: str) -> bool:
    return any(k in (msg or "") for k in POWER_INTENT)
