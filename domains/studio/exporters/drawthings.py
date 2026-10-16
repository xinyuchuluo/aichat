"""DrawThings 绘图提示词导出模板。"""
from __future__ import annotations


def export_drawthings(shots: list, db) -> str:
    """导出 DrawThings 绘图提示词。

    拼接分镜画面描述 + 引用资产的关键词/建议提示词。
    """
    lines = ["# DrawThings 绘图提示词", ""]
    for s in shots:
        lines.append(f"## {s.shot_id} {s.shot_name}")
        # 收集本镜头引用资产的关键词
        for link in db.list_links_for_shot(s.id):
            if link["asset_keywords"]:
                lines.append(f"  @{link['asset_name']}: {link['asset_keywords']}")
        if s.scene_text:
            lines.append(f"  画面: {s.scene_text}")
        lines.append("")
    return "\n".join(lines)
