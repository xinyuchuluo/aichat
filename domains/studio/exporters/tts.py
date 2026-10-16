"""旁白文本 + 角色 TTS 参数导出模板。"""
from __future__ import annotations

import json


def export_tts(shots: list, db) -> str:
    """导出旁白文本 + 角色 TTS 参数。

    拼接分镜旁白 + 引用角色的 TTS 配置。
    """
    lines = ["# 旁白 TTS 导出", ""]

    # 收集所有角色资产 + TTS 配置
    roles: dict[str, dict] = {}
    for s in shots:
        for link in db.list_links_for_shot(s.id):
            if link["asset_type"] == "角色" and link["id"] not in roles:
                # 取资产完整信息（含 tts_config）
                asset = db.get_asset(link["id"])
                if asset:
                    try:
                        tts = json.loads(asset.tts_config or "{}")
                    except (json.JSONDecodeError, ValueError):
                        tts = {}
                    roles[link["id"]] = {
                        "name": link["asset_name"],
                        "tts": tts,
                    }

    # TTS 配置表
    if roles:
        lines.append("## 角色 TTS 配置")
        for rid, info in roles.items():
            lines.append(f"### {info['name']}")
            if info["tts"]:
                lines.append(f"  {json.dumps(info['tts'], ensure_ascii=False)}")
            lines.append("")

    # 分镜旁白
    lines.append("## 分镜旁白")
    for s in shots:
        if s.narration:
            lines.append(f"### {s.shot_id} {s.shot_name}")
            lines.append(s.narration)
            lines.append("")

    return "\n".join(lines)
