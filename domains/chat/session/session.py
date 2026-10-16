"""会话管理器 — 创建/切换/持久化聊天会话。"""
from __future__ import annotations
import json
from pathlib import Path
from ..protocol.types import Session, Message, ChatRole


class SessionManager:
    """管理多个聊天会话。"""

    def __init__(self, data_dir: str | Path):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Session] = {}
        self._current_id: str | None = None
        self._load_all()

    @property
    def current(self) -> Session | None:
        if self._current_id and self._current_id in self._sessions:
            return self._sessions[self._current_id]
        return None

    def all_sessions(self) -> list[Session]:
        return sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)

    def create(self, title: str = "新对话", adapter_key: str = "generic",
               adapter_config: dict | None = None) -> Session:
        session = Session(title=title, adapter_key=adapter_key,
                          adapter_config=adapter_config or {})
        # 防御：Session.__init__ 里 uuid 可能为空吗？确保 id 绝对非空
        if not session.id:
            import uuid as _uuid
            session.id = _uuid.uuid4().hex[:12]
        self._sessions[session.id] = session
        self._current_id = session.id
        self._save(session)
        return session

    def switch(self, session_id: str) -> Session | None:
        if session_id in self._sessions:
            self._current_id = session_id
            return self._sessions[session_id]
        return None

    def delete(self, session_id: str) -> None:
        if session_id in self._sessions:
            del self._sessions[session_id]
            (self._data_dir / f"{session_id}.json").unlink(missing_ok=True)
            if self._current_id == session_id:
                # 自动切到剩余会话中最新那条，避免 current 悬空
                if self._sessions:
                    latest = max(self._sessions.values(), key=lambda s: s.updated_at)
                    self._current_id = latest.id
                else:
                    self._current_id = None

    def add_message(self, session_id: str, msg: Message) -> None:
        if session_id in self._sessions:
            s = self._sessions[session_id]
            s.messages.append(msg)
            s.touch()
            # 自动取标题：第一条用户消息的前 20 字
            # 兼容多种默认标题格式："新对话"、"新对话 - 通用聊天"、"新对话 - xxx"
            need_rename = (
                msg.role == ChatRole.USER
                and (s.title == "新对话" or s.title.startswith("新对话 - ") or s.title == "")
            )
            if need_rename:
                snippet = (msg.content or "").strip()
                # 取第一行的前 20 字，避免标题里带换行
                snippet = snippet.splitlines()[0] if snippet else snippet
                if snippet:
                    s.title = snippet[:20] + ("..." if len(snippet) > 20 else "")
            self._save(s)

    def get_messages(self, session_id: str) -> list[Message]:
        if session_id in self._sessions:
            return self._sessions[session_id].messages
        return []

    def _session_path(self, session_id: str) -> Path:
        return self._data_dir / f"{session_id}.json"

    def _save(self, session: Session) -> None:
        path = self._session_path(session.id)
        path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_all(self) -> None:
        # 注意：config.json 是配置文件，绝对不能当会话加载
        SKIP_FILES = {"config.json", "raw_memories.db", "tree_memories.db"}
        for path in self._data_dir.glob("*.json"):
            if path.name in SKIP_FILES:
                continue
            # 文件名必须是合法 session id (非空、非"config") → 对应 <id>.json
            stem = path.stem  # 去掉 .json 后的部分
            if not stem:
                # 空文件名（如 .json → 空 id 垃圾文件）直接删掉
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            try:
                raw = path.read_text(encoding="utf-8")
                if not raw.strip():
                    # 空文件（可能崩溃残留）→ 清掉
                    try:
                        path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                data = json.loads(raw)
                session = Session.from_dict(data)
                # 二次校验：id 必须和文件名一致，且非空
                if not session.id or session.id != stem:
                    # 如果会话 dict 里 id 是空/错了，用文件名（真正的存储 key）修正
                    session.id = stem
                # 会话最少得有 id；否则丢弃
                if not session.id:
                    continue
                self._sessions[session.id] = session
            except Exception as e:
                # 损坏文件打日志但不崩溃
                import sys
                print(f"[SessionManager] 跳过损坏的会话文件 {path.name}: {e}", file=sys.stderr)
                continue
        # 加载完成后，如果有会话，把 _current_id 设为最近更新的那个（避免启动时 all_sessions 非空但 current 为 None）
        if self._sessions:
            latest = max(self._sessions.values(), key=lambda s: s.updated_at)
            self._current_id = latest.id

    def save_all(self) -> None:
        for s in self._sessions.values():
            self._save(s)
