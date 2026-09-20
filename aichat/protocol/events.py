"""协议事件类型（用于信号/回调通信）。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamChunk:
    """流式输出块。"""
    content: str
    done: bool = False
    error: str | None = None


@dataclass
class SessionEvent:
    """会话事件。"""
    event_type: str  # "message_added" | "title_changed" | "session_created" | "session_deleted"
    session_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryUpdateEvent:
    """记忆更新事件。"""
    event_type: str  # "raw_appended" | "tree_summarized" | "vector_indexed"
    uuid: str = ""
    layer: str = ""  # "working" | "sqlite" | "tree" | "vector"
    data: dict[str, Any] = field(default_factory=dict)
