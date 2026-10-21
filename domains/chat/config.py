"""AIChat 配置。

配置文件落当前工作区 <workspace>/chat/config.json（不再是旧 ~/AIChat_Data）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

LEGACY_CONFIG = "~/AIChat_Data/config.json"


def _workspace_chat_dir() -> Path:
    """当前工作区的 chat 数据目录；内核不可用时用默认工作区路径兜底。"""
    try:
        from core import workspace as ws_mod

        # 必须用 current()：ensure_default() 会把当前工作区重置为 default
        ws = ws_mod.current() or ws_mod.ensure_default()
        return ws.data_path("chat")
    except Exception:  # noqa: BLE001
        return Path.home() / "AichatStudio" / "default" / "chat"


def _default_config_path() -> Path:
    return _workspace_chat_dir() / "config.json"


@dataclass
class Config:
    """全局配置。"""
    # LLM
    api_key: str = ""
    base_url: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.7
    max_tokens: int = 4096
    # 记忆
    data_dir: str = ""  # 空 → 自动解析为 <workspace>/chat
    summarize_threshold: int = 10  # 每 N 条触发树形归纳
    working_memory_max: int = 50
    working_memory_tokens: int = 8000
    # UI
    skin: str = "breeze"
    font_size: int = 14
    # 适配器
    default_adapter: str = "generic"

    def __post_init__(self):
        # 数据目录统一由工作区决定，旧配置里的 data_dir 一律忽略
        self.data_dir = str(_workspace_chat_dir())

    def save(self, path: str | Path | None = None) -> None:
        if path is None:
            path = _default_config_path()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 合并 dataclass 字段 + 额外 dict 风格属性（last_nf_project 等）
        payload = asdict(self)
        for k, v in self.__dict__.items():
            if k not in self.__dataclass_fields__ and not k.startswith("_"):
                payload[k] = v
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        if path is not None:
            candidates = [Path(path)]
        else:
            candidates = [_default_config_path()]
            legacy = Path(LEGACY_CONFIG).expanduser()
            if legacy.exists():
                candidates.append(legacy)  # 首次升级：读一次旧配置，不复制数据
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            obj = cls(**known)
            for k, v in data.items():
                if k not in cls.__dataclass_fields__:
                    setattr(obj, k, v)
            return obj
        return cls()

    # ---- dict 兼容（widgets/main_window.py 用了 config.get / config["xxx"]）----
    def get(self, key: str, default=None):
        """dict 风格访问：已知字段返回值，未知字段返回 __dict__ 里的或 default"""
        if key in self.__dataclass_fields__:
            return getattr(self, key)
        return self.__dict__.get(key, default)

    def __getitem__(self, key: str):
        if key in self.__dataclass_fields__:
            return getattr(self, key)
        if key in self.__dict__:
            return self.__dict__[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value):
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        return key in self.__dataclass_fields__ or key in self.__dict__
