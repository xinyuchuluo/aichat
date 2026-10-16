#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
continuity_state.py —— 连续性状态卡（gen↔guard 共享，零 token）
================================================================

根因（用户 2026-07-16 点明）：
    一致性守卫原本只有「名词事实模型」(FactLedger: 物品来源/人物生死/设定硬事实)，
    完全没有「时间/事件状态模型」。于是：
      - 同一物品两章各获取一次（第3、4章各采一遍黑心莲）→ 来源检查反而通过，抓不到；
      - 同一 deadline 在不同章被写成不同相对偏移（第3章「三天后」、第4章「明天」）→ 抓不到；
      - 地点无过渡跳变（第4章开头已在矿洞醒来，结尾才坠入矿洞）→ 抓不到。
    生成侧（assembler）也只注入了「高层脊柱 + 前瞻待办 + 末10条流水」，唯独缺一张
    「主角现在在第几天、人在哪、东西拿到没」的状态快照，LLM 只能靠猜 → 吃书。

本模块用一张 gen↔guard 共享的紧凑状态卡解决：
    - 抽取（零 token 正则）：每章日锚点/主角位置/已获物品/待办 deadline/微状态；
    - 持久化：.novelforge/continuity_state.json（轻量、零 DB 耦合、沙箱可跑）；
    - 生成侧：get_state_card() 产出 ~300-450 token 快照，注入 assembler 高优先级块；
    - 守卫侧：check_continuity() 检测「同物重复获取 / 相对 deadline 打架 / 地点跳变」；
    - 大纲侧：check_outline_continuity() 直接查 chapter_outlines.json 相邻章的状态交接
              （治「大纲有问题」这个根因——生成前就发现规划层错位）。

全程不调用 LLM，零 token。修正只报警/写待办，绝不读全文、绝不重写正文。
"""
from __future__ import annotations

import os
import re
import json
import datetime
import logging

logger = logging.getLogger("continuity_state")

# ---------------------------------------------------------------------------
# 中文数字 → int
# ---------------------------------------------------------------------------
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000}
_REL_DAY = {"明天": 1, "次日": 1, "翌日": 1, "隔天": 1, "大后天": 3,
            "后天": 2, "当日": 0, "当天": 0, "当日": 0}


def cn_to_int(s: str) -> int:
    """把「三」「十二」「二十」等中文数词转 int；纯阿拉伯直接 int。"""
    s = (s or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    # 含阿拉伯混合如「3天」已在调用前切走，这里只处理纯中文
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            v = _CN_DIGIT[ch]
            if v == 10 or v == 100 or v == 1000:
                cur = 1 if cur == 0 else cur
                total += cur * v
                cur = 0
            else:
                cur = v
        else:
            # 遇到非数字（如「天」「日」）时把 cur 结算
            total += cur
            cur = 0
    return total + cur


# ---------------------------------------------------------------------------
# 抽取正则
# ---------------------------------------------------------------------------
# 获取动词 + 物品名（最多 12 字，动词与物品间允许量词/虚词穿插）
# v2 拆分：OBTAIN = 真正「从来源取得新物」（采/获得/得到/捡到/挖/夺/抢…）；
#          PICKUP = 「捡起/取出/掏出/拿出/拾起」——首次拾得还是拿出已有的，需结合跨章血脉判定。
_ACQUIRE_RE = re.compile(
    r'(?:采[到得集]|获得|得到|拾得|摘得|挖出|挖到|捡到|取得|收下|收了|'
    r'夺[得到]|抢[得到]|摘下|采下|捞起|掘出)\s*'
    r'(?:[一二三四五六七八九十百零\d]*\s*[枚株朵棵截块片缕柄把只尾管页]\s*)?'  # 量词
    r'(?:的|之)?\s*'                                          # 「的」可穿插（采到的/采得的）
    r'([一-龥]{1,12})'
)
# 拾起/捡起/取出/掏出/拿出：单独一类，由 check_continuity 据「已持有」血脉判定是首次获取还是复用
_PICKUP_RE = re.compile(
    r'(?:取出|掏出|拿出|捡起|拾起)\s*'
    r'(?:[一二三四五六七八九十百零\d]*\s*[枚株朵棵截块片缕柄把只尾管页]\s*)?'
    r'(?:的|之)?\s*'
    r'([一-龥]{1,12})'
)
# 物品名必须以具体名词后缀结尾，或落在已知物品集合，避免「得到消息/获得信任」误报
_ITEM_TAILS = set(
    "莲花草果丹珠石铁剑刀书卷佩钥匙牌符图碎片残页根木晶骨甲羽鳞血液体"
    "钉卷轴残卷木盒玉简阵图殿壁简玦环链玉骨角筋砂粉蜜乳泉"
)
_KNOWN_ITEMS = {"黑心莲", "碎片", "钥匙", "古殿壁画", "碎日壁画", "玉佩",
                "镇魂钉", "残卷", "血书", "灵兽谷", "西谷矿洞", "矿洞",
                "黑水潭", "守魂钉", "断魂刀", "养魂木"}
# 「把/将 + 物品 + 交付动词」结构：先把宾语从把字句里抽出（如「把黑心莲交给」→ 黑心莲）
_BA_RE = re.compile(
    r'(?:把|将)\s*([一-龥]{1,12}?)\s*(?:交|交付|送|递交|呈交|还给|还于|给|呈|上交|献上)'
)

# deadline：相对偏移 + 交/送/给 + 宾语（subject）
# 保留旧式（交付动词紧挨）供大纲校验复用；章节级改用下方放宽版 _extract_deadlines。
_DEADLINE_RE = re.compile(
    r'(?:'
    r'(明天|后天|次日|翌日|隔天|大后天|当日|当天)'           # 相对日词
    r'|([一二三四五六七八九十百零\d]+)\s*[天日]后?'          # 数词 + 天/日 + 后?
    r')\s*[,，。、]?\s*'
    r'(?:之内|内|之內|左右|约)?\s*'
    r'(?:把|将|着|了)?\s*'
    r'([一-龥]{1,12}?)\s*'                                  # subject（交/送的宾语）
    r'(?:交|交付|送|递交|呈交|还给|还于|给|呈|上交|献上)'     # 交付动词
)

# ---- 章节级 deadline 放宽抽取（根治「三天后我见不到黑心莲」「三天后赵平会等」漏抽）----
# 只认「相对时间词 + 后文事件主体」，不强制紧跟交付动词。
_DEADLINE_CTX_RE = re.compile(
    r'(?:'
    r'(明天|后天|次日|翌日|隔天|大后天|当日|当天)'           # 相对日词
    r'|([一二三四五六七八九十百零\d]+)\s*[天日]后?'          # 数词 + 天/日 + 后?
    r')'
)
# deadline 指向的事件主体候选（出现于相对词后 30 字内即记为该事件）
_DEADLINE_SUBJECTS = ["黑心莲", "赵平", "石门", "玉佩", "矿洞", "灵兽谷", "兽栏",
                      "演武场", "残碑", "断魂刀", "镇魂钉", "西谷", "外务堂",
                      "黑水潭", "古殿", "碎日壁画", "镇魂阵", "玉骨"]
# 事件归一：见赵平 / 去兽栏 = 同一场杀局（showdown），与「交黑心莲」交付是两件不同的事，
# 故 赵平→兽栏，不并入 黑心莲。
_DEADLINE_ALIAS = {"赵平": "兽栏", "外务堂": "兽栏"}


def _offset_to_word(n: int) -> str:
    """把天数偏移转成中文相对词，用于给正文修正建议。"""
    if n <= 0:
        return "当天"
    if n == 1:
        return "明天"
    if n == 2:
        return "两天后"
    if n == 3:
        return "三天后"
    return f"{n}天后"


# 承诺类动词：出现于事件主体之后，才构成真正的 deadline（排除 incidental 提及）
_DEADLINE_ACTION_RE = re.compile(r'交|送|给|还|呈|上交|献上|交付|等|见面|碰头|动手|杀|进|入|前往|赶往|取回|夺回')
# 缺失类：主体之前出现，暗示「到某日前必须拿到/交不出」也算 deadline
_DEADLINE_MISSING_RE = re.compile(r'见不到|看不到|没有|没拿到|交不出|未拿到|不见')

# 一天内相对单位（sub-day）：半日/一个时辰/一盏茶/一炷香/片刻… 表示「当日稍后」，
# offset 记 0；若其后紧跟时段词则记 tod。此类 deadline 不参与跨章绝对日比较，
# 只用于「当日期限已过」的章节内时段校验。
_DEADLINE_SUBDAY_RE = re.compile(
    r'(?:半\s*[日天]'                                   # 半日
    r'|半个?\s*时辰'                                    # 半个时辰
    r'|[一二三四五六七八九十百零\d]+\s*个?\s*时辰'        # N 个时辰
    r'|一\s*盏?\s*茶'                                   # 一盏茶
    r'|一\s*炷?\s*香'                                   # 一炷香
    r'|须臾|片刻|霎时|瞬息|立刻|马上|随即|转瞬|稍后|不久)'
)


def _extract_deadlines(text: str) -> list:
    """放宽版 deadline 抽取（支持「天」与「当日时段」两轴）。

    - 天轴：相对日词（明天/三天后…）→ offset 为天数，参与跨章绝对日比较。
    - 时段轴：sub-day 单位（半日/一个时辰/一盏茶/片刻…）→ offset=0（当日稍后），
      若主体后紧跟时段词则记 tod；此类 deadline 不参与跨章绝对日比较，
      只用于「当日期限已过」的章节内时段校验。

    仅当主体与「交付/会面/进入/必须拿到」绑定才记为 deadline，排除 incidental 提及。
    """
    out: list = []
    seen = set()

    # 收集两类相对锚点：(match, subday, offset, unit)
    anchors: list = []
    for m in _DEADLINE_CTX_RE.finditer(text):
        rel, num = m.group(1), m.group(2)
        if rel:
            offset = _REL_DAY.get(rel, 1)
        elif num:
            offset = cn_to_int(num)
        else:
            continue
        anchors.append((m, False, offset, None))
    for m in _DEADLINE_SUBDAY_RE.finditer(text):
        anchors.append((m, True, 0, m.group(0)))

    anchors.sort(key=lambda a: a[0].start())
    for m, subday, offset, unit in anchors:
        ctx = text[m.end():m.end() + 30]
        subj = None
        s_idx = -1
        # 取上下文中「最早出现」的候选主体（而非列表顺序），避免 recap 句里
        # 靠后的 黑心莲 抢走本应判为「赵平 recap」的 赵平，导致误判为 deadline。
        for s in _DEADLINE_SUBJECTS:
            p = ctx.find(s)
            if p != -1 and (s_idx == -1 or p < s_idx):
                subj = s
                s_idx = p
        if not subj or s_idx < 0:
            continue
        orig_len = len(subj)          # 取别名前的长度，避免 after 窗口错位
        subj = _DEADLINE_ALIAS.get(subj, subj)
        before = ctx[max(0, s_idx - 4):s_idx]
        after = ctx[s_idx + orig_len:s_idx + orig_len + 8]
        is_deadline = (
            re.search(r'(把|将|进|入|前往|赶往)$', before)
            or _DEADLINE_ACTION_RE.search(after)
            or _DEADLINE_MISSING_RE.search(before)
        )
        if not is_deadline:
            continue
        # 时段：相对词后窗口内寻找交付时刻的时段词（day/subday 都记），用于时段轴校验
        tod = None
        tm = _TOD_RE.search(ctx)
        if tm:
            tod = {"label": tm.group(0), "rank": _TOD_MAP.get(tm.group(0), -1)}
        # 同章内同一事件同偏移只记一次
        sig = (offset, subj, subday, unit)
        if sig in seen:
            continue
        seen.add(sig)
        rec = {
            "offset": offset,
            "subject": subj,
            "raw": (m.group(0) + ctx[:12]).strip()[:40],
            "subday": subday,
            "unit": unit,
        }
        if tod:
            rec["tod"] = tod
        out.append(rec)
    return out


# ---- 多粒度时间流：见下方 _infer_time_flow（定义于时段轴之后，因其依赖 _TOD_MAP）----
# 取代生硬的「默认 +1」：跟随小说自身节奏（小时/天/月/年，不定时），
# 无明确时间标记的章诚实标 specified=False（carry-forward），绝不假造 +1。



# ---- 一天内的时段（time-of-day）轴：根治「只建模天、丢掉时段」的盲区 ----
# 时段词 → 序位 rank（0=凌晨 … 6=深夜），用于一天内先后排序与冲突判定。
# 覆盖 12 时辰（子丑寅卯…）与通俗时段词（黄昏/深夜/清晨…）。
# rank 0..6（0=凌晨 … 6=深夜），同组内所有词共享同一 rank（用于一天内排序）
_TOD_RANK = {
    0: ["凌晨", "破晓", "拂晓", "黎明"],
    1: ["清晨", "早晨", "早上", "天亮"],
    2: ["上午"],
    3: ["正午", "午时", "中午"],
    4: ["午后", "下午"],
    5: ["傍晚", "黄昏", "入夜", "薄暮"],
    6: ["夜晚", "夜里", "夜色", "深夜", "三更", "半夜", "更深"],
}
# 12 时辰精确并入对应时段（子/丑=深夜，寅=凌晨，卯=清晨，辰=上午，午=正午，未/申=午后，酉/戌=黄昏，亥=深夜）
_SHICHEN_RANK = {
    "子时": 6, "丑时": 6, "寅时": 0, "卯时": 1, "辰时": 2,
    "午时": 3, "未时": 4, "申时": 4, "酉时": 5, "戌时": 5, "亥时": 6,
}
_TOD_MAP = {}
for _r, _ws in _TOD_RANK.items():
    for _w in _ws:
        _TOD_MAP[_w] = _r
_TOD_MAP.update(_SHICHEN_RANK)
_TOD_RE = re.compile("|".join(re.escape(w) for w in sorted(_TOD_MAP, key=len, reverse=True)))

# ---------------------------------------------------------------------------
# 多粒度时间流（v5：取代生硬 +1，跟随小说自身节奏，不定时）
# ---------------------------------------------------------------------------
# 扫描章首过渡词，返回结构化 {label, delta_days, tod, specified, span}：
#   - subday（半日/时辰/一盏茶/片刻）→ delta_days 小数（<1），当天不跨日；
#   - day   （次日/三日后/数日后）→ 整数天；
#   - month （次月/数月后）→ delta_days≈30*N，展示「X月后」；
#   - year  （次年/一年后）→ delta_days≈365*N，展示「X年后」；
#   - 无明确标记 → specified=False（诚实「未明示」，上游 carry-forward，不假造 +1）。
# 用户核心诉求：小说时间流速按自身节奏，大纲须携带 EXPLICIT 时间点。
_TIME_FLOW_RE = re.compile(
    r'(次日清晨|次日一早|次日早晨|第二天一早|第二天早晨|第二天|次日|翌日|隔天|隔日|大后天|'
    r'当天|当日|当夜|同一[日天]|此时|此刻|'
    r'[一二三四五六七八九十百零\d]+\s*个?\s*月后|次月|下月|数月后|'
    r'一年后|次年|转年|来年|[一二三四五六七八九十百零\d]+\s*年后|'
    r'[一二三四五六七八九十百零\d]+\s*[天日]后|数\s*[天日]后|'
    r'半\s*[日天]|半个?\s*时辰|[一二三四五六七八九十百零\d]+\s*个?\s*时辰|'
    r'一\s*盏?\s*茶|一\s*炷?\s*香|须臾|片刻|霎时|瞬息|立刻|马上|随即|转瞬|稍后|不久)'
)


def _infer_time_flow(text: str) -> dict:
    """推断本章相对上一章的真实时间流（多粒度，跟随小说自身节奏）。

    返回 {label, delta_days, tod:{label,rank}|None, specified, span}：
      span='subday' → delta_days 小数（<1），当天不跨日；
      span='day'    → delta_days 整数天；
      span='month'  → delta_days≈30*N；
      span='year'   → delta_days≈365*N；
      specified=False → 章首无明确时间标记，delta_days=None（上游 carry-forward）。
    """
    head = text[:2000]
    m = _TIME_FLOW_RE.search(head)
    if not m:
        return {"label": "未明示", "delta_days": None, "tod": None,
                "specified": False, "span": None}
    s = m.group(0)
    # 句中弱副词（立刻/随即/稍后…）只是行文衔接，并非章节级时间过渡，
    # 不足以锚定本章时间点 → 视为未明示（carry-forward），不假造推进。
    _WEAK = {"立刻", "马上", "随即", "转瞬", "稍后", "不久", "须臾", "片刻", "霎时", "瞬息"}
    if s in _WEAK:
        return {"label": s, "delta_days": None, "tod": None,
                "specified": False, "span": None}
    # 捕获附带时段（如「次日清晨」→ tod=清晨）
    tod = None
    tm = _TOD_RE.search(s)
    if tm:
        tod = {"label": tm.group(0), "rank": _TOD_MAP.get(tm.group(0), -1)}
    flow_word = s.replace(tod["label"], "") if tod else s

    def _mk(delta, span):
        return {"label": s, "delta_days": delta, "tod": tod,
                "specified": True, "span": span}

    # sub-day
    if re.match(r'半\s*[日天]$', flow_word) or re.match(r'半个?\s*时辰$', flow_word):
        return _mk(0.5, "subday")
    mm = re.match(r'([一二三四五六七八九十百零\d]+)\s*个?\s*时辰$', flow_word)
    if mm:
        return _mk(round(cn_to_int(mm.group(1)) / 12.0, 4), "subday")
    if re.match(r'一\s*盏?\s*茶$|一\s*炷?\s*香$|须臾|片刻|霎时|瞬息|立刻|马上|随即|转瞬|稍后|不久', s):
        return _mk(0.01, "subday")
    # same day
    if re.match(r'当天|当日|当夜|同一[日天]|此时|此刻', flow_word):
        return _mk(0.0, "day")
    # month
    if re.match(r'次月|下月|数月后', flow_word):
        n = 3 if "数月" in flow_word else 1
        return _mk(30.0 * n, "month")
    mm = re.match(r'([一二三四五六七八九十百零\d]+)\s*个?\s*月后$', flow_word)
    if mm:
        return _mk(30.0 * cn_to_int(mm.group(1)), "month")
    # year
    if re.match(r'一年后|次年|转年|来年', flow_word):
        return _mk(365.0, "year")
    mm = re.match(r'([一二三四五六七八九十百零\d]+)\s*年后$', flow_word)
    if mm:
        return _mk(365.0 * cn_to_int(mm.group(1)), "year")
    # day positive
    if re.match(r'次日|第二天|翌日|隔天|隔日|第二天一早|次日清晨|次日早晨|第二天早晨', flow_word):
        return _mk(1.0, "day")
    if flow_word == "大后天":
        return _mk(3.0, "day")
    mm = re.match(r'([一二三四五六七八九十百零\d]+)\s*[天日]后$', flow_word)
    if mm:
        return _mk(float(cn_to_int(mm.group(1))), "day")
    if re.match(r'数\s*[天日]后', flow_word):
        return _mk(3.0, "day")
    return _mk(1.0, "day")  # 兜底（理论上不会到这）


def _infer_time_of_day(text: str) -> dict:
    """推断本章主导时段（章首 600 字优先，章尾 200 字次之）。

    返回 {label, rank}；全文无时段标记 → {label: None, rank: -1}（不强制，避免误报）。
    rank 用于一天内排序：0=凌晨 … 6=深夜。
    """
    head = text[:600]
    tail = text[-200:]
    for seg in (head, tail):
        m = _TOD_RE.search(seg)
        if m:
            w = m.group(0)
            return {"label": w, "rank": _TOD_MAP.get(w, -1)}
    return {"label": None, "rank": -1}


# 章首/章尾位置锚点
_LOC_ANCHOR = r'(?:晨曦|天光|日出|天亮|醒来|睁眼|睁开|迷蒙中|朦胧中|' \
              r'从[\s\S]{1,14}?出来|走出[\s\S]{1,14}?|回到[\s\S]{1,14}?|' \
              r'抵达[\s\S]{1,14}?|进入[\s\S]{1,14}?|来[到至][\s\S]{1,14}?|' \
              r'立于[\s\S]{1,14}?|身处[\s\S]{1,14}?)'
_LOC_TAIL = r'(?:矿洞|洞|谷|殿|潭|林|城|镇|山|崖|峰|府|院|房|屋|庙|宫|塔|' \
            r'路口|路边|边|间|穴|窟|洞府|秘境|境|村|寨|营|帐|厅|堂|道|巷|' \
            r'渊|海|河|湖|泽|原|野|窟|巢|巢穴|废墟|遗迹|壁)'
_LOC_OPEN_RE = re.compile(_LOC_ANCHOR + r'\s*([一-龥]{2,14}?' + _LOC_TAIL + r')')
_LOC_END_RE = re.compile(r'([\s\S]{0,40}?)(?:回到|抵达|进入|来[到至]|走出|离开)\s*([一-龥]{2,14}?' + _LOC_TAIL + r')')

# 章内「已在某地，后又进入某地」矛盾：开头位置 + 后文「进入/坠入同地」
_ENTER_RE = re.compile(r'(?:进入|坠入|坠进|走进|踏入|钻入|落入|跌入|跌进)\s*([一-龥]{2,14}?' + _LOC_TAIL + r')')


def _normalize_item(name: str) -> str:
    """剔除量词/虚词/把字句主语，取物品核心名。"""
    name = name.strip()
    # 去掉前导方向/状态动词，避免「透过矿洞」「沿着溪流」等被当位置
    name = re.sub(r'^(透过|隔着|沿着|顺着|绕过|穿过|离开|走向|望向|看向|迎着|'
                  r'在|从|到|至|来|去|往|向|对)\s*', '', name)
    # 去掉尾部冗余（标点/换行后截断）
    name = re.sub(r'[，。；：、！？\s].*$', '', name)
    # 去掉前导：把/将/我 + 量词/数词 + 的/之
    name = re.sub(r'^(把|将|我|他|她|其|这|那|之|此|些)\s*', '', name)
    name = re.sub(r'^[\d一二三四五六七八九十百零]+\s*[枚株朵棵截块片缕柄把只尾管页]\s*', '', name)
    name = re.sub(r'^(的|之|那|这|其|此|些)\s*', '', name)
    # 再把字句残留（如「我把」被截断后剩「我」）清掉
    name = re.sub(r'^(把|将|我|他|她)\s*', '', name)
    return name


def _clean_loc(name: str) -> str:
    """清理位置名：去前导方向/状态动词、去尾部标点，取核心地名。"""
    return _normalize_item(name)


def _acquire_items_from_text(text: str):
    """从正文抽取本章三类物品动作（零 token）。

    返回 (obtain, pickup, handover)：
      - obtain  : 真正「从来源取得新物」(采/获得/得到/捡到/挖/夺/抢…)
      - pickup  : 捡起/取出/掏出/拿出/拾起——是否首次获取需结合跨章血脉判定
      - handover: 把/将 <物品> 交/送/给/还——必是自己拥有的（复用/转交），非获取
    """
    obtain, pickup, handover = [], [], []
    so, sp, sh = set(), set(), set()
    # 1) 真正获取动词
    for m in _ACQUIRE_RE.finditer(text):
        raw = m.group(1)
        if _is_item(raw):
            nm = _normalize_item(raw)
            if nm and nm not in so:
                so.add(nm)
                obtain.append(nm)
    # 2) 拾起/取出类（需血脉判定）
    for m in _PICKUP_RE.finditer(text):
        raw = m.group(1)
        if _is_item(raw):
            nm = _normalize_item(raw)
            if nm and nm not in sp:
                sp.add(nm)
                pickup.append(nm)
    # 3) 把字句：把 <物品> 交给/送给（复用/转交，非获取）
    for m in _BA_RE.finditer(text):
        raw = m.group(1)
        if _is_item(raw):
            nm = _normalize_item(raw)
            if nm and nm not in sh:
                sh.add(nm)
                handover.append(nm)
    return obtain, pickup, handover


def _is_item(name: str) -> bool:
    n = _normalize_item(name)
    if not n:
        return False
    if n in _KNOWN_ITEMS:
        return True
    # 必须以具体名词后缀结尾
    for t in _ITEM_TAILS:
        if n.endswith(t):
            return True
    return False


# ---------------------------------------------------------------------------
# 同名消歧：bare 泛型名词排除
# ---------------------------------------------------------------------------
# 像「碎片 / 钥匙」这类只有泛型后缀（或缺区分性定语）的词，跨章大概率是不同物品，
# 不应作为「重复获取」判定键。带区分性定语的（暗色金属碎片 / 第四块碎片 / 金色晶石碎片 等）
# 仍参与判定。前缀+后缀型（铁钥匙 / 铜镜）也判为 bare，因为「铁钥匙」在跨章里通常是同一把。
_GENERIC_TAILS_BARE = {
    "碎片", "钥匙", "石", "草", "花", "剑", "珠", "丹", "符", "图", "书",
    "卷", "牌", "链", "环", "钉", "甲", "羽", "鳞", "血", "液", "体", "木", "晶",
    "骨", "角", "筋", "砂", "粉", "蜜", "乳", "泉", "残页", "根", "木盒", "玉简",
    "壁画", "残卷",
}
_GENERIC_PREFIX = {
    "铁", "铜", "木", "石", "金", "银", "玉", "黑", "白", "红", "暗", "青", "蓝",
    "绿", "黄", "紫", "灰", "赤",
}


def _is_bare_generic(name: str) -> bool:
    """物品名是否为「bare 泛型名词」——只有后缀，或「材质/颜色前缀+后缀」，缺乏区分性定语。"""
    n = _normalize_item(name)
    if not n:
        return False
    if n in _GENERIC_TAILS_BARE:
        return True
    if len(n) >= 2 and n[0] in _GENERIC_PREFIX and n[1:] in _GENERIC_TAILS_BARE:
        return True
    return False


def _split_acquired(st: dict):
    """兼容新旧账本：优先用拆分字段，旧格式退化为 acquired 全量。"""
    if "acquired_obtain" in st or "acquired_pickup" in st:
        return st.get("acquired_obtain", []), st.get("acquired_pickup", [])
    return st.get("acquired", []), []


def extract_chapter_state(text: str) -> dict:
    """从单章正文抽取连续性状态（零 token）。"""
    state: dict = {
        "acquired": [],          # 本章获取的物品核心名列表（obtain+pickup，供状态卡）
        "acquired_obtain": [],   # 真正从来源取得新物
        "acquired_pickup": [],   # 捡起/取出类（待血脉判定）
        "retrieved": [],         # 把/将 交/送/给/还（复用/转交，非获取）
        "deadlines": [],         # [{offset, subject, raw}]
        "loc_open": "",          # 章首位置
        "loc_end": "",           # 章尾位置
        "wake_open": False,      # 章首是否「醒来/睁眼」
        "internal_loc_conflict": False,  # 章内地点矛盾
    }
    if not text:
        return state

    # ---- 已获物品（拆分三类，供血脉判定） ----
    o, p, h = _acquire_items_from_text(text)
    state["acquired"] = o + p
    state["acquired_obtain"] = o
    state["acquired_pickup"] = p
    state["retrieved"] = h

    # ---- deadline（放宽抽取：相对时间词 + 后文事件主体） ----
    state["deadlines"] = _extract_deadlines(text)

    # ---- 多粒度时间流（取代生硬 +1：跟随小说自身节奏，含 subday/day/month/year）----
    state["time_flow"] = _infer_time_flow(text)
    df = state["time_flow"].get("delta_days")
    # None = 未明示，上游 carry-forward（build_day_index 不假造 +1）
    state["day_delta"] = df if df is not None else None

    # ---- 本章主导时段（多粒度时间轴的时段维度）----
    state["tod"] = _infer_time_of_day(text)

    # ---- 章首位置 ----
    head = text[:700]
    mo = _LOC_OPEN_RE.search(head)
    if mo:
        loc = _clean_loc(mo.group(1))
        state["loc_open"] = loc
        if re.search(r'醒来|睁眼|睁开|迷蒙中|朦胧中', mo.group(0)):
            state["wake_open"] = True

    # ---- 章尾位置 ----
    tail = text[-700:]
    me = _LOC_END_RE.search(tail)
    if me:
        state["loc_end"] = _clean_loc(me.group(2))

    # ---- 章内地点矛盾：开头已在 P，后文又「进入/坠入 P」 ----
    if state["loc_open"]:
        base = state["loc_open"]
        # 后文（跳过开头 700 字）搜索进入同地
        later = text[700:]
        for em in _ENTER_RE.finditer(later):
            if em.group(1) and (base in em.group(1) or em.group(1) in base):
                state["internal_loc_conflict"] = True
                break

    return state


# ---------------------------------------------------------------------------
# 账本构建 / 持久化
# ---------------------------------------------------------------------------
def _chapter_files(project_dir: str):
    d = os.path.join(project_dir, "chapters")
    if not os.path.isdir(d):
        return []
    out = []
    for fn in os.listdir(d):
        if re.match(r'^chapter_\d{4}\.md$', fn):
            cid = int(fn[8:12])
            out.append((cid, os.path.join(d, fn)))
    out.sort()
    return out


def build_ledger(project_dir: str, use_cache: bool = True) -> dict:
    """扫描全部章节，构建 {chapter_id: state} 账本，持久化到 JSON。"""
    cache = os.path.join(project_dir, ".novelforge", "continuity_state.json")
    ledger: dict = {}
    for cid, path in _chapter_files(project_dir):
        try:
            text = open(path, encoding="utf-8").read()
        except Exception:
            continue
        ledger[cid] = extract_chapter_state(text)

    # 计算近似「绝对日锚点」：以第1章为 Day0，遇到「次日/第二天/翌日」+1
    day = 0
    for cid in sorted(ledger.keys()):
        st = ledger[cid]
        if cid > 1:
            head = ""  # 已在上游抽取；这里仅用简单推进标记
        # 推进：若本章含「次日/第二天/翌日/隔天」开头，day+1（粗粒度）
        # 注意：仅作状态卡展示用，不参与冲突判定
        st["day_index"] = day
    # 顺次推进：每章最多 +1（保守），实际以开头相对词为准在 state_card 提示
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    payload = {
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "ledger": {str(k): v for k, v in ledger.items()},
    }
    try:
        with open(cache, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("连续性状态卡持久化失败（不影响运行）：%s", e)
    return ledger


def load_ledger(project_dir: str, use_cache: bool = True) -> dict:
    cache = os.path.join(project_dir, ".novelforge", "continuity_state.json")
    if use_cache and os.path.exists(cache):
        try:
            data = json.load(open(cache, encoding="utf-8"))
            return {int(k): v for k, v in data.get("ledger", {}).items()}
        except Exception:
            pass
    return build_ledger(project_dir, use_cache=False)


def build_day_index(ledger: dict) -> dict:
    """由每章 time_flow 推出绝对日锚点（第1章=1；章间按真实时间流推进；
    未明示章节 carry-forward 不假造 +1 —— 根治「生硬」与「大纲=章号」）。

    返回浮动天数坐标：sub-day 为小数（当天不跨日），月/年为大数（展示由 resolve_timeline 转义）。
    """
    result: dict = {}
    day = 0.0
    for cid in sorted(ledger.keys()):
        if cid == 1:
            day = 1.0
        else:
            dd = ledger[cid].get("day_delta")
            if dd is None:
                pass  # 未明示：沿用上章时间，不推进（诚实，不假造 +1）
            else:
                day += float(dd)
        result[cid] = day
    return result


def _human_time(abs_day: float, tod: dict | None, span: str | None) -> str:
    """把绝对日坐标转人类可读时间点标签（覆盖 天/月/年）。"""
    tod_lbl = (tod or {}).get("label")
    if span == "year":
        y = max(1, round(abs_day / 365.0))
        return f"{y}年后" + (f"·{tod_lbl}" if tod_lbl else "")
    if span == "month":
        mo = max(1, round(abs_day / 30.0))
        return f"{mo}月后" + (f"·{tod_lbl}" if tod_lbl else "")
    return f"第{int(round(abs_day))}天" + (f"·{tod_lbl}" if tod_lbl else "")


def resolve_timeline(ledger: dict) -> dict:
    """由 ledger 的 time_flow 链累积出每章绝对时间点（跟随真实节奏，不定时）。

    返回 cid -> {abs_day(float), abs_label(str), abs_tod(label|None), specified(bool)}。
    未明示章节 carry-forward（沿用上章 abs_day），abs_label 仍标「未明示」以显式暴露。
    """
    out: dict = {}
    cur = 1.0  # epoch：第1章=第1天（基准锚点）
    cur_tod = None
    first = True
    for cid in sorted(ledger.keys()):
        st = ledger[cid]
        tf = st.get("time_flow") or {"label": "未明示", "delta_days": None,
                                     "tod": None, "specified": False, "span": None}
        if not first and tf.get("delta_days") is not None:
            cur += float(tf["delta_days"])
        first = False
        tod = tf.get("tod") or cur_tod
        span = tf.get("span")
        if not tf.get("specified"):
            lbl = "未明示"
        elif span in ("month", "year"):
            lbl = _human_time(cur, tod, span)
        else:
            lbl = f"第{int(round(cur))}天" + (f"·{tod['label']}" if tod and tod.get('label') else "")
        out[cid] = {"abs_day": cur, "abs_label": lbl,
                    "abs_tod": (tod or {}).get("label"), "specified": tf.get("specified", False)}
        if tod:
            cur_tod = tod
    return out


def build_timeline_index(ledger: dict) -> dict:
    """多粒度时间轴：每章 {day, tod}。

    day 同 build_day_index（绝对日锚点）；tod 取自 state['tod']（时段维度，rank 0..6）。
    守卫时间线校验据此同时比较「天轴」与「时段轴」。
    """
    days = build_day_index(ledger)
    out: dict = {}
    for cid in days:
        tod = ledger[cid].get("tod") or {"label": None, "rank": -1}
        out[cid] = {"day": days[cid], "tod": tod}
    return out


# ---------------------------------------------------------------------------
# 生成侧：状态卡（注入 assembler）
# ---------------------------------------------------------------------------
def get_state_card(project_dir: str, chapter_id: int) -> str:
    """产出写给第 chapter_id 章前的紧凑状态快照（零 token）。"""
    ledger = load_ledger(project_dir, use_cache=True)
    prev_ids = [c for c in ledger if c < chapter_id]
    if not prev_ids:
        return ""
    prev = ledger[max(prev_ids)]

    # 已获物品并集（含本章之前的全部）——用血脉追踪，复用/转交不计入
    acquired_all: list[str] = []
    seen = set()
    held_card: set[str] = set()
    for c in sorted(ledger.keys()):
        if c >= chapter_id:
            break
        ob, pk = _split_acquired(ledger[c])
        for it in ob:
            if _is_bare_generic(it):
                continue
            if it in held_card:
                continue
            if it not in seen:
                seen.add(it)
                acquired_all.append(it)
            held_card.add(it)
        for it in pk:
            if _is_bare_generic(it):
                continue
            if it not in held_card and it not in seen:
                seen.add(it)
                acquired_all.append(it)
            held_card.add(it)
        # retrieved 跳过

    # 重复获取预警（前面章节已获取过的物品）
    cnt: dict[str, int] = {}
    for it in acquired_all:
        cnt[it] = cnt.get(it, 0) + 1
    dup = [k for k, v in cnt.items() if v > 1]

    # 待办 deadline（同一 subject 取最早设定的一条为基准，推导绝对目标日）
    day_index = build_day_index(ledger)
    tl = resolve_timeline(ledger)
    cur_day = day_index.get(chapter_id, 0)
    # 先求每个 subject 的基准绝对目标日（最早设定章）
    dl_base: dict[str, int] = {}
    for c in sorted(ledger.keys()):
        if c >= chapter_id:
            break
        for d in ledger[c].get("deadlines", []):
            if d.get("subday"):
                continue  # 当日稍后类 deadline 非跨章任务，不进状态卡
            if d["subject"] not in dl_base:
                dl_base[d["subject"]] = day_index.get(c, 0) + d["offset"]

    lines = ["【连续性状态卡】（写本章前必读，防止时间线/事件吃书）"]
    loc = prev.get("loc_end") or prev.get("loc_open") or "未知"
    lines.append(f"- 前一章（第{max(prev_ids)}章）结尾主角位置：{loc}")
    ch_tod = prev.get("tod") or {}
    if ch_tod.get("label"):
        lines.append(f"- 前一章主导时段：{ch_tod['label']}（本章时段须与之衔接，"
                     f"勿让『次日清晨』变成『同夜深夜』）")
    cur_card = tl.get(chapter_id, {})
    cur_label = cur_card.get("abs_label", f"第{int(round(cur_day))}天")
    prev_flow = prev.get("time_flow") or {}
    lines.append(f"- 当前时间点：{cur_label}（承接上一章『{prev_flow.get('label','未明示')}』，"
                 f"写本章时所有相对时间词按此推算）")
    if acquired_all:
        lines.append(f"- 截至上章已获得物品：{ '、'.join(acquired_all) }")
    else:
        lines.append("- 截至上章已获得物品：无")
    if dup:
        lines.append(f"- ⚠ 重复获取预警：{ '、'.join(dup) } 已在前面章节获取过，"
                     f"本章切勿再写「采到/获得/得到」同一物品")
    if dl_base:
        dl_lines = []
        for subj, abt in dl_base.items():
            req = abt - cur_day
            req_word = _offset_to_word(req)
            dl_lines.append(f"{subj}（绝对目标第{abt}天，本章若提及须写「{req_word}」）")
        lines.append("- 进行中任务/deadline（按绝对目标日对齐相对词）：" + "；".join(dl_lines))
    lines.append("- 时间线提示：同一事件的相对偏移（如「三天后」）在不同章须前后一致；"
                 "若基准章说「三天后」，本章若隔了一日应说「两天后」，勿说「明天」。")
    lines.append("- 地点提示：本章开头位置须与上一章结尾衔接；勿在开头直接『在矿洞醒来』"
                 "却又在后文『坠入/进入矿洞』。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 守卫侧：冲突检测
# ---------------------------------------------------------------------------
def check_continuity(project_dir: str, use_cache: bool = True) -> list:
    """检测三类连续性吃书，返回 issue 列表（兼容 guard_hook 格式）。"""
    ledger = load_ledger(project_dir, use_cache=use_cache)
    issues: list = []

    # 1) 同物重复获取（v2：跨章血脉追踪 + 动词分型 + bare 泛型排除）
    #    血脉：按章节顺序维护 held 集合。
    #      - acquire_obtain（采/获得/得到…）永远是「取得新物」；若物品已在 held → 真·重复获取
    #        （抓「黑心莲」类 bug：已在 ch3 采过，ch4 又采）。
    #      - acquire_pickup（捡起/取出…）仅当物品尚未在 held 才算首次获取；已在 held → 复用（不报错）。
    #      - retrieved（把/将 交/送/给…）必是自己拥有的，纯复用，跳过（也不改 held，避免「交出后
    #        再获取」被误判）。
    #      - bare 泛型名词（碎片/钥匙…）整体排除出重复获取判定，避免同名不同物误报。
    item_chapters: dict[str, list[int]] = {}
    for c in sorted(ledger.keys()):
        ob, pk = _split_acquired(ledger[c])
        for it in (ob + pk):
            item_chapters.setdefault(it, []).append(c)

    held: set[str] = set()
    flagged: set[str] = set()
    for c in sorted(ledger.keys()):
        ob, pk = _split_acquired(ledger[c])
        # 1a 真正获取动词：已在 held 即真·重复获取
        for it in ob:
            if _is_bare_generic(it):
                continue
            if it in held:
                flagged.add(it)
            else:
                held.add(it)
        # 1b 拾起/取出类：未持有→首次获取；已持有→复用（不报错）
        for it in pk:
            if _is_bare_generic(it):
                continue
            if it not in held:
                held.add(it)
        # 1c retrieved（把/将 交/送/给）：复用，跳过（不改动 held）
    for it in flagged:
        chs = item_chapters.get(it, [])
        issues.append({
            "level": "high",
            "category": "同物重复获取",
            "title": f"「{it}」被重复获取（已在之前章节持有，再次『采到/获得』）",
            "detail": "同一物品在已持有的前提下又被『采到/获得/得到』，属时间线/事件连续性吃书。"
                      "守卫原 FactLedger 只查来源，查不到重复获取。",
            "evidence": "第" + "、".join(str(x) for x in chs) + f"章均涉及获取「{it}」",
        })

    # 2) 相对 deadline 跨章「绝对目标日」一致性（根治 ch3↔ch5 基准漂移）
    #    旧逻辑只比「相对偏移字面值」（都写三天后=视为一致），抓不到基准漂移。
    #    新逻辑：绝对目标日 = 本章日锚点 + 相对偏移；同一事件在 ≥2 章被设定，
    #    若绝对目标日不同 → 报，并给出每章应写的相对词（让正文定向自洽）。
    day_index = build_day_index(ledger)
    subj_recs: dict[str, list] = {}
    for c in sorted(ledger.keys()):
        di = day_index.get(c)
        if di is None:
            continue
        for d in ledger[c].get("deadlines", []):
            if d.get("subday"):
                continue  # 当日稍后类 deadline 不参与跨章绝对日比较（时段轴另查）
            subj = d["subject"]
            tgt = di + d["offset"]
            subj_recs.setdefault(subj, []).append(
                (c, d["offset"], tgt, d.get("raw", ""), d.get("tod")))

    for subj, recs in subj_recs.items():
        if len(recs) < 2:
            continue
        targets = {t for _, _, t, _, _ in recs}
        if len(targets) <= 1:
            continue  # 绝对目标日一致，无需处理
        recs_sorted = sorted(recs, key=lambda x: x[0])
        anchor_c, anchor_off, anchor_tgt, _, _ = recs_sorted[0]
        lines = []
        for c, off, tgt, raw, tod in recs_sorted:
            req = anchor_tgt - day_index.get(c, 0)
            req_word = _offset_to_word(req)
            if tgt == anchor_tgt:
                lines.append(f"第{c}章：写「{off}天后」→绝对第{tgt}天 ✓（与基准第{anchor_c}章一致）")
            else:
                lines.append(f"第{c}章：写「{off}天后」→绝对第{tgt}天 ✗ 基准为第{anchor_tgt}天，"
                             f"本章第{day_index.get(c)}天，应改说「{req_word}」")
        issues.append({
            "level": "high",
            "category": "时间线矛盾(绝对日漂移)",
            "title": f"「{subj}」deadline 跨章基准漂移（绝对目标日不一致）",
            "detail": "同一事件在多个章节被写不同相对偏移，但由于各章日锚点不同，"
                      "解析出的『绝对到期日』相互打架（如 ch3『三天后』与 ch5『三天后』"
                      "本应指向同一天，却因隔了天数而漂移到不同日）。"
                      "修正：以最早设定该 deadline 的章节为基准，后续章按『基准绝对日 − 本章日』"
                      "改写相对词。",
            "evidence": "；".join(lines),
        })

    # 2b) 同日时段冲突（多粒度时间轴：天轴一致，但时段轴在不同章打架）
    #     同一事件在同绝对日的不同章节被写不同「时段」（如一处午时一处深夜）。
    for subj, recs in subj_recs.items():
        by_day: dict[int, list] = {}
        for c, off, tgt, raw, tod in recs:
            if tod and tod.get("rank", -1) >= 0:
                by_day.setdefault(tgt, []).append((c, tod))
        for day, lst in by_day.items():
            chs = {c for c, _ in lst}
            if len(chs) < 2:
                continue  # 仅跨章才算，章内同事件不同段位不算冲突
            ranks = {t["rank"] for _, t in lst}
            if len(ranks) <= 1:
                continue
            lines = [f"第{c}章：『{subj}』期限写在「{t['label']}」（绝对第{day}天）" for c, t in lst]
            issues.append({
                "level": "high",
                "category": "时间线矛盾(同日时段冲突)",
                "title": f"「{subj}」同日 deadline 时段打架（{'/'.join(t['label'] for _, t in lst)}）",
                "detail": "同一事件在同绝对日的不同章节被写了不同的时段，属时间轴时段维度吃书。",
                "evidence": "；".join(lines),
            })

    # 2c) 当日期限已过（章节主导时段晚于 deadline 写明时段）
    for c in sorted(ledger.keys()):
        ch_tod = ledger[c].get("tod") or {}
        cr = ch_tod.get("rank", -1)
        if cr < 0:
            continue
        di = day_index.get(c)
        for d in ledger[c].get("deadlines", []):
            if d.get("subday"):
                continue
            tod = d.get("tod")
            if tod and tod.get("rank", -1) >= 0 and tod["rank"] < cr:
                issues.append({
                    "level": "medium",
                    "category": "时间线矛盾(当日期限已过)",
                    "title": f"第{c}章：『{d['subject']}』期限写「{tod['label']}」但本章已是「{ch_tod.get('label')}」",
                    "detail": "deadline 明确写当日某时段，本章主导时段更晚，等于期限已过，时间轴自相矛盾。",
                    "evidence": f"第{c}章主导时段={ch_tod.get('label')}，deadline 时段={tod['label']}",
                })

    # 3) 地点跳变 / 章内地点矛盾
    prev_ids = sorted(ledger.keys())
    for i, c in enumerate(prev_ids):
        st = ledger[c]
        # 3a 章内矛盾
        if st.get("internal_loc_conflict") and st.get("loc_open"):
            issues.append({
                "level": "medium",
                "category": "地点跳变(章内矛盾)",
                "title": f"第{c}章开头已在「{st['loc_open']}」，后文又进入同一地点",
                "detail": "章节开头直接处于某地点，后文却描写『坠入/进入』该地点，"
                          "自身时间线顶牛。",
                "evidence": f"第{c}章开头位置={st['loc_open']}，后文存在『进入/坠入{st['loc_open']}』",
            })
        # 3b 跨章醒来跳变：本章开头醒来，位置与上一章结尾不同且无过渡
        if i > 0 and st.get("wake_open") and st.get("loc_open"):
            prev = ledger[prev_ids[i - 1]]
            pe = prev.get("loc_end") or prev.get("loc_open")
            cur = st["loc_open"]
            if pe and cur and pe != cur and not (pe in cur or cur in pe):
                issues.append({
                    "level": "medium",
                    "category": "地点跳变(无过渡)",
                    "title": f"第{c}章开头在「{cur}」醒来，与第{prev_ids[i-1]}章结尾「{pe}」无过渡",
                    "detail": "主角跨章位置无交代地翻转，空间连续性断裂。",
                    "evidence": f"第{prev_ids[i-1]}章结尾={pe} → 第{c}章开头醒来={cur}",
                })
    return issues


# ---------------------------------------------------------------------------
# 大纲侧：相邻章状态交接校验（治「大纲有问题」根因）
# ---------------------------------------------------------------------------
def check_outline_continuity(project_dir: str) -> list:
    """读 chapter_outlines.json，检测相邻章规划层错位（零 token）。"""
    path = os.path.join(project_dir, ".novelforge", "chapter_outlines.json")
    if not os.path.exists(path):
        return []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        # 兼容可能的 {outlines:[...]} 包装
        data = data.get("outlines", data.get("chapters", []))
    if not isinstance(data, list):
        return []

    def _oid(o):
        return int(str(o.get("chapter_id") or o.get("id") or o.get("chapter") or 0))

    data = sorted(data, key=_oid)
    issues = []

    def _acquire_in(outline) -> set:
        """从大纲文本里抓『采/获取 X』的 X（物品核心名）。"""
        blob = " ".join(str(outline.get(k, "")) for k in
                        ("core_events", "plot_summary", "opening_hook", "ending_hook",
                         "foreshadowing", "rhythm", "summary", "title"))
        found = set()
        for m in _ACQUIRE_RE.finditer(blob):
            if _is_item(m.group(1)):
                found.add(_normalize_item(m.group(1)))
        return found

    def _deadline_in(outline) -> dict:
        blob = " ".join(str(outline.get(k, "")) for k in
                        ("core_events", "plot_summary", "opening_hook", "ending_hook"))
        mp = {}
        for m in _DEADLINE_RE.finditer(blob):
            rel, num, subj = m.group(1), m.group(2), m.group(3)
            off = _REL_DAY.get(rel, 1) if rel else (cn_to_int(num) if num else None)
            subj = _normalize_item(subj)
            if subj and off is not None:
                mp.setdefault(subj, set()).add(off)
        return mp

    prev = None
    for o in data:
        cid = _oid(o)
        if cid == 0:
            continue
        if prev is not None:
            pcid, pout = prev
            # 重复获取事件
            a_prev = _acquire_in(pout)
            a_cur = _acquire_in(o)
            dup = a_prev & a_cur
            if dup:
                issues.append({
                    "level": "high",
                    "category": "大纲连续性冲突",
                    "title": f"大纲第{pcid}章与第{cid}章规划了同一获取事件：「{ '、'.join(dup) }」",
                    "detail": "相邻大纲都安排『采到/获得』同一物品，正文会重复获取，"
                              "属规划层错位（用户点明的『大纲有问题』根因）。",
                    "evidence": f"第{pcid}章与第{cid}章均规划获取「{ '、'.join(dup) }」",
                })
            # deadline 偏移不一致
            d_prev = _deadline_in(pout)
            d_cur = _deadline_in(o)
            for subj, offs in d_cur.items():
                if subj in d_prev and d_prev[subj] != offs:
                    issues.append({
                        "level": "high",
                        "category": "大纲连续性冲突",
                        "title": f"大纲第{pcid}/{cid}章对「{subj}」的 deadline 偏移不一致",
                        "detail": "相邻大纲对同一交付期限写出不同相对天数，生成后会时间线打架。",
                        "evidence": f"第{pcid}章={sorted(d_prev[subj])} 天；第{cid}章={sorted(offs)} 天",
                    })
        prev = (cid, o)

    # ---- 大纲时间流链（v5：显式时间点 + 跟随小说节奏，不定时）----
    # 用大纲存储的 time_flow 累积出绝对时间点；未明示章节 carry-forward。
    # 校验：①链单调不倒流；②存储 abs_day 与累积结果一致（防手写大纲错位）；
    #      ③同日章节时段不逆序；④大纲声明 time_flow vs 正文 ledger time_flow 一致性。
    plan = []
    for o in data:
        tl = o.get("timeline")
        if not tl or "time_flow" not in tl:
            issues.append({
                "level": "low",
                "category": "大纲时间线缺失",
                "title": f"大纲第{o.get('title','?')} 缺少 timeline.time_flow 字段",
                "detail": "该章大纲未铺显式时间点（time_flow），无法参与时间流链校验。"
                          "应为每章声明时间流（次日/三日后/半日后/数月后…）或由正文推断注入。",
                "evidence": "timeline.time_flow 缺失",
            })
            continue
        plan.append(o)

    # 累积绝对时间点（与 resolve_timeline 同源：epoch=第1章=1.0，首章不累加）
    resolved = []
    cur = 1.0
    cur_tod = None
    first = True
    for o in plan:
        tl = o["timeline"]
        tf = tl.get("time_flow") or {}
        dd = tf.get("delta_days")
        if not first and dd is not None:
            cur += float(dd)
        first = False
        tod = tf.get("tod")
        tod_label = tod.get("label") if isinstance(tod, dict) else None
        cur_tod = tod_label or cur_tod
        span = tf.get("span")
        if not tf.get("specified"):
            abs_label = "未明示"
        elif span in ("month", "year"):
            abs_label = _human_time(cur, {"label": cur_tod} if cur_tod else None, span)
        else:
            abs_label = f"第{int(round(cur))}天" + (f"·{cur_tod}" if cur_tod else "")
        resolved.append((o, cur, abs_label, tod_label, tf.get("specified", False), tf))

    prev_abs = None
    prev_tod = None
    prev_cid = None
    for o, abs_day, abs_label, tod_label, specified, tf in resolved:
        cid = o.get("chapter_id")
        # ① 链单调（未明示 carry-forward 不推进，不算倒流）
        if prev_abs is not None and abs_day < prev_abs - 1e-6:
            issues.append({
                "level": "high",
                "category": "大纲时间流倒流",
                "title": f"大纲第{cid}章时间点（{abs_label}）早于上一章",
                "detail": "大纲时间流链出现倒流，时间轴自相矛盾；请校正本章 time_flow。",
                "evidence": f"上一章绝对日≈{prev_abs:.2f}，本章≈{abs_day:.2f}",
            })
        # ② 存储 abs_day 与累积一致（防手写大纲错位）
        stored = o["timeline"].get("abs_day")
        if stored is not None and abs(stored - abs_day) > 1e-6:
            issues.append({
                "level": "high",
                "category": "大纲abs_day与累积链不一致",
                "title": f"大纲第{cid}章存储 abs_day={stored} 与累积时间流不符（应≈{abs_day:.2f}）",
                "detail": "大纲 time_flow 链累积出的绝对日与该章存储的 abs_day 不一致，"
                          "属手写大纲错位；以 time_flow 累积为准修正 abs_day。",
                "evidence": f"存储abs_day={stored}，累积≈{abs_day:.2f}",
            })
        # ③ 同日章节（绝对日相近）时段不逆序
        if prev_tod and tod_label and prev_abs is not None and abs(abs_day - prev_abs) < 0.5:
            r1 = _TOD_MAP.get(prev_tod, -1)
            r2 = _TOD_MAP.get(tod_label, -1)
            if r1 >= 0 and r2 >= 0 and r2 < r1:
                issues.append({
                    "level": "medium",
                    "category": "大纲时段逆序",
                    "title": f"大纲第{prev_cid}→{cid} 同日时段逆序（{prev_tod}→{tod_label}）",
                    "detail": "相邻同日章节的时段倒流，时间轴自相矛盾。",
                    "evidence": f"{prev_tod}(r{r1}) → {tod_label}(r{r2})",
                })
        prev_abs = abs_day
        prev_tod = tod_label
        prev_cid = cid

    # ④ 大纲声明 time_flow vs 正文 ledger time_flow 一致性（大纲须与正文时间流吻合）
    try:
        ledger = build_ledger(project_dir, use_cache=True)
        for o, abs_day, abs_label, tod_label, specified, tf in resolved:
            cid = _oid(o)
            st = ledger.get(cid)
            if not st:
                continue
            tf_body = st.get("time_flow") or {}
            if tf.get("specified") != tf_body.get("specified"):
                issues.append({
                    "level": "high",
                    "category": "大纲/正文时间流脱节",
                    "title": f"大纲第{cid}章声明时间流『{tf.get('label','未明示')}』，但正文推断为『{tf_body.get('label','未明示')}』",
                    "detail": "大纲显式时间点与正文实际时间流不一致——要么大纲瞎编了时间点，"
                              "要么正文过渡词缺失/错位。二者必须吻合（根治『大纲/正文两张皮』）。",
                    "evidence": f"大纲={tf.get('label')}（specified={tf.get('specified')}）；"
                                f"正文={tf_body.get('label')}（specified={tf_body.get('specified')}）",
                })
            elif tf.get("specified") and tf_body.get("specified"):
                if tf.get("span") != tf_body.get("span") or (
                    tf.get("delta_days") and tf_body.get("delta_days") and
                    abs(tf["delta_days"] - tf_body["delta_days"]) > 0.5
                ):
                    issues.append({
                        "level": "high",
                        "category": "大纲/正文时间流幅度不一致",
                        "title": f"大纲第{cid}章时间流幅度（{tf.get('label')}）与正文（{tf_body.get('label')}）不符",
                        "detail": "大纲与正文对同一章的时间推进幅度不一致，时间轴会打架。",
                        "evidence": f"大纲delta={tf.get('delta_days')}；正文delta={tf_body.get('delta_days')}",
                    })
    except Exception:
        pass  # ledger 构建失败时跳过交叉比对，不误报

    return issues


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="连续性状态卡（零token）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--check", action="store_true", help="检测章节连续性冲突")
    ap.add_argument("--check-outline", action="store_true", help="检测大纲跨章连续性冲突")
    ap.add_argument("--card", type=int, default=0, help="输出写给某章前的状态卡")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    if args.card:
        print(get_state_card(args.project, args.card))
    if args.check or args.check_outline:
        iss = []
        if args.check:
            iss += check_continuity(args.project, use_cache=not args.no_cache)
        if args.check_outline:
            iss += check_outline_continuity(args.project)
        print(f"issues: {len(iss)}")
        for it in iss:
            print(f"  [{it['level']}] {it['category']} | {it['title']}")
            print(f"      证据: {it['evidence']}")
