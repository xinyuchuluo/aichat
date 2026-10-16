"""AES Fernet 加密配置存储。

存储位置：<workspace>/config.enc + <workspace>/secret.key
- secret.key：首次启动生成，权限 0600
- config.enc：用 Fernet 对称加密
- 不入库（.gitignore 排除 config.enc 和 secret.key）
- 换机：用户重新配 API Key，或手动同步 secret.key + config.enc

升级自 data-analyst-tool/core/config_manager.py 的 AES 加密设计，
搬到内核让所有域共享。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet


class ConfigVault:
    """单个工作区的加密配置存储"""

    def __init__(self, workspace_root: Path) -> None:
        self._root: Path = Path(workspace_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._key_path: Path = self._root / "secret.key"
        self._enc_path: Path = self._root / "config.enc"
        self._fernet: Optional[Fernet] = None
        self._cache: dict[str, Any] = {}
        self._loaded: bool = False

    # ---- 密钥 ----
    def _ensure_key(self) -> Fernet:
        """首次启动生成 secret.key，权限 0600"""
        if self._fernet is not None:
            return self._fernet
        if not self._key_path.exists():
            key = Fernet.generate_key()
            self._key_path.write_bytes(key)
            try:
                os.chmod(self._key_path, 0o600)
            except OSError:
                # Windows 上 chmod 行为有限，忽略
                pass
        key = self._key_path.read_bytes()
        self._fernet = Fernet(key)
        return self._fernet

    # ---- 加载 / 写入 ----
    def load(self) -> dict[str, Any]:
        """解密读取全部配置"""
        if self._loaded:
            return self._cache
        fernet = self._ensure_key()
        if not self._enc_path.exists():
            self._cache = {}
            self._loaded = True
            return self._cache
        try:
            token = self._enc_path.read_bytes()
            plain = fernet.decrypt(token)
            self._cache = json.loads(plain.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            # 解密失败：密钥不匹配 / 文件损坏 → 让用户重配
            self._cache = {}
        self._loaded = True
        return self._cache

    def save(self) -> None:
        """加密写入全部配置"""
        fernet = self._ensure_key()
        plain = json.dumps(self._cache, ensure_ascii=False, indent=2).encode("utf-8")
        token = fernet.encrypt(plain)
        self._enc_path.write_bytes(token)
        try:
            os.chmod(self._enc_path, 0o600)
        except OSError:
            pass

    # ---- 单项读写 ----
    def get(self, key: str, default: Any = None) -> Any:
        if not self._loaded:
            self.load()
        return self._cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if not self._loaded:
            self.load()
        self._cache[key] = value
        self.save()

    def remove(self, key: str) -> None:
        if not self._loaded:
            self.load()
        if key in self._cache:
            del self._cache[key]
            self.save()

    def all(self) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        return dict(self._cache)

    def clear(self) -> None:
        self._cache = {}
        self._loaded = True
        self.save()


# 模块级缓存：workspace_id → ConfigVault
_vaults: dict[str, ConfigVault] = {}


def for_workspace(workspace_root: Path | str) -> ConfigVault:
    """取/建一个工作区的 ConfigVault"""
    root = str(workspace_root)
    if root not in _vaults:
        _vaults[root] = ConfigVault(Path(root))
    return _vaults[root]
