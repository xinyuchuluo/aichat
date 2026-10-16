"""Seedance/小云雀视频生成提示词导出模板。"""
from __future__ import annotations


def export_seedance(shots: list, db) -> str:
    """导出 Seedance 视频生成提示词。

    拼接分镜画面 + 旁白 + 引用资产描述/关键词。
    """
    lines = ["# Seedance 视频生成提示词", ""]
    for s in shots:
        lines.append(f"## {s.shot_id} {s.shot_name}")
        if s.scene_text:
            lines.append(f"  画面: {s.scene_text}")
        if s.narration:
            lines.append(f"  旁白: {s.narration}")
        if s.duration:
            lines.append(f"  时长: {s.duration}")
        for link in db.list_links_for_shot(s.id):
            if link["asset_keywords"]:
                lines.append(f"  @{link['asset_name']}: {link['asset_keywords']}")
        lines.append("")
    return "\n".join(lines)
