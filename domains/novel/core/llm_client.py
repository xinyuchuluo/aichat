"""
LLMClient - OpenAI 兼容 API 封装模块。

本模块是 NovelForge 项目与 LLM（大语言模型）交互的核心接口层。
支持多端点配置，兼容七牛云 (api.qnaigc.com) 和 sufy.com (openai.sufy.com) 等 OpenAI 兼容接口。

主要功能：
1. 多端点管理：支持配置多个 API 端点，按名称切换
2. 同步聊天补全：chat() 方法，支持重试和指数退避
3. 流式聊天补全：chat_stream() 方法，逐块返回生成文本
4. 文本向量化：embed() 方法，批量将文本转为向量表示
5. Token 计数：基于 TokenCounter 估算文本 token 数
6. 连接测试：test_connection() 和 test_embedding() 验证端点可用性

设计要点：
- 使用延迟导入 openai 库，避免启动时依赖问题
- 客户端实例按端点名称缓存，配置变更时自动清除缓存
- 所有 API 调用均内置重试机制（默认3次），采用指数退避策略
"""
from __future__ import annotations

import json
import time
from typing import Generator, Optional

from domains.novel.config import Config, LLMEndpoint
from domains.novel.core.token_counter import TokenCounter
from domains.novel.utils.logger import logger

# ---------------------------------------------------------------------------
# P9 模型能力护栏
# ---------------------------------------------------------------------------
# 已知模型能力声明。key 往往只支持部分模型（如本项目的 DeepSeek key 仅支持
# deepseek-chat，deepseek-reasoner / deepseek-v4-flash 在运行时才报 404）。
# 未在此表登记、但确实可用的模型仍可正常工作；此表仅用于"声明 + 安全回退"。
MODEL_CAPS = {
    "deepseek-chat": {"reasoning": False, "safe_default": True},
    "deepseek-reasoner": {"reasoning": True, "safe_default": False},
    "deepseek-v4-flash": {"reasoning": False, "safe_default": False},
    "gpt-4o": {"reasoning": False, "safe_default": True},
    "gpt-4o-mini": {"reasoning": False, "safe_default": True},
}
# 探测失败时的安全回退模型（绝大多数 OpenAI 兼容 key 都支持）
SAFE_DEFAULT_MODEL = "deepseek-chat"


class LLMClient:
    """
    OpenAI 兼容接口封装，支持多端点配置。

    职责：
    - 管理多个 API 端点的 OpenAI 客户端实例
    - 提供统一的聊天补全、向量化、Token 计数接口
    - 处理 API 调用失败时的重试逻辑

    使用示例：
        client = LLMClient(config)
        response = client.chat(messages=[{"role": "user", "content": "你好"}])
        for chunk in client.chat_stream(messages=...):
            print(chunk)
    """

    def __init__(self, config: Config):
        """
        初始化 LLM 客户端。

        参数：
            config: Config 对象，包含 API 端点配置、默认模型、温度等参数
        """
        self._config = config
        # 客户端缓存字典：key 为端点名称，value 为 OpenAI 客户端实例
        # 使用延迟初始化，首次访问某个端点时才创建对应客户端
        self._clients: dict[str, object] = {}  # name -> openai.OpenAI

    @property
    def config(self) -> Config:
        """获取当前配置对象。"""
        return self._config

    def update_config(self, config: Config) -> None:
        """
        更新配置并清除客户端缓存。

        说明：
        配置变更后必须清除客户端缓存，因为 base_url、api_key 等参数
        可能在新配置中发生了变化。清除后下次调用会自动重建客户端。

        参数：
            config: 新的 Config 对象
        """
        self._config = config
        self._clients.clear()  # 配置变更后重建 client
        self._cap_checked = False  # 配置变了，模型能力需重新探测

    # ------------------------------------------------------------------
    # P9 模型能力护栏
    # ------------------------------------------------------------------
    def _probe_model(self, model: str) -> tuple[bool, str]:
        """用 1-token 最小调用探测模型是否可用（零业务开销）。返回 (ok, error_msg)。"""
        try:
            client, _ = self._get_client()
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True, ""
        except Exception as e:  # 404 / 鉴权 / 网络 —— 都算不可用
            return False, str(e)

    def ensure_model(self) -> None:
        """惰性探测默认模型能力；不可用时回退安全默认，避免"运行时才崩"。

        只在首次实际调用前跑一次（self._cap_checked 缓存）。探测失败不影响后续——
        若连安全默认都不可用，则保留原配置，让真实调用抛出明确错误（比静默错模型好）。
        """
        if getattr(self, "_cap_checked", False):
            return
        self._cap_checked = True
        model = self._config.default_chat_model
        if not model:
            return
        ok, err = self._probe_model(model)
        if ok:
            return
        fb = SAFE_DEFAULT_MODEL
        if fb and fb != model:
            ok2, _ = self._probe_model(fb)
            if ok2:
                logger.warning(
                    f"[P9] 默认模型 '{model}' 不可用（{err}），回退安全默认 '{fb}'"
                )
                self._config.default_chat_model = fb
                return
        logger.warning(f"[P9] 默认模型 '{model}' 探测失败且回退也失败：{err}")

    def _get_client(self, endpoint_name: Optional[str] = None):
        """
        获取指定端点的 OpenAI 客户端实例（延迟初始化）。

        逻辑：
        1. 如果指定了端点名称，使用该端点；否则使用默认端点
        2. 检查客户端缓存，如果已创建则直接返回
        3. 如果未创建，使用端点配置初始化 OpenAI 客户端并缓存

        参数：
            endpoint_name: 端点名称（可选），为 None 时使用默认端点

        返回：
            tuple: (OpenAI 客户端实例, LLMEndpoint 配置对象)

        异常：
            ValueError: 未配置任何 API 端点时抛出
        """
        # 延迟导入 openai 库，避免在模块加载时就依赖该库
        from openai import OpenAI

        # 确定要使用哪个端点
        if endpoint_name:
            ep = self._config.get_endpoint(endpoint_name)
        else:
            ep = self._config.get_default_endpoint()

        if not ep:
            raise ValueError("未配置任何 API 端点，请先在设置中添加。")

        # 缓存检查：如果此端点尚未创建客户端，则创建并缓存
        if ep.name not in self._clients:
            self._clients[ep.name] = OpenAI(
                base_url=ep.base_url,
                api_key=ep.api_key,
                timeout=self._config.request_timeout,
            )
        return self._clients[ep.name], ep

    def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        endpoint: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
        retry: int = 3,
    ) -> str:
        """
        同步聊天补全（非流式）。

        功能说明：
        向指定的 LLM 端点发送聊天请求，等待完整响应后返回。
        内置重试机制，失败时按指数退避策略等待后重试。

        参数：
            messages: OpenAI 格式的消息列表，例如：
                      [{"role": "user", "content": "你好"}]
            model: 模型名称（可选），为 None 时使用配置中的 default_chat_model
            endpoint: 端点名称（可选），为 None 时使用默认端点
            temperature: 温度参数（可选），控制生成随机性，为 None 时使用配置值
            max_tokens: 最大生成 token 数（可选），为 None 时使用配置值
            response_format: 响应格式约束，例如 {"type": "json_object"} 强制返回 JSON
            retry: 最大重试次数，默认为 3

        返回：
            生成的完整文本字符串（如果模型返回为空则返回空字符串）

        异常：
            ValueError: 未配置默认聊天模型时抛出
            RuntimeError: 重试次数用尽后仍失败时抛出，包含最后一次错误原因
        """
        client, ep = self._get_client(endpoint)
        self.ensure_model()  # P9：惰性探测 + 安全回退
        used_model = model or self._config.default_chat_model
        if not used_model:
            raise ValueError("未配置默认聊天模型。")

        # 使用参数值或配置默认值
        temp = temperature if temperature is not None else self._config.temperature
        max_tok = max_tokens or self._config.max_tokens

        # 构建 API 调用参数
        kwargs = {
            "model": used_model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
        }
        if response_format:
            kwargs["response_format"] = response_format

        last_error = None
        for attempt in range(retry):
            try:
                resp = client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or ""
            except Exception as e:
                last_error = e
                wait = 2 ** attempt  # 指数退避：第1次等1秒，第2次等2秒，第3次等4秒
                logger.warning(f"API调用失败(第{attempt+1}次): {e}，{wait}秒后重试")
                time.sleep(wait)

        raise RuntimeError(f"API调用失败（重试{retry}次后）: {last_error}")

    @staticmethod
    def _looks_truncated(raw: str) -> bool:
        """启发式判断响应是否被 max_tokens 截断（导致 JSON 不完整）。

        - 空响应：视为截断（重试提高上限）。
        - 去空白不以 '}'（对象）或 ']'（数组）结尾：几乎一定是被切断。
        - 括号未配平：被切断。
        """
        if not raw or not raw.strip():
            return True
        s = raw.strip()
        if s[-1] not in ("}", "]"):
            return True
        # 括号配平检查（忽略字符串内的括号）
        depth = 0
        in_str = False
        esc = False
        for ch in s:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth < 0:
                    return True
        return depth != 0

    def chat_json(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        endpoint: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
        retry: int = 3,
        max_tokens_cap: int = 16000,
    ) -> dict:
        """带**截断检测**的 JSON 调用（P4 防护）。

        `chat()` 只在网络/API 异常时重试，对「max_tokens 截断导致 JSON 残破」
        无感知——这正是 25 事件 JSON 静默丢失的根因。本方法在每次调用后：
          1. 启发式判断是否被截断（_looks_truncated）；
          2. 若截断 → 翻倍 max_tokens 重试（封顶 max_tokens_cap）；
          3. 仍无法解析 → 抛 RuntimeError（携带最后片段，便于排查）。
        返回解析后的 dict。正常完整响应走 json.loads，无额外开销。
        """
        cur_max = max_tokens or self._config.max_tokens
        last_raw = ""
        for attempt in range(retry):
            try:
                raw = self.chat(
                    messages=messages, model=model, endpoint=endpoint,
                    temperature=temperature, max_tokens=cur_max,
                    response_format=response_format, retry=1,
                )
            except Exception:
                # 网络/API 异常交给 chat 内部退避；这里直接冒泡（避免掩盖真实错误）
                raise
            last_raw = raw or ""
            if self._looks_truncated(last_raw):
                cur_max = min(cur_max * 2, max_tokens_cap)
                logger.warning(f"响应疑似被截断（长度 {len(last_raw)}），提高 max_tokens 至 {cur_max} 重试")
                continue
            try:
                return json.loads(last_raw)
            except json.JSONDecodeError:
                cur_max = min(cur_max * 2, max_tokens_cap)
                logger.warning(f"JSON 解析失败，提高 max_tokens 至 {cur_max} 重试")
                continue
        raise RuntimeError(
            f"chat_json 在 {retry} 次内始终截断/无法解析（最后片段: {last_raw[-200:]!r}）"
        )

    def chat_stream(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        endpoint: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retry: int = 3,
    ) -> Generator[str, None, None]:
        """
        流式聊天补全，逐块 yield 文本。
        """
        client, ep = self._get_client(endpoint)
        self.ensure_model()  # P9：惰性探测 + 安全回退
        used_model = model or self._config.default_chat_model
        if not used_model:
            raise ValueError("未配置默认聊天模型。")

        temp = temperature if temperature is not None else self._config.temperature
        max_tok = max_tokens or self._config.max_tokens

        last_error = None
        for attempt in range(retry):
            try:
                stream = client.chat.completions.create(
                    model=used_model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=max_tok,
                    stream=True,
                )
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return  # 成功完成
            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(f"流式API失败(第{attempt+1}次): {e}，{wait}秒后重试")
                time.sleep(wait)

        raise RuntimeError(f"流式API失败（重试{retry}次后）: {last_error}")

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
        model: Optional[str] = None,
        endpoint: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retry: int = 3,
    ) -> dict:
        """带工具调用的同步聊天（OpenAI function-calling 协议）。

        返回：
            {
              "content": str,                       # 模型文本（可能为空）
              "tool_calls": [{"id","name","arguments":dict}],
              "assistant_message": dict,            # 可原样 append 回 messages 的消息
            }
        若 model 不支持 tools，会抛出明确异常（被上层捕获）。
        """
        client, ep = self._get_client(endpoint)
        self.ensure_model()  # P9：惰性探测 + 安全回退
        used_model = model or self._config.default_chat_model
        if not used_model:
            raise ValueError("未配置默认聊天模型。")
        temp = temperature if temperature is not None else self._config.temperature
        max_tok = max_tokens or self._config.max_tokens

        last_error = None
        for attempt in range(retry):
            try:
                resp = client.chat.completions.create(
                    model=used_model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=max_tok,
                    tools=tools,
                    tool_choice=tool_choice,
                )
                msg = resp.choices[0].message
                content = msg.content or ""
                tool_calls: list[dict] = []
                for tc in (msg.tool_calls or []):
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    tool_calls.append(
                        {"id": tc.id, "name": tc.function.name, "arguments": args}
                    )
                # 构造可回灌的 assistant message（tool_calls 需 arguments 为字符串）
                assistant_message = {"role": "assistant", "content": content}
                if tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": t["id"],
                            "type": "function",
                            "function": {
                                "name": t["name"],
                                "arguments": json.dumps(t["arguments"], ensure_ascii=False),
                            },
                        }
                        for t in tool_calls
                    ]
                return {
                    "content": content,
                    "tool_calls": tool_calls,
                    "assistant_message": assistant_message,
                }
            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(f"工具调用失败(第{attempt+1}次): {e}，{wait}秒后重试")
                time.sleep(wait)
        raise RuntimeError(f"工具调用失败（重试{retry}次后）: {last_error}")

    def embed(
        self,
        texts: list[str],
        model: Optional[str] = None,
        endpoint: Optional[str] = None,
        retry: int = 3,
    ) -> list[list[float]]:
        """
        批量文本 embedding。

        Args:
            texts: 文本列表
            model: embedding 模型名
            endpoint: 端点名
        Returns:
            向量列表
        """
        client, ep = self._get_client(endpoint or self._config.default_embed_endpoint)
        used_model = model or self._config.default_embed_model
        if not used_model:
            raise ValueError("未配置 embedding 模型。")

        all_embeddings: list[list[float]] = []
        batch_size = 16  # 避免单次请求过大

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            last_error = None
            for attempt in range(retry):
                try:
                    resp = client.embeddings.create(model=used_model, input=batch)
                    for item in resp.data:
                        all_embeddings.append(item.embedding)
                    break
                except Exception as e:
                    last_error = e
                    wait = 2 ** attempt
                    logger.warning(f"Embedding失败(第{attempt+1}次): {e}，{wait}秒后重试")
                    time.sleep(wait)
            else:
                raise RuntimeError(f"Embedding失败: {last_error}")

        return all_embeddings

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        """估算 token 数。"""
        return TokenCounter.count(text, model)

    def count_messages(self, messages: list[dict], model: Optional[str] = None) -> int:
        """估算 messages 总 token 数。"""
        return TokenCounter.count_messages(messages, model)

    def test_connection(self, endpoint_name: Optional[str] = None) -> tuple[bool, str]:
        """测试端点连通性。"""
        try:
            client, ep = self._get_client(endpoint_name)
            # 简单发一条消息测试
            used_model = self._config.default_chat_model
            if not used_model and ep.models:
                used_model = ep.models[0]
            if not used_model:
                return False, "未配置模型"

            resp = client.chat.completions.create(
                model=used_model,
                messages=[{"role": "user", "content": "你好"}],
                max_tokens=10,
            )
            return True, f"连接成功（{ep.name} / {used_model}）"
        except Exception as e:
            return False, f"连接失败: {e}"

    def test_embedding(self, endpoint_name: Optional[str] = None) -> tuple[bool, str]:
        """测试 embedding 端点。"""
        try:
            client, ep = self._get_client(endpoint_name or self._config.default_embed_endpoint)
            used_model = self._config.default_embed_model
            if not used_model:
                return False, "未配置 embedding 模型"

            resp = client.embeddings.create(model=used_model, input=["测试"])
            dim = len(resp.data[0].embedding)
            return True, f"连接成功（维度: {dim}）"
        except Exception as e:
            return False, f"Embedding失败: {e}"
