"""AIChat 配置。"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


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
    data_dir: str = "~/AIChat_Data"
    summarize_threshold: int = 10  # 每 N 条触发树形归纳
    working_memory_max: int = 50
    working_memory_tokens: int = 8000
    # UI
    skin: str = "cyberpunk"
    font_size: int = 14
    # 适配器
    default_adapter: str = "generic"

    def __post_init__(self):
        old = Path(str(Path("~/.aichat").expanduser()))
        self.data_dir = str(Path(self.data_dir).expanduser())
        # 从老的隐藏目录 ~/.aichat 一次性迁移数据到新的显式目录（只跑一次）
        try:
            _new = Path(self.data_dir)
            _mig_flag = _new / "_migrated_from_dot_aichat"
            if old.exists() and old.is_dir() and not _mig_flag.exists():
                _new.mkdir(parents=True, exist_ok=True)
                for _src in old.iterdir():
                    _dst = _new / _src.name
                    try:
                        if _src.is_dir() and not _dst.exists():
                            shutil.copytree(_src, _dst, dirs_exist_ok=True)
                        elif _src.is_file() and not _dst.exists():
                            shutil.copy2(_src, _dst)
                    except Exception:
                        pass
                try:
                    _mig_flag.write_text("migration done", encoding="utf-8")
                except Exception:
                    pass
        except Exception:
            pass

    def save(self, path: str | Path | None = None) -> None:
        if path is None:
            path = Path(self.data_dir) / "config.json"
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
        if path is None:
            path = Path("~/AIChat_Data/config.json").expanduser()
        path = Path(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # 已知字段走 dataclass 构造
                known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
                obj = cls(**known)
                # 额外字段（last_nf_project 等）直接 setattr
                for k, v in data.items():
                    if k not in cls.__dataclass_fields__:
                        setattr(obj, k, v)
                return obj
            except Exception:
                pass
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
