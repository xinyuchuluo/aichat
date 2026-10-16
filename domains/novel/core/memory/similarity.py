"""零依赖中文文本相似度，用于事实提取的 upsert 合并（不调 LLM、零 token）。

设计原则（贴合项目「零 token 守卫」哲学）：纯本地算法，不依赖外部嵌入模型/网络。
主信号用 difflib.SequenceMatcher（对中文改写描述极有效），并辅以字符包含度兜底，
防止合并后变长的 head 描述导致新抽出的短描述失配。
"""
import difflib
import re
from typing import Iterable, Optional

_STOP = set(
    "的 了 在 是 与 和 从 到 向 中 上 下 他 她 它 这 那 个 有 被 将 已 也 又 还 就 都 而 及 或 "
    "之 其 此 该 我 你 你们 我们 他们 自己 什么 怎么 如何 为何 因为 所以 但是 然而 于是 "
    "突然 渐渐 似乎 仿佛 好像 如同 犹如 显得 露出 泛起 微微 缓缓 轻轻 悄悄 暗暗 隐隐 透出 "
    "浮现 出现 感到 觉得 看到 发现 知道 明白 想起 回忆 记得".split()
)


def normalize(text: str) -> str:
    if not text:
        return ""
    text = str(text).replace("　", " ").strip()
    text = re.sub(r"[^\w一-鿿]", " ", text)
    return text.lower()


def keywords(text: str) -> list:
    toks = re.findall(r"[一-鿿]{2,4}", normalize(text))
    return [t for t in toks if t not in _STOP]


def jaccard(a: Iterable, b: Iterable) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _char_containment(a: str, b: str) -> float:
    """b 的字符有多少比例出现在 a 中（字符级包含度）。"""
    sa, sb = set(a), set(b)
    if not sb:
        return 0.0
    return len(sa & sb) / len(sb)


def similarity(a: str, b: str) -> float:
    """综合相似度 0~1：序列相似 + 关键词 Jaccard + 关键词子集 + 字符双向包含。"""
    if not a or not b:
        return 0.0
    na, nb = normalize(a), normalize(b)
    # 主信号：字符序列相似（捕捉「楚渊掌心的红纹延伸到…」这类同线改写）
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    # 辅信号：关键词 Jaccard
    ka, kb = keywords(a), keywords(b)
    jac = jaccard(ka, kb)
    # 辅信号：关键词子集包含
    contain = 0.0
    if ka and kb:
        if set(ka) <= set(kb):
            contain = 0.9
        elif set(kb) <= set(ka):
            contain = 0.9
    # 辅信号：字符双向包含（防止长 head 描述吞掉短描述失配）
    cc_ab = _char_containment(na, nb)
    cc_ba = _char_containment(nb, na)
    return max(seq, jac, contain, cc_ab, cc_ba)


def best_match(candidate: str, items, get_text, threshold: float = 0.6):
    """在 items 中找与 candidate 相似度最高且 >= threshold 的项。

    返回 (record, score) 或 None。
    """
    best = None
    best_score = 0.0
    for it in items:
        text = get_text(it)
        score = similarity(candidate, text)
        if score > best_score:
            best_score = score
            best = it
    if best is not None and best_score >= threshold:
        return best, best_score
    return None
