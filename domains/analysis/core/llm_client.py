"""
llm_client.py — 统一 LLM 调用层，支持任意 OpenAI 兼容接口
API 地址可自定义，支持自定义模型名称

优化：客户端实例复用 + 连接预热，解决"首次调用必超时"问题
"""
from __future__ import annotations
import time
from openai import OpenAI, APITimeoutError, APIError

# ── 模块级客户端缓存 ──────────────────────────────────────
# 同一 (base_url, api_key) 组合复用 OpenAI 客户端实例，
# 避免每次调用都重新建立 TCP/TLS 连接（冷启动开销 1~5 秒）
_client_cache = {}
_client_cache_max = 8  # 最多缓存 N 个不同配置的客户端


def _get_or_create_client(base_url: str, api_key: str, timeout: int) -> OpenAI:
    """获取或创建缓存的 OpenAI 客户端"""
    cache_key = (base_url, api_key)
    client = _client_cache.get(cache_key)
    if client is not None:
        return client

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
    )

    # 缓存上限保护：超过时清除最旧的条目
    if len(_client_cache) >= _client_cache_max:
        oldest_key = next(iter(_client_cache))
        del _client_cache[oldest_key]

    _client_cache[cache_key] = client
    return client


def _warmup_connection(client: OpenAI, model: str, log_callback=None) -> bool:
    """
    轻量级连接预热：发送一个极简请求来建立 TCP/TLS 连接。
    使用 client.models.list() 而非 chat.completions.create()，
    因为前者是元数据查询，几乎不消耗 token 和时间。
    返回 True 表示预热成功，False 表示跳过或失败（不阻塞主流程）。
    """
    try:
        t0 = time.time()
        # models.list 是最轻量的 API 调用，仅用于触发 DNS+TCP+TLS 握手
        list(client.models.list(limit=1))
        elapsed = time.time() - t0
        if log_callback:
            log_callback(f"  [预热] 连接就绪 ({elapsed:.1f}s)")
        return True
    except Exception as e:
        # 预热失败不阻塞主流程（可能是 API 不支持 /models 端点）
        if log_callback:
            log_callback(f"  [预热] 跳过 ({e})")
        return False


def call_llm(
    base_url: str,
    model: str,
    api_key: str,
    prompt: str,
    system: str = "你是一名专业的数据分析师。",
    temperature: float = 0.3,
    max_tokens: int = 8192,
    log_callback=None,
    timeout: int = 300,
    max_retries: int = 2,
    thinking: bool = False,
) -> str:
    """
    调用指定 API 端点的 LLM，返回文本内容。
    支持任意 OpenAI 兼容接口（如 DeepSeek、通义千问、Gemini、自建服务等）

    base_url: API 端点地址，如 https://api.deepseek.com/v1
    model: 模型名称，如 deepseek-chat、gemini-3.1-pro-preview
    api_key: API 密钥
    log_callback: 可选，接受 str 参数，用于向 GUI 回传日志
    timeout: 单次请求超时（秒），默认300秒（兼容慢速模型）
    max_retries: 超时/连接错误时重试次数，默认2次
    thinking: 是否启用深度思考模式（通过 extra_body 传递，依赖 API 支持）
    """
    if not base_url:
        raise ValueError("API 地址不能为空，请在设置页配置")
    if not api_key:
        raise ValueError("API Key 不能为空，请在设置页配置")
    if not model:
        raise ValueError("模型名称不能为空，请在设置页配置")

    # 清理 URL 末尾的斜杠
    base_url = base_url.rstrip("/")

    # 判断是否需要预热（在缓存查询之前）
    cache_key = (base_url, api_key)
    is_fresh = cache_key not in _client_cache

    # 使用缓存客户端（避免每次重建连接）
    client = _get_or_create_client(base_url, api_key, timeout)

    # 首次使用新端点时进行连接预热（建立 TCP/TLS 连接，消除冷启动延迟）
    if is_fresh:
        _warmup_connection(client, model, log_callback)

    if log_callback:
        log_callback(f"  → 调用 [{model}]，发送 {len(prompt)} 字符...")
        # 显示 prompt 前 300 字符预览（方便用户确认内容）
        prompt_preview = prompt[:300].replace("\n", " ↵ ")
        if len(prompt) > 300:
            prompt_preview += f"... (共{len(prompt)}字符)"
        log_callback(f"  📝 Prompt 预览: {prompt_preview}")

    last_error = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            # 上游错误（502/503）用更长退避，最长60秒
            wait = min(2 ** attempt * 5, 60)
            if log_callback:
                log_callback(f"  ⏳ 第 {attempt} 次重试（首次调用可能因冷启动延迟），等待 {wait}s...")
            time.sleep(wait)

        start = time.time()
        try:
            # 构建 extra_body（用于传递深度思考等非标准参数）
            extra = {}
            if thinking:
                # 通用 OpenAI 兼容方式：通过 extra_body 传递 thinking 参数
                # DeepSeek 支持 extra_body={"thinking": True}
                # 其他 API 可能使用不同字段，此处为通用实现
                extra["thinking"] = True

            create_kwargs = dict(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if extra:
                create_kwargs["extra_body"] = extra
            if log_callback and thinking:
                log_callback(f"  🧠 深度思考模式已启用")

            resp = client.chat.completions.create(**create_kwargs)
            elapsed = time.time() - start
            content = resp.choices[0].message.content

            if log_callback:
                tokens = getattr(resp.usage, "total_tokens", "?")
                retry_note = f"（第{attempt}次重试成功）" if attempt > 0 else ""
                log_callback(f"  ← [{model}] 响应完成{retry_note}，用时 {elapsed:.1f}s，Tokens: {tokens}")

            return content
        except APITimeoutError as e:
            elapsed = time.time() - start
            last_error = e
            msg = f"[{model}] 请求超时（已等待 {elapsed:.0f}s）"
            if log_callback:
                cold_hint = "【可能是首次调用冷启动】" if attempt == 0 and is_fresh else ""
                log_callback(f"  ⚠ {msg}{cold_hint}，尝试重试...")
            if attempt == max_retries:
                raise RuntimeError(f"LLM API 请求超时，已重试 {max_retries} 次仍未成功。\n"
                                   f"模型：{model}\n"
                                   f"建议：\n"
                                   f"1. 检查网络连接\n"
                                   f"2. 使用更快的模型或减小数据量\n"
                                   f"3. 更换 API 端点")
        except APIError as e:
            elapsed = time.time() - start
            last_error = e
            # 提取状态码（不同 openai 版本可能放在不同属性）
            status = getattr(e, "status_code", None) or getattr(e, "http_status", None)
            if status in (502, 503, 504):
                msg = f"[{model}] 上游服务繁忙（HTTP {status}，已等待 {elapsed:.0f}s）"
            elif status == 429:
                msg = f"[{model}] 请求过于频繁/超出限额（HTTP {status}，已等待 {elapsed:.0f}s）"
            elif status:
                msg = f"[{model}] API 返回错误（HTTP {status}，已等待 {elapsed:.0f}s）"
            else:
                msg = f"[{model}] 请求出错（{e}，已等待 {elapsed:.0f}s）"
            if log_callback:
                log_callback(f"  ⚠ {msg}，尝试重试...")
            if attempt == max_retries:
                raise RuntimeError(f"LLM API 调用失败，已重试 {max_retries} 次仍未成功。\n"
                                   f"最后错误：{msg}\n\n"
                                   f"建议：\n"
                                   f"1. 该模型当前负载较高，请稍后重试\n"
                                   f"2. 在「API 配置」中更换其他模型（如 Gemini、GPT-4）\n"
                                   f"3. 增加「超时时间」设置，给模型更多响应时间\n"
                                   f"4. 检查 API 端点是否稳定")
        except Exception as e:
            elapsed = time.time() - start
            last_error = e
            if log_callback:
                log_callback(f"  ⚠ 请求出错: {e}，尝试重试...")
            if attempt == max_retries:
                raise

    raise last_error


def test_connection(base_url: str, model: str, api_key: str) -> tuple[bool, str]:
    """测试 API 连接是否有效，返回 (成功, 消息)"""
    try:
        result = call_llm(
            base_url=base_url,
            model=model,
            api_key=api_key,
            prompt="请回复：连接成功",
            system="你是一个测试助手。",
            max_tokens=20,
        )
        return True, f"连接成功：{result[:50]}"
    except Exception as e:
        return False, f"连接失败：{str(e)}"
