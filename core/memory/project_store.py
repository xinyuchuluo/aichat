"""项目注册表 — cwd 最长路径匹配。

不往项目仓库里塞任何识别文件（避免污染别人的仓库），
项目身份只存在本地 projects.db：

    project_id   人类可读 slug + 路径短哈希，如 aichat-3f9a1c
    root_path    项目根绝对路径（唯一）
    name         显示名
    profile      所属档案（personal / company）

记忆 scope 约定为 ``project:<project_id>``，
同一个项目被任何软件写入时都落到同一 scope。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    root_path    TEXT NOT NULL UNIQUE,
    profile      TEXT NOT NULL DEFAULT 'personal',
    aliases      TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_projects_profile ON projects(profile);
"""


def _now_iso() -> str:
    return datetime.now().isoformat()


def _norm(path: str | Path) -> str:
    """统一成可比较的绝对路径（展开 ~，解析符号链接）。"""
    p = Path(path).expanduser()
    try:
        return os.path.normcase(str(p.resolve()))
    except OSError:
        return os.path.normcase(str(p.absolute()))


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:24] or "project"


def project_scope(project_id: str) -> str:
    return f"project:{project_id}"


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    root_path: str
    profile: str = "personal"
    aliases: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_seen_at: str = ""

    @property
    def root(self) -> Path:
        return Path(self.root_path)

    @property
    def scope(self) -> str:
        return project_scope(self.project_id)

    @property
    def brief_path(self) -> Path:
        return self.root / "PROJECT_MEMORY.md"

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "root_path": self.root_path,
            "profile": self.profile,
            "scope": self.scope,
            "aliases": self.aliases,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
            "brief_path": str(self.brief_path),
        }


class ProjectStore:
    """projects.db 的读写门面。"""

    def __init__(self, db_path: str | Path, profile: str = "personal"):
        self._db_path = str(db_path)
        self._profile = profile
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(20):
            try:
                with self._conn() as conn:
                    current = conn.execute("PRAGMA journal_mode").fetchone()[0]
                    if current != "wal":
                        conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.executescript(SCHEMA)
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 19:
                    raise
                time.sleep(0.05)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ---- 写入 ----

    def register(
        self,
        root: str | Path,
        name: str = "",
        aliases: tuple[str, ...] | list[str] = (),
    ) -> Project:
        """注册项目根目录；同路径重复注册只更新名称/别名。

        为什么必须查冲突：别名/显示名是"按名字找项目"的唯一入口，一旦两个项目共用
        （真实事故：新项目 StudyLab 注册时带了 `学习项目,学习实验室`，把 Study 的名字抢走了，
        之后 `brief 学习实验室` 指向那个零记忆的新项目），客户端就会找错项目、以为"没注册"。
        """
        root_path = _norm(root)
        display = (name or Path(root_path).name or "project").strip()
        alias_list = [a.strip() for a in aliases if a and a.strip()]
        alias_text = ",".join(alias_list)
        now = _now_iso()

        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE root_path = ?", (root_path,)
            ).fetchone()
            pid = row["project_id"] if row else f"{_slug(display)}-{hashlib.sha1(root_path.encode()).hexdigest()[:6]}"

            # 名字/别名唯一性：同一个名字只能属于一个项目
            conflicts = self._name_conflicts(display, alias_list, exclude=pid)
            if conflicts:
                detail = "；".join(f"`{key}` 已属于 {owner}" for key, owner in conflicts)
                raise ValueError(
                    f"项目名/别名冲突：{detail}。换一个名字，或先从原项目里移除该别名。"
                )

            if row:
                conn.execute(
                    """UPDATE projects SET name = ?, aliases = ?, updated_at = ?
                       WHERE project_id = ?""",
                    (display, alias_text, now, pid),
                )
            else:
                conn.execute(
                    """INSERT INTO projects
                       (project_id, name, root_path, profile, aliases,
                        created_at, updated_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, '')""",
                    (pid, display, root_path, self._profile, alias_text, now, now),
                )
        result = self.get(pid)
        assert result is not None
        return result

    def _name_conflicts(
        self, display: str, aliases: list[str], exclude: str = ""
    ) -> list[tuple[str, str]]:
        """返回 [(冲突的名字, 占用者描述)]；exclude 是本次注册的项目 id（改自己不算冲突）。"""
        wanted = {v for v in [display, *aliases] if v}
        out: list[tuple[str, str]] = []
        for project in self.list():
            if project.project_id == exclude:
                continue
            taken = {project.name, *[a for a in project.aliases.split(",") if a]}
            for key in sorted(wanted & taken):
                out.append((key, f"{project.name}({project.project_id})"))
        return out

    def touch(self, project_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET last_seen_at = ? WHERE project_id = ?",
                (_now_iso(), project_id),
            )

    # ---- 读取 ----

    def get(self, project_id: str) -> Project | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self) -> list[Project]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row(r) for r in rows]

    def resolve(self, cwd: str | Path) -> Project | None:
        """按最长根路径匹配当前目录；匹配不到返回 None。"""
        target = _norm(cwd)
        best: Project | None = None
        best_len = -1
        for project in self.list():
            root = _norm(project.root_path)
            if target == root or target.startswith(root.rstrip(os.sep) + os.sep):
                if len(root) > best_len:
                    best, best_len = project, len(root)
        if best is not None:
            self.touch(best.project_id)
        return best

    def resolve_ref(self, ref: str) -> Project | None:
        """按 project_id / 路径 / 显示名解析项目。"""
        if not ref:
            return None
        direct = self.get(ref)
        if direct:
            return direct
        by_path = self.get_by_path(ref)
        if by_path:
            return by_path
        for project in self.list():
            if project.name == ref or ref in project.aliases.split(","):
                return project
        return None

    def get_by_path(self, path: str | Path) -> Project | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE root_path = ?", (_norm(path),)
            ).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row: sqlite3.Row) -> Project:
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            root_path=row["root_path"],
            profile=row["profile"],
            aliases=row["aliases"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_seen_at=row["last_seen_at"],
        )
