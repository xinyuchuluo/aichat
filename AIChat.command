#!/bin/bash
# ============================================================
# AIChat — 通用 AI 聊天软件 (macOS 双击启动)
# ============================================================
cd "$(dirname "$0")"
SCRIPT_DIR="$( pwd )"
echo "╔══════════════════════════════════════════════════╗"
echo "║         AIChat — 通用 AI 聊天软件                ║"
echo "╚══════════════════════════════════════════════════╝"
echo "    软件目录: $SCRIPT_DIR"
echo "    数据目录: $HOME/.aichat"
echo ""
echo "[1/3] 检查依赖..."
MISSING=""
for MOD in PyQt6 chromadb openai; do
    if ! python3 -c "import ${MOD}" 2>/dev/null; then MISSING="${MISSING} ${MOD}"; fi
done
if [ -n "$MISSING" ]; then
    echo "      缺少依赖: $MISSING → 自动安装..."
    pip3 install --quiet $MISSING
    echo "      安装完成 ✓"
else
    echo "      依赖齐全 ✓"
fi
echo ""
echo "[2/3] 启动主窗口..."
PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH}" python3 "$SCRIPT_DIR/main.py"
EXIT_CODE=$?
echo ""
echo "[3/3] 程序已退出 (exit=$EXIT_CODE)"
read -n 1 -s -r -p "按任意键关闭..."
echo
