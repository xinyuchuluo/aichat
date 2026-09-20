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
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        if path is None:
            path = Path("~/AIChat_Data/config.json").expanduser()
        path = Path(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception:
                pass
        return cls()
