"""文本处理工具。"""
from __future__ import annotations

import re
from typing import Generator


def split_paragraphs(text: str) -> list[str]:
    """按空行分割段落，保留段尾换行。"""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in paragraphs if p.strip()]


def split_sentences(text: str) -> list[str]:
    """中文友好的分句。按。！？；…等标点分割。"""
    # 保留分隔符
    pattern = r"(?<=[。！？；…\n])"
    sentences = re.split(pattern, text)
    return [s.strip() for s in sentences if s.strip()]


def chunk_text(
    text: str,
    target_size: int = 500,
    overlap: int = 50,
) -> list[str]:
    """
    将长文本分块。

    策略：
    1. 优先按自然段落分块
    2. 段落超长则按句子切分
    3. 目标 chunk 大小 target_size 字符
    4. 保留 overlap 字符重叠窗口
    """
    if len(text) <= target_size:
        return [text]

    paragraphs = split_paragraphs(text)
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # 段落本身超长，按句子切
        if len(para) > target_size:
            if current:
                chunks.append(current)
                current = ""

            sentences = split_sentences(para)
            sent_buf = ""
            for sent in sentences:
                if len(sent_buf) + len(sent) <= target_size:
                    sent_buf += sent
                else:
                    if sent_buf:
                        chunks.append(sent_buf)
                        # overlap：保留尾部
                        if overlap > 0 and len(sent_buf) > overlap:
                            sent_buf = sent_buf[-overlap:] + sent
                        else:
                            sent_buf = sent
                    else:
                        # 单句就超长，硬切
                        for i in range(0, len(sent), target_size - overlap):
                            chunks.append(sent[i:i + target_size])
                        sent_buf = ""
            if sent_buf:
                current = sent_buf
        # 当前块 + 段落不超长，追加
        elif len(current) + len(para) + 2 <= target_size:
            current = (current + "\n\n" + para) if current else para
        else:
            if current:
                chunks.append(current)
            # overlap
            if overlap > 0 and len(current) > overlap:
                current = current[-overlap:] + "\n\n" + para
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks


def extract_keywords(text: str, top_n: int = 10) -> list[str]:
    """
    简单关键词提取（用于档案库触发检索）。
    优先用 jieba，不可用则按 2-gram。
    """
    try:
        import jieba
        import jieba.analyse
        return jieba.analyse.extract_tags(text, topK=top_n)
    except ImportError:
        # 2-gram fallback
        cleaned = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\s]", "", text)
        tokens = cleaned.split()
        bigrams: list[str] = []
        for token in tokens:
            chars = re.findall(r"[\u4e00-\u9fa5]", token)
            for i in range(len(chars) - 1):
                bigrams.append(chars[i] + chars[i + 1])
        # 频率排序
        from collections import Counter
        freq = Counter(bigrams)
        return [w for w, _ in freq.most_common(top_n)]


def truncate_text(text: str, max_chars: int, strategy: str = "tail") -> str:
    """
    裁剪文本到指定长度。

    strategy:
        tail  - 保留开头，裁剪尾部
        head  - 保留结尾，裁剪开头
        middle - 保留头尾，裁剪中间
    """
    if len(text) <= max_chars:
        return text

    if strategy == "tail":
        return text[:max_chars] + "…"
    elif strategy == "head":
        return "…" + text[-max_chars:]
    elif strategy == "middle":
        keep = max_chars // 2
        return text[:keep] + "\n…（省略）…\n" + text[-keep:]
    return text[:max_chars]


def count_words(text: str) -> int:
    """统计字数（中文字符 + 英文单词）。"""
    chinese = len(re.findall(r"[\u4e00-\u9fa5]", text))
    english = len(re.findall(r"[a-zA-Z]+", text))
    return chinese + english
