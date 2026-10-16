"""第五层一致性守卫：大纲 ↔ 正文（回顾）冲突检测。

设计定位（重要）：
- 大纲 core_events/plot_summary 是「规划摘要」（抽象概括语言），正文 summary 是「正文回顾」
  （具体叙事语言），两者字面相似度天然极低，纯相似度/字面匹配会把所有吻合章也判成漂移
  （假阳性 ~100%）。因此本模块**不做语义级一致性判定**，只检测「零 token 可确定的硬冲突」：
  1. outline_fabricated_entity (high)：大纲提及的具体事物（书名号《》/引号专名）在正文全文
     与章节回顾中均未出现 —— 确定性「大纲虚构了该事物」。
  2. opening_mismatch (medium)：大纲 opening_hook 与正文开头无 12-gram 重叠 —— 疑似 ch4 类
     空间/情节断点（如「矿洞三号巷道」醒来但上文在灵兽谷岩洞夜宿）。
  3. body_entity_missing_in_outline (low)：正文回顾含书名号专名而大纲未覆盖 —— 提示把正文
     新设定同步回大纲（非错误，属同步建议）。
- 能力边界：本守卫**不保证抓「情节走向不同但无硬实体」的漂移**（如大纲写调虎离山、正文走
  骨片裂谷，双方都无虚构专名）。彻底治本需「生成时正文回流重写大纲」（见 MEMORY 裁决原则）。
- 全程零 token、纯规则。
"""
import json
import os
import re

# 引号内若出现这些名词性字，视为可能专名（过滤口语/内心独白）
_NOUN_HINT = r"[兽石剑丹经图殿莲珠盒牌锁钥脉息气龙魂碑阵符鼎炉妖魔灵决功法宗门秘境废墟古殿]"


# checkpoint 命中判定：统一复用框架闸门的三级容错匹配（P18，2026-08-05）。
# 此处原为裸 `ck not in body` 精确子串——与 framework_constraint._checkpoint_present
# 的近义匹配标准不一致，导致同一个 checkpoint 被框架闸门判「已落地」、却被本守卫
# 判「缺失」，两路守卫自相矛盾，且精确子串对正文换词（『断剑残骸』↔『断剑』）
# 必然假报警。守卫结论必须唯一，否则用户学会忽略它，整套零 token 守卫就失去根基。
def _ck_present(ck, body):
    try:
        from domains.novel.framework_constraint import _checkpoint_present
        return _checkpoint_present(ck, body)
    except Exception:
        return ck in body


class OutlineIssue:
    def __init__(self, severity, chapter_id, itype, message, evidence=None):
        self.severity = severity
        self.chapter_id = chapter_id
        self.type = itype
        self.message = message
        self.evidence = evidence or []

    def __str__(self):
        return f"[outline_vs_body][{self.severity}] ch{self.chapter_id}: {self.message}"

    def __repr__(self):
        return self.__str__()

    def to_dict(self):
        """统一成 dict 表示，与六层主守卫及其他 issue 对象一致。
        下游（自纠闸门 / 严重度过滤 / 写章拦截）按 dict 用 .get()，缺此会导致
        'OutlineIssue' object has no attribute 'get' 崩溃、自纠静默失效。
        level 由 severity 派生（下游 _self_correct_gate 按 level 过滤 high/medium）。"""
        return {
            "severity": self.severity,
            "level": self.severity,
            "chapter_id": self.chapter_id,
            "type": self.type,
            "category": self.type,
            "title": str(self.message),
            "message": str(self.message),
            "detail": str(self.message),
            "evidence": list(self.evidence or []),
        }


def _load_outline(project_dir):
    p = os.path.join(project_dir, ".novelforge", "chapter_outlines.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _body(project_dir, cid):
    p = os.path.join(project_dir, "chapters", f"chapter_{cid:04d}.md")
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
        return f.read()


# 去除 markdown 标题行（# 开头），用于判断正文是否「仅标题占位」
_TITLE_RE = re.compile(r"^\s*#+\s*.*$", re.MULTILINE)


def _strip_md_headings(text):
    return _TITLE_RE.sub("", text or "")


def _is_stub_body(body, min_chars=30):
    """判断正文是否为空占位（缺失或仅标题，无实质叙事）。

    悬空计划（深大纲规划完、正文永不写，如 ch61/62 仅 '# 第61章 …'）会让
    大纲↔正文 守卫失去比对基准、产生误报。此类章应跳过硬冲突检查，由
    scan_hanging_plans 单独以信息级呈现，而非当作错误。
    """
    if not body or not body.strip():
        return True
    real = _strip_md_headings(body).strip()
    return len(real) < min_chars


def _summary(project_dir, cid):
    p = os.path.join(project_dir, ".novelforge", "summaries", f"chapter_{cid:04d}.json")
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return (d.get("plot_progress") or "") + " " + (d.get("one_line_summary") or "")


def _hard_entities(text):
    """提取大纲/回顾中的硬实体：书名号内 + 引号内含名词性字的专名。"""
    ents = set(re.findall(r"《([^》]+)》", text))
    for m in re.findall(r"“([^”]{1,10})”", text):
        if re.search(_NOUN_HINT, m):
            ents.add(m)
    return ents


def _ngram_hit(s, text, n=12):
    """s 中是否存在任一个 n 字连续子串出现在 text（跳步扫描降耗）。"""
    if len(s) < n:
        return True
    step = max(1, n // 3)
    for i in range(0, len(s) - n + 1, step):
        if s[i : i + n] in text:
            return True
    return False


def check_outline_body_consistency(project_dir, use_cache=True):
    """返回 OutlineIssue 列表（零 token）。"""
    data = _load_outline(project_dir)
    issues = []
    for ch in data:
        cid = ch.get("chapter_id")
        core = ch.get("core_events") or ""
        ps = ch.get("plot_summary") or ""
        if isinstance(ps, str) and ps.strip().startswith("{"):
            try:
                ps = json.dumps(json.loads(ps), ensure_ascii=False)
            except Exception:
                pass
        outline_text = core + " " + ps
        body = _body(project_dir, cid)
        # P5：正文缺失或仅标题占位 → 失去比对基准，跳过硬冲突检查，避免悬空计划误报
        if _is_stub_body(body):
            continue
        summ = _summary(project_dir, cid)

        # 1. 虚构实体：大纲硬实体 既不在正文也不在回顾
        fab = [e for e in _hard_entities(outline_text) if e not in body and e not in summ]
        if fab:
            issues.append(
                OutlineIssue(
                    "high",
                    cid,
                    "outline_fabricated_entity",
                    f"大纲提及的具体事物在正文中均未出现（疑似虚构）：{', '.join(fab)}",
                    list(fab),
                )
            )

        # 2. opening 冲突：opening_hook 整段无 12-gram 命中正文开头
        #    框架优先章(framework_controlled)的大纲 opening_hook 是规划而非正文快照，
        #    正文首句只丰富过程，不要求 12-gram 重叠，故豁免以免误报。
        oh = ch.get("opening_hook") or ""
        if (oh and body and not _ngram_hit(oh, body[:600], 12)
                and not ch.get("framework_controlled")):
            issues.append(
                OutlineIssue(
                    "medium",
                    cid,
                    "opening_mismatch",
                    "大纲 opening_hook 与正文开头无重叠（疑似空间/情节断点）",
                    [oh[:50]],
                )
            )

        # 3. 反向遗漏（low，同步建议）：回顾含书名号专名而大纲未覆盖
        miss = [e for e in re.findall(r"《([^》]+)》", summ) if e not in outline_text]
        if miss:
            issues.append(
                OutlineIssue(
                    "low",
                    cid,
                    "body_entity_missing_in_outline",
                    f"正文回顾含大纲未覆盖的设定（建议同步大纲）：{', '.join(miss[:6])}",
                    list(miss[:6]),
                )
            )

        # 4. beat 落地（medium，治已病兜底）：深大纲 event_progress 每 beat 的 checkpoint
        #    必须在正文中出现；缺失=该事件进展未落地（AI 处理器跑偏）。
        #    框架优先章(framework_controlled)的正文只丰富过程，checkpoint 仍应出现，
        #    但为与 opening_mismatch 一致、防极端误报，仍跳过 framework_controlled 章。
        eps = ch.get("event_progress") or []
        if eps and body and not ch.get("framework_controlled"):
            missed = []
            for b in eps:
                ck = (b.get("checkpoint") or "").strip()
                if ck and not _ck_present(ck, body):
                    missed.append(ck)
            if missed:
                issues.append(
                    OutlineIssue(
                        "medium",
                        cid,
                        "beat_missed",
                        f"深大纲事件进展未落地（正文缺失 checkpoints）：{', '.join(missed[:6])}",
                        list(missed[:6]),
                    )
                )
    return issues


def scan_hanging_plans(project_dir):
    """P5 主动诊断：列出「有大纲规划、但正文缺失/仅标题占位」的悬空计划章。

    这是 ouline_vs_body 守卫对 stub 章跳过的「信息级补偿」——不把悬空计划当错误，
    但要在写章前/体检时显式呈现，提示用户「这些章规划了却没写」，避免「规划完、
    正文永不写」的状态无人察觉。零 token、纯文件检查。

    返回 list[dict]：{chapter_id, has_brief, has_deep, reason}
    """
    data = _load_outline(project_dir)
    result = []
    for ch in data:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("chapter_id")
        has_brief = bool((ch.get("core_events") or ch.get("plot_summary")))
        has_deep = bool(ch.get("event_progress"))
        body = _body(project_dir, cid)
        if _is_stub_body(body):
            reason = "正文缺失" if (not body or not body.strip()) else "正文仅标题占位（未写）"
            result.append({
                "chapter_id": cid,
                "has_brief": has_brief,
                "has_deep": has_deep,
                "reason": reason,
            })
    return result
