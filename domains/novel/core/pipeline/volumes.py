"""卷（volume）数据层 — 范围元数据模型。

设计：卷不改动任何章节文件存储（章节仍平铺在 chapters/），
只用 .novelforge/volumes.json 记录每卷的章节范围 [start_chapter, end_chapter]、
标题、主题、概要。插入/删除章节时只调整卷边界数字，风险最低。

卷成员判定：chapter_id 落在 [start_chapter, end_chapter] 内即属于该卷。
若某章因删除留下空洞而不在任何卷范围内，由 assign_chapters_to_volumes 兜底归到最近卷。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

VOLUMES_FILE = ".novelforge/volumes.json"


def _volumes_path(project_root) -> Path:
    return Path(project_root) / ".novelforge" / "volumes.json"


def load_volumes(project_root) -> list[dict]:
    """读取卷定义；不存在或损坏返回空列表。"""
    p = _volumes_path(project_root)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        vols = data.get("volumes", []) if isinstance(data, dict) else (data or [])
        return [dict(v) for v in vols]
    except Exception:
        return []


def save_volumes(project_root, volumes: list[dict]) -> None:
    """写回卷定义，并重排 order 字段。"""
    p = _volumes_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    cleaned = []
    for i, v in enumerate(volumes, 1):
        nv = dict(v)
        nv["order"] = i
        nv["id"] = nv.get("id", i)
        cleaned.append(nv)
    p.write_text(
        json.dumps({"volumes": cleaned}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _chapter_ids(project_root) -> list[int]:
    chapters_dir = Path(project_root) / "chapters"
    if not chapters_dir.exists():
        return []
    ids = []
    for p in chapters_dir.glob("chapter_*.md"):
        try:
            ids.append(int(p.stem.split("_")[1]))
        except (ValueError, IndexError):
            continue
    return sorted(ids)


def default_volumes(project_root) -> list[dict]:
    """为尚无卷定义的项目合成一个默认卷，覆盖全部现有章节。"""
    ids = _chapter_ids(project_root)
    max_id = max(ids) if ids else 0
    if max_id == 0:
        return []
    return [{
        "id": 1,
        "order": 1,
        "title": "第一卷",
        "theme": "",
        "start_chapter": 1,
        "end_chapter": max_id,
        "summary": "",
    }]


def get_volumes_or_default(project_root) -> list[dict]:
    """有卷定义则返回，否则返回合成的默认卷（不写盘）。"""
    vols = load_volumes(project_root)
    return vols if vols else default_volumes(project_root)


def get_volume_for_chapter(volumes: list[dict], chapter_id: int) -> Optional[dict]:
    """返回包含该章的卷（范围判定）。"""
    for v in volumes:
        if v.get("start_chapter", 0) <= chapter_id <= v.get("end_chapter", 0):
            return v
    return None


def _find_volume_index(volumes: list[dict], chapter_id: int) -> Optional[int]:
    """返回 chapter_id 应归属的卷下标；无法精确匹配时归到最近卷。"""
    if not volumes:
        return None
    for i, v in enumerate(volumes):
        if v.get("start_chapter", 0) <= chapter_id <= v.get("end_chapter", 0):
            return i
    starts = [v.get("start_chapter", 1) for v in volumes]
    if chapter_id < min(starts):
        return 0
    if chapter_id > max(v.get("end_chapter", 1) for v in volumes):
        return len(volumes) - 1
    # 落在空洞中：选与 chapter_id 距离最近的卷首
    best, best_d = 0, None
    for i, s in enumerate(starts):
        d = abs(s - chapter_id)
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def assign_chapters_to_volumes(
    volumes: list[dict], chapters: list[dict]
) -> tuple[dict, list]:
    """把章节列表分配到各卷。

    返回 (buckets, unassigned)：
      buckets: {卷下标: [章节 dict...]}
      unassigned: 无法归入任何卷的章节（volumes 为空时出现）
    chapters 元素需含 chapter_id。
    """
    buckets = {i: [] for i in range(len(volumes))}
    unassigned: list = []
    for ch in sorted(chapters, key=lambda c: c.get("chapter_id", 0)):
        vi = _find_volume_index(volumes, ch.get("chapter_id", 0))
        if vi is None:
            unassigned.append(ch)
        else:
            buckets[vi].append(ch)
    return buckets, unassigned


def shift_for_insert(volumes: list[dict], insert_pos: int) -> list[dict]:
    """插入章节后，按 insert_pos 顺延各卷边界。

    规则（基于插入前章节状态）：
      - 卷完全在插入点之前 (end < pos)：不变
      - 卷完全在插入点之后 (start > pos)：start/end 各 +1
      - 卷包含插入点 (start <= pos <= end)：end +1（卷内多一章）
    """
    out = []
    for v in volumes:
        s, e = v.get("start_chapter", 1), v.get("end_chapter", 1)
        if e < insert_pos:
            ns, ne = s, e
        elif s > insert_pos:
            ns, ne = s + 1, e + 1
        else:
            ns, ne = s, e + 1
        nv = dict(v)
        nv["start_chapter"] = ns
        nv["end_chapter"] = ne
        out.append(nv)
    return out


def shift_for_delete(volumes: list[dict], delete_pos: int) -> list[dict]:
    """删除章节后，按 delete_pos 顺延各卷边界（最佳努力；空洞由 assign 兜底）。

      - 卷完全在删除点之前：不变
      - 卷完全在删除点之后：start/end 各 -1
      - 卷包含删除点：end -1；若卷变空 (start>end) 则丢弃该卷
    """
    out = []
    for v in volumes:
        s, e = v.get("start_chapter", 1), v.get("end_chapter", 1)
        if e < delete_pos:
            ns, ne = s, e
        elif s > delete_pos:
            ns, ne = s - 1, e - 1
        else:
            ns, ne = s, e - 1
        if ns <= ne:
            nv = dict(v)
            nv["start_chapter"] = ns
            nv["end_chapter"] = ne
            out.append(nv)
    return out
