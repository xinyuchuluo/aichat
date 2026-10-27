"""MemSync MCP server — 让任意 AI 工具读/写同一份项目记忆。

传输：stdio，newline-delimited JSON-RPC 2.0（兼容 Content-Length 帧）。
零第三方依赖，Mac/Win 通用。

工具（渐进披露：先 resolve → brief，需要细节再 search/timeline/get）：
    mem_project_resolve   当前目录属于哪个项目
    mem_project_brief     项目简报（人工段 + 自动段）
    mem_project_register  注册项目根目录（需 --allow-write）
    mem_search            关键词检索（默认全库，可按项目收窄）
    mem_timeline          项目时间线
    mem_get               按 uuid 取原始记录
    mem_note              追加一条记忆（需 --allow-write，只追加不改删）

写入纪律：只 append，不 update/delete；每条记录带 source。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any

from .brief import refresh_project_file, render_brief
from .engine import MemoryEngine
from .paths import MemoryProfile, resolve_profile
from .project_store import Project, ProjectStore
from .types import (
    MemoryRecord,
    VALID_OBSERVATIONS,
    OBSERVATION_DECISION,
    SLOT_OBSERVATIONS,
    normalize_observation,
    now_iso,
)

SERVER_NAME = "memsync"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

_NOT_WRITABLE = "当前为只读模式：启动 memsync-mcp 时加 --allow-write 才能写入。"


class ToolError(Exception):
    """工具层错误（回给模型看，不中断连接）。"""


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


_PROJECT_REF = {
    "project_id": {"type": "string", "description": "项目 id（可省略，用 cwd 解析）"},
    "ref": {
        "type": "string",
        "description": "项目标识：project_id / 显示名 / 别名（如 study、学习、学习实验室）。"
                       "不知道名字就先调 mem_projects 看清单",
    },
    "cwd": {"type": "string", "description": "工作目录，默认进程 cwd"},
}

_TOOLS: list[dict] = [
    {
        "name": "mem_project_resolve",
        "description": "定位项目：传 ref（id/显示名/别名）按名字查，传 cwd 按目录查。"
                       "返回 registered / project_id / 简报路径；未注册时回 hint。",
        "inputSchema": _schema({"ref": _PROJECT_REF["ref"], "cwd": _PROJECT_REF["cwd"]}),
    },
    {
        "name": "mem_projects",
        "description": "列出所有已注册项目（project_id / 显示名 / 别名 / 路径 / 记忆条数）。"
                       "不知道项目叫什么、或同名混淆时，先调这个。",
        "inputSchema": _schema({}),
    },
    {
        "name": "mem_project_brief",
        "description": "读取项目简报：人工维护段 + 自动同步段，最多 200 行。开工前先读这个。",
        "inputSchema": _schema(_PROJECT_REF),
    },
    {
        "name": "mem_project_register",
        "description": "把项目根目录注册到本地项目表，并生成/刷新 PROJECT_MEMORY.md。",
        "inputSchema": _schema(
            {
                "path": {"type": "string", "description": "项目根绝对路径"},
                "name": {"type": "string", "description": "显示名"},
                "aliases": {"type": "array", "items": {"type": "string"}},
            },
            ["path"],
        ),
    },
    {
        "name": "mem_search",
        "description": "关键词检索历史记忆（中文可用）。不带项目则全库检索。",
        "inputSchema": _schema(
            {
                "query": {"type": "string"},
                "project_id": _PROJECT_REF["project_id"],
                "cwd": _PROJECT_REF["cwd"],
                "top_k": {"type": "integer", "default": 10},
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "限定 observation_type，如 decision/bugfix/fact",
                },
            },
            ["query"],
        ),
    },
    {
        "name": "mem_timeline",
        "description": "按时间倒序读项目记忆（默认 30 条）。",
        "inputSchema": _schema(
            {
                "project_id": _PROJECT_REF["project_id"],
                "cwd": _PROJECT_REF["cwd"],
                "limit": {"type": "integer", "default": 30},
            }
        ),
    },
    {
        "name": "mem_get",
        "description": "按 uuid 取一条原始记忆。",
        "inputSchema": _schema({"uuid": {"type": "string"}}, ["uuid"]),
    },
    {
        "name": "mem_note",
        "description": "追加一条项目记忆（决策/修复/事实…）。只追加，不修改不删除。需 --allow-write。",
        "inputSchema": _schema(
            {
                "content": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": sorted(VALID_OBSERVATIONS),
                    "default": OBSERVATION_DECISION,
                },
                "key": {"type": "string", "description": "状态型记忆的槽位键（fact/preference 必填）"},
                "project_id": _PROJECT_REF["project_id"],
                "cwd": _PROJECT_REF["cwd"],
                "source": {"type": "string", "description": "写入方，如 trae/codex/workbuddy"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            ["content"],
        ),
    },
]


def _fmt_record(record: MemoryRecord, score: float | None = None) -> str:
    head = f"[{record.observation_type}] {record.timestamp[:16].replace('T', ' ')}"
    if score is not None:
        head += f" score={score:.3f}"
    head += f" scope={record.scope or '-'} uuid={record.uuid}"
    return f"{head}\n{record.content}"


class MemSyncServer:
    def __init__(
        self,
        profile: MemoryProfile,
        allow_write: bool = False,
        cwd: str = "",
    ):
        self.profile = profile
        self.allow_write = allow_write
        self._engine = MemoryEngine(profile.root)
        self._projects = ProjectStore(profile.projects_db, profile.name)
        self._default_cwd = cwd or os.getcwd()

    # ---- JSON-RPC ----

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        msg_id = message.get("id")
        if not method or msg_id is None:
            return None  # 通知（notifications/initialized 等）无需响应

        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                        "profile": self.profile.name,
                    },
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _TOOLS}
            elif method == "tools/call":
                result = self._call_tool(message.get("params") or {})
            else:
                return _error(msg_id, -32601, f"method not found: {method}")
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as exc:  # noqa: BLE001  -- 单次调用失败不能拖垮连接
            return _error(msg_id, -32603, f"{type(exc).__name__}: {exc}")

    def _call_tool(self, params: dict) -> dict:
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True}
        try:
            return {"content": [{"type": "text", "text": handler(args)}]}
        except ToolError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            }

    # ---- 项目定位 ----

    def _resolve(self, args: dict) -> Project | None:
        # project_id 是历史字段；ref 是"按名字找"的通用入口（id / 显示名 / 别名 / 路径都行）
        ref = str(args.get("project_id") or args.get("ref") or "")
        if ref:
            project = self._projects.resolve_ref(ref)
            if project is None:
                project = self._projects.get_by_path(ref)
            return project
        return self._projects.resolve(args.get("cwd") or self._default_cwd)

    def _memory_count(self, project_id: str) -> int:
        try:
            conn = sqlite3.connect(self.profile.raw_db)
            try:
                return int(
                    conn.execute(
                        "SELECT count(*) FROM raw_memories WHERE scope = ? AND is_expired = 0",
                        (f"project:{project_id}",),
                    ).fetchone()[0]
                )
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 统计失败不该拖垮定位
            return -1

    def _require(self, args: dict) -> Project:
        project = self._resolve(args)
        if project is None:
            ref = args.get("ref") or args.get("project_id")
            where = f"ref={ref}" if ref else f"cwd={args.get('cwd') or self._default_cwd}"
            known = "、".join(f"{p.name}({p.project_id})" for p in self._projects.list()) or "（空）"
            raise ToolError(
                f"未找到项目：{where}。已注册项目：{known}。"
                "按名字查用 ref=显示名或别名；不知道名字先调 mem_projects。"
            )
        return project

    # ---- 工具实现 ----

    def _tool_mem_projects(self, args: dict) -> str:
        rows = [
            {
                "project_id": p.project_id,
                "name": p.name,
                "aliases": [a for a in p.aliases.split(",") if a],
                "root_path": p.root_path,
                "memories": self._memory_count(p.project_id),
                "last_seen_at": p.last_seen_at or "",
            }
            for p in self._projects.list()
        ]
        return json.dumps(
            {"profile": self.profile.name, "count": len(rows), "projects": rows},
            ensure_ascii=False,
            indent=2,
        )

    def _tool_mem_project_resolve(self, args: dict) -> str:
        project = self._resolve(args)
        if project is None:
            ref = args.get("ref") or args.get("project_id")
            cwd = args.get("cwd") or self._default_cwd
            return json.dumps(
                {
                    "registered": False,
                    "queried": {"ref": ref or "", "cwd": cwd},
                    "profile": self.profile.name,
                    "hint": "按名字查请传 ref=显示名/别名；不知道名字先调 mem_projects；"
                            "确实没注册过再 mem_project_register(path=...)",
                },
                ensure_ascii=False,
                indent=2,
            )
        return json.dumps(
            {"registered": True, "profile": self.profile.name, **project.to_dict()},
            ensure_ascii=False,
            indent=2,
        )

    def _tool_mem_project_brief(self, args: dict) -> str:
        project = self._require(args)
        if self.allow_write:
            refresh_project_file(self._engine, project)
        return render_brief(self._engine, project)

    def _tool_mem_project_register(self, args: dict) -> str:
        self._require_write()
        root = str(args.get("path") or "").strip()
        if not root:
            raise ToolError("path 不能为空")
        try:
            project = self._projects.register(
                root, name=str(args.get("name") or ""), aliases=args.get("aliases") or ()
            )
        except ValueError as exc:   # 名字/别名冲突
            raise ToolError(str(exc)) from exc
        path = refresh_project_file(self._engine, project)
        return json.dumps(
            {"registered": True, **project.to_dict(), "brief_created": str(path)},
            ensure_ascii=False,
            indent=2,
        )

    def _tool_mem_search(self, args: dict) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("query 不能为空")
        project = self._resolve(args)
        scope = project.scope if project else ""
        types = tuple(t for t in (args.get("types") or ()) if t in VALID_OBSERVATIONS)
        top_k = max(1, min(int(args.get("top_k") or 10), 50))

        results = self._engine.search(
            query, scope=scope, top_k=top_k, observation_types=types
        )
        if not results:
            return "（无命中）"
        blocks = []
        for i, r in enumerate(results, 1):
            head = (
                f"[{i}] [{r.observation_type}] "
                f"{r.timestamp[:16].replace('T', ' ')} score={r.score:.3f} "
                f"scope={r.scope or '-'} uuid={r.uuid}"
            )
            blocks.append(f"{head}\n{r.content}")
        return "\n\n".join(blocks)

    def _tool_mem_timeline(self, args: dict) -> str:
        project = self._require(args)
        limit = max(1, min(int(args.get("limit") or 30), 200))
        records = self._engine.timeline(project.scope, limit=limit)
        if not records:
            return "（暂无记录）"
        return "\n\n".join(_fmt_record(r) for r in records)

    def _tool_mem_get(self, args: dict) -> str:
        uuid_str = str(args.get("uuid") or "").strip()
        record = self._engine.get(uuid_str)
        if record is None:
            raise ToolError(f"未找到 uuid: {uuid_str}")
        return _fmt_record(record)

    def _tool_mem_note(self, args: dict) -> str:
        self._require_write()
        project = self._require(args)
        content = str(args.get("content") or "").strip()
        if not content:
            raise ToolError("content 不能为空")

        obs = normalize_observation(str(args.get("type") or ""), OBSERVATION_DECISION)
        key = str(args.get("key") or "").strip()
        if obs in SLOT_OBSERVATIONS and not key:
            raise ToolError(f"{obs} 是状态型记忆，必须提供 key（槽位名）")

        source = str(args.get("source") or "mcp").strip() or "mcp"
        metadata = {
            "source": source,
            "project_id": project.project_id,
            "key": key,
            "tags": list(args.get("tags") or ()),
        }
        record = MemoryRecord(
            session_id=f"memsync:{source}",
            content=content,
            role="external",
            scope=project.scope,
            observation_type=obs,
            metadata=metadata,
            timestamp=now_iso(),
            fact_slot=key,
        )
        self._engine.append_record(record)
        refresh_project_file(self._engine, project)
        return json.dumps(
            {"ok": True, "uuid": record.uuid, "scope": project.scope, "type": obs},
            ensure_ascii=False,
            indent=2,
        )

    def _require_write(self) -> None:
        if not self.allow_write:
            raise ToolError(_NOT_WRITABLE)

    # ---- 传输 ----

    def serve(self, stdin=None, stdout=None) -> None:
        inp = stdin or sys.stdin
        out = stdout or sys.stdout
        for raw in _iter_messages(inp):
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"[memsync] invalid json: {exc}", file=sys.stderr)
                continue
            response = self.handle(message)
            if response is not None:
                out.write(json.dumps(response, ensure_ascii=False) + "\n")
                out.flush()


def _iter_messages(stream):
    """同时兼容 newline-delimited 与 Content-Length 帧。"""
    while True:
        line = stream.readline()
        if not line:
            return
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("content-length:"):
            try:
                length = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                continue
            # 跳过剩余头部直到空行
            while True:
                header = stream.readline()
                if not header or not header.strip():
                    break
            yield stream.read(length)
            continue
        yield stripped


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memsync-mcp", description="MemSync MCP server (stdio)")
    parser.add_argument("--profile", default="", help="记忆档案，默认 personal 或 MEMSYNC_PROFILE")
    parser.add_argument("--cwd", default="", help="默认工作目录（客户端未传 cwd 时使用）")
    parser.add_argument(
        "--allow-write",
        action="store_true",
        default=os.environ.get("MEMSYNC_ALLOW_WRITE") == "1",
        help="允许 mem_note / mem_project_register 写入",
    )
    args = parser.parse_args(argv)

    profile = resolve_profile(args.profile)
    server = MemSyncServer(profile, allow_write=args.allow_write, cwd=args.cwd)
    print(
        f"[memsync] profile={profile.name} root={profile.root} "
        f"write={'on' if server.allow_write else 'off'}",
        file=sys.stderr,
    )
    server.serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
