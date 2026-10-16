"""提示词仓库服务。

回应"提示词会不断优化、不是软件生成的"诉求：
- 域注册默认模板（prompts/<domain>/<scene>.j2，Jinja2 占位符）
- 用户在 prompts/ 目录编辑实际内容，覆盖模板
- 运行时按 domain + scene 取最终 prompt，带版本号写入快照供追溯
- 可 git 回滚
- 任何 .py 文件禁止硬编码 prompt 字符串

目录结构：
    prompts/
    ├── chat/
    │   ├── system.j2
    │   └── memory_inject.j2
    ├── novel/
    │   ├── outline.j2
    │   ├── writing.j2
    │   ├── discussion.j2
    │   ├── style_presets/
    │   │   ├── 玄幻.j2
    │   │   └── 都市.j2
    │   └── power_canon_block.j2
    ├── analysis/
    │   ├── plan.j2
    │   ├── script.j2
    │   ├── analysis.j2
    │   └── report.j2
    ├── warehouse/
    │   ├── parse_sql.j2
    │   └── suggest_term.j2
    └── studio/
        └── exporters/
            ├── drawthings.j2
            ├── blender.j2
            ├── seedance.j2
            ├── tts.j2
            └── markdown.j2

版本机制：每次 set_template 自增版本号，旧版本存
    prompts/.versions/<domain>__<scene>__v<n>.j2
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PromptRepo:
    """提示词仓库"""

    def __init__(self, root: Path) -> None:
        self._root: Path = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._versions_dir: Path = self._root / ".versions"
        self._versions_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path: Path = self._root / ".versions" / "meta.json"
        self._meta: dict[str, dict[str, int]] = self._load_meta()

    def _load_meta(self) -> dict[str, dict[str, int]]:
        if not self._meta_path.exists():
            return {}
        try:
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_meta(self) -> None:
        self._meta_path.write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- 路径 ----
    def _template_path(self, domain: str, scene: str) -> Path:
        return self._root / domain / f"{scene}.j2"

    def _versioned_path(self, domain: str, scene: str, version: int) -> Path:
        return self._versions_dir / f"{domain}__{scene}__v{version}.j2"

    def _meta_key(self, domain: str, scene: str) -> str:
        return f"{domain}__{scene}"

    # ---- 读 ----
    def get_raw(self, domain: str, scene: str) -> Optional[str]:
        """读取原始模板文本，不存在返回 None"""
        path = self._template_path(domain, scene)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get(self, domain: str, scene: str, **vars: Any) -> str:
        """读取并渲染 Jinja2 模板"""
        raw = self.get_raw(domain, scene)
        if raw is None:
            raise FileNotFoundError(f"prompt template not found: {domain}/{scene}")
        try:
            from jinja2 import Environment, StrictUndefined
        except ImportError as e:
            raise RuntimeError("jinja2 not installed; run pip install jinja2") from e
        env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
        template = env.from_string(raw)
        return template.render(**vars)

    def list_templates(self, domain: Optional[str] = None) -> list[dict]:
        """列出所有模板"""
        result: list[dict] = []
        if domain:
            domains = [domain]
        else:
            domains = [p.name for p in self._root.iterdir() if p.is_dir() and not p.name.startswith(".")]
        for d in domains:
            dpath = self._root / d
            if not dpath.exists():
                continue
            for f in dpath.rglob("*.j2"):
                rel = f.relative_to(dpath).as_posix()
                scene = rel[:-3]  # 去掉 .j2
                key = self._meta_key(d, scene)
                version = self._meta.get(key, {}).get("version", 1)
                result.append(
                    {
                        "domain": d,
                        "scene": scene,
                        "path": str(f),
                        "version": version,
                    }
                )
        return result

    def version(self, domain: str, scene: str) -> int:
        key = self._meta_key(domain, scene)
        return self._meta.get(key, {}).get("version", 1)

    # ---- 写 ----
    def set_template(self, domain: str, scene: str, content: str) -> int:
        """用户编辑模板：保存新版本 + 旧版本归档"""
        path = self._template_path(domain, scene)
        path.parent.mkdir(parents=True, exist_ok=True)

        # 备份当前版本
        key = self._meta_key(domain, scene)
        cur_version = self._meta.get(key, {}).get("version", 1)
        if path.exists():
            old_path = self._versioned_path(domain, scene, cur_version)
            shutil.copy2(path, old_path)

        # 写新版本
        path.write_text(content, encoding="utf-8")
        new_version = cur_version + 1
        self._meta[key] = {"version": new_version, "updated_at": _now_iso()}
        self._save_meta()
        return new_version

    def rollback(self, domain: str, scene: str, to_version: int) -> None:
        """回滚到指定版本"""
        key = self._meta_key(domain, scene)
        cur_version = self._meta.get(key, {}).get("version", 1)
        if to_version >= cur_version:
            raise ValueError(f"to_version {to_version} >= current {cur_version}")
        target_path = self._versioned_path(domain, scene, to_version)
        if not target_path.exists():
            raise FileNotFoundError(f"version {to_version} not found: {target_path}")
        # 当前版本归档
        cur_path = self._template_path(domain, scene)
        if cur_path.exists():
            cur_archive = self._versioned_path(domain, scene, cur_version)
            shutil.copy2(cur_path, cur_archive)
        # 覆盖
        shutil.copy2(target_path, cur_path)
        self._meta[key] = {"version": to_version, "updated_at": _now_iso()}
        self._save_meta()

    # ---- 注册默认 ----
    def register_default(self, domain: str, scene: str, content: str) -> None:
        """域启动时注册默认模板：若用户已编辑则不覆盖"""
        path = self._template_path(domain, scene)
        if path.exists():
            return  # 用户已编辑
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        key = self._meta_key(domain, scene)
        if key not in self._meta:
            self._meta[key] = {"version": 1, "updated_at": _now_iso()}
            self._save_meta()


# 模块级单例
_repo: Optional[PromptRepo] = None


def init(root: Path | str) -> PromptRepo:
    global _repo
    _repo = PromptRepo(Path(root))
    return _repo


def get() -> PromptRepo:
    if _repo is None:
        raise RuntimeError("PromptRepo not initialized; call core.prompt_repo.init first")
    return _repo
