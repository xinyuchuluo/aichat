#!/usr/bin/env python3
"""MemSync 命令行 — 人工注册项目、看简报、记一条决策。

    python3 scripts/memsync.py resolve                     # 当前目录属于哪个项目
    python3 scripts/memsync.py register ~/AIChat --name AIChat
    python3 scripts/memsync.py brief --refresh            # 刷新并打印项目简报
    python3 scripts/memsync.py note "改用 core/vector_spi" --type decision --source trae
    python3 scripts/memsync.py search 记忆内核 --limit 5
    python3 scripts/memsync.py timeline --limit 20

全局参数：--profile company / --read-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.memory.brief import refresh_project_file, render_brief  # noqa: E402
from core.memory.engine import MemoryEngine  # noqa: E402
from core.memory.paths import MemoryProfile, resolve_profile  # noqa: E402
from core.memory.project_store import ProjectStore  # noqa: E402
from core.memory.types import (  # noqa: E402
    MemoryRecord,
    OBSERVATION_DECISION,
    SLOT_OBSERVATIONS,
    VALID_OBSERVATIONS,
    normalize_observation,
    now_iso,
)


def _profile(args) -> MemoryProfile:
    return resolve_profile(args.profile)


def _find(store: ProjectStore, ref: str):
    project = store.resolve_ref(ref) if ref else store.resolve(os.getcwd())
    if project is None:
        raise SystemExit(f"未找到项目：{ref or os.getcwd()}（先执行 register）")
    return project


def cmd_resolve(args) -> None:
    profile = _profile(args)
    store = ProjectStore(profile.projects_db, profile.name)
    project = store.resolve(args.path or os.getcwd())
    if project is None:
        print(json.dumps(
            {"registered": False, "cwd": args.path or os.getcwd(), "profile": profile.name},
            ensure_ascii=False, indent=2))
        return
    print(json.dumps({"registered": True, "profile": profile.name, **project.to_dict()},
                     ensure_ascii=False, indent=2))


def cmd_register(args) -> None:
    profile = _profile(args)
    store = ProjectStore(profile.projects_db, profile.name)
    engine = MemoryEngine(profile.root)
    aliases = [a for a in (args.aliases or "").split(",") if a.strip()]
    try:
        project = store.register(args.path, name=args.name, aliases=aliases)
    except ValueError as exc:      # 名字/别名冲突：直接说清楚，别让客户端去猜
        raise SystemExit(f"[memsync] {exc}")
    path = refresh_project_file(engine, project)
    print(json.dumps({"registered": True, **project.to_dict(), "brief": str(path)},
                     ensure_ascii=False, indent=2))


def cmd_projects(args) -> None:
    profile = _profile(args)
    store = ProjectStore(profile.projects_db, profile.name)
    for project in store.list():
        print(f"{project.project_id}\t{project.name}\t{project.root_path}")


def cmd_brief(args) -> None:
    profile = _profile(args)
    store = ProjectStore(profile.projects_db, profile.name)
    project = _find(store, args.ref)
    engine = MemoryEngine(profile.root)
    if args.refresh:
        path = refresh_project_file(engine, project)
        print(f"[memsync] 已刷新 {path}\n", file=sys.stderr)
    print(render_brief(engine, project))


def cmd_note(args) -> None:
    if args.read_only:
        raise SystemExit("只读模式：不能写入记忆")
    profile = _profile(args)
    store = ProjectStore(profile.projects_db, profile.name)
    project = _find(store, args.ref)
    engine = MemoryEngine(profile.root)

    obs = normalize_observation(args.type, OBSERVATION_DECISION)
    if obs not in VALID_OBSERVATIONS:
        raise SystemExit(f"未知 type: {args.type}")
    key = (args.key or "").strip()
    if obs in SLOT_OBSERVATIONS and not key:
        raise SystemExit(f"{obs} 是状态型记忆，必须提供 --key")
    source = (args.source or "cli").strip() or "cli"

    record = MemoryRecord(
        session_id=f"memsync:{source}",
        content=args.content,
        role="external",
        scope=project.scope,
        observation_type=obs,
        metadata={
            "source": source,
            "project_id": project.project_id,
            "key": key,
            "tags": list(args.tag or ()),
        },
        timestamp=now_iso(),
        fact_slot=key,
    )
    engine.append_record(record)
    refresh_project_file(engine, project)
    print(json.dumps({"ok": True, "uuid": record.uuid, "scope": project.scope},
                     ensure_ascii=False, indent=2))


def cmd_search(args) -> None:
    profile = _profile(args)
    store = ProjectStore(profile.projects_db, profile.name)
    project = store.resolve_ref(args.ref) if args.ref else None
    engine = MemoryEngine(profile.root)
    types = tuple(t for t in (args.types or "").split(",") if t in VALID_OBSERVATIONS)
    results = engine.search(
        args.query,
        scope=project.scope if project else "",
        top_k=args.limit,
        observation_types=types,
    )
    if not results:
        print("（无命中）")
        return
    for i, r in enumerate(results, 1):
        print(f"[{i}] [{r.observation_type}] {r.timestamp[:16]} score={r.score:.3f} "
              f"scope={r.scope or '-'} uuid={r.uuid}")
        print(r.content)
        print()


def cmd_timeline(args) -> None:
    profile = _profile(args)
    store = ProjectStore(profile.projects_db, profile.name)
    project = _find(store, args.ref)
    engine = MemoryEngine(profile.root)
    records = engine.timeline(project.scope, limit=args.limit)
    if not records:
        print("（暂无记录）")
        return
    for r in records:
        print(f"[{r.observation_type}] {r.timestamp[:16]} {r.uuid}")
        print(r.content)
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memsync", description="MemSync 项目记忆命令行")
    parser.add_argument("--profile", default="", help="记忆档案，默认 personal / MEMSYNC_PROFILE")
    parser.add_argument("--read-only", action="store_true", help="禁止写入")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("resolve", help="当前目录属于哪个项目")
    p.add_argument("path", nargs="?", default="")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("register", help="注册项目根目录")
    p.add_argument("path")
    p.add_argument("--name", default="")
    p.add_argument("--aliases", default="")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("projects", help="列出已注册项目")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("brief", help="打印项目简报")
    p.add_argument("ref", nargs="?", default="", help="project_id 或路径，默认当前目录")
    p.add_argument("--refresh", action="store_true", help="同时刷新 PROJECT_MEMORY.md 自动段")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("note", help="追加一条记忆")
    p.add_argument("content")
    p.add_argument("--type", default=OBSERVATION_DECISION, choices=sorted(VALID_OBSERVATIONS))
    p.add_argument("--key", default="", help="状态型记忆槽位键")
    p.add_argument("--ref", default="", help="project_id 或路径，默认当前目录")
    p.add_argument("--source", default="cli")
    p.add_argument("--tag", action="append")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("search", help="关键词检索")
    p.add_argument("query")
    p.add_argument("--ref", default="", help="限定项目；省略则全库")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--types", default="", help="逗号分隔的 observation_type")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("timeline", help="项目时间线")
    p.add_argument("ref", nargs="?", default="")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_timeline)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
