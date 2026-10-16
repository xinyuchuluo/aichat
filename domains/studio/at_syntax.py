"""@资产 语法解析（PRD §三）。

正则匹配：@([^\s，。；！？]+)
提取资产名 → 在项目资产池检索 → 成功建立连线，失败告警 + 弹窗就地新建。
纯字符串匹配，无 AI 语义理解。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# PRD §三：@([^\s，。；！？]+)
ASSET_PATTERN = re.compile(r"@([^\s，。；！？]+)")


@dataclass
class ParsedRef:
    """@资产 解析结果"""
    name: str         # 资产名（去 @ 前缀）
    exists: bool      # 是否存在于项目资产池
    asset_id: int = 0 # 存在时 asset_id；不存在为 0


def parse_asset_refs(text: str) -> list[str]:
    """从文本中提取所有 @资产名（去重，保持首次出现顺序）。"""
    if not text:
        return []
    seen: set[str] = set()
    names: list[str] = []
    for m in ASSET_PATTERN.finditer(text):
        name = m.group(1)
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def resolve_refs(text: str, project_id: int, db) -> list[ParsedRef]:
    """解析文本里的 @资产，并查库校验是否存在。

    Returns: 每个 @ 引用的 ParsedRef 列表
    """
    names = parse_asset_refs(text)
    refs: list[ParsedRef] = []
    for name in names:
        asset = db.find_asset_by_name(project_id, name)
        if asset:
            refs.append(ParsedRef(name=name, exists=True, asset_id=asset.id))
        else:
            refs.append(ParsedRef(name=name, exists=False))
    return refs


def highlight_missing(text: str, project_id: int, db) -> str:
    """给文本里不存在的 @资产 标红告警（用于 UI 渲染）。

    返回 HTML：缺失的 @资产名 红色高亮。
    """
    refs = resolve_refs(text, project_id, db)
    missing = {r.name for r in refs if not r.exists}
    if not missing:
        return text

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        if name in missing:
            return f'<span style="color:#ef4444">@{name}</span>'
        return m.group(0)

    return ASSET_PATTERN.sub(_replace, text)
