"""小说分类与主流文字量规划。

不同载体/类型的小说对应不同的主流篇幅与叙事节奏：
- 网文（玄幻/都市/科幻）：长篇连载，百万字级，宏大慢热
- 科幻（中长篇/短中篇）：设定严谨，结构紧凑或中等
- 短篇：精炼紧凑，快速收束

分类在新建项目时选择，写入 project.json 的 novel_category 字段，并在
总纲 / 大纲生成时注入对应的「长远规划指导」，使 AI 按该类型的主流规格
规划篇幅、卷数、节奏，而不是永远按同一套模板写。
"""

from __future__ import annotations

# 分类表：每个分类对应主流字数规划与节奏策略
NOVEL_CATEGORIES: list[dict] = [
    {
        "key": "webnovel_xuanhuan",
        "label": "网文·玄幻/仙侠",
        "target_words": 2_000_000,
        "chapter_words": 4000,
        "volumes": 5,
        "pacing": "epic_slow",
        "desc": "长篇连载，宏大世界观，多层势力与纪元，慢热铺陈",
        "planning_guide": (
            "本书属于网络长篇玄幻/仙侠。请严格按主流网文规格做长远规划：\n"
            "- 世界观务必宏大：构建多层位面/纪元/万古布局，万族林立、法则森严、历史深邃，"
            "存在超越主角的古老存在与更广阔的天地；\n"
            "- 总篇幅以约 200 万字（数百章量级）为基准展开，分 5 卷左右，每卷有清晰主题与情绪基调；\n"
            "- 节奏以「史诗慢热」为准：主线推进与大量缓冲章、氛围章、世界观特写章、人物内心章交替，"
            "重大事件（战斗/转折/真相）拆成多章层层展开，信息随主角经历渐进揭示、留白、给读者猜测空间；\n"
            "- 允许甚至鼓励慢热铺陈，不急于一次性倾倒设定或过早抵达高潮。"
        ),
    },
    {
        "key": "webnovel_urban",
        "label": "网文·都市/言情",
        "target_words": 1_200_000,
        "chapter_words": 3500,
        "volumes": 4,
        "pacing": "standard",
        "desc": "都市/言情长篇，人物关系驱动，节奏适中",
        "planning_guide": (
            "本书属于网络长篇都市/言情。请按主流网文规格规划：总篇幅约 120 万字，分 4 卷左右；"
            "以人物关系与情感线驱动，节奏适中，主线与日常/感情线交替，允许舒缓的人物互动章，"
            "重大转折跨章展开，信息随情节自然带出。"
        ),
    },
    {
        "key": "webnovel_scifi",
        "label": "网文·科幻",
        "target_words": 1_000_000,
        "chapter_words": 4000,
        "volumes": 5,
        "pacing": "slow",
        "desc": "科幻长篇，硬核设定与人文思辨兼具，慢热展开",
        "planning_guide": (
            "本书属于网络长篇科幻。请按主流科幻网文规格规划：总篇幅约 100 万字，分 5 卷左右；"
            "世界观（科技水平/社会形态/宇宙尺度）须自洽且具想象力，硬核设定与人物命运、人文思辨交织；"
            "节奏偏慢热，重大科学谜题与文明冲突跨章展开，设定随主角探索渐进揭示。"
        ),
    },
    {
        "key": "scifi_novel",
        "label": "科幻·中长篇",
        "target_words": 500_000,
        "chapter_words": 4000,
        "volumes": 3,
        "pacing": "standard",
        "desc": "传统出版向科幻中长篇，设定严谨，结构紧凑",
        "planning_guide": (
            "本书属于传统出版向科幻中长篇。请按主流科幻长篇规格规划：总篇幅约 50 万字，分 3 卷；"
            "设定严谨自洽，结构紧凑，主线集中，重大转折前置铺垫、跨章推进，结尾收束有力。"
        ),
    },
    {
        "key": "scifi_short",
        "label": "科幻·短中篇",
        "target_words": 150_000,
        "chapter_words": 4000,
        "volumes": 1,
        "pacing": "tight",
        "desc": "科幻短中篇，单一高概念，快速收束",
        "planning_guide": (
            "本书属于科幻短中篇。请按主流短中篇规格规划：总篇幅约 15 万字，单卷；"
            "聚焦单一高概念，节奏紧凑，开篇即入题，设定高效交代，结尾留有余韵或反转，不铺张支线。"
        ),
    },
    {
        "key": "short_story",
        "label": "短篇",
        "target_words": 30_000,
        "chapter_words": 3500,
        "volumes": 1,
        "pacing": "tight",
        "desc": "短篇小说，精炼紧凑，单线或双线，快速抵达核心",
        "planning_guide": (
            "本书属于短篇小说。请按主流短篇规格规划：总篇幅 3 万字以内，单卷，章节精炼（每章一个清晰推进）；"
            "主线集中、不铺张支线；节奏紧凑，开篇即入题，信息高效交代，结尾留有余韵或反转；不需多卷。"
        ),
    },
    {
        "key": "literary_long",
        "label": "传统文学·长篇",
        "target_words": 800_000,
        "chapter_words": 4000,
        "volumes": 4,
        "pacing": "literary_slow",
        "desc": "文学性长篇，人物心理与社会图景，舒缓叙事",
        "planning_guide": (
            "本书属于文学性长篇。请按主流文学长篇规格规划：总篇幅约 80 万字，分 4 卷；"
            "侧重人物心理嬗变与社会图景，叙事舒缓，重氛围与细节，信息随人物视角渐进揭示，留白得当。"
        ),
    },
    {
        "key": "light_novel",
        "label": "轻小说",
        "target_words": 600_000,
        "chapter_words": 3500,
        "volumes": 4,
        "pacing": "light",
        "desc": "轻小说，轻松明快，单元剧与主线交织",
        "planning_guide": (
            "本书属于轻小说。请按主流轻小说规格规划：总篇幅约 60 万字，分 4 卷；"
            "轻松明快，单元剧与主线交织，对话驱动，节奏轻快但关键转折仍须铺垫与跨章展开。"
        ),
    },
]

DEFAULT_CATEGORY_KEY = "webnovel_xuanhuan"


def planned_chapter_count(target_words: int, chapter_words: int) -> int:
    """根据目标总字数与单章字数，推导应规划的章节总数（向上取整）。

    例如 200 万字、单章约 4000 字 → 约 500 章。这是让「设定字数」真正成立的关键：
    章节数必须由 target_words 推导，而不是写死一个小数。
    """
    cw = max(500, int(chapter_words))
    tw = max(1, int(target_words))
    return max(1, -(-tw // cw))  # 等价于 math.ceil(tw / cw)


def planned_chapter_count_for(category_key: str, target_words: int) -> int:
    """按分类的 chapter_words 推导规划章节数。"""
    return planned_chapter_count(target_words, get_category(category_key)["chapter_words"])


def get_category(key: str) -> dict:
    """按 key 取分类；未找到返回默认分类。"""
    for c in NOVEL_CATEGORIES:
        if c["key"] == key:
            return c
    return NOVEL_CATEGORIES[0]


def category_labels() -> list[str]:
    """返回所有分类的中文标签列表（供 UI 下拉）。"""
    return [c["label"] for c in NOVEL_CATEGORIES]


def key_by_label(label: str) -> str:
    """中文标签反查 key；未找到返回默认分类 key。"""
    for c in NOVEL_CATEGORIES:
        if c["label"] == label:
            return c["key"]
    return DEFAULT_CATEGORY_KEY


def category_guide_for(key: str) -> str:
    """返回该分类的「长远规划指导」文本（注入总纲/大纲 prompt）。"""
    return get_category(key)["planning_guide"]
