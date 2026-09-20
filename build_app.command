#!/bin/bash
cd "$(dirname "$0")"
echo "Build AIChat.app..."
pip3 install --quiet pyinstaller
rm -rf build dist __pycache__
echo "打包已准备就绪，需要时手动执行：" 
echo "  python3 -m PyInstaller --clean --noconfirm AIChat.spec" 
read -n 1 -s -r -p "按任意键关闭..."
echo
