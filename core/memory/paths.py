"""记忆档案（profile）路径解析。

个人项目与公司项目物理隔离，各用一个独立档案：

    ~/AichatStudio/_memory/personal/    个人项目（默认）
    ~/AichatStudio/_memory/company/     公司设备 / 公司项目

每个档案目录内是完整的一套库：

    raw_memories.db / tree_memories.db / vector_index.db   四层记忆（MemoryEngine）
    projects.db                                            项目注册表（可变）

档案名可由环境变量 MEMSYNC_PROFILE 指定，或由调用方显式传入。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROFILE = "personal"
MEMORY_ROOT_NAME = "_memory"

# 档案名只允许小写字母、数字、下划线、连字符，避免路径穿越
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# 从旧工作区接管的库文件
_DB_FILES = ("raw_memories.db", "tree_memories.db", "vector_index.db")


def memory_root() -> Path:
    """所有档案的根目录：~/AichatStudio/_memory/"""
    return Path.home() / "AichatStudio" / MEMORY_ROOT_NAME


@dataclass(frozen=True)
class MemoryProfile:
    """一个记忆档案（独立的一套库）。"""

    name: str
    root: Path

    @property
    def projects_db(self) -> Path:
        return self.root / "projects.db"

    @property
    def raw_db(self) -> Path:
        return self.root / "raw_memories.db"

    def ensure(self) -> "MemoryProfile":
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def import_legacy(self, legacy_dir: str | Path) -> list[str]:
        """首次升级：把旧版 <workspace>/chat/memory/ 的库原样接管到本档案。

        仅在目标库不存在时复制，绝不覆盖已有数据。返回被复制的文件名。
        """
        legacy = Path(legacy_dir).expanduser()
        if not legacy.is_dir():
            return []
        copied: list[str] = []
        for name in _DB_FILES:
            src = legacy / name
            if not src.is_file():
                continue
            dst = self.root / name
            if dst.exists():
                continue
            self.root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            # WAL 模式下的增量文件一并带走，避免首启丢事务
            for suffix in ("-wal", "-shm"):
                side = legacy / f"{name}{suffix}"
                if side.is_file():
                    shutil.copy2(side, Path(f"{dst}{suffix}"))
            copied.append(name)
        return copied


def resolve_profile(name: str = "") -> MemoryProfile:
    """解析档案：入参 > MEMSYNC_PROFILE 环境变量 > personal。"""
    raw = (name or os.environ.get("MEMSYNC_PROFILE", "") or DEFAULT_PROFILE).strip().lower()
    if not _PROFILE_RE.match(raw):
        raise ValueError(f"invalid memory profile name: {raw!r}")
    profile = MemoryProfile(name=raw, root=memory_root() / raw)
    return profile.ensure()
