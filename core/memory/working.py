"""L1 工作记忆 — 纯内存，不持久化。

只服务当前会话的上下文组装，**不参与检索排序**
（记忆检索由 L2/L3/L4 负责，避免近期消息压过长期事实）。

接受任意带 role/content 的消息对象（域自己的 Message 或 dict），
内核不依赖具体域类型。
"""

from __future__ import annotations

from collections import deque
from typing import Any


def _role_str(msg: Any) -> str:
    role = getattr(msg, "role", None)
    if role is None and isinstance(msg, dict):
        role = msg.get("role")
    return str(getattr(role, "value", role) or "")


def _content_str(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("content", "") or "")
    return str(getattr(msg, "content", "") or "")


def _to_llm(msg: Any) -> dict:
    fn = getattr(msg, "to_llm_dict", None)
    if callable(fn):
        return fn()
    return {"role": _role_str(msg), "content": _content_str(msg)}


class WorkingMemory:
    """L1 工作记忆：纯内存，不持久化。"""

    def __init__(self, max_messages: int = 50, max_tokens: int = 8000):
        self._messages: deque[Any] = deque(maxlen=max_messages)
        self._max_tokens = max_tokens
        self._compressed_count = 0

    def add(self, msg: Any) -> None:
        self._messages.append(msg)

    def add_many(self, msgs: list[Any]) -> None:
        for m in msgs:
            self.add(m)

    def get_messages(self) -> list[Any]:
        return list(self._messages)

    def get_llm_messages(self) -> list[dict]:
        msgs = list(self._messages)
        if not msgs:
            return []
        # 粗估 token：1 char ≈ 0.5 token
        estimated = int(sum(len(_content_str(m)) for m in msgs) * 0.5)
        if estimated <= self._max_tokens:
            return [_to_llm(m) for m in msgs]

        system_msgs = [m for m in msgs if _role_str(m) == "system"]
        non_system = [m for m in msgs if _role_str(m) != "system"]
        if len(non_system) <= 4:
            return [_to_llm(m) for m in msgs]

        keep = max(4, int(len(non_system) * 0.8))
        kept = non_system[-keep:]
        self._compressed_count += len(non_system) - keep
        result = [_to_llm(m) for m in system_msgs]
        if self._compressed_count > 0:
            result.append({
                "role": "system",
                "content": f"[上下文已压缩，省略了 {self._compressed_count} 条早期消息]",
            })
        result.extend(_to_llm(m) for m in kept)
        return result

    def clear(self) -> None:
        self._messages.clear()
        self._compressed_count = 0

    def restore(self, messages: list[Any]) -> None:
        self.clear()
        self.add_many(messages)

    def get_stats(self) -> dict:
        return {
            "message_count": len(self._messages),
            "compressed_count": self._compressed_count,
            "estimated_tokens": int(
                sum(len(_content_str(m)) for m in self._messages) * 0.5
            ),
        }
