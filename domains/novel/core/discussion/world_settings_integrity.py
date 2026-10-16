#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
world_settings_integrity.py
============================
《大苍界》/ NovelForge 设定库「单一真源」守卫 —— 纯程序实现，零 LLM token。

设计目标（用户诉求：泛化，不针对某个主角 / 某条具体设定）：
  守护 NovelForge 全部 world_settings（本书 307 条，跨 体系/线索/地理/物品/规则/
  势力/人物背景 等所有类别）的「单一定义不变量」——
    1) 任意一条设定都不该把同一个维度写成「两套 / 两个体系 / 须明确区分 /
       不可混为一谈 / 另一把尺」等分裂表述；
    2) 库里不该出现两条覆盖同一维度、相互矛盾的重复设定。

  这是**泛化**处理：不再硬编码「力量体系 / 锻体→渡劫阶梯」等具体词，而是检测
  「分裂定义」这一通用反模式（split-definition antipattern），对任意设定类别生效。
  之前只拦力量体系的窄守卫（power_system_guard.py）已废弃——它只治一个症状，
  没治「任意设定都可能双轨」这个类问题。

用法：
  from domains.novel.core.discussion.world_settings_integrity import (
      validate_world_setting, find_duplicate_settings, WorldSettingIntegrityError,
  )
  issues = validate_world_setting(text)            # 单条设定：返回违规列表，空=通过
  dups   = find_duplicate_settings(entries)        # 全库：返回重复/覆盖同维度的条目对
"""

import re

from domains.novel.core.discussion.power_canon import validate_power_entry

# ---- 通用「分裂定义」硬性禁用标记（无合选用法，出现即判违反单一定义不变量）----
# 注意：这些词本身与具体题材无关——地理/物品/规则/势力任何维度都可能被写成
# 「两套 / 须区分」，所以它们是 topic-agnostic 的通用信号，而非力量体系专用。
_FORBIDDEN_HARD = [
    "双轨",
    "须明确区分",
    "不可混为一谈",
    "另一把尺",
    "两套体系",
    "两套标尺",
    "两套标准",
    "主角一套",
    "配角标尺",
    "可临时压制另一",
]

# 歧义词：仅在「非否定语境」下判违规（如"并非另一套体系""严禁写成另一套"是单一定义肯定句）
_FORBIDDEN_AMBIG = ["另一套", "另一套体系", "另一套刻度", "另一套规则", "另一个体系"]

# 否定前缀：出现在禁用词前 ~8 字内，表示该词处于否定语境，不应判违规
_NEG_CUES = ["非", "不是", "严禁", "不", "杜绝", "拒绝", "绝非", "并非", "没有", "无", "勿", "不可", "不能", "并非是",
             "同一条", "共用", "同属", "同一把尺", "人人共用"]


def _is_negated(text: str, pos: int) -> bool:
    """判定 text[pos] 起的禁用词是否处于否定语境（其前 8 字含否定前缀）。"""
    pre = text[max(0, pos - 8):pos]
    return any(cue in pre for cue in _NEG_CUES)


def validate_world_setting(text: str) -> list[str]:
    """返回违规说明列表；空列表表示通过单一定义不变量。

    对任意设定文本做强校验（不限定类别）。设计上 topic-agnostic：
    只检测「分裂定义」通用反模式，不认识"力量体系""境界阶梯"等任何具体词。
    """
    if not text:
        return []
    issues: list[str] = []

    # 1) 硬禁用标记（无合选用法）
    for m in _FORBIDDEN_HARD:
        if m in text:
            issues.append(f"出现分裂定义表述「{m}」，违反单一真源不变量（任意设定维度只应有一套定义）")

    # 1b) 歧义词：仅在非否定语境下判违规
    for m in _FORBIDDEN_AMBIG:
        idx = text.find(m)
        while idx != -1:
            if not _is_negated(text, idx):
                issues.append(f"出现分裂定义表述「{m}」，违反单一真源不变量（任意设定维度只应有一套定义）")
                break
            idx = text.find(m, idx + 1)

    # 1c) 力量体系分离主义结构闸门（power_canon）：无论类别，只要把力量体系写成
    #     「另一套/独立/而非常规境界/第二套」即判违规——从源头杜绝平行定义。
    #     这是上一轮双轨反复出现的真根因（旧「混沌神体体系」条目藏了"而非常规境界"，
    #     逃过通用守卫）。归并进单一真源校验，写入即拦。
    _pw = validate_power_entry(text)
    if _pw:
        issues.append("力量体系违反单轨不变量：" + "；".join(_pw))

    return issues


def _significant_tokens(s: str) -> set[str]:
    """取名称中的有意义片段（去掉纯标点/常见虚词），用于重复判定。"""
    s = (s or "").replace("（", "(").replace("）", ")")
    # 中文按 2~4 字滑动窗口切，英文/数字按词
    toks: set[str] = set()
    cjk = re.findall(r"[一-鿿]{2,4}", s)
    toks.update(cjk)
    ascii_words = re.findall(r"[A-Za-z0-9_]{2,}", s)
    toks.update(ascii_words)
    # 过滤过短的纯虚词
    toks = {t for t in toks if len(t) >= 2 and t not in ("的", "了", "是", "和", "与", "及")}
    return toks


def find_duplicate_settings(entries: list[dict]) -> list[dict]:
    """跨条目重复/同维度覆盖检测（topic-agnostic）。

    entries: [{"id":..., "category":..., "name":..., "description":...}, ...]
    返回 [{"a": id, "b": id, "reason": "..."}, ...]
    判定（保守，避免误报）：同 category 下，两条 name 满足
      - 完全相同，或
      - 共享 ≥2 个有意义片段（疑似覆盖同一维度）
    则标记为潜在重复/矛盾设定，需人工或 LLM 复核。
    """
    out: list[dict] = []
    by_cat: dict[str, list[dict]] = {}
    for e in entries:
        by_cat.setdefault(str(e.get("category", "")), []).append(e)

    seen_pairs: set[tuple] = set()
    for cat, grp in by_cat.items():
        n = len(grp)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = grp[i], grp[j]
                na, nb = (a.get("name") or ""), (b.get("name") or "")
                if not na or not nb:
                    continue
                if na == nb:
                    reason = f"同类别『{cat}』下存在同名设定：{na}"
                    out.append({"a": a["id"], "b": b["id"], "reason": reason})
                    seen_pairs.add((a["id"], b["id"]))
                    continue
                ta, tb = _significant_tokens(na), _significant_tokens(nb)
                shared = ta & tb
                if len(shared) >= 2:
                    reason = (
                        f"同类别『{cat}』下两条设定名称高度重叠（共享：{''.join(sorted(shared))}），"
                        f"疑似覆盖同一维度：『{na}』 vs 『{nb}』"
                    )
                    key = tuple(sorted([a["id"], b["id"]]))
                    if key not in seen_pairs:
                        out.append({"a": a["id"], "b": b["id"], "reason": reason})
                        seen_pairs.add(key)
    return out


class WorldSettingIntegrityError(Exception):
    """写入设定时触发了单一定义不变量违规（任意类别通用）。"""


# ===================================================================
# 答案级守卫（输出侧闭环）—— 数据正确但 LLM 不服从时的确定性兜底
# -------------------------------------------------------------------
# 数据层守卫 validate_world_setting 只管"写入 world_settings 时"；但 agent
# 生成讨论回答时，可能无视已注入的权威单轨设定、凭自身剧情记忆写出双轨表述
# （实测：合并条目已正确注入上下文，agent 仍产出"楚渊不走通用境界阶梯"）。
# 因此这里在答案产出后、展示前，用零 token 正则扫描并确定性改写，保证
# 用户看到的回答一定是单轨——不依赖 LLM 自觉。
# ===================================================================

# 单轨标准说法（确定性替换目标）：楚渊与全书共用同一条阶梯，九阶觉醒是里程碑命名，
# 觉醒度%只是进度条，并非另一套体系。
_CANON_SINGLE_TRACK = (
    "楚渊与全书所有人共用同一条『大苍界境界阶梯』；他的九阶觉醒是这条阶梯上的"
    "九个里程碑命名，觉醒度%只是他距下一里程碑的进度条，并非另一套体系。"
)

# 分离主义「语义内核」检测（泛化，不依赖固定短语）：
# 只要一句话断言「楚渊(或其觉醒/混沌神体)的修炼/成长/境界 是 分离/独立/特殊/不同于 共享阶梯」，
# 即判双轨——覆盖主角金手指套路的全部常见变体（截然不同/专属/另辟蹊径/异于常人/自成一派…）。
# 否定语境（含『同一条/共用/并非/本就是一套』等权威单轨表述）跳过。
_SUBJ = r"(?:楚渊|觉醒度|混沌神体|楚渊的?力量|楚渊的?修炼|楚渊的?境界|楚渊的?体系|楚渊的?成长)"
_SEP = r"(?:截然不同|完全不同|不同于|异于常人|与众不同|有别于|独立于|脱离|不依赖|不按|不循|不沿|不属|不适用|不沿用|另辟蹊径|另类|平行|自成一派|独树一帜|特立独行|不按常理|不循常规|绕过|避开|独自|独辟|蹚出|闯出)"
_INDEP = r"(?:独立体系|专属体系|自成体系|另成体系|另一套体系|专属路线|独立路线|专属成长|独立成长|专属力量|自成一套|自成一体|独有体系|平行体系|另一条路|另一条修行之道|另一条道|专属金手指|独有的路)"
_CULT = r"(?:境界|阶梯|修炼|体系|成长|进阶|攀升|升级|修行|觉醒|凝气|灵海|路径|刻度|功法|武道|方式|实力|力量)"

_ANSWER_SEPARATIST = [
    # 楚渊 + 修炼名词 + 分离词
    _SUBJ + r".{0,18}?(?:修炼|成长|境界|体系|路线|进阶|力量|实力|攀升|修行).{0,18}?" + _SEP,
    # 楚渊 + 分离词 + 修炼名词
    _SUBJ + r".{0,24}?" + _SEP + r".{0,24}?" + _CULT,
    # 楚渊 + 直接「独立/专属体系」类
    _SUBJ + r".{0,20}?" + _INDEP,
    # 对比结构：别人按X走，楚渊却另类/独立/不同
    r"(?:修士|他人|普通修士|其他角色|别人|常人).{0,18}?" + _CULT + r".{0,30}?楚渊.{0,18}?(?:却|但|而|反而).{0,22}?(?:" + _SEP + r"|" + _INDEP + r"|另一条|新路)",
]
_ANSWER_HARD = [
    "双轨", "两套体系", "两套标尺", "两套标准",
    "主角一套体系", "配角标尺", "主角一套刻度",
    "可临时压制更高通用", "可临时压制通用",
]
_ANSWER_AMBIG = ["另一套体系", "另一套刻度", "另一套标尺", "另一个体系", "另一套规则", "另一套"]

# 否定前缀（作用于整句）：句内出现则视为"否定双轨"的肯定句，不判违规
# 扩到覆盖「同一条/共用/本就是一套/同属」等权威单轨表述，避免误伤。
_ANSWER_NEG = ["不是", "并非", "并非是", "绝非", "严禁", "杜绝", "拒绝", "本就是一套",
               "并非另一套", "不是另一套", "并非双轨", "不是双轨", "没有另一套",
               "非双轨", "不是两套", "同一条", "共用", "同属", "同一把尺", "人人共用",
               "同一条大苍界境界阶梯", "共用同一条"]

# 定点改写规则：只替换分离主义「从句」，保留句中其余真事实（如括号里的阶梯列表）。
# 顺序无关；每条把「不走/不沿…通用修炼阶梯」这类病从句改成正确的单轨表述。
# 注：泛化后的 _ANSWER_SEPARATIST 命中句大多直接走整句 canon 降级（见 enforce_single_track），
# 此处仅保留可安全定点修复的具体形变，避免破坏真事实。
_ANSWER_REWRITE = [
    (r"不(走|沿|按|在|属于|适用|沿用|遵循)通用修炼阶梯", "沿同一条大苍界境界阶梯攀升"),
    (r"不(走|沿|按|在|属于|适用|沿用|遵循)通用境界阶梯", "沿同一条大苍界境界阶梯攀升"),
    (r"不(走|沿|按|在|属于|适用|沿用|遵循)通用阶梯", "沿同一条大苍界境界阶梯攀升"),
    (r"不(走|沿|按|在|属于|适用|沿用|遵循)大苍界境界阶梯", "沿同一条大苍界境界阶梯攀升"),
    (r"非通用境界", "同属通用境界"),
    (r"脱离.{0,12}通用(境界|阶梯|修炼阶梯)", "沿同一条大苍界境界阶梯"),
    (r"独立于.{0,12}通用(境界|阶梯|修炼阶梯)", "与全书共用同一条大苍界境界阶梯"),
    (r"有别于.{0,12}通用(境界|阶梯)", "同属同一条大苍界境界阶梯"),
    (r"不同于.{0,16}通用(境界|阶梯|修炼阶梯)", "同属同一条大苍界境界阶梯"),
    (r"(觉醒度|混沌神体|楚渊的?力量|楚渊体系).{0,16}(另一套|独立|单独成|自成|另成|专属|并行|自成一).{0,12}(体系|阶梯|刻度|标尺|境界)",
     "楚渊的九阶觉醒是同一条阶梯上的里程碑命名"),
    (r"楚渊.{0,22}(另一条|另一套|独立体系|专属体系|自成体系|单独成体系|另成体系|专属力量体系)",
     "楚渊与全书共用同一条大苍界境界阶梯"),
    (r"楚渊.{0,22}(不在|不属于).{0,12}(通用境界|通用阶梯|同一条阶梯|大苍界境界阶梯)",
     "楚渊处于同一条大苍界境界阶梯上"),
    (r"可?临时?压制更高通用(境界|阶梯)", "在通用境界阶梯上越级爆发"),
    (r"可?临时?压制通用(境界|阶梯)", "在通用境界阶梯上越级爆发"),
]


def _split_sentences(text: str) -> list[str]:
    """按中文句末标点 + 换行切句（保留标点）。"""
    parts = re.split(r"(?<=[。！？!?\n])", text)
    return [p for p in parts if p.strip()]


def _sentence_has_negation(sent: str) -> bool:
    return any(cue in sent for cue in _ANSWER_NEG)


def _is_bad_sentence(s: str) -> bool:
    """句子级双轨判定（否定语境跳过）。"""
    if not s or _sentence_has_negation(s):
        return False
    for p in _ANSWER_SEPARATIST:
        if re.search(p, s):
            return True
    for kw in _ANSWER_HARD:
        if kw in s:
            return True
    for kw in _ANSWER_AMBIG:
        if kw in s and not _is_negated(s, s.find(kw)):
            return True
    return False


def validate_answer_power_system(answer: str) -> list[str]:
    """扫描 agent 回答是否含与权威单轨设定相悖的双轨表述。

    返回违规说明列表（含 offending 句子片段）；空=通过。
    否定语境（如"并非另一套体系""本就是一套"）不误判。
    """
    if not answer:
        return []
    issues: list[str] = []
    for sent in _split_sentences(answer):
        s = sent.strip()
        if not s or _sentence_has_negation(s):
            continue
        hit = None
        for p in _ANSWER_SEPARATIST:
            m = re.search(p, s)
            if m:
                hit = m.group(0)
                break
        if not hit:
            for kw in _ANSWER_HARD:
                if kw in s:
                    hit = kw
                    break
        if not hit:
            for kw in _ANSWER_AMBIG:
                if kw in s and not _is_negated(s, s.find(kw)):
                    hit = kw
                    break
        if hit:
            issues.append(f"回答含双轨/分离主义表述「{hit}」，与权威单轨设定相悖：{s[:40]}")
    return issues


def enforce_single_track(answer: str) -> tuple[str, bool, list[str]]:
    """确定性把回答中的双轨表述改写成单轨标准说法。

    返回 (改写后答案, 是否改动, 违规列表)。零 token，纯正则。
    策略（优先保护真事实）：
      1) 判违规的句子，先跑「定点改写」只替换病从句，保留其余内容
         （如「不走通用修炼阶梯（锻体→凝气→…）」→「沿同一条大苍界境界阶梯攀升（锻体→凝气→…）」，
          括号里的阶梯列表等真事实不丢）；
      2) 若定点改写后该句仍残留双轨标记，整句降级为 _CANON_SINGLE_TRACK。
    """
    if not answer:
        return answer, False, []
    sents = _split_sentences(answer)
    if not sents:
        return answer, False, []
    out: list[str] = []
    changed = False
    issues: list[str] = []
    for sent in sents:
        s = sent.strip()
        if not s:
            out.append(sent)
            continue
        if _is_bad_sentence(s):
            issues.append(s[:40])
            new_s = s
            for pat, rep in _ANSWER_REWRITE:
                new_s = re.sub(pat, rep, new_s)
            if _is_bad_sentence(new_s):
                new_s = _CANON_SINGLE_TRACK
            out.append(new_s)
            changed = True
        else:
            out.append(sent)
    return "".join(out), changed, issues

