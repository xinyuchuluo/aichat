"""Token 计数器。"""
from __future__ import annotations

import re
from typing import Optional

# tiktoken 可选
try:
    import tiktoken
    _HAS_TIKTOKEN = True
except ImportError:
    _HAS_TIKTOKEN = False


class TokenCounter:
    """
    Token 计数策略：
    1. 优先使用 tiktoken（OpenAI 模型）
    2. 中文文本：按 1 字 ≈ 1.5 token 估算（保守）
    3. 英文文本：按 1 word ≈ 1.3 token 估算
    4. 混合文本：分别统计中英文字符数后合并
    """

    _encoders: dict[str, "tiktoken.Encoding"] = {}

    @classmethod
    def count(cls, text: str, model: Optional[str] = None) -> int:
        if not text:
            return 0

        # 尝试 tiktoken
        if _HAS_TIKTOKEN and model:
            try:
                enc = cls._get_encoder(model)
                if enc:
                    return len(enc.encode(text))
            except Exception:
                pass

        # 混合估算
        return cls._estimate(text)

    @classmethod
    def count_messages(cls, messages: list[dict], model: Optional[str] = None) -> int:
        """估算 messages 列表的总 token 数。"""
        total = 0
        for msg in messages:
            # 每条消息有约 4 token 开销（role + 结构）
            total += 4
            content = msg.get("content", "")
            if isinstance(content, str):
                total += cls.count(content, model)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += cls.count(part["text"], model)
        total += 2  # 结尾辅助 token
        return total

    @classmethod
    def _get_encoder(cls, model: str):
        if model in cls._encoders:
            return cls._encoders[model]
        try:
            if "gpt-4" in model or "gpt-3.5" in model:
                enc = tiktoken.encoding_for_model(model)
            else:
                enc = tiktoken.get_encoding("cl100k_base")
            cls._encoders[model] = enc
            return enc
        except Exception:
            return None

    @staticmethod
    def _estimate(text: str) -> int:
        """中英混合 token 估算。"""
        chinese_chars = len(re.findall(r"[\u4e00-\u9fa5]", text))
        english_words = len(re.findall(r"[a-zA-Z]+", text))
        # 数字和标点按 0.5 token 估算
        other_chars = len(re.findall(r"[0-9]", text))

        # 保守估算：中文 1.5 token/字，英文 1.3 token/词
        return int(chinese_chars * 1.5 + english_words * 1.3 + other_chars * 0.5 + 0.5)
