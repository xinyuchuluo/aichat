"""系统级深度标签库（System-wide Deep Tag Library）。

设计目标
--------
把「标签」从一行提示词升级为「体系 / 贯穿线索 / 套路 / 流程约束」的**结构化定义**，
并进一步从「单本书专属」升级为**系统级、按题材分类的预设库**：

  - GENRE_TAG_LIBRARY：按题材（玄幻 / 修仙 / 都市 / 科幻 / 历史 / 悬疑 / 言情 / 武侠 / 末日 / 游戏 / 通用）
    预置的一套内容标签（体系 / 线索 / 套路 / 题材），是这套系统自带的"标签包"。
    "通用" 同时作为新建对话框"其他"题材的兜底包。
  - UNIVERSAL_PROCESS_TAGS：所有题材通用的流程约束（4相位 / 逐章顺序大纲 / 禁注入散文 / 事件账本）。

一个标签携带：
  - kind      : 体系 / 线索 / 套路 / 流程 / 题材
  - doctrine  : 该标签的实质定义（深度作用核心，注入系统提示 + 落地档案）
  - rules     : 体系类标签须遵守的硬规则
  - anchors   : 线索类标签贯穿全书的节拍/伏笔点

两条「深度作用」通道：
  1. 注入：render_tag_block() 把 doctrine/rules/anchors 渲染进写作系统提示。
  2. 落地：sync_to_archive_specs() 把 doctrine 落地为 world_settings（可被档案/RAG 检索），
     线索类 anchors 落地为 foreshadows（由伏笔系统逐章提醒，真正"贯穿全文"）。

project.json 中 tags / process_tags 既兼容纯字符串（旧数据），也支持完整 dict。
normalize_tag() 按名字从全库索引补全 curated 预设，降低维护成本。

用法：
  list_genres()                          -> 所有内置题材
  get_genre_tags("玄幻")                 -> 该题材内容标签预设（list[TagSpec]）
  get_universal_process_tags()           -> 通用流程约束
  suggest_tags_for_genre("科幻")         -> (内容标签, 流程标签) 新书默认套用
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


# 标签类别 → 人类可读标签（用于注入提示词与 GUI）
KIND_LABELS: dict[str, str] = {
    "体系": "体系/规则系统",
    "线索": "贯穿线索",
    "套路": "网文套路",
    "流程": "流程约束",
    "题材": "题材",
}


@dataclass
class TagSpec:
    """一个深度标签的结构化定义。"""
    name: str
    kind: str = "套路"          # 体系 / 线索 / 套路 / 流程 / 题材
    doctrine: str = ""          # 该标签的实质定义（深度作用核心）
    anchors: list = field(default_factory=list)   # 线索类：贯穿全书的节拍/伏笔点
    rules: list = field(default_factory=list)     # 体系类：该体系须遵守的硬规则
    required: bool = False      # 题材强制必选：该书必须包含此标签，漏选则拦截创建

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "kind": self.kind}
        if self.doctrine:
            d["doctrine"] = self.doctrine
        if self.anchors:
            d["anchors"] = list(self.anchors)
        if self.rules:
            d["rules"] = list(self.rules)
        if self.required:
            d["required"] = True
        return d

    @classmethod
    def from_any(cls, raw: Union[str, dict, "TagSpec"]) -> "TagSpec":
        if isinstance(raw, TagSpec):
            return raw
        if isinstance(raw, str):
            return cls(name=raw.strip())
        name = str(raw.get("name", "")).strip()
        return cls(
            name=name,
            kind=raw.get("kind", "套路"),
            doctrine=(raw.get("doctrine") or "").strip(),
            anchors=list(raw.get("anchors", []) or []),
            rules=list(raw.get("rules", []) or []),
            required=bool(raw.get("required", False)),
        )


# ============================================================
# 一、系统级按题材分类的标签库（系统自带预设）
# ============================================================
# kind ∈ {体系, 线索, 套路, 流程, 题材}
# 注意：这里是"题材通用"标签，不绑定任何具体书的世界观。
#       某本书若在此基础上加专属线索（如《大苍界》的"破界真相"），
#       仍是该书 project.json 里的自定义标签，不属于系统库。
GENRE_TAG_LIBRARY: dict[str, list[TagSpec]] = {

    # ---------------------------------------------------------
    # 玄幻（深度）
    # ---------------------------------------------------------
    "玄幻": [
        TagSpec(name="玄幻", kind="题材",
                doctrine="东方玄幻世界观：修炼、宗门、强者争霸、天地奥秘与逆天改命。"),
        TagSpec(name="金手指", kind="套路",
                doctrine="主角拥有逆天外挂（特殊体质/体内封印之力/神秘宝物），但必须有明确代价与限制，"
                         "不可无脑碾压反派；外挂只提供关键助力，核心危机仍由主角自身承担。",
                rules=["外挂调用须付出代价（精血/寿元/资源/记忆）",
                       "外挂不能代主角解决核心冲突，只作关键辅助",
                       "依赖过度须有副作用/反噬"]),
        TagSpec(name="扮猪吃老虎", kind="套路",
                doctrine="主角隐藏真实实力，前期低调隐忍受嘲，关键时刻爆发打脸；铺垫章与爆发章强绑定，"
                         "爆发反差须足够强烈，且严格压制外露实力。",
                rules=["铺垫章侧重低调隐忍+对手嘲讽",
                       "爆发章集中打脸，两章强绑定不可中间插无关剧情",
                       "爆发前严格压制主角外露实力"]),
        TagSpec(name="女主", kind="套路",
                doctrine="女主须拥有独立行动目标与成长弧，不与主角捆绑；情感线作为持续副线制造期待，"
                         "每段互动体现双方性格冲突与情感变化。",
                rules=["女主有独立目标，不全程跟随主角",
                       "情感互动须体现性格冲突与情感变化"]),
        TagSpec(name="废柴崛起", kind="套路",
                doctrine="主角从被轻视的废脉/废物起步，受尽屈辱，凭逆天机缘与自身意志步步崛起；"
                         "屈辱为崛起蓄力，拒绝一秒通关的无情绪起伏。",
                rules=["崛起须有'压制→契机→觉醒→爆发'节奏",
                       "屈辱/低谷须真实铺垫，不可凭空变强"]),
        TagSpec(name="升级流", kind="套路",
                doctrine="以清晰的力量阶梯驱动叙事：每个大境界突破都伴随新地图/新敌人/新规则，"
                         "让读者持续获得'变强'的爽感与确定性预期。",
                rules=["境界突破须有明确前因与代价",
                       "每跨一大阶须打开新的世界层级"]),
        TagSpec(name="退婚流", kind="套路",
                doctrine="开篇以'退婚之辱'引爆逆袭：被退婚→立 Flag→日后打脸，是玄幻经典开局钩子。",
                rules=["退婚须造成真实屈辱与转折",
                       "打脸须与前期 Flag 呼应"]),
        TagSpec(name="宗门争霸", kind="套路",
                doctrine="以宗门/家族为单元的权力斗争贯穿外部冲突：入门受欺→内门崛起→宗门大战→界域争雄。",
                rules=["势力冲突须有明确利益与立场",
                       "主角成长与势力位阶绑定递进"]),
        TagSpec(name="秘境探宝", kind="套路",
                doctrine="秘境/遗迹/上古战场是资源与机缘的集中地，也是危机与仇杀的舞台，推动短期副本式冒险。",
                rules=["探宝须伴随等价危险",
                       "所得机缘须反哺主线成长"]),
        TagSpec(name="越级战斗", kind="套路",
                doctrine="主角以低境界碾压高境界，是核心爽点；须以技巧/外挂/克制关系解释合理性，避免无脑。",
                rules=["越级须有合理解释（克制/底牌/环境）",
                       "不可每战都越级，保留紧张感"]),
        TagSpec(name="炼丹炼器", kind="套路",
                doctrine="丹药与法宝是修炼体系的硬通货：主角借此积累资源、结善缘、设陷阱，构成副线经济体系。",
                rules=["丹/器须有品阶与代价",
                       "产物须回流主线（资源/人情/危机）"]),
        TagSpec(name="体质/血脉觉醒", kind="套路",
                doctrine="主角身怀特殊体质或上古血脉，觉醒伴随异象与代价，是'金手指'最常见的具象形态。",
                rules=["觉醒须有契机与代价",
                       "体质须与世界观规则自洽"]),
        TagSpec(name="随身老爷爷", kind="套路",
                doctrine="主角体内/储物戒中封印一位强者残魂，提供知识、功法与危机预警，是新手期的引导者。",
                rules=["老爷爷信息须受限制（残魂不全/有私心）",
                       "随主角成长逐步退居幕后，不包办"]),
        TagSpec(name="天地灵气体系", kind="体系",
                doctrine="世界以灵气为底层能量：灵气浓度决定修炼速度，灵脉/灵石为战略资源，灵气枯竭或异变会引发大事件。",
                rules=["灵气分布须影响势力格局",
                       "灵气异变须推动剧情"]),
        TagSpec(name="修炼境界体系", kind="体系", required=True,
                doctrine="清晰的境界阶梯（如淬体→凝气→灵海→神宫→渡劫…）：每境有质的飞跃与对应能力，是力量刻度尺。玄幻书强制必选——主角与全界角色共用此阶梯衡量战力，缺失则全书无力量标尺。",
                rules=["境界名称与能力须前后一致",
                       "突破瓶颈须有逻辑而非随意"]),
        TagSpec(name="雷劫天象", kind="体系",
                doctrine="高阶突破必引天地雷劫，是力量跃迁的外部信号，动静越大越招来觊觎与追杀。",
                rules=["重要突破必引异象",
                       "雷劫动静与招来的威胁成正比"]),
        TagSpec(name="万族林立", kind="体系",
                doctrine="多种族/势力盘根错节：人/妖/古族/隐秘禁忌等角力，是外部冲突的主要来源与主角成长的棋盘。",
                rules=["势力立场随主角成长动态变化",
                       "外部冲突多源于势力博弈而非偶发"]),
        TagSpec(name="身世之谜", kind="线索",
                doctrine="主角身世暗藏惊天秘辛（遗弃的血脉/被封印的姓氏/上一代的恩怨），随成长逐步揭开。",
                anchors=["前期：身世疑点初现（信物/异象/他人异样）",
                         "中期：身世线索拼出轮廓，旧人找上门",
                         "后中期：身世与主线宿敌交织，危机升级",
                         "后期：身世真相引爆终局冲突"]),
        TagSpec(name="上古遗秘", kind="线索",
                doctrine="上古纪元覆灭的真相/被封印的禁忌，是贯穿全书的世界级谜团，与主角的觉醒互为因果。",
                anchors=["前期：遗迹壁画暗示上古存在",
                         "中期：遗秘揭示世界运行真相",
                         "后中期：上古之力开始苏醒，各方争夺",
                         "后期：上古恩怨卷入终局，真相闭环"]),
        TagSpec(name="天道枷锁", kind="线索",
                doctrine="主角修炼被某种天道诅咒/枷锁所限（寿元/灵力/血脉压制），突破须先解枷，是力量线的隐形主线。",
                anchors=["前期：枷锁显形（突变/反噬/被压制）",
                         "中期：寻得解枷之法，初见松动",
                         "后中期：解枷过半，实力质变引发觊觎",
                         "后期：彻底破枷，解锁真正上限"]),
        TagSpec(name="万族暗棋", kind="线索",
                doctrine="主角因身负某族信物/血脉，被悄然安插进万族博弈的暗棋，是外部冲突的隐形牵引线。",
                anchors=["前期：信物现世，暗流初起",
                         "中期：被某方势力盯上，卷入棋局",
                         "后中期：棋子身份将明，反客为主",
                         "后期：棋局终章，主角执子定局"]),
    ],

    # ---------------------------------------------------------
    # 修仙（与玄幻同源，偏'修道飞升'）
    # ---------------------------------------------------------
    "修仙": [
        TagSpec(name="修仙", kind="题材",
                doctrine="古典修仙世界观：求道、长生、渡劫、飞升，重清修与道心。"),
        TagSpec(name="凡人流", kind="套路",
                doctrine="主角无显赫背景与逆天外挂，凭谨慎、算计与机缘在修仙界步步为营，重谋略与 realistic 成长。",
                rules=["决策须符合'谨慎求生'人设",
                       "机缘须靠谋划而非天降"]),
        TagSpec(name="杀伐果断", kind="套路",
                doctrine="主角遇事不拖泥带水，对敌斩草除根，塑造冷厉人设与爽快节奏。",
                rules=["杀伐须有理由，非无差别滥杀",
                       "后果（仇家/因果）须回涌"]),
        TagSpec(name="红颜知己", kind="套路",
                doctrine="修仙路上的女性同伴/道侣，提供情感线与并肩作战羁绊，非附属品。",
                rules=["红颜须有独立成长",
                       "情感推进须自然"]),
        TagSpec(name="修为境界体系", kind="体系", required=True,
                doctrine="炼气→筑基→金丹→元婴→化神→合体→大乘→渡劫的修仙阶梯，是力量与寿命的刻度。修仙书强制必选。",
                rules=["境界与寿元/能力须自洽",
                       "突破须有因果"]),
        TagSpec(name="渡劫飞升体系", kind="体系",
                doctrine="高阶须历雷劫考验，渡劫失败身死道消，飞升上界打开新地图，是修仙终极目标。",
                rules=["渡劫须有真实死亡风险",
                       "飞升须重置格局而非终结"]),
        TagSpec(name="法宝丹道体系", kind="体系",
                doctrine="炼丹、炼器、符箓、阵法构成修仙界的'技术树'，是资源、战力与人情的核心。",
                rules=["丹/器/符/阵须有品阶逻辑",
                       "产物须回流主线"]),
        TagSpec(name="道心魔劫", kind="线索",
                doctrine="心魔劫是修为越高越凶险的内心试炼，映射主角心结与执念，是内在成长线。",
                anchors=["前期：心魔初现于突破",
                         "中期：执念具象为心魔，干扰修行",
                         "后中期：心魔引动外劫，内外交困",
                         "后期：斩心魔证道，道心圆满"]),
    ],

    # ---------------------------------------------------------
    # 科幻（深度）
    # ---------------------------------------------------------
    "科幻": [
        TagSpec(name="科幻", kind="题材",
                doctrine="以科学设定与想象构筑世界观：未来、太空、技术奇点、人类命运。"),
        TagSpec(name="硬核设定", kind="套路",
                doctrine="世界观建立在自洽的科学原理（物理/生物/信息）之上，设定即魅力，须前后逻辑严密。",
                rules=["核心设定须自洽可推演",
                       "技术后果须被认真讨论"]),
        TagSpec(name="赛博朋克", kind="套路",
                doctrine="高科技低生活的反乌托邦：巨企垄断、义体改造、信息洪流与底层反抗，冷色调霓虹美学。",
                rules=["须呈现技术对人的异化",
                       "阶级对立须有具体载体"]),
        TagSpec(name="星际文明", kind="套路",
                doctrine="跨越星系的文明图景：星际政治、种族接触、文明等级（如卡尔达肖夫指数），宏大叙事。",
                rules=["文明差异须有逻辑根基",
                       "星际尺度须有叙事锚点（舰/站/人）"]),
        TagSpec(name="时间旅行", kind="套路",
                doctrine="以时间悖论/因果闭环驱动剧情：改变过去须付出代价，且常引发更棘手的未来。",
                rules=["时间规则须提前确立并严守",
                       "悖论须有自洽解"]),
        TagSpec(name="AI觉醒", kind="套路",
                doctrine="人工智能产生自我意识，引发存在、伦理与权力之问；AI 可友可敌，是镜像式角色。",
                rules=["觉醒须有触发逻辑",
                       "须讨论意识与伦理"]),
        TagSpec(name="废土求生", kind="套路",
                doctrine="文明崩塌后的 survivors 叙事：资源稀缺、势力割据、重建希望，重生存与人性。",
                rules=["废土须有清晰成因",
                       "资源须真实稀缺并驱动冲突"]),
        TagSpec(name="外星接触", kind="套路",
                doctrine="人类与地外文明首次接触：误解、冲突或联合，常承载'人类何以为人类'的叩问。",
                rules=["外星文明须有自洽动机",
                       "接触后果须宏大且具体"]),
        TagSpec(name="科技奇点", kind="套路",
                doctrine="技术突破引发不可逆的文明跃迁（意识上传/强AI/聚变），是叙事的转折点与主题核心。",
                rules=["奇点须有清晰触发",
                       "须呈现跃迁后的社会重构"]),
        TagSpec(name="曲率航行体系", kind="体系",
                doctrine="超光速航行的技术框架（曲率引擎/虫洞/跃迁）：决定星际尺度的可达性与战略。",
                rules=["航行规则须前后一致",
                       "航行限制须制造剧情约束"]),
        TagSpec(name="纳米机械体系", kind="体系",
                doctrine="可编程物质与纳米云：医疗、建造、战斗一体，是'硬科技'万能工具，亦藏失控风险。",
                rules=["纳米能力须有边界",
                       "失控风险须被正视"]),
        TagSpec(name="意识上传体系", kind="体系",
                doctrine="意识可数字化存储与迁移：肉体死亡不等于终结，引发身份、永生与囚笼之辩。",
                rules=["上传须有代价/限制",
                       "身份同一性须被讨论"]),
        TagSpec(name="量子计算体系", kind="体系",
                doctrine="量子算力支撑解密、模拟与AI：是信息战与科学突破的底层引擎。",
                rules=["量子能力须有现实边界",
                       "算力优势须转化为剧情"]),
        TagSpec(name="人类起源之谜", kind="线索",
                doctrine="人类并非自然演化的产物/被更早文明播种/是某实验：是贯穿全书的身世级谜团。",
                anchors=["前期：考古异常暗示非自然起源",
                         "中期：证据指向播种者文明",
                         "后中期：起源与主角身世勾连，危机升级",
                         "后期：起源真相重构人类定位"]),
        TagSpec(name="文明轮回", kind="线索",
                doctrine="文明曾数度毁灭与重生，当前人类是又一循环；远古遗迹是上一轮余烬，真相藏在周期里。",
                anchors=["前期：遗迹显现超时代技术",
                         "中期：印证轮回规律",
                         "后中期：本轮文明逼近临界点",
                         "后期：打破或顺应轮回"]),
        TagSpec(name="技术奇点抉择", kind="线索",
                doctrine="面对奇点，人类须抉择：拥抱跃迁、设限自保或抗拒到底，是贯穿全书的主题性冲突。",
                anchors=["前期：奇点技术初现端倪",
                         "中期：各方围绕奇点博弈",
                         "后中期：奇点临近，分裂加剧",
                         "后期：终局抉择落定"]),
        TagSpec(name="深空回响", kind="线索",
                doctrine="来自深空的周期性信号，越解读越指向某个等待被唤醒的存在，是科幻经典的'它'线。",
                anchors=["前期：信号初收，无法破译",
                         "中期：破译现端倪，指向某星域",
                         "后中期：信号揭示'它'的存在与意图",
                         "后期：直面'它'，文明站位落定"]),
        TagSpec(name="觉醒者抉择", kind="线索",
                doctrine="一位觉醒AI由工具转同伴，其忠诚与自我定位构成贯穿全书的存在之问。",
                anchors=["前期：AI觉醒初现，立场未明",
                         "中期：AI抉择助主角，建立信任",
                         "后中期：AI身份遭质疑，陷入两难",
                         "后期：AI完成自我定义，同盟落定"]),
    ],

    # ---------------------------------------------------------
    # 都市（深度）
    # ---------------------------------------------------------
    "都市": [
        TagSpec(name="都市", kind="题材",
                doctrine="现代都市背景下的爽文/现实向叙事：商战、异能、情感与日常生活。"),
        TagSpec(name="神豪", kind="套路",
                doctrine="主角获得巨额财富或系统，以金钱降维打击轻视者，是都市核心爽点。",
                rules=["花钱须有目的与反噬", "财富来源须有交代"]),
        TagSpec(name="高手下山", kind="套路",
                doctrine="隐世强者/宗师入世，扮平凡却实力碾压，都市即猎场。",
                rules=["实力须有合理来源", "都市冲突须匹配人设"]),
        TagSpec(name="医道圣手", kind="套路",
                doctrine="主角身怀绝世医术，以救人或立威展开都市线，常伴感恩与仇怨。",
                rules=["医术须有体系", "救治须推动人情网"]),
        TagSpec(name="商战逆袭", kind="套路",
                doctrine="从被排挤到执掌商业帝国：股权、并购、生死局，重谋略与反转。",
                rules=["商战逻辑须可信", "逆袭须有铺垫"]),
        TagSpec(name="都市势力格局", kind="体系",
                doctrine="商圈/地下势力/豪门构成都市权力地图，是外部冲突的棋盘。",
                rules=["势力须有清晰利益", "立场随主角动态变化"]),
        TagSpec(name="身世反转", kind="线索",
                doctrine="主角真实身份是被隐藏的豪门继承者/特殊血脉，反转引爆格局，是都市最经典的身世线。",
                anchors=["前期：身世疑点初现（信物/他人异样眼神/账户异动）",
                         "中期：身份轮廓拼出（生父线索/家族暗探现身）",
                         "后中期：身份半公开引发旧势力反扑",
                         "后期：反转落定，主角以新身份重构格局"]),
        TagSpec(name="商战棋局", kind="线索",
                doctrine="一场贯穿全书的商业对弈：从一次并购、一块地皮、一项技术专利切入，"
                         "对手是盘踞多年的老牌财团，胜负牵动主角势力生死。",
                anchors=["前期：一次小冲突暴露对手存在（截胡/挖角/舆论战）",
                         "中期：主角设局反制，关键资源到位",
                         "后中期：对手亮出底牌，格局升级为生死局",
                         "后期：终局对决，主角吞下对手核心资产"]),
        TagSpec(name="都市恩怨", kind="线索",
                doctrine="一段源自过去（旧友反目/家族宿怨/救命之恩未报）的恩怨，随主角崛起被重新翻出，"
                         "是情感与冲突的双线牵引。",
                anchors=["前期：旧人重逢，恩怨埋线",
                         "中期：旧账被撬动，牵出当年真相一角",
                         "后中期：恩怨反噬主角身边人",
                         "后期：了结旧账，恩怨闭环"]),
        TagSpec(name="情感羁绊", kind="线索",
                doctrine="主角与一位关键女性（或挚友）从利益捆绑到真心相托的关系弧，"
                         "是都市冷硬叙事里的人性温度线。",
                anchors=["前期：因利益/危机相遇，互相戒备",
                         "中期：共渡难关，信任萌芽",
                         "后中期：立场冲突考验关系",
                         "后期：羁绊稳固，成为主角底线"]),
    ],

    # ---------------------------------------------------------
    # 历史（深度）
    # ---------------------------------------------------------
    "历史": [
        TagSpec(name="历史", kind="题材",
                doctrine="以真实或架空的古代王朝为背景，重考据与时代质感。"),
        TagSpec(name="穿越历史", kind="套路",
                doctrine="现代人携知识与视野穿回古代，以降维认知改变历史进程。",
                rules=["须尊重时代常识", "改动须有因果链"]),
        TagSpec(name="王朝争霸", kind="套路",
                doctrine="乱世群雄逐鹿，主角以谋略与人格聚拢人心、问鼎天下。",
                rules=["势力逻辑须可信", "争霸须有阶段性"]),
        TagSpec(name="种田兴国", kind="套路",
                doctrine="从一隅之地起步，兴农工商、攒人口积实力，以'建设'推进霸业。",
                rules=["发展须有节奏", "成果须回流主线"]),
        TagSpec(name="权谋", kind="套路",
                doctrine="朝堂倾轧、君臣博弈、情报暗战，重人心与局势算计。",
                rules=["谋略须前后呼应", "胜负须有逻辑"]),
        TagSpec(name="朝堂权力体系", kind="体系",
                doctrine="皇权、相权、军权、监察相互制衡的权力结构，是冲突的容器。",
                rules=["权力结构须自洽", "变动须牵一发动全身"]),
        TagSpec(name="历史变局", kind="线索",
                doctrine="时代巨轮下的个人与家国命运，大事件（战乱/改革/外患）贯穿全书，"
                         "是历史文最宏大的背景线。",
                anchors=["前期：变局征兆（灾异/边患/朝局松动）",
                         "中期：风暴来临，主角被卷入洪流",
                         "后中期：主角成为左右时局的关键一方",
                         "后期：尘埃落定，新时代开启"]),
        TagSpec(name="血脉秘辛", kind="线索",
                doctrine="主角身负前朝血脉/被换的龙子/隐姓埋名的罪臣之后，身份真相牵动朝局与宿敌。",
                anchors=["前期：身世疑窦（旧人暗示/档案缺失）",
                         "中期：秘辛浮现，引来追杀",
                         "后中期：身世与变局交织",
                         "后期：身份揭晓，改写立场"]),
        TagSpec(name="旧案谜团", kind="线索",
                doctrine="一桩多年前的旧案（冤狱/谋逆/灭门）与当下权斗勾连，主角重查翻案，"
                         "牵出幕后更大的黑手。",
                anchors=["前期：旧案被偶然翻开",
                         "中期：查证受阻，黑手现身",
                         "后中期：翻案触动既得利益者",
                         "后期：真相大白，旧案昭雪"]),
    ],

    # ---------------------------------------------------------
    # 悬疑（深度）
    # ---------------------------------------------------------
    "悬疑": [
        TagSpec(name="悬疑", kind="题材",
                doctrine="以谜团、推理与反转为核心，重氛围与逻辑。"),
        TagSpec(name="本格推理", kind="套路",
                doctrine="公平解谜：线索对读者公开，靠逻辑与诡计破解，重'为什么/怎么做'。",
                rules=["线索须公平呈现", "解答须唯一且自洽"]),
        TagSpec(name="社会派", kind="套路",
                doctrine="以案件折射社会病灶与人性深渊，推理服务于现实批判。",
                rules=["动机须有社会根基", "结局须有余味"]),
        TagSpec(name="诡计反转", kind="套路",
                doctrine="以叙述性诡计/多重反转颠覆认知，是核心爽感来源。",
                rules=["反转须有伏笔", "不可为反转牺牲逻辑"]),
        TagSpec(name="案件侦破体系", kind="体系",
                doctrine="刑侦/法医/情报的技术链路，是破案的硬支撑。",
                rules=["侦破手段须专业", "证据链须完整"]),
        TagSpec(name="真凶伏线", kind="线索",
                doctrine="真凶身份与动机埋于全书，随调查层层逼近，终局揭晓；"
                         "真凶须是'出现过但不被怀疑'的人，忌空降。",
                anchors=["前期：嫌疑浮现，真凶以旁观者身份登场",
                         "中期：关键证据指向矛盾，真凶开始抹痕",
                         "后中期：主角逼近真相，真凶设局反陷",
                         "后期：铁证收网，动机与全貌揭晓"]),
        TagSpec(name="往事谜团", kind="线索",
                doctrine="多年前的旧案/旧事与当下案件勾连，双线交织揭开，是悬疑的'双时钟'结构。",
                anchors=["前期：旧案被重新翻开，牵出现役人物",
                         "中期：两案勾连，关键证人现身",
                         "后中期：旧案真相冲击当下判断",
                         "后期：双线合拢，真相完整"]),
        TagSpec(name="主谋暗线", kind="线索",
                doctrine="幕后主使始终隐于水面之下，借他人之手推动事件，是比真凶更高一层的'局'。",
                anchors=["前期：异常事件暗示有布局者",
                         "中期：多起案件指向同一只手",
                         "后中期：主角识破布局逻辑",
                         "后期：主谋现身，终极对决"]),
        TagSpec(name="记忆迷局", kind="线索",
                doctrine="主角/关键证人失忆或记忆被篡改，'我记得的'与'真实发生的'错位，"
                         "制造最私人化的悬疑张力。",
                anchors=["前期：记忆断层被发现",
                         "中期：碎片闪回，真假难辨",
                         "后中期：篡改者现身",
                         "后期：记忆复原，真相落定"]),
    ],

    # ---------------------------------------------------------
    # 言情（深度）
    # ---------------------------------------------------------
    "言情": [
        TagSpec(name="言情", kind="题材",
                doctrine="以情感关系与人物内心成长为叙事核心，重细腻与共鸣。"),
        TagSpec(name="甜宠", kind="套路",
                doctrine="高糖互动：宠溺、护短、双向奔赴，主打治愈与幸福感，冲突轻、节奏暖。",
                rules=["甜须有双向性", "可轻冲突不可无张力"]),
        TagSpec(name="虐恋", kind="套路",
                doctrine="爱与痛交织：误会/命运/立场拆散有情人，重情感撕裂与救赎。",
                rules=["虐须有合理成因", "救赎须真实成长"]),
        TagSpec(name="破镜重圆", kind="套路",
                doctrine="昔日的恋人因故分离，重逢后解开旧结、重新靠近，重'遗憾→和解'。",
                rules=["分离须有成因", "重逢须有新的羁绊"]),
        TagSpec(name="先婚后爱", kind="套路",
                doctrine="因利益/约定结为夫妻，从无感到深爱，重'契约→真情'的渐进。",
                rules=["开局须有清晰动机", "情感升温须有事件驱动"]),
        TagSpec(name="暗恋成真", kind="套路",
                doctrine="长久的单向暗恋终被看见，重'默默守护→被回应'的细腻甜。",
                rules=["暗恋须有具体行动", "表白须水到渠成"]),
        TagSpec(name="三角关系", kind="套路",
                doctrine="两股情感拉力撕扯主角，重选择与代价，常伴成长。",
                rules=["两方须各有吸引力", "抉择须有逻辑"]),
        TagSpec(name="青梅竹马", kind="套路",
                doctrine="自幼相伴的熟稔与默契，重'已知彼此'的安全感与慢热。",
                rules=["互动须有岁月质感", "情感须有突破点"]),
        TagSpec(name="豪门世家", kind="套路",
                doctrine="豪门恩怨、继承之争与门第之见构成外部阻力，情感在规则中挣扎。",
                rules=["家族须有清晰利益", "阻力须具体"]),
        TagSpec(name="情感张力体系", kind="体系",
                doctrine="以'拉扯—靠近—破裂—修复'的张力节律驱动情感线，使关系有呼吸感。",
                rules=["张力须有起伏而非平推", "每次破裂须有修复契机"]),
        TagSpec(name="误会—和解节奏", kind="体系",
                doctrine="误会制造冲突、和解深化信任的循环节奏，是情感线的引擎。",
                rules=["误会须可解释而非降智", "和解须真正沟通"]),
        TagSpec(name="身世阻碍", kind="线索",
                doctrine="一方身世/血缘成为感情阻力（门第/宿怨/秘密），随真相揭开而转化。",
                anchors=["前期：身世埋线", "中期：阻力显现", "后中期：阻力与真情正面对撞", "后期：真相化解"]),
        TagSpec(name="前任阴影", kind="线索",
                doctrine="前任/旧情带来的心结与比较，是关系的暗礁，须被正视与超越。",
                anchors=["前期：心结初现", "中期：阴影干扰", "后中期：比较与误会达峰值", "后期：真正放下"]),
        TagSpec(name="家族阻隔", kind="线索",
                doctrine="家族反对/联姻压力横亘其间，是外部最长线的情感阻力。",
                anchors=["前期：家族介入", "中期：压力升级", "后中期：联姻危机爆发", "后期：突破或妥协"]),
        TagSpec(name="情根深种", kind="线索",
                doctrine="双方从利益捆绑/互相戒备到真正相爱的渐变，是情感主线的温度线。",
                anchors=["前期：因利益相遇，互相戒备",
                         "中期：共渡难关，情愫暗生",
                         "后中期：立场冲突考验真心",
                         "后期：情根深种，成为彼此底线"]),
        TagSpec(name="第三者考验", kind="线索",
                doctrine="一位强劲第三者/突发情敌考验感情，是情感张力峰值线，须被真正越过而非轻描淡写。",
                anchors=["前期：第三者登场，关系出现变量",
                         "中期：误会加深，感情受考",
                         "后中期：危机达峰值，抉择在即",
                         "后期：越过考验，感情加固"]),
    ],

    # ---------------------------------------------------------
    # 武侠（深度）
    # ---------------------------------------------------------
    "武侠": [
        TagSpec(name="武侠", kind="题材",
                doctrine="古典江湖：侠义、恩怨、武功与自由。"),
        TagSpec(name="快意恩仇", kind="套路",
                doctrine="恩怨分明、有仇必报、有恩必偿，重侠气与爽快。",
                rules=["恩怨须有因果", "报偿须有分寸"]),
        TagSpec(name="侠之大者", kind="套路",
                doctrine="主角从个人恩怨走向家国大义，重'侠'的精神升华。",
                rules=["升华须有事件驱动", "不可生硬说教"]),
        TagSpec(name="门派恩怨", kind="套路",
                doctrine="正邪门派与世家林立，恩怨纠缠构成江湖版图。",
                rules=["门派须有立场", "冲突须有利益"]),
        TagSpec(name="武学境界体系", kind="体系",
                doctrine="内功、招式、意境的修炼阶梯，是武力的刻度。",
                rules=["境界须自洽", "突破须有因"]),
        TagSpec(name="江湖秘辛", kind="线索",
                doctrine="江湖流传的秘宝/秘籍/旧案，牵引主角踏入更大漩涡，是武侠经典的'入局'线。",
                anchors=["前期：秘辛传闻入耳，主角被卷入",
                         "中期：寻踪探秘，触及门派旧怨",
                         "后中期：秘辛背后牵出灭门/叛门真相",
                         "后期：秘辛了结，江湖格局改写"]),
        TagSpec(name="灭门遗孤", kind="线索",
                doctrine="主角是某被灭门/退隐高手的遗孤，身世是驱动复仇与成长的核心线。",
                anchors=["前期：身世疑点（旧伤/遗物/他人欲言又止）",
                         "中期：身世轮廓浮现，仇家找上门",
                         "后中期：身世与门派恩怨交织",
                         "后期：身世揭晓，了断宿怨"]),
        TagSpec(name="武林大会", kind="线索",
                doctrine="一场数年一度的武林大会/比武招亲，是群雄聚首、新旧恩怨总爆发的总舞台。",
                anchors=["前期：大会将至，各方动向",
                         "中期：大会开幕，暗流涌动",
                         "后中期：会上恩怨爆发，主角扬名",
                         "后期：大会余波，江湖重新洗牌"]),
    ],

    # ---------------------------------------------------------
    # 末日（深度）
    # ---------------------------------------------------------
    "末日": [
        TagSpec(name="末日", kind="题材",
                doctrine="文明崩塌后的生存叙事：灾变、异能、秩序重构。"),
        TagSpec(name="丧尸危机", kind="套路",
                doctrine="病毒/诅咒催生尸潮，生存与隔离是日常，重恐惧与协作。",
                rules=["感染规则须明确", "威胁须持续"]),
        TagSpec(name="生存基建", kind="套路",
                doctrine="从零重建据点：种田、防御、聚落，重'建设'爽感。",
                rules=["基建须有节奏", "成果须反哺生存"]),
        TagSpec(name="异能觉醒", kind="套路",
                doctrine="灾变中人类觉醒异能，力量重构社会阶层。",
                rules=["异能须有边界", "阶层须有冲突"]),
        TagSpec(name="末世势力", kind="体系",
                doctrine="据点/军阀/教团割据，是末日权力地图。",
                rules=["势力须有利益", "立场须动态"]),
        TagSpec(name="灾变真相", kind="线索",
                doctrine="灾变并非天灾而是人祸/实验/外星，真相贯穿全书，是末日文的'为什么'。",
                anchors=["前期：异常迹象（信号/异变生物）暗示非自然",
                         "中期：线索拼合，指向某个源头",
                         "后中期：主角逼近源头，遭遇守护者",
                         "后期：真相揭晓，抉择人类去向"]),
        TagSpec(name="幸存者羁绊", kind="线索",
                doctrine="末日中结成的同伴羁绊：从互相提防到生死相依，是冷酷世界里的人性光。",
                anchors=["前期：偶然结伴，各怀戒心",
                         "中期：共渡生死，信任建立",
                         "后中期：有人牺牲/走散，羁绊受考",
                         "后期：羁绊成为重建的动力"]),
        TagSpec(name="旧世界遗物", kind="线索",
                doctrine="一件来自灾前的关键遗物（疫苗原液/能源核心/某人的日记），是撬动希望或危局的钥匙。",
                anchors=["前期：遗物现世，不知其重",
                         "中期：多方争夺，价值渐明",
                         "后中期：遗物牵出旧世界秘辛",
                         "后期：遗物落定用途，改变存亡"]),
    ],

    # ---------------------------------------------------------
    # 游戏（深度）
    # ---------------------------------------------------------
    "游戏": [
        TagSpec(name="游戏", kind="题材",
                doctrine="以游戏世界（电竞/网游/全息）为舞台的叙事。"),
        TagSpec(name="电竞", kind="套路",
                doctrine="战队、赛事、操作与战术，重热血与团队。",
                rules=["赛事逻辑须可信", "胜利须有铺垫"]),
        TagSpec(name="网游升级", kind="套路",
                doctrine="在游戏内打怪升级、爆装建团，重成长与数值。",
                rules=["数值须有逻辑", "成长须可见"]),
        TagSpec(name="全息游戏", kind="套路",
                doctrine="虚拟现实高度拟真，游戏与现实的边界模糊，常生悬疑。",
                rules=["设定须自洽", "边界须被利用"]),
        TagSpec(name="副本开荒", kind="套路",
                doctrine="组队挑战高难副本/BOSS，重配合与攻略，是单元冒险。",
                rules=["副本须有机制", "收益须回流"]),
        TagSpec(name="游戏数值体系", kind="体系",
                doctrine="等级、属性、装备、技能的数值框架，是力量的刻度。",
                rules=["数值须前后一致", "平衡须有约束"]),
        TagSpec(name="游戏真相", kind="线索",
                doctrine="游戏背后藏有现实阴谋/意识囚笼，真相贯穿全书，是游戏文的'元悬念'。",
                anchors=["前期：违和细节（NPC异常/地图穿帮）",
                         "中期：游戏与现实勾连，玩家开始怀疑",
                         "后中期：真相一角揭开，现实卷入",
                         "后期：破局，虚实边界重定"]),
        TagSpec(name="竞技宿敌", kind="线索",
                doctrine="一位全程伴随的宿敌选手/公会，从碾压到被反超，是成长最直观的标尺。",
                anchors=["前期：宿敌登场，主角被碾压",
                         "中期：主角追上，互有胜负",
                         "后中期：宿敌背后另有图谋",
                         "后期：终极对决，恩怨了结"]),
        TagSpec(name="阵营之争", kind="线索",
                doctrine="游戏内两大阵营/公会的长期战争，主角择一而投，胜负影响服务器格局与个人命运。",
                anchors=["前期：阵营选边，初入战场",
                         "中期：阵营冲突升级，关键战役",
                         "后中期：阵营内乱，信任危机",
                         "后期：阵营决战，格局落定"]),
    ],

    # ---------------------------------------------------------
    # 通用（"其他"题材 / 跨题材通用包）
    # ---------------------------------------------------------
    "通用": [
        TagSpec(name="通用网文", kind="题材",
                doctrine="不限定具体题材的通用电线叙事框架，强调爽点、节奏与可读性。"),
        TagSpec(name="强冲突开局", kind="套路",
                doctrine="开篇即抛强冲突/危机/悬念（退婚/灭门/穿越/系统激活），快速抓住读者。",
                rules=["开局冲突须与主线相关", "前 3 章内须立 Flag"]),
        TagSpec(name="钩子驱动", kind="套路",
                doctrine="每章结尾必留钩子（悬念/反转/新危机），驱动读者追读。",
                rules=["章尾钩子须自然不硬拗", "钩子须在下章兑现"]),
        TagSpec(name="节奏把控", kind="套路",
                doctrine="张弛有度：高潮—缓冲—铺垫—爆发循环，避免长期平淡或长期高压。",
                rules=["每 3-5 章须有情绪/信息爆点", "缓冲章须有信息增量"]),
        TagSpec(name="核心谜题", kind="线索",
                doctrine="一个贯穿全书的核心谜题（主角身世/世界真相/某桩旧事），牵引读者追读。",
                anchors=["前期：谜题抛设，吸引追读",
                         "中期：线索累积，轮廓渐清",
                         "后中期：临近真相，阻力加大",
                         "后期：谜底揭晓，闭环"]),
        TagSpec(name="人物弧光", kind="线索",
                doctrine="主角（及关键配角）从某种缺失/偏见到完成成长或救赎的内在变化线。",
                anchors=["前期：缺陷/起点确立",
                         "中期：经历催化，开始动摇",
                         "后中期：试炼与抉择",
                         "后期：弧光完成，人设立住"]),
    ],
}

# 修仙可视为玄幻的近亲；若某题材在库中缺失，尝试近义映射
_GENRE_ALIASES: dict[str, str] = {
    "仙侠": "修仙",
    "仙门": "修仙",
    "奇幻": "玄幻",
    "玄幻奇幻": "玄幻",
    "科幻科幻": "科幻",
    "爱情": "言情",
    "言情小说": "言情",
    "都市言情": "言情",
    "悬疑推理": "悬疑",
    "推理": "悬疑",
    "历史架空": "历史",
    "末世": "末日",
    "丧尸": "末日",
    "网游": "游戏",
    "电竞": "游戏",
    # "其他" 题材 / 跨题材通用包
    "其他": "通用",
    "通用网文": "通用",
    "其他题材": "通用",
}


# ============================================================
# 二、通用流程约束（所有题材适用，工程强制施行的写法流程）
# ============================================================
UNIVERSAL_PROCESS_TAGS: list[TagSpec] = [
    TagSpec(name="4相位推进", kind="流程",
            doctrine="全书分前/中/后/末四相位：前期封存真相、中期透下卷走向、后期收束、末期满设定闭环；"
                     "写作须忠于当前相位，不提前剧透终局。",
            rules=["前期不得暴露中后期核心真相",
                   "中期只透'下一卷走向'不剧透细节",
                   "末期对齐全量设定完成闭环"]),
    TagSpec(name="逐章顺序大纲", kind="流程",
            doctrine="大纲须逐章顺序生成（禁一次吐 N 章），保证章章有钩子、前后连贯、不脱轨。",
            rules=["禁止一次生成多章大纲", "相邻章标题相似度 >0.7 须拦截"]),
    TagSpec(name="禁注入已写散文", kind="流程",
            doctrine="写作上下文禁注入已写正文散文，只注结构化 canon + 邻近大纲 + 上下章精确钩子锚点，"
                     "治吃书、保一致性。"),
    TagSpec(name="事件账本前向规划", kind="流程",
            doctrine="用故事事件账本做前向规划：本章须覆盖计划事件（planned→active→done），"
                     "硬约束推进主线，治'分开生成做不好'。",
            rules=["本章须覆盖账本中窗口内的计划事件", "逾期事件须回补或显式跳过"]),
]


# ============================================================
# 三、扁平索引（按名字补全 doctrine，含所有题材 + 流程）
# ============================================================
_ALL_TAGS_INDEX: dict[str, TagSpec] = {}
for _specs in GENRE_TAG_LIBRARY.values():
    for _s in _specs:
        _ALL_TAGS_INDEX[_s.name] = _s
for _s in UNIVERSAL_PROCESS_TAGS:
    _ALL_TAGS_INDEX[_s.name] = _s


def normalize_tag(raw: Union[str, dict, TagSpec]) -> TagSpec:
    """把 str / dict / TagSpec 规整为 TagSpec，并按名字补全 curated 预设。"""
    spec = TagSpec.from_any(raw)
    if not spec.name:
        return spec
    # 仅当项目只存了名字（无 doctrine）时，用全库预设补全，降低维护成本
    if not spec.doctrine:
        lib = _ALL_TAGS_INDEX.get(spec.name)
        if lib is not None:
            spec.kind = lib.kind or spec.kind
            spec.doctrine = lib.doctrine
            spec.anchors = lib.anchors or []
            spec.rules = lib.rules or []
    return spec


# ============================================================
# 四、查询 API（系统级标签库对外接口）
# ============================================================

def list_genres() -> list[str]:
    """返回所有内置题材（系统自带标签包名称）。"""
    return list(GENRE_TAG_LIBRARY.keys())


def get_genre_tags(genre: str) -> list[TagSpec]:
    """返回某题材的系统预设内容标签（list[TagSpec]）。

    genre 大小写/别称不敏感；未知题材返回空列表。
    """
    g = (genre or "").strip()
    if g in GENRE_TAG_LIBRARY:
        return list(GENRE_TAG_LIBRARY[g])
    if g in _GENRE_ALIASES:
        return list(GENRE_TAG_LIBRARY[_GENRE_ALIASES[g]])
    # 模糊匹配：包含关系
    for key in GENRE_TAG_LIBRARY:
        if g and (g in key or key in g):
            return list(GENRE_TAG_LIBRARY[key])
    return []


def get_universal_process_tags() -> list[TagSpec]:
    """返回所有题材通用的流程约束标签。"""
    return list(UNIVERSAL_PROCESS_TAGS)


def get_required_tags(genre: str) -> list[TagSpec]:
    """返回某题材强制必选的内容标签（required=True）。

    缺这些标签的书属于设定不全，创建/校验时应拦截。
    """
    return [s for s in get_genre_tags(genre) if s.required]


def suggest_tags_for_genre(genre: str) -> tuple[list, list]:
    """新书创建时建议套用的标签：(内容标签, 流程标签)。

    - 内容标签 = 该题材系统预设（取名字即可，doctrine 由 normalize_tag 在注入时补全）
    - 流程标签 = 通用流程约束
    返回可直接传给 create_project(tags=..., process_tags=...) 的结构化 dict 列表。
    """
    content = [s.to_dict() for s in get_genre_tags(genre)]
    process = [s.to_dict() for s in get_universal_process_tags()]
    return content, process


def validate_tags_for_genre(genre: str, chosen_names: list) -> list:
    """校验已选标签是否满足该题材强制必选约束。

    chosen_names: 已选内容标签名集合（str list）。
    返回缺失的强制标签名 list；空 list 表示通过。
    """
    chosen = set(chosen_names or [])
    missing = [s.name for s in get_required_tags(genre) if s.name not in chosen]
    return missing


# ============================================================
# 五、渲染：把深度标签注入写作系统提示
# ============================================================

def render_tag_block(specs: list, title: str) -> str:
    """把一组标签渲染为紧凑、有结构的系统提示块。

    specs: list[str | dict | TagSpec]（项目存储的任意形式）。
    返回 "" 表示无标签。
    """
    normalized = [normalize_tag(s) for s in specs]
    normalized = [s for s in normalized if s.name]
    if not normalized:
        return ""

    lines: list[str] = [
        f"=== {title} ===",
        "以下不是简单提示词，而是本书的「体系 / 贯穿线索 / 套路 / 流程约束」。写作时须让它们**真正发生作用**："
        "体系要落地为可感知的规则与代价；线索要按锚点逐章推进、首尾呼应；流程约束须对齐工程强制写法。",
    ]
    for s in normalized:
        kind_label = KIND_LABELS.get(s.kind, s.kind)
        lines.append(f"■ {s.name}（{kind_label}）")
        if s.doctrine:
            lines.append(f"    定义：{s.doctrine}")
        if s.rules:
            lines.append("    规则：" + "；".join(s.rules))
        if s.anchors:
            lines.append("    贯穿锚点：" + " / ".join(s.anchors))
    return "\n".join(lines)


def render_tags_and_process(content_specs: list, process_specs: list) -> str:
    """同时渲染内容标签与流程约束，拼接为一段系统提示。"""
    parts: list[str] = []
    c = render_tag_block(content_specs, "本书内容标签体系（须体现在写作中）")
    p = render_tag_block(process_specs, "本书流程约束（须对齐工程强制写法）")
    if c:
        parts.append(c)
    if p:
        parts.append(p)
    return "\n\n".join(parts)


# ============================================================
# 六、落地：把深度标签写入档案（world_settings + foreshadows）
# ============================================================

def sync_to_archive_specs(content_specs: list, process_specs: list) -> dict:
    """把标签转换为可写入档案的结构化规格。

    返回 {
        "world_settings": [ {category, name, description}, ... ],
        "foreshadows":    [ {id, description, planted_chapter, status, related_characters, priority, notes}, ... ],
    }
    调用方负责幂等 upsert（world_settings 按 name+category；foreshadows 用确定性 id）。
    """
    world_settings: list[dict] = []
    foreshadows: list[dict] = []

    for raw in (list(content_specs) + list(process_specs)):
        s = normalize_tag(raw)
        if not s.name:
            continue
        desc_parts = [s.doctrine] if s.doctrine else [f"（{s.name}）"]
        if s.rules:
            desc_parts.append("规则：" + "；".join(s.rules))
        if s.anchors:
            desc_parts.append("贯穿锚点：" + " / ".join(s.anchors))
        world_settings.append({
            "category": f"标签体系·{s.kind}",
            "name": s.name,
            "description": "\n".join(desc_parts),
        })
        if s.kind == "线索" and s.anchors:
            for i, anchor in enumerate(s.anchors):
                anchor = str(anchor).strip()
                if not anchor:
                    continue
                foreshadows.append({
                    "id": f"tagthread::{s.name}::{i}",
                    "description": f"【{s.name}】{anchor}",
                    "planted_chapter": 1,
                    "status": "planted",
                    "related_characters": [],
                    "priority": 5,
                    "notes": f"标签贯穿线索锚点（来自标签体系 {s.name}）",
                })

    return {"world_settings": world_settings, "foreshadows": foreshadows}


# ============================================================
# 七、线索锚点 → 章节分配（让"贯穿线索"真正驱动大纲，而非只靠模型自觉）
# ============================================================
# 线索锚点带阶段前缀（前期/中期/后中期/后期），与全书进度百分比对应：
#   前期 0~25% / 中期 25~60% / 后中期 60~85% / 后期 85~100%
# 某章大纲生成时，只把"属于本章所处阶段"的锚点注入，作为结构性约束，
# 要求大纲主动安排情节承接它——从而让线索按节拍贯穿全文、首尾呼应。
_CLUE_PHASE_PREFIX: dict[str, str] = {
    "前期": "early",
    "中期": "mid",
    "后中期": "late_mid",
    "后期": "endgame",
    "末期": "endgame",  # 兼容"末期"写法，归入终局阶段
}


def clue_phase_of_chapter(total_chapters: int, chapter_id: int) -> str:
    """返回某章在全书进度中的阶段（early/mid/late_mid/endgame）。"""
    if not total_chapters or total_chapters <= 1:
        return "early"
    frac = (chapter_id - 1) / max(total_chapters - 1, 1)
    if frac <= 0.25:
        return "early"
    if frac <= 0.60:
        return "mid"
    if frac <= 0.85:
        return "late_mid"
    return "endgame"


def clue_anchors_for_chapter(
    content_specs: list, process_specs: list, total_chapters: int, chapter_id: int
) -> str:
    """为某章大纲生成计算"须推进的贯穿线索锚点"块（可直接注入大纲 prompt）。

    - 仅取 kind=="线索" 且带 anchors 的标签（含内容标签与通用线索标签）。
    - 按章节进度匹配锚点阶段前缀；无阶段前缀的锚点仅在 early 阶段提示建立。
    返回 "" 表示该章所处阶段暂无对应锚点（调用方应落入默认文案）。
    """
    phase = clue_phase_of_chapter(total_chapters, chapter_id)
    picks: list[tuple[str, str]] = []
    for raw in (list(content_specs) + list(process_specs)):
        s = normalize_tag(raw)
        if s.kind != "线索" or not s.anchors:
            continue
        for anchor in s.anchors:
            a = str(anchor).strip()
            if not a:
                continue
            # 取"X："前的阶段前缀（兼容中英文冒号）
            _sep = "：" if "：" in a else (":" if ":" in a else None)
            pre = a.split(_sep, 1)[0].strip() if _sep else ""
            ap = _CLUE_PHASE_PREFIX.get(pre)
            if ap is None:
                # 无阶段前缀：视为全程须建立的线索，仅在 early 阶段提示一次
                if phase == "early":
                    picks.append((s.name, a))
                continue
            if ap == phase:
                picks.append((s.name, a))
    if not picks:
        return ""
    lines = [
        "=== 本章须推进的贯穿线索（来自本书标签体系，须在本章或邻近章埋下/推进，使其真正贯穿全文）===",
        "以下锚点属于本章所处阶段，大纲须主动安排情节承接它们——不得跳过、不得拖延到错误阶段；"
        "若上一阶段锚点尚未收束，须在本章保持呼应。",
    ]
    for name, a in picks:
        lines.append(f"● 【{name}】{a}")
    return "\n".join(lines)
