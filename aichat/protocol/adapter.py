"""适配器协议接口。

适配器是外部系统接入聊天软件的桥梁：
- 提供系统提示词
- 组装上下文（注入领域知识/项目数据）
- 定义工具
- 处理 LLM 输出的结构化响应

示例：
  GenericAdapter — 通用闲聊
  NovelForgeAdapter — 集成 NovelForge 小说项目记忆/讨论规则
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Generator, Protocol
from .types import Message, Session, MemoryResult


class ChatAdapter(ABC):
    """聊天适配器抽象基类。"""

    @property
    @abstractmethod
    def key(self) -> str:
        """适配器唯一标识（如 'generic', 'novelforge'）。"""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """UI 显示名。"""
        ...

    @property
    def description(self) -> str:
        return ""

    @abstractmethod
    def build_system_prompt(self, session: Session) -> str:
        """构建系统提示词。"""
        ...

    @abstractmethod
    def build_context_messages(self, session: Session, memory_results: list[MemoryResult]) -> list[Message]:
        """构建上下文消息（注入记忆/领域知识）。

        返回额外的 system/context 消息，插入到用户消息之前。
        """
        ...

    def get_tools(self) -> list[dict]:
        """返回工具定义（可选，默认无工具）。"""
        return []

    def handle_tool_call(self, name: str, arguments: dict) -> str | None:
        """处理工具调用（可选）。返回工具结果文本，或 None 表示无工具。"""
        return None

    def on_response(self, session: Session, response: str) -> dict[str, Any] | None:
        """LLM 响应后回调（可选）。

        适配器可解析响应中的结构化内容（如 NovelForge 的提案 JSON），
        返回额外数据供 UI/记忆层使用。
        """
        return None

    def get_scope(self, session: Session) -> str:
        """返回记忆 scope 命名空间。"""
        return f"{self.key}:{session.id}"

    def get_memory_filters(self, session: Session) -> dict[str, Any]:
        """返回记忆检索的 metadata 过滤条件。"""
        return {}
