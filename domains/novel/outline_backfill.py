#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
outline_backfill.py —— 大纲回流同步（B 方案，零 token）
===================================================================
写章后，用「抽取式」规则把正文要点写回 chapter_outlines.json 的【已写章节】，
使大纲永远 ≈ 正文，彻底消灭「大纲规划 ↔ 正文走向」语义级漂移。

为什么是零 token：
  - opening_hook  ← 正文开头首句（保证 12-gram 与正文重叠，第五层守卫不报 opening_mismatch）
  - ending_hook   ← 正文结尾末句
  - plot_summary  ← 章节回顾 one_line_summary（回顾本就由正文生成，天然对齐）
  - core_events   ← 章节回顾 plot_progress（按句拆解为正文实际事件）
  - 不触碰 timeline / time_point / foreshadowing / title 等规划与前瞻字段
    （已写章的时间锚点受相邻章承接校验锁死，改之会触发 check_outline_continuity 连锁报警）

只回写【有正文文件】的章节；尚未创作的未来章（无 chapter_NNNN.md）保留原始规划，
仍作为后续生成的引导上下文。回写是幂等且可逆的——源永远是正文，随时可重跑还原。

用法：
  from domains.novel.outline_backfill import sync_outline_from_body
  sync_outline_from_body(PROJECT_DIR, chapter_id=11)          # 单章回写
  sync_outline_from_body(PROJECT_DIR)                          # 全量回写（一次性修正 ch11+ 漂移）
"""
import os
import re
import sys
import json
import shutil
import datetime
import argparse

# 允许直接以脚本运行，也允许被 import
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _body_text(project_dir, chapter_id):
    path = os.path.join(project_dir, 'chapters', f'chapter_{chapter_id:04d}.md')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return f.read()


def _summary(project_dir, chapter_id):
    path = os.path.join(project_dir, '.novelforge', 'summaries',
                         f'chapter_{chapter_id:04d}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _split_sentences(text):
    """中文/英文句号切分，返回去空后的句子列表（保留句末标点）。"""
    parts = []
    buf = ''
    for ch in text:
        buf += ch
        if ch in '。！？!?；;':
            s = buf.strip()
            if s:
                parts.append(s)
            buf = ''
    s = buf.strip()
    if s:
        parts.append(s)
    return parts


def _body_clean(body):
    """去掉标题行（# 开头）与首尾空白，返回纯叙事文本。"""
    lines = [ln for ln in body.splitlines() if not ln.strip().startswith('#')]
    return '\n'.join(lines).strip()


def _first_sentence(body):
    sents = _split_sentences(_body_clean(body))
    return sents[0] if sents else ''


def _last_sentence(body):
    sents = _split_sentences(_body_clean(body))
    return sents[-1] if sents else ''


# ---------------------------------------------------------------------------
# 防呆：回顾（summary）可能过期——若它声称「本章获取 X」但正文并未在本章获取，
# 写回大纲会触发 check_outline_continuity 的「相邻大纲重复获取」报警。
# 因此回写前用正文校验：正文未确认的获取动词一律中性化（零 token）。
# 以下正则/物品判定与 continuity_state 的 _ACQUIRE_RE / _is_item 同口径，
# 避免耦合导入，就地复刻。
# ---------------------------------------------------------------------------
_ACQUIRE_RE = re.compile(
    r'(?:采[到得集]|获得|得到|拾得|摘得|挖出|挖到|捡到|取得|收下|收了|'
    r'夺[得到]|抢[得到]|摘下|采下|捞起|掘出)\s*'
    r'(?:[一二三四五六七八九十百零\d]*\s*[枚株朵棵截块片缕柄把只尾管页]\s*)?'  # 量词
    r'(?:的|之)?\s*'                                          # 「的」可穿插
    r'([一-龥]{1,12})'
)
_ITEM_TAILS = set(
    "莲花草果丹珠石铁剑刀书卷佩钥匙牌符图碎片残页根木晶骨甲羽鳞血液体"
    "钉卷轴残卷木盒玉简阵图殿壁简玦环链玉骨角筋砂粉蜜乳泉"
)
_KNOWN_ITEMS = {"黑心莲", "碎片", "钥匙", "古殿壁画", "碎日壁画", "玉佩",
                "镇魂钉", "残卷", "血书", "灵兽谷", "西谷矿洞", "矿洞",
                "黑水潭", "守魂钉", "断魂刀", "养魂木"}


def _normalize_item_local(name):
    name = (name or '').strip()
    name = re.sub(r'^(透过|隔着|沿着|顺着|绕过|穿过|离开|走向|望向|看向|迎着|'
                  r'在|从|到|至|来|去|往|向|对)\s*', '', name)
    name = re.sub(r'[，。；：、！？\s].*$', '', name)
    name = re.sub(r'^(把|将|我|他|她|其|这|那|之|此|些)\s*', '', name)
    name = re.sub(r'^[\d一二三四五六七八九十百零]+\s*[枚株朵棵截块片缕柄把只尾管页]\s*', '', name)
    name = re.sub(r'^(的|之|那|这|其|此|些)\s*', '', name)
    name = re.sub(r'^(把|将|我|他|她)\s*', '', name)
    return name


def _is_item_local(name):
    n = _normalize_item_local(name)
    if not n:
        return False
    if n in _KNOWN_ITEMS:
        return True
    for t in _ITEM_TAILS:
        if n.endswith(t):
            return True
    return False


def _body_confirms_acquire(body, item_norm):
    """正文是否真正在本章获取了 item_norm（与守卫同口径）。"""
    for m in _ACQUIRE_RE.finditer(body or ''):
        raw = m.group(1)
        if _is_item_local(raw) and _normalize_item_local(raw) == item_norm:
            return True
    return False


def _neutralize_unconfirmed_acquire(text, body):
    """把正文未确认的『获取 X』动词中性化为『受命获取 X』（不在 _ACQUIRE_RE 动词表内，
    不会触发相邻大纲重复获取报警）。返回 (new_text, n_fixed)。"""
    if not text:
        return text, 0
    fixed = [0]

    def repl(m):
        item_raw = m.group(1)
        if not _is_item_local(item_raw):
            return m.group(0)
        item_norm = _normalize_item_local(item_raw)
        if _body_confirms_acquire(body, item_norm):
            return m.group(0)          # 正文确在本章获取，保留
        fixed[0] += 1
        return '受命获取' + item_raw    # 中性化：去掉获取动词与量词

    new_text = _ACQUIRE_RE.sub(repl, text)
    return new_text, fixed[0]


def _sync_fields(body, summ):
    """返回待写回的四个字段（均源自正文/回顾，零 token）。
    回顾若声称本章获取某物但正文未确认，则中性化其获取动词，避免毒化大纲。"""
    opening = _first_sentence(body)
    ending = _last_sentence(body)
    if summ:
        one = (summ.get('one_line_summary') or '').strip()
        prog = (summ.get('plot_progress') or '').strip()
        plot_summary = one or (opening + ' ' + ending)
        core_events = prog or plot_summary
    else:
        plot_summary = (opening + ' ' + ending).strip()
        core_events = plot_summary
    # 防呆：用正文校验回顾里的获取动词
    opening, _ = _neutralize_unconfirmed_acquire(opening, body)
    ending, _ = _neutralize_unconfirmed_acquire(ending, body)
    plot_summary, _ = _neutralize_unconfirmed_acquire(plot_summary, body)
    core_events, _ = _neutralize_unconfirmed_acquire(core_events, body)
    return {
        'opening_hook': opening,
        'ending_hook': ending,
        'plot_summary': plot_summary,
        'core_events': core_events,
    }


def sync_outline_from_body(project_dir, chapter_id=None, dry_run=False, backup=True,
                           framework_first=False):
    """
    把正文要点回写进 chapter_outlines.json 的已写章节。

    framework_first（默认 False，保持旧行为以兼容已写 60 章的语义漂移治疗）：
      True  = 框架优先模式：大纲不追正文。只记录非权威的 body_snippet 供参考，
              并标记 framework_controlled=True，绝不覆盖权威规划字段
              (core_events/plot_summary/opening_hook/ending_hook)。
              这是「正文由大纲生成、只丰富过程」的落地——大纲是权威源，正文不反向改写大纲。
      False = 旧 B 方案：用正文要点回写大纲（让大纲≈正文，治语义漂移）。

    - chapter_id: 仅回写该章；None=全量回写。
    - dry_run: 只计算不落盘。
    - backup: 落盘前备份原文件（全量回写建议 True；流水线单章回写建议 False，因幂等可逆）。
    返回报告 dict: {synced:[...], skipped:[...], backup:path|None}
    """
    out_path = os.path.join(project_dir, '.novelforge', 'chapter_outlines.json')
    with open(out_path, encoding='utf-8') as f:
        data = json.load(f)

    report = {'synced': [], 'skipped': []}
    for ch in data:
        cid = ch.get('chapter_id')
        if chapter_id is not None and cid != chapter_id:
            continue
        body = _body_text(project_dir, cid)
        if body is None:
            # 未来章：无正文，保留原始规划
            report['skipped'].append(cid)
            continue
        if framework_first:
            # 框架优先：大纲不追正文。仅记录非权威参考片段，不碰权威规划。
            snip = (_first_sentence(body) + ' / ' + _last_sentence(body)).strip()
            ch['framework_controlled'] = True
            ch['body_snippet'] = snip
            report['synced'].append(cid)
            continue
        summ = _summary(project_dir, cid)
        fields = _sync_fields(body, summ)
        for k, v in fields.items():
            ch[k] = v
        report['synced'].append(cid)

    if not dry_run:
        if backup:
            stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            bak = out_path + f'.pre_backfill_{stamp}'
            shutil.copy(out_path, bak)
            report['backup'] = bak
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return report


def main():
    ap = argparse.ArgumentParser(description='大纲回流同步（零 token）')
    ap.add_argument('--project', required=True)
    ap.add_argument('--chapter', type=int, default=None, help='仅回写单章；省略=全量')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-backup', action='store_true',
                    help='落盘前不备份（流水线单章回写用，幂等可逆）')
    args = ap.parse_args()
    rep = sync_outline_from_body(
        args.project,
        chapter_id=args.chapter,
        dry_run=args.dry_run,
        backup=not args.no_backup,
    )
    print('synced:', rep['synced'])
    if rep['skipped']:
        print('skipped (无正文/未来章):', rep['skipped'])
    if 'backup' in rep:
        print('backup:', rep['backup'])


if '__main__' == '__name__':
    main()
