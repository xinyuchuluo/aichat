"""聊天适配器集合。

适配器是外部系统接入聊天协议的桥梁：
- GenericAdapter — 通用闲聊
- NovelForgeAdapter — 集成 NovelForge 小说项目记忆/讨论规则
- DataAnalysisAdapter — 集成 data-analyst-tool 数据分析工作流程
"""
from .generic import GenericAdapter
from .novelforge import NovelForgeAdapter
from .data_analysis import DataAnalysisAdapter


# 适配器注册表
_ADAPTERS: dict[str, type] = {}


def register_adapter(cls) -> None:
    """注册适配器类。"""
    _ADAPTERS[cls().key] = cls


def get_adapter(key: str, **kwargs):
    """获取适配器实例。"""
    if not _ADAPTERS:
        _register_all()
    cls = _ADAPTERS.get(key, GenericAdapter)
    return cls(**kwargs)


def list_adapters() -> list[dict]:
    """列出所有已注册适配器。"""
    if not _ADAPTERS:
        _register_all()
    return [{"key": k, "name": cls().display_name, "desc": cls().description} for k, cls in _ADAPTERS.items()]


def register_adapters(main_window, config) -> None:
    """向主窗口注册所有适配器（供 UI 选择）。"""
    adapters = list_adapters()
    main_window.set_available_adapters(adapters)


def _register_all() -> None:
    """注册所有内置适配器。"""
    register_adapter(GenericAdapter)
    for cls in (NovelForgeAdapter, DataAnalysisAdapter):
        try:
            register_adapter(cls)
        except Exception:
            pass


# 自动注册
_register_all()
