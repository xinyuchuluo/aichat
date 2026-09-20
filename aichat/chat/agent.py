"""聊天 Agent — 消息组装 + LLM 调用 + 流式输出。"""
from __future__ import annotations
from typing import Generator, Any
from ..protocol.types import Message, ChatRole, Session, MemoryResult
from ..protocol.adapter import ChatAdapter
from ..memory.engine import MemoryEngine


class ChatAgent:
    """核心聊天 Agent。"""

    def __init__(self, llm_client: Any, memory: MemoryEngine,
                 adapter: ChatAdapter, config: Any = None):
        self._llm = llm_client
        self._memory = memory
        self._adapter = adapter
        self._config = config

    def build_messages(self, session: Session,
                       memory_results: list[MemoryResult] | None = None) -> list[dict]:
        """组装 LLM 消息列表。

        链路：system_prompt + 适配器上下文(注入记忆) + 工作记忆消息
        """
        messages: list[dict] = []

        # 1. 系统提示词
        sys_prompt = self._adapter.build_system_prompt(session)
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})

        # 2. 适配器上下文（注入记忆/领域知识）
        context_msgs = self._adapter.build_context_messages(
            session, memory_results or []
        )
        for m in context_msgs:
            messages.append(m.to_llm_dict())

        # 3. 工作记忆消息（经过 token 裁剪）
        wm = self._memory.get_working_memory(session.id)
        messages.extend(wm.get_llm_messages())

        return messages

    def chat(self, session: Session, user_text: str,
             memory_results: list[MemoryResult] | None = None
             ) -> Generator[dict, None, None]:
        """流式聊天。

        流程：
        1. 用户消息入工作记忆 + L2 SQLite
        2. 组装 messages（system + context + working memory）
        3. 调 LLM 流式输出
        4. AI 响应入工作记忆 + L2 SQLite
        5. 适配器 on_response 回调

        Yields: {"type": "chunk", "content": "..."} | {"type": "done", "content": full}
        """
        scope = self._adapter.get_scope(session)

        # 1. 用户消息入库
        user_msg = Message(role=ChatRole.USER, content=user_text)
        self._memory.commit_message(user_msg, session.id, scope=scope,
                                    metadata={"adapter": self._adapter.key})

        # 2. 组装 messages
        messages = self.build_messages(session, memory_results)

        # 3. LLM 流式调用
        full_response = ""
        try:
            if hasattr(self._llm, "stream_chat"):
                for chunk in self._llm.stream_chat(messages, **self._llm_kwargs()):
                    if isinstance(chunk, str):
                        full_response += chunk
                        yield {"type": "chunk", "content": chunk}
                    elif isinstance(chunk, dict):
                        content = chunk.get("content", "")
                        full_response += content
                        yield {"type": "chunk", "content": content}
            else:
                # 非流式调用
                response = self._llm.chat(messages, **self._llm_kwargs())
                full_response = response if isinstance(response, str) else str(response)
                yield {"type": "chunk", "content": full_response}
        except Exception as e:
            yield {"type": "error", "content": str(e)}
            return

        # 4. 解析工具调用 <|tool|> 并执行，把结果拼接回 AI 回答内容
        try:
            from ..tools import extract_and_run
            processed_text, tool_results = extract_and_run(full_response)
        except Exception as _te:
            processed_text, tool_results = full_response, []
        # 4.5 提取 AI 回复中的隐藏记忆指令（<|memory|> 标签）
        #     用户只看到 clean_text（干净正文），记忆项写入记忆引擎
        try:
            from ..tools import extract_memory
            clean_text, memory_items = extract_memory(processed_text)
        except Exception:
            clean_text, memory_items = processed_text, []

        # 4.55 提取流程蓝图标签（<|flow|>）— 用户只看到正文，FlowRun 存入 metadata 供 UI 渲染任务卡
        flow_run = None
        try:
            from ..flow import parse_flow_tag, strip_flow_tag
            flow_run = parse_flow_tag(clean_text)
            if flow_run:
                clean_text = strip_flow_tag(clean_text)
        except Exception:
            pass

        final_text = clean_text

        ai_msg = Message(
            role=ChatRole.ASSISTANT,
            content=final_text,
            metadata={"adapter": self._adapter.key,
                      "tool_calls": tool_results or [],
                      "memory_items": memory_items or [],
                      "flow_run": flow_run.to_dict() if flow_run else None},
        )
        self._memory.commit_message(ai_msg, session.id, scope=scope,
                                    metadata=ai_msg.metadata)

        # 4.6 把提取出的记忆项逐条写入记忆引擎（L1→L2→L3→L4）
        if memory_items:
            for mi in memory_items:
                mem_type = mi.get("type", "fact")
                data = mi.get("data", {})
                # 根据类型构造记忆内容
                if mem_type == "fact":
                    mem_content = data.get("content", "")
                    mem_meta = {
                        "memory_type": "fact",
                        "tags": data.get("tags", []),
                        "confidence": data.get("confidence", 0.8),
                    }
                elif mem_type == "preference":
                    mem_content = f"[偏好] {data.get('key', '?')}: {data.get('value', '?')}"
                    if data.get("context"):
                        mem_content += f"（{data['context']}）"
                    mem_meta = {
                        "memory_type": "preference",
                        "key": data.get("key", ""),
                        "value": data.get("value", ""),
                        "context": data.get("context", ""),
                    }
                elif mem_type == "entity":
                    attrs = data.get("attributes", {})
                    attrs_str = ", ".join(f"{k}={v}" for k, v in attrs.items())
                    rels = data.get("relations", [])
                    mem_content = f"[实体] {data.get('name', '?')}: {attrs_str}"
                    if rels:
                        mem_content += f" → 关系: {', '.join(rels)}"
                    mem_meta = {
                        "memory_type": "entity",
                        "name": data.get("name", ""),
                        "attributes": attrs,
                        "relations": rels,
                    }
                elif mem_type == "tree_node":
                    mem_content = f"[分支] {data.get('summary', '')}"
                    mem_meta = {
                        "memory_type": "tree_node",
                        "summary": data.get("summary", ""),
                        "parent": data.get("parent", "root"),
                        "children": data.get("children", []),
                    }
                elif mem_type == "reminder":
                    mem_content = f"[提醒] {data.get('content', '')}"
                    mem_meta = {
                        "memory_type": "reminder",
                        "priority": data.get("priority", "normal"),
                        "expire_after": data.get("expire_after", ""),
                    }
                else:
                    mem_content = str(data.get("content", data))
                    mem_meta = {"memory_type": mem_type}

                if not mem_content.strip():
                    continue

                # 写入记忆引擎：以 SYSTEM role 存储记忆事实
                # 这样 LLM 在下一轮能看到（L1 工作记忆），也会进 L2/L3/L4
                from ..protocol.types import Message as _Msg, ChatRole as _CR
                mem_msg = _Msg(
                    role=_CR.SYSTEM,
                    content=f"[记忆·{mem_type}] {mem_content}",
                    metadata={"adapter": self._adapter.key, **mem_meta},
                )
                self._memory.commit_message(mem_msg, session.id, scope=scope,
                                            metadata=mem_msg.metadata)

        # 工具结果如果有，再往后续消息里补一条 role=TOOL 摘要（让下一轮 LLM 看得到）
        if tool_results:
            summary_lines = []
            for tr in tool_results:
                name = tr.get("name", "?")
                r = tr.get("result", {})
                if isinstance(r, dict):
                    ok = r.get("ok")
                    tail = r.get("stderr") or r.get("error") or (
                        f"matched {r.get('match_count')} files, scanned {r.get('scanned_files')}"
                        if name == "find_files" else f"exit={r.get('exit_code')}"
                    )
                else:
                    ok = True; tail = ""
                summary_lines.append(f"- tool:{name} ok={ok}  {tail}")
            tm = Message(
                role=ChatRole.TOOL,
                content="\n".join(summary_lines) + "\n详细已拼在上面的 assistant 消息中。",
                metadata={"adapter": self._adapter.key},
            )
            self._memory.commit_message(tm, session.id, scope=scope,
                                        metadata=tm.metadata)

        # 5. 适配器回调（回调最终文本）
        self._adapter.on_response(session, final_text)

        yield {"type": "done", "content": final_text,
               "flow_run": flow_run.to_dict() if flow_run else None}

    def retrieve_memory(self, session: Session, query_text: str) -> list[MemoryResult]:
        """检索记忆（适配器注入过滤条件）。"""
        from ..protocol.types import MemoryQuery
        scope = self._adapter.get_scope(session)
        filters = self._adapter.get_memory_filters(session)
        mq = MemoryQuery(
            query=query_text,
            session_id=session.id,
            scope=scope,
            top_k=10,
            filters=filters,
        )
        return self._memory.retrieve(mq)

    def _llm_kwargs(self) -> dict:
        """从 config 提取 LLM 调用参数。"""
        if self._config is None:
            return {}
        return {
            "model": getattr(self._config, "model", ""),
            "temperature": getattr(self._config, "temperature", 0.7),
            "max_tokens": getattr(self._config, "max_tokens", 4096),
            "api_key": getattr(self._config, "api_key", ""),
            "base_url": getattr(self._config, "base_url", ""),
        }
