"""讨论功能：跨会话记忆（分层）。

严格区分两层记忆，与「文章内容」无关：
- **软件级记忆**（跨项目）：用户对软件行为 / 回答方式 / 规则 的偏好。
  存代码仓 `novelforge/novelforge/.novelforge/discussion_rules.json`
- **项目级记忆**（针对当前小说）：本书规则（如"主角的剑叫青云"）与
  已拍板决策（如"确定第27章为转折点"）。
  存 `{project_root}/.novelforge/discussion_memory.json`

文章内容由 MemoryEngine / 正文承载；本模块只存「用户说过的话 / 定过的规则 / 拍板的决策」。
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

_SOFTWARE_RULES_FILE = "discussion_rules.json"
_PROJECT_MEMORY_FILE = "discussion_memory.json"
_MAX_ITEMS = 40   # 每档最多保留条目
_MAX_INJECT = 8   # 注入上下文时每档最多带几条


class DiscussionMemory:
    def __init__(self, project_root: str = "", config_dir: str = ""):
        self._project_root = project_root or ""
        # 软件级目录与 config.json 同级（默认：代码仓 .novelforge）
        if config_dir:
            self._software_dir = config_dir
        else:
            # memory.py 位于 novelforge/novelforge/novelforge/core/discussion/，
            # 向上 4 级 = novelforge/novelforge，再拼 .novelforge
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            self._software_dir = os.path.join(base, ".novelforge")
        self._software: dict = self._load(
            os.path.join(self._software_dir, _SOFTWARE_RULES_FILE), {"rules": []}
        )
        self._project: dict = self._load(self._project_path(), {"rules": [], "decisions": []})

    # ---- 路径 / IO ----
    def _project_path(self) -> str:
        if not self._project_root:
            return ""
        return os.path.join(self._project_root, ".novelforge", _PROJECT_MEMORY_FILE)

    @staticmethod
    def _load(path: str, default: dict) -> dict:
        if not path or not os.path.isfile(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return default

    @staticmethod
    def _save(path: str, data: dict) -> None:
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    @staticmethod
    def _dedup(items: list[dict]) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for it in items:
            c = str(it.get("content", "")).strip()
            if not c or c in seen:
                continue
            seen.add(c)
            out.append(it)
        return out

    # ---- 写入 ----
    def add(self, scope: str, contents: list[str], rev: int = 0) -> None:
        """写入记忆条目。scope: software | project_rule | project_decision。
        rev: 当前设定版本号（settings_revision），写入时一并记录，便于后续判断是否过期。
        """
        if not contents:
            return
        now = time.strftime("%Y-%m-%d %H:%M")
        for c in contents:
            c = c.strip()
            if not c:
                continue
            item = {"content": c, "ts": now, "rev": rev}
            if scope == "software":
                self._software.setdefault("rules", []).append(item)
            elif scope == "project_rule":
                self._project.setdefault("rules", []).append(item)
            else:
                self._project.setdefault("decisions", []).append(item)
        if self._software.get("rules"):
            self._software["rules"] = self._dedup(self._software["rules"])[-_MAX_ITEMS:]
            self._save(os.path.join(self._software_dir, _SOFTWARE_RULES_FILE), self._software)
        p_rules = self._project.get("rules") or []
        p_dec = self._project.get("decisions") or []
        if p_rules or p_dec:
            self._project["rules"] = self._dedup(p_rules)[-_MAX_ITEMS:]
            self._project["decisions"] = self._dedup(p_dec)[-_MAX_ITEMS:]
            self._save(self._project_path(), self._project)

    # ---- 读取（注入上下文）----
    def format_injection(self, current_rev: int = 0) -> str:
        """注入上下文的记忆文本：软件规则 + 本书规则 + 本书决策（各限量、去重）。
        current_rev: 当前设定版本号；若某条记忆的 rev 小于当前版本，标注『基于旧版设定，
        可能已失效』，防止 agent/用户把过期结论当真。
        """
        parts: list[str] = []

        def _clean(content: str) -> str:
            """防御性：记忆条目若含双轨/分离主义表述，注入前确定性改写成单轨。"""
            try:
                from domains.novel.core.discussion.world_settings_integrity import (
                    enforce_single_track,
                )
                c, _, _ = enforce_single_track(content)
                return c
            except Exception:
                return content

        def _mark(item: dict, rev: int) -> str:
            c = _clean(item.get("content", ""))
            if rev and item.get("rev", 0) and item["rev"] < rev:
                return f"- {c}  ［基于旧版设定v{item['rev']}，当前已v{rev}，可能已失效，请重新确认］"
            return f"- {c}"
        sw = self._dedup(self._software.get("rules") or [])[-_MAX_INJECT:]
        if sw:
            parts.append("【软件级规则（你定的，跨项目必须遵守）】\n" + "\n".join(_mark(r, current_rev) for r in sw))
        pr = self._dedup(self._project.get("rules") or [])[-_MAX_INJECT:]
        if pr:
            parts.append("【本书规则（你对这本书定过的约定）】\n" + "\n".join(_mark(r, current_rev) for r in pr))
        pd = self._dedup(self._project.get("decisions") or [])[-_MAX_INJECT:]
        if pd:
            parts.append("【本书已拍板的决策（讨论结论，避免重复讨论）】\n" + "\n".join(_mark(d, current_rev) for d in pd))
        return "\n\n".join(parts)
