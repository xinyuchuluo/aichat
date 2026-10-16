"""LLM 客户端 SPI + 步骤级 profile 路由。

升级自 data-analyst-tool/core/llm_client.py 的设计（OpenAI 兼容、客户端缓存、
首次连接预热、按步骤路由到不同 profile），搬到内核让所有域共享。

api_profiles 与 step_routing 配置存 core/config_vault.py（AES 加密）：
    {
      "api_profiles": [
        {"name": "GLM-4.6", "base_url": "https://open.bigmodel.cn/v1", "model": "glm-4.6"},
        {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}
      ],
      "step_routing": {
        "chat:default": "DeepSeek",
        "novel:outline": "GLM-4.6",
        "analysis:plan": "GLM-4.6",
        "warehouse:parse": "GLM-4.6"
      }
    }
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Generator, Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMProfile:
    name: str
    base_url: str
    model: str
    api_key: str = ""
    extra: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.extra is None:
            self.extra = {}


@dataclass
class LLMResponse:
    text: str
    profile: str
    model: str
    elapsed_ms: int
    raw: Any = None


class LLMClient:
    """统一 LLM 调用入口"""

    def __init__(self, profiles: list[LLMProfile], step_routing: dict[str, str]) -> None:
        self._profiles: dict[str, LLMProfile] = {p.name: p for p in profiles}
        self._routing: dict[str, str] = dict(step_routing)
        # openai 客户端实例缓存 + 首次预热
        self._clients: dict[str, Any] = {}
        self._warmed: set[str] = set()

    # ---- profile 管理 ----
    def list_profiles(self) -> list[LLMProfile]:
        return list(self._profiles.values())

    def get_profile(self, name: str) -> Optional[LLMProfile]:
        return self._profiles.get(name)

    def set_profile(self, name: str, base_url: str, model: str, api_key: str, extra: Optional[dict] = None) -> None:
        self._profiles[name] = LLMProfile(name, base_url, model, api_key, extra or {})
        # 失效缓存
        self._clients.pop(name, None)
        self._warmed.discard(name)

    def remove_profile(self, name: str) -> None:
        self._profiles.pop(name, None)
        self._clients.pop(name, None)
        self._warmed.discard(name)
        # 路由表清理
        self._routing = {k: v for k, v in self._routing.items() if v != name}

    # ---- 路由 ----
    def set_step_routing(self, step: str, profile_name: str) -> None:
        if profile_name not in self._profiles:
            raise KeyError(f"profile {profile_name!r} not registered")
        self._routing[step] = profile_name

    def resolve_profile(self, step: str) -> LLMProfile:
        """根据 step 找到 profile，找不到时回退到 default，再找不到抛错"""
        name = self._routing.get(step) or self._routing.get("default")
        if name is None or name not in self._profiles:
            raise KeyError(f"no profile routed for step {step!r} and no default fallback")
        return self._profiles[name]

    # ---- 客户端实例 ----
    def _get_client(self, profile: LLMProfile) -> Any:
        """openai.OpenAI 客户端实例缓存"""
        if profile.name in self._clients:
            return self._clients[profile.name]
        try:
            from openai import OpenAI  # 延迟导入，避免内核硬依赖
        except ImportError as e:
            raise RuntimeError("openai package not installed; run pip install openai") from e
        client = OpenAI(base_url=profile.base_url, api_key=profile.api_key)
        self._clients[profile.name] = client
        return client

    def _warmup(self, profile: LLMProfile) -> None:
        """首次连接预热：消除冷启动超时"""
        if profile.name in self._warmed:
            return
        try:
            client = self._get_client(profile)
            # 触发一次极短的 models.list 调用，预热连接
            if hasattr(client, "models"):
                list(client.models.list(limit=1))
            self._warmed.add(profile.name)
            logger.info("LLM profile %s warmed up", profile.name)
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM warmup failed for %s: %s", profile.name, e)

    # ---- 调用 ----
    def call(
        self,
        prompt: str,
        step: str,
        *,
        system: Optional[str] = None,
        history: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        profile = self.resolve_profile(step)
        self._warmup(profile)
        client = self._get_client(profile)
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=profile.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, profile=profile.name, model=profile.model, elapsed_ms=elapsed_ms, raw=resp)

    def call_stream(
        self,
        prompt: str,
        step: str,
        *,
        system: Optional[str] = None,
        history: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Generator[str, None, LLMResponse]:
        """流式调用，yield 增量文本片段；return LLMResponse 总览"""
        profile = self.resolve_profile(step)
        self._warmup(profile)
        client = self._get_client(profile)
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        t0 = time.perf_counter()
        stream = client.chat.completions.create(
            model=profile.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )
        collected = []
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                collected.append(delta)
                yield delta
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return LLMResponse(
            text="".join(collected),
            profile=profile.name,
            model=profile.model,
            elapsed_ms=elapsed_ms,
            raw=None,
        )

    # ---- 探活 ----
    def test_connection(self, profile_name: str) -> bool:
        profile = self._profiles.get(profile_name)
        if profile is None:
            return False
        try:
            client = self._get_client(profile)
            if hasattr(client, "models"):
                list(client.models.list(limit=1))
            self._warmed.add(profile.name)
            return True
        except Exception:  # noqa: BLE001
            return False


# 模块级单例
_client: Optional[LLMClient] = None


def init(profiles: list[LLMProfile], step_routing: dict[str, str]) -> LLMClient:
    """应用启动时初始化全局 LLM 客户端"""
    global _client
    _client = LLMClient(profiles, step_routing)
    return _client


def get() -> LLMClient:
    if _client is None:
        raise RuntimeError("LLMClient not initialized; call core.llm_client.init first")
    return _client
