"""L1 工作记忆 — 纯内存，上下文裁剪压缩，不持久化。

负责：
- 维护当前会话的近期消息窗口
- 超出 token 上限时压缩旧消息（保留首尾，中间摘要）
- 无磁盘 I/O
"""
from __future__ import annotations
from collections import deque
from ..protocol.types import Message, ChatRole
import time


class WorkingMemory:
    """L1 工作记忆：纯内存，不持久化。"""

    def __init__(self, max_messages: int = 50, max_tokens: int = 8000):
        self._messages: deque[Message] = deque(maxlen=max_messages)
        self._max_tokens = max_tokens
        self._compressed_count = 0  # 已压缩的消息数

    def add(self, msg: Message) -> None:
        self._messages.append(msg)

    def add_many(self, msgs: list[Message]) -> None:
        for m in msgs:
            self.add(m)

    def get_messages(self) -> list[Message]:
        """返回当前工作记忆中的消息（可能被压缩过）。"""
        return list(self._messages)

    def get_llm_messages(self) -> list[dict]:
        """返回 LLM 格式的消息列表，经过 token 预算裁剪。"""
        msgs = list(self._messages)
        if not msgs:
            return []
        # 简单 token 估算：1 char ≈ 0.5 token
        total_chars = sum(len(m.content) for m in msgs)
        estimated_tokens = int(total_chars * 0.5)
        if estimated_tokens <= self._max_tokens:
            return [m.to_llm_dict() for m in msgs]
        # 超预算：保留 system + 最后 N 条，中间压缩
        system_msgs = [m for m in msgs if m.role == ChatRole.SYSTEM]
        non_system = [m for m in msgs if m.role != ChatRole.SYSTEM]
        if len(non_system) <= 4:
            return [m.to_llm_dict() for m in msgs]
        # 保留最后 80% 的非 system 消息
        keep = max(4, int(len(non_system) * 0.8))
        kept = non_system[-keep:]
        self._compressed_count += len(non_system) - keep
        result = [m.to_llm_dict() for m in system_msgs]
        if self._compressed_count > 0:
            result.append({
                "role": "system",
                "content": f"[上下文已压缩，省略了 {self._compressed_count} 条早期消息]"
            })
        result.extend(m.to_llm_dict() for m in kept)
        return result

    def clear(self) -> None:
        self._messages.clear()
        self._compressed_count = 0

    def get_stats(self) -> dict:
        return {
            "message_count": len(self._messages),
            "compressed_count": self._compressed_count,
            "estimated_tokens": int(sum(len(m.content) for m in self._messages) * 0.5),
        }

    def restore(self, messages: list[Message]) -> None:
        """从持久化恢复消息到工作记忆。"""
        self.clear()
        self.add_many(messages)
