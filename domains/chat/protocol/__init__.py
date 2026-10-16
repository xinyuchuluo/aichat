"""通用聊天协议层：消息、会话、记忆、适配器接口。"""
from .types import Message, Session, MemoryRecord, MemoryQuery, MemoryResult, ToolCall, ChatRole
from .adapter import ChatAdapter
from .events import StreamChunk, SessionEvent, MemoryUpdateEvent
