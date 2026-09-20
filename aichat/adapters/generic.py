"""通用聊天适配器 — 纯闲聊，无领域知识注入。"""
from __future__ import annotations
from ..protocol.types import Message, Session, MemoryResult, ChatRole
from ..protocol.adapter import ChatAdapter


class GenericAdapter(ChatAdapter):
    """通用聊天适配器。"""

    @property
    def key(self) -> str:
        return "generic"

    @property
    def display_name(self) -> str:
        return "通用聊天"

    @property
    def description(self) -> str:
        return "通用 AI 助手，无特定领域上下文"

    def build_system_prompt(self, session: Session) -> str:
        from ..tools import TOOL_PROTOCOL_MD, MEMORY_PROTOCOL_MD
        result = (
            "你是一个通用 AI 助手。你可以回答问题、提供建议、进行闲聊。"
            "请用中文回复，保持友好和简洁。\n"
            "当用户让你『执行某个命令』『跑一段脚本』『找一下某个文件』"
            "『搜搜某段代码』时，请用下方协议的 <|tool| ...> 标签调用宿主能力；"
            "不要编造命令输出，必须等宿主返回 <|tool_result|> 再继续作答。\n"
            + TOOL_PROTOCOL_MD
            + "\n"
            + MEMORY_PROTOCOL_MD
        )
        try:
            from ..flow import get_flow_protocol_md
            result += "\n" + get_flow_protocol_md()
        except Exception:
            pass
        return result

    def build_context_messages(self, session: Session,
                                memory_results: list[MemoryResult]) -> list[Message]:
        """注入记忆检索结果。"""
        if not memory_results:
            return []
        # 将记忆结果组装为 system 上下文
        memory_text = "\n".join(
            f"- {r.content[:200]}" for r in memory_results[:5]
        )
        return [Message(
            role=ChatRole.SYSTEM,
            content=f"以下是相关的历史记忆，供你参考：\n{memory_text}"
        )]
