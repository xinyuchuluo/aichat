"""NovelForge 全局配置管理。"""
from __future__ import annotations

import json
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_DEFAULT_CONFIG = {
    "endpoints": [],
    "default_chat_model": "",
    "default_embed_model": "",
    "default_embed_endpoint": "",
    "temperature": 0.8,
    "max_tokens": 4096,
    "request_timeout": 120,
    "context_window": 128000,
    "recent_summary_count": 5,
    "rag_top_k": 8,
    # RAG 原始散文注入（写作 prompt 内）：默认关闭。
    # 一致性靠档案库结构化事实 + 邻近大纲 + 上一章结尾精确文本，而非把已写正文再喂回模型
    # （等价于「手动把小说念给 AI 听」，token 高且问题照旧）。仅当用户明确需要风格/细节参考时开启。
    "rag_inject_into_writing": False,
    # 轻量 RAG：仅检索设定/伏笔，不注入完整正文散文（兼顾长线一致性、不爆 token、不产生文本重复）
    "rag_inject_foreshadow_only": False,
    "chunk_size": 500,
    "chunk_overlap": 50,
    "adjacent_outline_window": 3,
    # 主线脊柱账本（治百万/千万字主线崩溃）：本地零 LLM 更新，始终注入且体量恒定
    "story_spine_enabled": True,
    "spine_milestone_max": 15,          # 主线关键节点保留上限（稀疏采样，横跨全书）
    # 配角轮换（治「只有主角太无聊」）：近窗口在场人物名单，供每章带戏
    "active_cast_window": 30,           # 视为「近期在场」的章节窗口
    "active_cast_max": 8,               # 花名册人数上限
    # 世界观注入封顶（防百万字后设定条目无限膨胀）
    "world_settings_max": 40,
    "lan_port": 8765,
    # 人物注入窗口化（方案B：档案库窗口化，防长篇小说人物无限膨胀）
    # 写作时只注入「与本章程相关（关键词命中）+ 近期在场（最近 window 章更新过）」的人物，封顶 max。
    # 既控 token，又提质：上百号人全塞进提示词会淹没主角、劣化一致性。
    "character_inject_max": 15,        # 单章注入人物档案上限（>15 既爆 token 又劣化质量）
    "character_window": 40,            # 视为「近期在场」的章节窗口（人物 canon 窗口化用）
    # 活跃伏笔注入上限（活跃伏笔虽已过滤未回收，但长线作品可达上百条；按优先级保留前 max 条）
    "foreshadow_max": 50,
    "validation_level": "normal",
    # 写作提示词分层 + 预算门控
    "quality_tier": "standard",        # concise | standard | ultimate
    "system_prompt_budget": 6000,      # 写作系统提示 token 上限（L0/L1 常驻，L2 超预算丢弃）
    # 文风：一条固定的写法指令（如「冷峻白描、对话精炼、少用修饰语」），
    # 作为常驻硬约束注入写作系统提示。文风由提示词规定，而非注入长段原文模仿（省 token、稳定）。
    "writing_style": "",
    # 质量审查通道（独立调用，不进写作提示词）
    # 默认关闭：每章写后自动跑一次钩子审查会额外消耗 LLM 调用（500 章 = 500 次）。
    # 改为「前置约束（写时即遵守）+ 按需校验（手动点「校验」/全书最终校验）」，避免凭空烧 token。
    "enable_quality_review": False,    # 是否每章写后自动运行钩子/去重/反AI味审查（默认关）
    "quality_auto_fix": False,         # 审查发现问题后是否自动单轮修订（默认关，仅报告）
}


@dataclass
class LLMEndpoint:
    name: str
    base_url: str
    api_key: str
    models: list[str] = field(default_factory=list)
    is_default: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "models": self.models,
            "is_default": self.is_default,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LLMEndpoint":
        return cls(
            name=d.get("name", ""),
            base_url=d.get("base_url", ""),
            api_key=d.get("api_key", ""),
            models=d.get("models", []),
            is_default=d.get("is_default", False),
        )


@dataclass
class Config:
    """全局配置。"""
    endpoints: list[LLMEndpoint] = field(default_factory=list)
    default_chat_model: str = ""
    default_embed_model: str = ""
    default_embed_endpoint: str = ""
    temperature: float = 0.8
    max_tokens: int = 4096
    request_timeout: int = 120
    context_window: int = 128000
    recent_summary_count: int = 5
    rag_top_k: int = 8
    rag_inject_into_writing: bool = False
    rag_inject_foreshadow_only: bool = False
    chunk_size: int = 500
    chunk_overlap: int = 50
    adjacent_outline_window: int = 3
    story_spine_enabled: bool = True
    spine_milestone_max: int = 15
    active_cast_window: int = 30
    active_cast_max: int = 8
    world_settings_max: int = 40
    lan_port: int = 8765
    character_inject_max: int = 15
    character_window: int = 40
    foreshadow_max: int = 50
    validation_level: str = "normal"
    # 写作提示词分层 + 预算门控
    quality_tier: str = "standard"
    system_prompt_budget: int = 6000
    writing_style: str = ""
    # 质量审查通道
    enable_quality_review: bool = False
    quality_auto_fix: bool = False
    skin: str = "mocha"

    # 运行时：优先使用项目本地目录，兜底到用户主目录
    config_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / ".novelforge")

    @classmethod
    def load(cls) -> "Config":
        """从 .novelforge/config.json 加载配置，不存在则用默认值。"""
        # 优先使用项目本地目录（可执行文件所在目录）
        config_dir = Path(__file__).resolve().parent.parent / ".novelforge"
        config_path = config_dir / "config.json"

        data = copy.deepcopy(_DEFAULT_CONFIG)

        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                data.update(saved)
            except Exception:
                pass  # 配置损坏时用默认值

        cfg = cls(
            endpoints=[LLMEndpoint.from_dict(e) for e in data.get("endpoints", [])],
            default_chat_model=data.get("default_chat_model", ""),
            default_embed_model=data.get("default_embed_model", ""),
            default_embed_endpoint=data.get("default_embed_endpoint", ""),
            temperature=data.get("temperature", 0.8),
            max_tokens=data.get("max_tokens", 4096),
            request_timeout=data.get("request_timeout", 120),
            context_window=data.get("context_window", 128000),
            recent_summary_count=data.get("recent_summary_count", 5),
            rag_top_k=data.get("rag_top_k", 8),
            rag_inject_into_writing=bool(data.get("rag_inject_into_writing", False)),
            rag_inject_foreshadow_only=bool(data.get("rag_inject_foreshadow_only", False)),
            chunk_size=data.get("chunk_size", 500),
            chunk_overlap=data.get("chunk_overlap", 50),
            adjacent_outline_window=data.get("adjacent_outline_window", 3),
            story_spine_enabled=bool(data.get("story_spine_enabled", True)),
            spine_milestone_max=int(data.get("spine_milestone_max", 15)),
            active_cast_window=int(data.get("active_cast_window", 30)),
            active_cast_max=int(data.get("active_cast_max", 8)),
            world_settings_max=int(data.get("world_settings_max", 40)),
            lan_port=int(data.get("lan_port", 8765)),
            character_inject_max=int(data.get("character_inject_max", 15)),
            character_window=int(data.get("character_window", 40)),
            foreshadow_max=int(data.get("foreshadow_max", 50)),
            validation_level=data.get("validation_level", "normal"),
            quality_tier=data.get("quality_tier", "standard"),
            system_prompt_budget=int(data.get("system_prompt_budget", 6000)),
            writing_style=data.get("writing_style", ""),
            enable_quality_review=bool(data.get("enable_quality_review", False)),
            quality_auto_fix=bool(data.get("quality_auto_fix", False)),
            skin=data.get("skin", "mocha"),
            config_dir=config_dir,
        )
        return cfg

    def save(self) -> None:
        """保存配置到 ~/.novelforge/config.json。"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.config_dir / "config.json"
        data = {
            "endpoints": [e.to_dict() for e in self.endpoints],
            "default_chat_model": self.default_chat_model,
            "default_embed_model": self.default_embed_model,
            "default_embed_endpoint": self.default_embed_endpoint,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "request_timeout": self.request_timeout,
            "context_window": self.context_window,
            "recent_summary_count": self.recent_summary_count,
            "rag_top_k": self.rag_top_k,
            "rag_inject_into_writing": self.rag_inject_into_writing,
            "rag_inject_foreshadow_only": self.rag_inject_foreshadow_only,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "adjacent_outline_window": self.adjacent_outline_window,
            "story_spine_enabled": self.story_spine_enabled,
            "spine_milestone_max": self.spine_milestone_max,
            "active_cast_window": self.active_cast_window,
            "active_cast_max": self.active_cast_max,
            "world_settings_max": self.world_settings_max,
            "lan_port": self.lan_port,
            "character_inject_max": self.character_inject_max,
            "character_window": self.character_window,
            "foreshadow_max": self.foreshadow_max,
            "validation_level": self.validation_level,
            "quality_tier": self.quality_tier,
            "system_prompt_budget": self.system_prompt_budget,
            "writing_style": self.writing_style,
            "enable_quality_review": self.enable_quality_review,
            "quality_auto_fix": self.quality_auto_fix,
            "skin": self.skin,
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_default_endpoint(self) -> Optional[LLMEndpoint]:
        """获取默认端点。"""
        for ep in self.endpoints:
            if ep.is_default:
                return ep
        return self.endpoints[0] if self.endpoints else None

    def get_endpoint(self, name: str) -> Optional[LLMEndpoint]:
        """按名称获取端点。"""
        for ep in self.endpoints:
            if ep.name == name:
                return ep
        return None

    def to_dict(self) -> dict:
        return {
            "endpoints": [e.to_dict() for e in self.endpoints],
            "default_chat_model": self.default_chat_model,
            "default_embed_model": self.default_embed_model,
            "default_embed_endpoint": self.default_embed_endpoint,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "request_timeout": self.request_timeout,
            "context_window": self.context_window,
            "recent_summary_count": self.recent_summary_count,
            "rag_top_k": self.rag_top_k,
            "rag_inject_into_writing": self.rag_inject_into_writing,
            "rag_inject_foreshadow_only": self.rag_inject_foreshadow_only,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "adjacent_outline_window": self.adjacent_outline_window,
            "story_spine_enabled": self.story_spine_enabled,
            "spine_milestone_max": self.spine_milestone_max,
            "active_cast_window": self.active_cast_window,
            "active_cast_max": self.active_cast_max,
            "world_settings_max": self.world_settings_max,
            "lan_port": self.lan_port,
            "character_inject_max": self.character_inject_max,
            "character_window": self.character_window,
            "foreshadow_max": self.foreshadow_max,
            "validation_level": self.validation_level,
            "quality_tier": self.quality_tier,
            "system_prompt_budget": self.system_prompt_budget,
            "writing_style": self.writing_style,
            "enable_quality_review": self.enable_quality_review,
            "quality_auto_fix": self.quality_auto_fix,
            "skin": self.skin,
        }
