"""Blender bpy 脚本生成 Prompt 导出模板。"""
from __future__ import annotations


def export_blender(shots: list, db) -> str:
    """导出 Blender bpy 脚本生成 Prompt。

    拼接分镜机位描述 + 白模可行性备注 + 引用资产描述。
    """
    lines = ["# Blender bpy 脚本生成 Prompt", ""]
    for s in shots:
        lines.append(f"## {s.shot_id} {s.shot_name}")
        lines.append(f"  机位: {s.camera_desc}")
        lines.append(f"  时长: {s.duration}")
        if s.blender_feasibility_note:
            lines.append(f"  白模可行性: {s.blender_feasibility_note}")
        for link in db.list_links_for_shot(s.id):
            if link["asset_desc"]:
                lines.append(f"  @{link['asset_name']}: {link['asset_desc']}")
        lines.append("")
    return "\n".join(lines)
