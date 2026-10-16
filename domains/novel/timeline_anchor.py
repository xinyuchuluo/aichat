"""第七层一致性守卫：简大纲时间锚点（time_anchor）校验 —— 零 token。

设计定位：
- 简大纲的 `time_anchor` 字段是「跨章时间台账」，格式约定为「距上章+N天；剧情日=第M天」。
  它在写章时被钉进本章硬约束（assembler._build_chapter_spec 顶部），用于防止 AI 时间混乱。
- 本模块只做**大纲层**的确定性校验（纯正则、不读正文、不调模型），检测三类硬错：
    1. time_regression (high)    —— 本章剧情日 < 上一章剧情日，时间倒流；
    2. time_inconsistent (medium)—— 上章剧情日 + 本章「距上章天数」≠ 本章剧情日，两个数字自相矛盾；
    3. time_anchor_missing (low) —— 本书已启用时间锚点，但本章缺失（仅在存在其他锚点章时才报，
       避免老书全量刷屏）。
- 能力边界：只校验能被解析出的**数字型**锚点。「数日后」「良久」等模糊表述无法量化，
  按缺失处理但不报错（记为不可解析，跳过）。语义层面的时间合理性（如"三天横跨大陆"）
  不在此守卫职责内。
"""
import json
import os
import re

_CN_NUM = {
    "零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 剧情日：剧情日=第17天 / 剧情日 第17天 / 剧情第17天 / 故事第17天
_STORY_DAY_RE = re.compile(r"(?:剧情|故事)\s*(?:日|天)?\s*[=＝:：]?\s*第?\s*(\d+)\s*天")
# 兜底：文本中出现「第17天」且未匹配到上面的显式剧情日
_BARE_DAY_RE = re.compile(r"第\s*(\d+)\s*天")
# 距上章：距上章+3天 / 距上一章 3 天 / 较上章+3天
_DELTA_RE = re.compile(r"(?:距|较|离)\s*上\s*(?:一)?\s*章\s*[+＋]?\s*(\d+)\s*天")
# 裸增量：+3天（无「距上章」前缀时使用）
_BARE_DELTA_RE = re.compile(r"[+＋]\s*(\d+)\s*天")
# 中文数字增量：距上章三天 / 三天后
_CN_DELTA_RE = re.compile(r"(?:距|较|离)\s*上\s*(?:一)?\s*章\s*([零一两二三四五六七八九十]+)\s*天")

# 固定语义词 → 距上章天数
_PHRASE_DELTA = [
    (re.compile(r"当日|同日|当天|同一天|紧接"), 0),
    (re.compile(r"次日|翌日|第二天|隔日"), 1),
    (re.compile(r"后日|第三天"), 2),
]


class TimeAnchorIssue:
    def __init__(self, severity, chapter_id, itype, message, evidence=None):
        self.severity = severity
        self.chapter_id = chapter_id
        self.type = itype
        self.message = message
        self.evidence = evidence or []

    def __str__(self):
        return f"[timeline_anchor][{self.severity}] ch{self.chapter_id}: {self.message}"

    def __repr__(self):
        return self.__str__()

    def to_dict(self):
        """统一为 dict 表示（与六层守卫的 issue 形状一致），避免下游代码对对象调 .get() 崩溃。"""
        return {
            "severity": self.severity,
            "level": self.severity,
            "chapter_id": self.chapter_id,
            "type": self.type,
            "category": self.type,
            "title": self.message,
            "message": self.message,
            "detail": self.message,
            "evidence": list(self.evidence or []),
        }


def _cn_to_int(s):
    """把简单中文数字转 int（支持 一~十九、二十~九十九）。失败返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if all(c.isdigit() for c in s):
        return int(s)
    if "十" in s:
        left, _, right = s.partition("十")
        tens = _CN_NUM.get(left, 1) if left else 1
        ones = _CN_NUM.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(s) == 1:
        return _CN_NUM.get(s)
    return None


def parse_time_anchor(text):
    """解析时间锚点文本，返回 (story_day, delta_days)，无法解析的项为 None。

    story_day: 当前剧情日（第几天），int 或 None
    delta_days: 距上一章过了几天，int 或 None
    """
    t = str(text or "").strip()
    if not t:
        return (None, None)

    story_day = None
    m = _STORY_DAY_RE.search(t)
    if m:
        story_day = int(m.group(1))

    delta = None
    m = _DELTA_RE.search(t)
    if m:
        delta = int(m.group(1))
    if delta is None:
        m = _CN_DELTA_RE.search(t)
        if m:
            delta = _cn_to_int(m.group(1))
    if delta is None:
        for rx, val in _PHRASE_DELTA:
            if rx.search(t):
                delta = val
                break
    if delta is None:
        m = _BARE_DELTA_RE.search(t)
        if m:
            delta = int(m.group(1))

    # 兜底剧情日：文本里有「第N天」但没被识别成增量来源
    if story_day is None:
        for m in _BARE_DAY_RE.finditer(t):
            val = int(m.group(1))
            if delta is not None and val == delta and "距" not in t[max(0, m.start() - 4):m.start()]:
                continue
            story_day = val
            break

    return (story_day, delta)


def _load_outlines(project_dir):
    p = os.path.join(project_dir, ".novelforge", "chapter_outlines.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("chapters") or data.get("outlines") or []
    return data if isinstance(data, list) else []


def check_timeline_anchors(outlines=None, project_dir=None, report_missing=True):
    """校验整本书的时间锚点连贯性，返回 TimeAnchorIssue 列表（零 token）。

    传 outlines（list of dict）优先；否则从 project_dir 读 chapter_outlines.json。
    """
    if outlines is None:
        outlines = _load_outlines(project_dir or ".")
    rows = sorted(
        [o for o in outlines if isinstance(o, dict)],
        key=lambda o: o.get("chapter_id") or 0,
    )
    issues = []
    any_anchor = any(str(o.get("time_anchor") or "").strip() for o in rows)
    if not any_anchor:
        return issues  # 本书未启用时间锚点，不打扰

    prev_day = None
    prev_cid = None
    for o in rows:
        cid = o.get("chapter_id")
        raw = str(o.get("time_anchor") or "").strip()
        if not raw:
            if report_missing:
                issues.append(TimeAnchorIssue(
                    "low", cid, "time_anchor_missing",
                    "本书已启用时间锚点，但本章简大纲缺 time_anchor（写章时无法锁定时间推进）",
                ))
            continue

        day, delta = parse_time_anchor(raw)

        if day is not None and prev_day is not None and day < prev_day:
            issues.append(TimeAnchorIssue(
                "high", cid, "time_regression",
                f"时间倒流：本章剧情日 第{day}天 早于 第{prev_cid}章 的 第{prev_day}天",
                [raw],
            ))

        if day is not None and delta is not None and prev_day is not None:
            expect = prev_day + delta
            if expect != day:
                issues.append(TimeAnchorIssue(
                    "medium", cid, "time_inconsistent",
                    f"时间锚点自相矛盾：上章 第{prev_day}天 + 距上章{delta}天 = 第{expect}天，"
                    f"但本章标注 第{day}天",
                    [raw],
                ))

        if day is not None:
            prev_day = day
            prev_cid = cid
        elif delta is not None and prev_day is not None:
            prev_day = prev_day + delta
            prev_cid = cid

    return issues


def check_chapter_anchor(outlines, chapter_id):
    """单章检查（写章前闸门用）：只返回与该章相关的 issue。零 token。"""
    return [i for i in check_timeline_anchors(outlines=outlines, report_missing=False)
            if i.chapter_id == chapter_id]


# —— 时间锚点确定性对齐（软件兜底，零 token）——
# LLM 常把「距上章天数」与「剧情日」算矛盾（如写"次日清晨"却标 +0 天、剧情日却 +1）。
# 不让 AI 做算数题：软件以「剧情日」为准反算「距上章天数 = 剧情日 - 上章剧情日」，
# 写回字符串，使 time_anchor 内部恒满足 上章剧情日 + 距上章天数 = 本章剧情日。
_PAREN = re.compile(r"[（(][^）)]*[）)]")


def _extract_prose(raw):
    """提取 time_anchor 中的括号备注（如「次日清晨」），保留时间语义。"""
    return " ".join(_PAREN.findall(raw or ""))


def reconcile_time_anchor(raw, prev_day):
    """把 time_anchor 的「剧情日」与「距上章天数」对齐，消除 LLM 算术自相矛盾。零 token。

    Args:
        raw: 本章 LLM 生成的 time_anchor 原文
        prev_day: 上一章剧情日(int)；None 表示开篇首章（不强行推算，原样返回）
    Returns:
        对齐后的 time_anchor 字符串。若无法解析则原样返回，交给守卫报缺失。
    """
    if prev_day is None:
        return raw if raw is not None else ""
    raw = str(raw or "").strip()
    if not raw:
        return raw
    day, delta = parse_time_anchor(raw)
    if day is None and delta is None:
        return raw  # 无法解析，交给守卫报缺失（low）
    prose = _extract_prose(raw)
    if day is None:
        # 仅给 delta：推算剧情日
        day = prev_day + delta
    else:
        # 以剧情日为准反算 delta（保留作者本意：次日清晨 → +1）
        if day < prev_day:
            day = prev_day  # 时间倒流钳制，防时光倒退（regression 守卫另报 high）
        delta = day - prev_day
    prose_str = prose if prose else ""
    return f"距上章+{delta}天{prose_str}；剧情日=第{day}天"
