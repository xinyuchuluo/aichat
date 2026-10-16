"""
config_manager.py — API Key 的加密存储与读取
使用 AES (Fernet) 加密，Key 存于用户目录 ~/.data_analyst/config.enc
支持自定义 API 端点（任意 OpenAI 兼容接口）

数据结构（v2）：
  api_profiles: 命名接口配置列表，每个包含 name/url/key/model/timeout
  step_*_profile: 各步骤使用的接口名称（指向 api_profiles 中某项的 name）
  chat_profile: AI 对话使用的接口名称
"""
from __future__ import annotations
import os
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".data_analyst"
CONFIG_FILE = CONFIG_DIR / "config.enc"
KEY_FILE = CONFIG_DIR / "secret.key"


def _get_fernet():
    from cryptography.fernet import Fernet
    if not KEY_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        KEY_FILE.write_bytes(key)
        KEY_FILE.chmod(0o600)
    return Fernet(KEY_FILE.read_bytes())


def save_config(config: dict):
    """保存配置（加密）"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    f = _get_fernet()
    encrypted = f.encrypt(json.dumps(config, ensure_ascii=False).encode())
    CONFIG_FILE.write_bytes(encrypted)


def load_config() -> dict:
    """读取配置（解密）"""
    if not CONFIG_FILE.exists():
        return {}
    try:
        f = _get_fernet()
        decrypted = f.decrypt(CONFIG_FILE.read_bytes())
        return json.loads(decrypted.decode())
    except Exception:
        return {}


# ── 默认配置（v2：命名接口配置模式）─────────────────────────────
DEFAULT_CONFIG = {
    # 命名 API 接口配置列表（每个是一套完整的 URL + Key + Model + 超时）
    "api_profiles": [
        {
            "name": "GLM-4.6 直连",
            "url": "https://open.bigmodel.cn/api/paas/v4/",
            "key": "",
            "model": "GLM-4.6",
            "timeout": 600,
            "thinking": False,
        },
        {
            "name": "Gemini 中转",
            "url": "https://openai.sufy.com/v1",
            "key": "",
            "model": "gemini-3.1-pro-preview",
            "timeout": 300,
            "thinking": False,
        },
        {
            "name": "DeepSeek",
            "url": "https://api.deepseek.com/v1",
            "key": "",
            "model": "deepseek-chat",
            "timeout": 120,
            "thinking": False,
        },
    ],

    # 各步骤使用哪个接口（存的是 api_profiles 中的 name）
    "step_script_profile": "Gemini 中转",     # Step 2: 生成提炼脚本
    "step_analysis_profile": "GLM-4.6 直连",  # Step 4: 深度推理分析
    "step_report_profile": "Gemini 中转",     # Step 5: 生成 HTML 报告
    "chat_profile": "Gemini 中转",             # AI 对话

    # 通用
    "output_dir": str(Path.home() / "data_analyst_output"),
}


def migrate_old_config(config: dict) -> dict:
    """
    将旧版扁平字段迁移到新的 api_profiles 结构。
    如果检测到旧字段但无 api_profiles，自动迁移。
    迁移后保留旧字段（向后兼容），但优先使用新结构。
    """
    has_old = any(k in config for k in (
        "script_api_url", "analysis_api_url", "report_api_url"
    ))
    has_new = "api_profiles" in config and config["api_profiles"]

    if has_old and not has_new:
        profiles = []
        # 收集旧步骤配置，去重合并为 profiles
        step_map = {
            "script": ("script_api_url", "script_api_key", "script_model", "script_timeout", "Step 2 脚本"),
            "analysis": ("analysis_api_url", "analysis_api_key", "analysis_model", "analysis_timeout", "Step 4 分析"),
            "report": ("report_api_url", "report_api_key", "report_model", "report_timeout", "Step 5 报告"),
        }
        seen_keys = set()
        for step_key, (uk, kk, mk, tk, label) in step_map.items():
            url = config.get(uk, "")
            key = config.get(kk, "")
            model = config.get(mk, "")
            timeout = config.get(tk, 300)
            profile_key = (url, model)
            if url and profile_key not in seen_keys:
                seen_keys.add(profile_key)
                profiles.append({
                    "name": f"{model} ({label})",
                    "url": url,
                    "key": key,
                    "model": model,
                    "timeout": int(timeout) if timeout else 300,
                })
                # 映射步骤到该 profile
                config[f"step_{step_key}_profile"] = profiles[-1]["name"]

        if profiles:
            config["api_profiles"] = profiles
            # 对话模型迁移
            chat_model = config.get("chat_model", "")
            if chat_model:
                config["chat_profile"] = chat_model

    return config


def resolve_profile(config: dict, profile_name: str) -> dict:
    """
    根据接口名称从 api_profiles 中解析出完整的连接参数。
    返回: {"url", "key", "model", "timeout", "thinking"}
    如果找不到对应 profile，返回空字典。
    """
    profiles = config.get("api_profiles", [])
    for p in profiles:
        if p.get("name") == profile_name:
            return {
                "url": p.get("url", ""),
                "key": p.get("key", ""),
                "model": p.get("model", ""),
                "timeout": int(p.get("timeout", 300)),
                "thinking": bool(p.get("thinking", False)),
            }
    return {}
# 常用模型推荐列表（用于新建接口时的模型下拉提示）
COMMON_MODELS = [
    "GLM-4.6",
    "gemini-3.1-pro-preview",
    "gemini-2.0-flash",
    "deepseek-chat",
    "deepseek-reasoner",
    "qwen-max",
    "qwen-plus",
    "gpt-4o",
    "gpt-4o-mini",
]
