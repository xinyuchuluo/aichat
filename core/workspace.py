"""工作区契约：多工作区、声明启用域、数据物理隔离。

工作区结构（用户家目录下）：
    ~/AichatStudio/                  Mac; Win: %USERPROFILE%\\AichatStudio
    ├── workspaces.json              工作区索引
    ├── default/                     默认工作区
    │   ├── workspace.json          本工作区元数据（声明启用哪些域）
    │   ├── chat/                   聊天记忆数据
    │   ├── novel/                  小说项目（每本书一个子目录）
    │   ├── studio/                 短片项目
    │   ├── warehouse/              数仓元数据库
    │   ├── analysis/               数据分析历史
    │   ├── config.enc              AES 加密配置（API Key 等）
    │   └── secret.key             Fernet 对称密钥（权限 0600）
    └── work/                       工作机工作区（公司机只用这个）

公司机隔离：不是双安装包，是同 App + workspace.json 的 enabled_domains
只列 ["chat", "warehouse"]，其他域目录不创建、UI 不挂载。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DOMAINS: tuple[str, ...] = ("chat", "novel", "studio", "warehouse", "analysis")


def _home_root() -> Path:
    """跨平台工作区根目录。Mac: ~/AichatStudio; Win: %USERPROFILE%\\AichatStudio"""
    return Path.home() / "AichatStudio"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkspaceMeta:
    """workspace.json 元数据"""

    id: str
    display_name: str
    enabled_domains: list[str] = field(default_factory=lambda: list(DEFAULT_DOMAINS))
    data_root: str = ""
    created_at: str = ""
    schema_version: int = 1

    @classmethod
    def new(cls, wid: str, name: str, domains: Optional[list[str]] = None) -> "WorkspaceMeta":
        root = _home_root() / wid
        return cls(
            id=wid,
            display_name=name,
            enabled_domains=domains if domains is not None else list(DEFAULT_DOMAINS),
            data_root=str(root),
            created_at=_now_iso(),
            schema_version=1,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "enabled_domains": self.enabled_domains,
            "data_root": self.data_root,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorkspaceMeta":
        return cls(
            id=d["id"],
            display_name=d["display_name"],
            enabled_domains=d.get("enabled_domains", list(DEFAULT_DOMAINS)),
            data_root=d.get("data_root", ""),
            created_at=d.get("created_at", ""),
            schema_version=d.get("schema_version", 1),
        )


class Workspace:
    """单个工作区句柄"""

    def __init__(self, meta: WorkspaceMeta):
        self.meta = meta
        self._root: Path = Path(meta.data_root) if meta.data_root else _home_root() / meta.id

    @property
    def root(self) -> Path:
        return self._root

    def data_path(self, domain: str) -> Path:
        """返回 <workspace>/<domain>/，自动创建"""
        if domain not in self.meta.enabled_domains:
            raise ValueError(f"domain {domain!r} not enabled in workspace {self.meta.id}")
        p = self._root / domain
        p.mkdir(parents=True, exist_ok=True)
        return p

    def enabled_domains(self) -> list[str]:
        return list(self.meta.enabled_domains)

    def is_domain_enabled(self, domain: str) -> bool:
        return domain in self.meta.enabled_domains

    def workspace_json_path(self) -> Path:
        return self._root / "workspace.json"

    def config_enc_path(self) -> Path:
        return self._root / "config.enc"

    def secret_key_path(self) -> Path:
        return self._root / "secret.key"


class WorkspaceManager:
    """工作区索引与切换"""

    INDEX_FILE = "workspaces.json"  # 位于 _home_root() 下

    def __init__(self) -> None:
        self._root: Path = _home_root()
        self._root.mkdir(parents=True, exist_ok=True)
        self._index_path: Path = self._root / self.INDEX_FILE
        self._current: Optional[Workspace] = None

    # ---- 索引 ----
    def list_workspaces(self) -> list[WorkspaceMeta]:
        if not self._index_path.exists():
            return []
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            return [WorkspaceMeta.from_dict(item) for item in data.get("workspaces", [])]
        except (json.JSONDecodeError, KeyError):
            return []

    def _save_index(self, items: list[WorkspaceMeta]) -> None:
        payload = {"workspaces": [item.to_dict() for item in items], "updated_at": _now_iso()}
        self._index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def find(self, wid: str) -> Optional[WorkspaceMeta]:
        for item in self.list_workspaces():
            if item.id == wid:
                return item
        return None

    # ---- 创建 ----
    def create(self, wid: str, display_name: str, domains: Optional[list[str]] = None) -> Workspace:
        if self.find(wid) is not None:
            raise ValueError(f"workspace {wid!r} already exists")
        meta = WorkspaceMeta.new(wid, display_name, domains)
        meta.data_root = str(self._root / wid)
        ws = Workspace(meta)
        # 落盘
        ws.root.mkdir(parents=True, exist_ok=True)
        ws.workspace_json_path().write_text(
            json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 索引
        items = self.list_workspaces()
        items.append(meta)
        self._save_index(items)
        return ws

    # ---- 加载 / 切换 ----
    def load(self, wid: str) -> Workspace:
        meta = self.find(wid)
        if meta is None:
            raise KeyError(f"workspace {wid!r} not found in index")
        # 兜底：若 data_root 缺失或 workspace.json 不存在，重建
        ws = Workspace(meta)
        if not ws.workspace_json_path().exists():
            ws.root.mkdir(parents=True, exist_ok=True)
            ws.workspace_json_path().write_text(
                json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return ws

    def switch(self, wid: str) -> Workspace:
        ws = self.load(wid)
        self._current = ws
        return ws

    def current(self) -> Optional[Workspace]:
        return self._current

    def ensure_default(self) -> Workspace:
        """首次启动：若没有任何工作区，创建 default"""
        if self.list_workspaces():
            # 已有，切到 default（或第一个）
            target = self.find("default") or self.list_workspaces()[0]
            return self.switch(target.id)
        return self.switch("default") if self.find("default") else self.create(
            "default", "默认工作区", list(DEFAULT_DOMAINS)
        )


# 模块级单例（域通过 core.workspace 调用）
manager = WorkspaceManager()


def current() -> Optional[Workspace]:
    return manager.current()


def ensure_default() -> Workspace:
    return manager.ensure_default()
