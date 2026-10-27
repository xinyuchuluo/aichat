#!/usr/bin/env python3
"""MemSync MCP server 启动器（stdio）。

用法（在任意 MCP 客户端里配置）：

    command: python3
    args: ["/Users/chenzhichao/AIChat/scripts/memsync_mcp.py", "--allow-write"]

只读部署去掉 --allow-write；公司档案加 --profile company。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.memory.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
