"""
main.py — 程序入口
"""
from __future__ import annotations
import sys
import re
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from .gui.main_window import MainWindow


# ===== 启动时安全检查：确保 pipeline 加载了最新的 _sanitize_script =====
try:
    from core.pipeline import AnalysisPipeline

    # 检查关键方法是否存在
    if not hasattr(AnalysisPipeline, '_sanitize_script'):
        print("[警告] pipeline.py 缺少 _sanitize_script，正在注入...")

        def _sanitize_script(self, script_code: str):
            """（启动时注入）自动修复 AI 脚本中的已知错误模式"""
            fixes = []
            # encoding
            pat = re.compile(r"(pd\.read_excel\([^)]*?),\s*encoding\s*=\s*['][^']*[']\s*([,)])", re.DOTALL)
            s, n = pat.subn(r'\1\2', script_code)
            if n > 0:
                fixes.append(f"移除 read_excel encoding ({n}处)")
                script_code = s
            # dtype=str
            pat2 = re.compile(r'(pd\.read_excel\([^)]*?),\s*dtype\s*=\s*(?:[\'"]str[\'"]|str)\s*([,)])', re.DOTALL)
            s2, n2 = pat2.subn(r'\1\2', script_code)
            if n2 > 0:
                fixes.append(f"移除 read_excel dtype= str ({n2}处)")
                script_code = s2
            # describe skipna
            pat3 = re.compile(r'\.describe\(([^)]*?),?\s*skipna\s*=\s*\w+\s*,?\s*([^)]*)\)', re.DOTALL)
            def _fd(m):
                b, a = m.group(1).strip().rstrip(','), m.group(2).strip().lstrip(',')
                return f'.describe({", ".join(p for p in [b, a] if p)})'
            s3, n3 = pat3.subn(_fd, script_code)
            if n3 > 0:
                fixes.append(f"移除 describe skipna ({n3}处)")
                script_code = s3
            # df.corr()
            s4, n4 = re.subn(r'\bdf\.corr\(\)', 'df.select_dtypes(include="number").corr()', script_code)
            if n4 > 0:
                fixes.append(f"修复 df.corr() ({n4}处)")
                script_code = s4
            # select_dtypes object
            s5, n5 = re.subn(r"select_dtypes\(include=\['object'\]\)", "select_dtypes(include=['object', 'string'])", script_code)
            if n5 > 0:
                fixes.append(f"修复 select_dtypes object ({n5}处)")
                script_code = s5
            return script_code, fixes

        AnalysisPipeline._sanitize_script = _sanitize_script
        print("[OK] _sanitize_script 已注入")
    else:
        print("[OK] pipeline._sanitize_script 已就绪")
except Exception as e:
    print(f"[警告] 安全检查失败: {e}")
# ===== 安全检查结束 =====


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("智能数据分析工具")
    app.setApplicationVersion("1.0.0")

    # 设置全局字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
