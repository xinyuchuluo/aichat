"""AIChatStudio 平台内核 core/

写一次，五域共用。内核纪律（硬约束）：
1. 内核不许 import 任何域（core/ 下任何文件不得 from domains.*）
2. 域只许 import 内核
3. 域之间不许互相 import
4. 提示词/背景/模板全部走 prompts/ 数据文件，不许硬编码进 .py
5. 任何 LLM 产出不得直接落库，必须经"提案→闸门→权威"通道

公共服务清单：
- workspace       多工作区、声明启用域、数据物理隔离
- llm_client      OpenAI 兼容接口 + 步骤级 profile 路由
- config_vault    AES Fernet 加密配置存储
- proposal_gate   提案→闸门→权威落库（三档严格度）
- guard_registry  零 token 守卫注册框架
- graph_primitives 关系/事件/DAG 原语
- vector_spi      向量检索 SPI（默认 sqlite-vec 实现）
- prompt_repo     提示词仓库服务（Jinja2 + 版本号 + 可回滚）
"""

__all__ = [
    "workspace",
    "llm_client",
    "config_vault",
    "proposal_gate",
    "guard_registry",
    "graph_primitives",
    "vector_spi",
    "prompt_repo",
]
