"""项目简报 — 仓库内 PROJECT_MEMORY.md 的组装与刷新。

双轨：
- 人工段：开发者/用户自己写，MemSync 永不覆盖
- 自动段：夹在 AUTO_BEGIN / AUTO_END 标记之间，由本地记忆库生成

简报读取顺序（也是给 agent 的优先级）：
人工段 > 库里最新有效事实 > 最近决策/修复/事件时间线
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .project_store import Project

if TYPE_CHECKING:  # pragma: no cover - 仅类型提示
    from .engine import MemoryEngine

AUTO_BEGIN = "<!-- memsync:auto:begin -->"
AUTO_END = "<!-- memsync:auto:end -->"

MAX_BRIEF_LINES = 200
MAX_FACTS = 20
MAX_TIMELINE = 25
MAX_CONTENT_CHARS = 300

_LABEL = {
    "fact": "事实",
    "preference": "偏好",
    "entity": "实体",
    "reminder": "提醒",
    "decision": "决策",
    "bugfix": "修复",
    "event": "事件",
    "flow_step": "流程",
    "chat": "对话",
}

_TIMELINE_TYPES = ("decision", "bugfix", "event", "flow_step")


def _label(observation_type: str) -> str:
    return _LABEL.get(observation_type, observation_type or "记录")


def _fmt_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value or ""


def _one_line(text: str, limit: int = MAX_CONTENT_CHARS) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _template(project: Project) -> str:
    return (
        f"# PROJECT_MEMORY — {project.name}\n"
        "\n"
        "> 本文件供任何 AI 编码工具阅读：进入项目先读它，就知道定位、约束与当前进度。\n"
        "> 「自动同步段」由 MemSync 从本地记忆库刷新，请勿手动编辑；其余内容人工维护、永不被覆盖。\n"
        "\n"
        "## 人工备注\n"
        "\n"
        "- 项目定位：\n"
        "- 关键约束：\n"
        "- 当前目标：\n"
        "- 常用命令：\n"
        "\n"
        f"{AUTO_BEGIN}\n"
        f"{AUTO_END}\n"
    )


def build_auto_section(engine: "MemoryEngine", project: Project) -> str:
    """生成自动段内容（不含标记）。"""
    lines: list[str] = [
        "## 自动同步段（MemSync 生成）",
        "",
        f"- 项目：{project.name}（`{project.project_id}`）",
        f"- 路径：`{project.root_path}`",
    ]

    timeline = [
        r for r in engine.timeline(project.scope, limit=80)
        if r.observation_type in _TIMELINE_TYPES
    ]
    latest = timeline[0].timestamp if timeline else ""
    lines.append(f"- 最近更新：{_fmt_time(latest) or '暂无记录'}")
    lines.append("")

    lines.append("### 当前有效事实")
    facts = _current_facts(engine, project)
    if facts:
        lines.extend(
            f"- [{_label(r.observation_type)}] {_one_line(r.content)}"
            f"（{_fmt_time(r.timestamp)}）"
            for r in facts
        )
    else:
        lines.append("- （暂无）")
    lines.append("")

    lines.append("### 最近决策与变更")
    if timeline:
        lines.extend(
            f"- {_fmt_time(r.timestamp)} [{_label(r.observation_type)}] "
            f"{_one_line(r.content)}（来源：{r.metadata.get('source', r.role or 'unknown')}）"
            for r in timeline[:MAX_TIMELINE]
        )
    else:
        lines.append("- （暂无）")
    return "\n".join(lines)


def _current_facts(engine: "MemoryEngine", project: Project) -> list:
    """槽位当前值（状态型记忆只取最新值）。"""
    slots = engine.sqlite.slots(project.scope)
    uuids = [uid for (_, _), uid in slots.items()]
    if not uuids:
        return []
    records = [r for r in engine.sqlite.get_by_uuids(uuids) if not r.is_expired]
    records.sort(key=lambda r: r.timestamp, reverse=True)
    return records[:MAX_FACTS]


def _replace_auto_section(text: str, auto: str) -> str:
    begin = text.find(AUTO_BEGIN)
    if begin < 0:
        return text.rstrip() + "\n\n" + AUTO_BEGIN + "\n" + auto + "\n" + AUTO_END + "\n"
    end = text.find(AUTO_END, begin)
    tail = "" if end < 0 else text[end + len(AUTO_END):]
    return text[:begin] + AUTO_BEGIN + "\n" + auto + "\n" + AUTO_END + tail


def render_brief(engine: "MemoryEngine", project: Project) -> str:
    """返回完整简报文本（人工段 + 最新自动段），不落盘，最多 200 行。"""
    path = project.brief_path
    base = path.read_text(encoding="utf-8") if path.is_file() else _template(project)
    text = _replace_auto_section(base, build_auto_section(engine, project))
    return _limit_lines(text)


def _limit_lines(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= MAX_BRIEF_LINES:
        return text
    kept = lines[: MAX_BRIEF_LINES - 1]
    kept.append(f"...（简报已截断，完整原文见 PROJECT_MEMORY.md）")
    return "\n".join(kept)


def refresh_project_file(
    engine: "MemoryEngine", project: Project, path: str | Path | None = None
) -> Path:
    """把最新自动段写回 PROJECT_MEMORY.md；人工段原样保留。"""
    target = Path(path) if path else project.brief_path
    base = target.read_text(encoding="utf-8") if target.is_file() else _template(project)
    updated = _replace_auto_section(base, build_auto_section(engine, project))
    if not target.is_file() or target.read_text(encoding="utf-8") != updated:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(updated, encoding="utf-8")
    return target


def manual_section(text: str) -> str:
    """仅取人工段（标记之前的内容）。"""
    idx = text.find(AUTO_BEGIN)
    return (text if idx < 0 else text[:idx]).rstrip()
