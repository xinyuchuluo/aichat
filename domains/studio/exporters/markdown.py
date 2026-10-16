"""Markdown 完整分镜文档导出模板。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Storyboard, StudioDB


def export_markdown(shots: list, db) -> str:
    """导出 Markdown 分镜文档。

    自动收集所有 @ 引用资产，按角色/场景/道具分组，
    拼接资产描述、关键词、建议提示词，再拼接分镜机位、画面描述、旁白。
    """
    lines = ["# 分镜文档", ""]

    # 收集所有引用资产（去重）
    all_assets: dict[str, dict] = {}  # asset_id -> asset dict
    for shot in shots:
        for link in db.list_links_for_shot(shot.id):
            if link["id"] not in all_assets:
                all_assets[link["id"]] = link

    # 资产分组
    by_type: dict[str, list] = {"角色": [], "场景": [], "道具": []}
    for a in all_assets.values():
        by_type.setdefault(a["asset_type"], []).append(a)

    for cat in ["角色", "场景", "道具"]:
        items = by_type.get(cat, [])
        if items:
            lines.append(f"## {cat}")
            for a in items:
                lines.append(f"### {a['asset_name']}")
                if a["asset_desc"]:
                    lines.append(a["asset_desc"])
                if a["asset_keywords"]:
                    lines.append(f"**关键词**: {a['asset_keywords']}")
                lines.append("")

    # 分镜列表
    lines.append("## 分镜")
    for s in shots:
        lines.append(f"### {s.shot_id} {s.shot_name}")
        lines.append(f"- **时长**: {s.duration}")
        lines.append(f"- **机位**: {s.camera_desc}")
        lines.append(f"- **状态**: {s.status}")
        if s.scene_text:
            lines.append(f"- **画面**: {s.scene_text}")
        if s.narration:
            lines.append(f"- **旁白**: {s.narration}")
        if s.blender_feasibility_note:
            lines.append(f"- **白模可行性**: {s.blender_feasibility_note}")
        lines.append("")

    return "\n".join(lines)
