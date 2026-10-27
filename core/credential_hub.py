"""统一凭证中心（credential_hub）

所有软件的 API Key 唯一来源 = AIChat 工作区 vault（core/config_vault）。
原三套独立存储：
  - AIChat 壳      -> <workspace>/config.enc            （已是 vault）
  - NovelForge     -> <repo>/domains/.novelforge/config.json  （明文 api_key，泄露风险）
  - data-analyst   -> ~/.data_analyst/config.enc        （AES）

统一后：凭证只存在于 <workspace>/config.enc（AES Fernet），各软件读取，不再各存。
studio / warehouse 不调 LLM，不消费 key，但同属本体系（未来接入 LLM 也走 vault）。

注意：本模块属于 core，严禁 import 任何 domains（见 ARCHITECTURE §6 内核纪律）。
读取 data-analyst 旧 enc 时直接内联 Fernet 解密，不依赖 domains 代码。

用法：
  from core.credential_hub import get_api_profiles, set_api_profiles, ensure_migrated
  profiles = get_api_profiles()   # 首次调用触发 ensure_migrated（把旧 key 并入 vault）
  set_api_profiles(profiles)      # 写回 vault
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from core.config_vault import for_workspace

# 统一工作区（与 shell/main.py 的 ensure_default() 一致：首个工作区 default）
DEFAULT_WORKSPACE = Path.home() / "AichatStudio" / "default"

# 旧存储位置（迁移源；迁移后原文件备份为 .bak）
_NOVEL_CONFIG = Path(__file__).resolve().parent.parent / "domains" / ".novelforge" / "config.json"
_ANALYSIS_CONFIG = Path.home() / ".data_analyst" / "config.enc"
_ANALYSIS_KEY = Path.home() / ".data_analyst" / "secret.key"

_MIGRATED_FLAG = False


def get_api_profiles() -> list[dict]:
    """读取统一凭证（vault.api_profiles）。首次调用触发迁移。"""
    global _MIGRATED_FLAG
    if not _MIGRATED_FLAG:
        ensure_migrated()
    vault = for_workspace(DEFAULT_WORKSPACE)
    return vault.get("api_profiles", []) or []


def set_api_profiles(profiles: list[dict]) -> None:
    """写回统一凭证到 vault。"""
    vault = for_workspace(DEFAULT_WORKSPACE)
    vault.set("api_profiles", profiles)


def get_step_routing() -> dict:
    vault = for_workspace(DEFAULT_WORKSPACE)
    return vault.get("step_routing", {}) or {}


def set_step_routing(routing: dict) -> None:
    vault = for_workspace(DEFAULT_WORKSPACE)
    vault.set("step_routing", routing)


def ensure_migrated() -> dict:
    """把旧位置凭证并入 vault（幂等）。

    - vault 已有 api_profiles -> 跳过
    - 否则从 NovelForge 明文、data-analyst enc 收集，去重后写入 vault
    - 迁移成功后备份旧文件为 .bak

    返回报告 dict：{imported, sources, skipped}
    """
    global _MIGRATED_FLAG
    report: dict[str, Any] = {"imported": 0, "sources": [], "skipped": []}
    try:
        vault = for_workspace(DEFAULT_WORKSPACE)
        existing = vault.get("api_profiles", []) or []
        if existing:
            report["skipped"].append("vault 已有 api_profiles，跳过迁移")
            return _finish(report)

        collected: list[dict] = []

        # 1) NovelForge 明文 endpoints（在仓库内，含明文 api_key）
        if _NOVEL_CONFIG.exists():
            try:
                data = json.loads(_NOVEL_CONFIG.read_text(encoding="utf-8"))
                for e in data.get("endpoints", []) or []:
                    models = e.get("models") or ([e.get("model")] if e.get("model") else [])
                    collected.append({
                        "name": e.get("name", ""),
                        "base_url": e.get("base_url", ""),
                        "api_key": e.get("api_key", ""),
                        "model": models[0] if models else "",
                        "models": models,
                    })
                if any(c["api_key"] for c in collected):
                    report["sources"].append("novel")
            except Exception:
                pass

        # 2) data-analyst AES api_profiles（内联解密，避免 import domains）
        if _ANALYSIS_CONFIG.exists() and _ANALYSIS_KEY.exists():
            try:
                from cryptography.fernet import Fernet

                fernet = Fernet(_ANALYSIS_KEY.read_bytes())
                plain = fernet.decrypt(_ANALYSIS_CONFIG.read_bytes())
                ad = json.loads(plain.decode("utf-8"))
                for p in ad.get("api_profiles", []) or []:
                    collected.append({
                        "name": p.get("name", ""),
                        "base_url": p.get("url", ""),
                        "api_key": p.get("key", ""),
                        "model": p.get("model", ""),
                        "models": [p.get("model", "")] if p.get("model") else [],
                    })
                if any(c["api_key"] for c in collected):
                    report["sources"].append("analysis")
            except Exception:
                pass

        # 去重（按 name + base_url）
        seen: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for c in collected:
            key = (c["name"], c["base_url"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(c)

        if deduped:
            vault.set("api_profiles", deduped)
            report["imported"] = len(deduped)
            # 备份旧文件（含原明文 key，.bak 仅供回滚，位于 .novelforge/ 已 gitignore）
            if _NOVEL_CONFIG.exists():
                shutil.copy2(_NOVEL_CONFIG, str(_NOVEL_CONFIG) + ".bak")
            if _ANALYSIS_CONFIG.exists():
                shutil.copy2(_ANALYSIS_CONFIG, str(_ANALYSIS_CONFIG) + ".bak")
            # 迁移后把 NovelForge 本地明文 config.json 的 api_key 就地清空，避免明文残留
            if _NOVEL_CONFIG.exists():
                try:
                    _nc = json.loads(_NOVEL_CONFIG.read_text(encoding="utf-8"))
                    for _e in _nc.get("endpoints", []) or []:
                        _e["api_key"] = ""
                    _NOVEL_CONFIG.write_text(
                        json.dumps(_nc, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                except Exception:
                    pass
    finally:
        _MIGRATED_FLAG = True
    return report


def _finish(report: dict) -> dict:
    global _MIGRATED_FLAG
    _MIGRATED_FLAG = True
    return report
