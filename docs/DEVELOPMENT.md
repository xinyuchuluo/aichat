# AIChatStudio 开发指南

> 如何加新域、打包、同步、跨平台注意事项、常见坑。

## 1. 加新域（5 步）

假设要加一个域 `media`（媒体管理）：

### 步骤 1：建目录

```
domains/media/
├── __init__.py      # 注册 DomainManifest
├── models.py        # 数据层（SQLite 表 + CRUD）
├── widgets/
│   ├── __init__.py
│   └── main_window.py  # 主面板 QWidget
└── (其他模块)
```

### 步骤 2：写 widgets/main_window.py

```python
from __future__ import annotations
from PyQt6.QtWidgets import QMainWindow, QTabWidget, QVBoxLayout, QLabel

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("媒体管理")
        self.setMinimumSize(800, 600)
        self.setCentralWidget(QLabel("媒体管理域"))
```

### 步骤 3：写 __init__.py 注册域

```python
from __future__ import annotations
from typing import Optional

_main_window: Optional[object] = None

def _build_main_window():
    global _main_window
    if _main_window is not None:
        return _main_window
    from .widgets.main_window import MainWindow
    _main_window = MainWindow()
    return _main_window

def register_domain() -> None:
    from shell.main_window import DomainManifest, register_domain as _register
    _register(DomainManifest(
        key="media",
        display_name="媒体管理",
        widget_factory=_build_main_window,
        supports_standalone_window=True,
    ))

try:
    register_domain()
except Exception:
    pass
```

### 步骤 4：加入工作区默认域

编辑 `core/workspace.py` 的 `DEFAULT_ENABLED_DOMAINS`，加 `"media"`。

### 步骤 5：验证

```bash
python3 main.py
# 侧边栏应出现"媒体管理"，点击能加载 Tab
```

## 2. 打包

### 2.1 Mac（.app）

```bash
pip install pyinstaller
pyinstaller pack/mac.spec
# 产物在 dist/AIChatStudio.app
```

### 2.2 Win（.exe）

```cmd
pip install pyinstaller
pyinstaller pack\win.spec
:: 产物在 dist\AIChatStudio.exe
```

### 2.3 spec 文件要点

- `hiddenimports`：含 `domains.*` 各域的子模块（PyInstaller 自动分析可能漏）
- `datas`：含 `prompts/` 目录（提示词是数据文件，必须打包）
- `excludes`：排除 `chromadb`（不依赖了）
- `sqlglot`：可选依赖，spec 里标注为可选

## 3. 同步方案

### 3.1 日常双机同步

```bash
# 拉取
git pull

# 改代码
# ...

# 提交推送
git add .
git commit -m "描述"
git push origin main
```

### 3.2 AI 改完等确认（AGENTS.md 铁律）

AI 改完代码**不自动 push**。流程：
1. AI 改完 → 告诉你改了哪些文件 + git status
2. 你确认 → 说"push" → AI 才执行 `git push origin main`
3. 你不确认 → 改动只在本地工作区

### 3.3 chat-warehouse 分支（未来，公司机最小部署）

```bash
# 从 main 创建分支，只保留 chat + warehouse 域
bash pack/sync-branch.sh

# 或手动：
git checkout -b chat-warehouse
git merge main -X theirs
# 删掉不需要的域目录
rm -rf domains/novel domains/studio domains/analysis
git add -A
git commit -m "chat-warehouse: 最小部署"
git push origin chat-warehouse
```

公司机：
```bash
git clone -b chat-warehouse git@github.com:xinyuchuluo/aichat.git
cd aichat
pip install -r requirements.txt
python3 main.py
```

### 3.4 git ls-remote 复核（防 git status 失真）

AGENTS.md 继承 data-analyst-tool 的铁律：`git status` 偶尔失真（本地认为 ahead 但远程已更新），用 `git ls-remote` 复核：

```bash
git ls-remote origin refs/heads/main
# 对比本地 HEAD
git rev-parse HEAD
```

## 4. 跨平台注意事项

### 4.1 Python 版本

- 最低 Python 3.9（Mac 系统自带 /usr/bin/python3 是 3.9）
- analysis 域全部文件加 `from __future__ import annotations`（兼容 3.9 的 `X | None` 语法）
- 如果升级到 3.10+，可以去掉 future annotations

### 4.2 字体 fallback

```css
"PingFang SC", "Helvetica Neue", "Microsoft YaHei",
system-ui, -apple-system, "Segoe UI", Roboto, sans-serif
```

- Mac：PingFang SC 命中（中文字），Helvetica Neue 命中（英文字）
- Win：Microsoft YaHei 命中（中文字），Segoe UI 命中（英文字）
- Qt 字体警告 `Microsoft YaHei not found` 在 Mac 上无害（fallback 链命中了 PingFang SC）

### 4.3 放弃 ChromaDB

原因：
- C++ 依赖在 Windows 上偶发编译失败
- PyInstaller 打包体积大（~200MB）
- sqlite-vec + numpy + BM25 三级降级已够用

替代方案：`core/vector_spi.py` 三级降级
```
sqlite-vec（原生 C 扩展，快）→ numpy（纯 Python，中等）→ BM25（关键词，兜底）
```

### 4.4 sqlglot 可选

数仓域 SQL 血缘解析依赖 sqlglot。未安装时：
- Tab 仍可打开（显示"sqlglot 未安装"提示）
- 解析功能禁用
- 其他域不受影响

安装：`pip install sqlglot`

### 4.5 本地模型（可选）

2060 显卡弱，但 Mac 可跑简单 AI。`core/llm_client.py` 支持配置驱动：
- API 模式（DeepSeek/OpenAI/通义）：走网络
- 本地模式：走 Ollama small llama
- 按工作区/机器配

requirements.txt 里 ollama/tiktoken/jieba 标注为可选。

## 5. 常见坑

### 5.1 QTabWidget.setTabData 不存在

PyQt6 的 `QTabWidget` 没有 `setTabData` 方法，要用 `tabBar().setTabData`：

```python
# 错（AttributeError: 'QTabWidget' object has no attribute 'setTabData'）
self._tabs.setTabData(idx, key)

# 对
self._tabs.tabBar().setTabData(idx, key)
self._tabs.tabBar().tabData(idx)
```

### 5.2 Config dataclass 没有 get 方法

原 AIChat 代码混用 `config.xxx`（dataclass 风格）和 `config.get("xxx")`（dict 风格）。修复：给 Config 加 dict 兼容方法：

```python
def get(self, key, default=None):
    if key in self.__dataclass_fields__:
        return getattr(self, key)
    return self.__dict__.get(key, default)

def __getitem__(self, key): ...
def __setitem__(self, key, value): ...
def __contains__(self, key): ...
```

### 5.3 Python 3.9 不支持 X | None

data-analyst-tool 用了 `str | None` 类型注解（3.10+ 语法）。修复：给所有 .py 加 `from __future__ import annotations`，让注解变字符串不运行时解析。

批量加的脚本：
```python
import os
for root, dirs, files in os.walk('domains/analysis'):
    if '__pycache__' in root: continue
    for f in files:
        if not f.endswith('.py'): continue
        path = os.path.join(root, f)
        with open(path) as fp: content = fp.read()
        if 'from __future__ import annotations' in content: continue
        # 找 docstring 末尾位置，插入 future import
        ...
```

### 5.4 tar 中转绕过 cp 权限问题

某些情况下 `cp -r /tmp/src ~/dest` 报 `Permission denied`（原因不明，可能是 macOS 保护）。用 tar 中转：

```bash
cd /tmp/src && tar cf - . | (cd ~/dest && tar xf -)
```

### 5.5 NovelForge 清场

NovelForge 包根目录有 13 个一次性脚本（regen_*.py / fix_*.py / auto_fix.py）和 3 个 backup 目录。迁移时**只 copy 内部 `novelforge/` 子目录**，散落脚本不进新仓库——天然清场。

### 5.6 字体警告无害

启动时看到 `qt.qpa.fonts: Populating font family aliases took 55 ms` 是 Qt 在 fallback 链里找 Microsoft YaHei（Mac 上不存在）。PingFang SC 已命中，渲染正常。可忽略。

## 6. 开发流程

```
1. 改代码
2. 本地验证：python3 main.py（或针对某域的 launch_standalone.py）
3. git add <具体文件>（不用 git add -A，避免误加）
4. git commit -m "描述"
5. 告诉用户改了什么 + git status
6. 等用户说"push"才 git push origin main
7. git ls-remote 复核远程 HEAD == 本地 HEAD
```

## 7. AGENTS.md 纪律引用

本指南的同步纪律继承自 [AGENTS.md](../AGENTS.md)，核心铁律：
- AI 不自动 push
- commit + push 铁律（用户确认才 push）
- `git ls-remote` 复核（防 git status 失真）
- 双机同步前先 pull
