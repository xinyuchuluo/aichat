"""5 套导出模板（PRD §四 Tab4）。

每个模板是一个 Python 函数：输入分镜 + 关联资产，输出拼接好的文本。
纯字符串拼接，无 AI 润色。

模板列表：
  - markdown: 完整分镜文档
  - drawthings: DrawThings 绘图提示词
  - blender: Blender bpy 脚本生成 Prompt
  - seedance: Seedance/小云雀视频生成提示词
  - tts: 旁白文本 + 角色 TTS 参数
"""
from __future__ import annotations

from .markdown import export_markdown
from .drawthings import export_drawthings
from .blender import export_blender
from .seedance import export_seedance
from .tts import export_tts

__all__ = [
    "export_markdown",
    "export_drawthings",
    "export_blender",
    "export_seedance",
    "export_tts",
]
