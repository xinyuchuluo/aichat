"""协议核心数据类型。

所有消息、记忆、会话的通用数据结构，适配器只需实现接口即可接入。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
import uuid


class ChatRole(str, Enum):
    """消息角色。"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """一条聊天消息。"""
    role: ChatRole
    content: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
    # 适配器可附加的额外字段
    adapter_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "adapter_data": self.adapter_data,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        return cls(
            role=ChatRole(d.get("role", "user")),
            content=d.get("content", ""),
            id=d.get("id", ""),
            timestamp=d.get("timestamp", ""),
            metadata=d.get("metadata", {}),
            adapter_data=d.get("adapter_data", {}),
        )

    def to_llm_dict(self) -> dict:
        """转为 LLM API 格式 {role, content}。

        - 如果 metadata['images'] 非空（元素是 dict: {'mime':..., 'b64':..., 'desc':'可选说明'}），
          则 content 以 GPT-4V / Qwen-VL / GLM-4V 兼容的『list[content_parts]』形式输出：
          [{"type":"text","text":"..."},{"type":"image_url","image_url":{"url":"data:image/png;base64,xxxxx"}}]
        - 否则走传统纯文本 string。
        """
        images: list[dict] = (self.metadata or {}).get("images", []) or []
        if images:
            parts: list[dict] = []
            if self.content:
                parts.append({"type": "text", "text": self.content})
            for img in images:
                mime = img.get("mime") or "image/png"
                b64 = img.get("b64") or ""
                if not b64:
                    continue
                url = f"data:{mime};base64,{b64}"
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": url, "detail": "auto"},
                })
            return {"role": self.role.value, "content": parts}
        return {"role": self.role.value, "content": self.content}


@dataclass
class Session:
    """一个聊天会话。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "新对话"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    adapter_key: str = "generic"  # 使用的适配器标识
    adapter_config: dict[str, Any] = field(default_factory=dict)
    # 工作记忆中的消息历史（内存）
    messages: list[Message] = field(default_factory=list)

    def touch(self):
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "adapter_key": self.adapter_key,
            "adapter_config": self.adapter_config,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Session:
        return cls(
            id=d.get("id", ""),
            title=d.get("title", "新对话"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            adapter_key=d.get("adapter_key", "generic"),
            adapter_config=d.get("adapter_config", {}),
            messages=[Message.from_dict(m) for m in d.get("messages", [])],
        )


# 记忆数据类型统一由内核 core.memory 定义，域只做引用，
# 保证 chat 与外部软件（MCP / 导入器）看到同一套契约。
from core.memory.types import (  # noqa: E402
    MemoryQuery,
    MemoryRecord,
    MemoryResult,
)


@dataclass
class ToolCall:
    """工具调用（适配器可定义工具）。"""
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
