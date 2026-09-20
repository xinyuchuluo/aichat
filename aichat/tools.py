"""Agent Skill 功能池 — 参考 OpenAI function calling / LangChain Tool 的注册模式。

每个 skill 是一个带名称、描述、参数 schema、handler 函数的可调用单元。
LLM 通过 <|tool| name="xxx">JSON</|tool|> 标签调用，宿主执行后以
<|tool_result| name="xxx"><![CDATA[ ... ]]></|tool_result> 返回。

内置 Skill：
  1. exec_shell     — 跑 shell 命令（白名单目录 + 超时 + 截断 + 危险命令拦截）
  2. find_files     — 本地 glob + grep 搜索
  3. read_file      — 读本地文件内容（支持行号范围、自动编码检测）
  4. write_file     — 写/创建本地文件（带安全沙箱）
  5. list_directory — 列目录内容（ls -la 风格）
  6. web_fetch      — 抓取 URL 网页内容（HTTP GET + 正文提取）
  7. python_eval    — 执行 Python 表达式 / 代码块（安全沙箱）
  8. get_system_info — 获取系统信息（OS / CPU / 内存 / 磁盘）

扩展方式：
  @skill("my_tool", "描述", {...schema...})
  def my_tool(**kwargs) -> dict:
      ...
"""
from __future__ import annotations

import dataclasses
import glob as _glob
import json
import os
import platform
import re
import shutil
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any, Callable


# ============================================================
# Skill 注册表（参考 OpenAI function calling 的设计）
# ============================================================

@dataclasses.dataclass
class SkillSpec:
    """单个 Skill 的规格说明（类似 OpenAI function definition）。"""
    name: str
    description: str
    parameters: dict[str, Any]        # JSON Schema 风格的参数描述
    handler: Callable[..., dict]      # 实际执行函数
    category: str = "general"         # 分类（file/shell/web/code/system）


SKILL_REGISTRY: dict[str, SkillSpec] = {}


def skill(name: str, description: str, parameters: dict[str, Any],
          category: str = "general") -> Callable:
    """装饰器：注册一个 skill 到 SKILL_REGISTRY。"""
    def decorator(func: Callable[..., dict]) -> Callable[..., dict]:
        SKILL_REGISTRY[name] = SkillSpec(
            name=name, description=description,
            parameters=parameters, handler=func, category=category,
        )
        return func
    return decorator


def list_skills() -> list[SkillSpec]:
    """返回所有已注册的 skill 列表。"""
    return list(SKILL_REGISTRY.values())


# ============================================================
# 安全沙箱：白名单根目录
# ============================================================
_ALLOWED_ROOTS: list[Path] = [
    Path.home(),
    Path("/tmp"),
    Path.cwd(),
]

_BINARY_HINTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
                 ".zip", ".gz", ".tar", ".7z", ".mp4", ".mp3", ".exe",
                 ".dll", ".so", ".dylib", ".pyc", ".bin", ".db",
                 ".class", ".jar", ".war", ".o", ".a", ".woff", ".ttf"}

_TEXT_MAX_BYTES = 2 * 1024 * 1024  # 单文件最多读 2MB 文本


def _is_path_safe(raw_path: str | None) -> tuple[Path, bool]:
    """检查路径是否在白名单内，返回 (resolved_path, is_safe)。"""
    if not raw_path:
        return Path.cwd(), True
    p = Path(os.path.expanduser(raw_path)).resolve()
    for root in _ALLOWED_ROOTS:
        try:
            p.relative_to(root.resolve())
            return p, True
        except ValueError:
            continue
    return p, False


def _looks_text(path: Path) -> bool:
    if path.suffix.lower() in _BINARY_HINTS:
        return False
    try:
        with path.open("rb") as fh:
            chunk = fh.read(1024)
        if b"\x00" in chunk:
            return False
    except OSError:
        return False
    return True


# ============================================================
# 1. exec_shell
# ============================================================
_DANGEROUS_RE = re.compile(
    r"(^|\s)(rm\s+-rf\s+/\s*$|mkfs|dd\s+of=/dev/(sd|hd|nvme)|chmod\s+(-R\s+)?777\s+/\s|:"
    r"\s*\(\s*:\s*\)\s*;\s*:\s*\)\s*\|\s*:\s*&\s*"
    r"|shutdown\s|reboot\s|halt\s|poweroff\s)",
    re.IGNORECASE,
)


def _safe_cwd(raw_cwd: str | None) -> str:
    if not raw_cwd:
        return str(Path.cwd())
    p, safe = _is_path_safe(raw_cwd)
    return str(p) if safe else str(Path.home())


@skill("exec_shell",
    "运行 shell 命令 / 程序。支持管道、通配符。危险命令会被拦截。",
    {
        "command": {"type": "string", "description": "要执行的 shell 命令"},
        "cwd": {"type": "string", "description": "可选，工作目录"},
        "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
    },
    category="shell",
)
def exec_shell(command: str, *, cwd: str | None = None,
               timeout: int = 30, safe: bool = True, **_kw) -> dict[str, Any]:
    t0 = time.time()
    if safe and _DANGEROUS_RE.search(command):
        return {"ok": False, "stdout": "",
                "stderr": f"[blocked] 命令被安全策略拦截：{command}",
                "exit_code": 126, "duration_ms": 0}
    timeout = max(1, min(timeout, 300))
    safe_cwd = _safe_cwd(cwd)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=safe_cwd, timeout=timeout,
            capture_output=True, text=True,
            env={**os.environ, "LANG": "zh_CN.UTF-8", "PYTHONIOENCODING": "utf-8"},
        )
        out = proc.stdout or ""
        err = proc.stderr or ""
        cap = 8000
        if len(out) > cap:
            out = out[:cap] + f"\n... [stdout 截断，原始长度 {len(out)}]"
        if len(err) > cap:
            err = err[:cap] + f"\n... [stderr 截断，原始长度 {len(err)}]"
        return {"ok": proc.returncode == 0, "stdout": out, "stderr": err,
                "exit_code": proc.returncode,
                "duration_ms": int((time.time() - t0) * 1000), "cwd": safe_cwd}
    except subprocess.TimeoutExpired as e:
        return {"ok": False,
                "stdout": (e.stdout or "")[:2000] if isinstance(e.stdout, str) else "",
                "stderr": f"[timeout] 命令超过 {timeout}s 未完成。",
                "exit_code": 124,
                "duration_ms": int((time.time() - t0) * 1000), "cwd": safe_cwd}
    except Exception as e:
        return {"ok": False, "stdout": "",
                "stderr": f"[error] {type(e).__name__}: {e}",
                "exit_code": 1,
                "duration_ms": int((time.time() - t0) * 1000), "cwd": safe_cwd}


# ============================================================
# 2. find_files
# ============================================================
@skill("find_files",
    "本地文件搜索：glob 模式匹配文件名，可选在文件内容里 grep 关键词。",
    {
        "pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
        "path": {"type": "string", "description": "搜索根目录，默认 ~"},
        "keyword": {"type": "string", "description": "可选，在文件内容里 grep"},
        "limit": {"type": "integer", "description": "最多返回条数，默认 50"},
    },
    category="file",
)
def find_files(*, pattern: str = "**/*", path: str | None = None,
               keyword: str | None = None, limit: int = 50,
               case_sensitive: bool = False, **_kw) -> dict[str, Any]:
    t0 = time.time()
    root = Path(os.path.expanduser(path or "~")).resolve()
    limit = max(1, min(limit, 500))
    matches: list[dict[str, Any]] = []
    scanned = 0

    if not root.exists():
        return {"error": f"根目录不存在: {root}", "matches": [],
                "scanned_files": 0, "duration_ms": 0}

    _, safe = _is_path_safe(str(root))
    if not safe:
        root = Path.home()

    try:
        g_iter = _glob.glob(os.path.join(str(root), pattern), recursive=True)
    except Exception as e:
        return {"error": f"glob 失败: {e}", "matches": [],
                "scanned_files": 0, "duration_ms": 0}

    kw = keyword.strip() if keyword else ""
    kw_lower = kw.lower() if not case_sensitive and kw else None

    for fp in g_iter:
        if len(matches) >= limit:
            break
        try:
            p = Path(fp)
            if not p.is_file():
                continue
            scanned += 1
            if not kw:
                size = p.stat().st_size
                matches.append({"path": str(p), "size_bytes": size})
                continue
            try:
                if p.stat().st_size > _TEXT_MAX_BYTES:
                    continue
            except OSError:
                continue
            if not _looks_text(p):
                continue
            try:
                line_matches: list[dict] = []
                with p.open("r", encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, start=1):
                        hay = line if case_sensitive else line.lower()
                        needle = kw if case_sensitive else (kw_lower or "")
                        if needle and needle in hay:
                            line_matches.append({"line": i, "text": line.rstrip("\n")[:200]})
                            if len(line_matches) >= 10:
                                break
                if line_matches:
                    matches.append({"path": str(p), "size_bytes": p.stat().st_size,
                                    "matches": line_matches})
            except (OSError, UnicodeDecodeError):
                continue
        except (OSError, PermissionError):
            continue

    return {"query": {"pattern": pattern, "path": str(root),
                     "keyword": keyword, "limit": limit},
            "scanned_files": scanned, "match_count": len(matches),
            "matches": matches, "duration_ms": int((time.time() - t0) * 1000),
            "os": platform.system()}


# ============================================================
# 3. read_file — 读本地文件内容
# ============================================================
@skill("read_file",
    "读取本地文件内容。支持行号范围、自动编码检测、二进制文件检测。",
    {
        "path": {"type": "string", "description": "文件路径"},
        "start_line": {"type": "integer", "description": "起始行号（从 1 开始），默认 1"},
        "end_line": {"type": "integer", "description": "结束行号，默认文件末尾"},
        "max_lines": {"type": "integer", "description": "最多读取行数，默认 500"},
    },
    category="file",
)
def read_file(path: str, *, start_line: int = 1, end_line: int | None = None,
              max_lines: int = 500, **_kw) -> dict[str, Any]:
    t0 = time.time()
    p = Path(os.path.expanduser(path)).resolve()

    _, safe = _is_path_safe(str(p))
    if not safe:
        return {"ok": False, "error": f"路径不在允许范围内: {p}"}
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {path}"}
    if not p.is_file():
        return {"ok": False, "error": f"不是文件: {path}"}

    size = p.stat().st_size
    if size > _TEXT_MAX_BYTES and not end_line:
        max_lines = min(max_lines, 200)

    if p.suffix.lower() in _BINARY_HINTS or not _looks_text(p):
        return {"ok": True, "path": str(p), "size_bytes": size,
                "is_binary": True, "content": "",
                "note": "二进制文件，不显示内容", "duration_ms": 0}

    try:
        with p.open("r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError as e:
        return {"ok": False, "error": f"读取失败: {e}"}

    total_lines = len(lines)
    start = max(1, start_line) - 1
    end = end_line if end_line else total_lines
    end = min(end, total_lines, start + max_lines)

    selected = lines[start:end]
    numbered = []
    for i, ln in enumerate(selected, start=start + 1):
        numbered.append(f"{i:>5} | {ln.rstrip()}")

    content = "\n".join(numbered)
    cap = 12000
    if len(content) > cap:
        content = content[:cap] + f"\n... [内容截断，显示 {end - start}/{total_lines} 行]"

    return {"ok": True, "path": str(p), "size_bytes": size,
            "total_lines": total_lines, "showed_lines": f"{start + 1}-{end}",
            "content": content, "is_binary": False,
            "duration_ms": int((time.time() - t0) * 1000)}


# ============================================================
# 4. write_file — 写/创建文件
# ============================================================
_WRITE_DANGEROUS = re.compile(r"(\.\./|/etc/|/usr/|/var/|/sys/|/proc/|/dev/)", re.IGNORECASE)

@skill("write_file",
    "写入或创建本地文件。已存在则覆盖。路径必须在白名单目录内。",
    {
        "path": {"type": "string", "description": "文件路径"},
        "content": {"type": "string", "description": "文件内容"},
        "append": {"type": "boolean", "description": "是否追加模式，默认 false（覆盖）"},
    },
    category="file",
)
def write_file(path: str, content: str, *, append: bool = False, **_kw) -> dict[str, Any]:
    t0 = time.time()
    p = Path(os.path.expanduser(path)).resolve()

    _, safe = _is_path_safe(str(p))
    if not safe:
        return {"ok": False, "error": f"路径不在允许范围内: {p}"}
    if _WRITE_DANGEROUS.search(str(p)):
        return {"ok": False, "error": f"路径包含敏感目录: {p}"}

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with p.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        size = p.stat().st_size
        return {"ok": True, "path": str(p), "size_bytes": size,
                "mode": "append" if append else "write",
                "duration_ms": int((time.time() - t0) * 1000)}
    except OSError as e:
        return {"ok": False, "error": f"写入失败: {e}",
                "duration_ms": int((time.time() - t0) * 1000)}


# ============================================================
# 5. list_directory — 列目录内容
# ============================================================
@skill("list_directory",
    "列出目录内容（类似 ls -la）。显示文件名、大小、修改时间、类型。",
    {
        "path": {"type": "string", "description": "目录路径，默认 ~"},
        "show_hidden": {"type": "boolean", "description": "是否显示隐藏文件，默认 false"},
        "sort_by": {"type": "string", "description": "排序方式: name/size/time，默认 name"},
    },
    category="file",
)
def list_directory(*, path: str | None = None, show_hidden: bool = False,
                   sort_by: str = "name", **_kw) -> dict[str, Any]:
    t0 = time.time()
    root = Path(os.path.expanduser(path or "~")).resolve()

    _, safe = _is_path_safe(str(root))
    if not safe:
        return {"ok": False, "error": f"路径不在允许范围内: {root}"}
    if not root.exists():
        return {"ok": False, "error": f"目录不存在: {root}"}
    if not root.is_dir():
        return {"ok": False, "error": f"不是目录: {root}"}

    entries: list[dict] = []
    try:
        for item in root.iterdir():
            if not show_hidden and item.name.startswith("."):
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            entries.append({
                "name": item.name,
                "path": str(item),
                "type": "dir" if item.is_dir() else "file",
                "size_bytes": stat.st_size if item.is_file() else 0,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            })
    except PermissionError:
        return {"ok": False, "error": f"无权限访问: {root}"}

    if sort_by == "size":
        entries.sort(key=lambda e: e["size_bytes"], reverse=True)
    elif sort_by == "time":
        entries.sort(key=lambda e: e["modified"], reverse=True)
    else:
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))

    return {"ok": True, "path": str(root), "count": len(entries),
            "entries": entries,
            "duration_ms": int((time.time() - t0) * 1000)}


# ============================================================
# 6. web_fetch — 抓取 URL 内容
# ============================================================
@skill("web_fetch",
    "抓取 URL 网页内容（HTTP GET）。自动提取正文文本，去掉 HTML 标签。",
    {
        "url": {"type": "string", "description": "要抓取的 URL"},
        "max_chars": {"type": "integer", "description": "最多返回字符数，默认 6000"},
        "timeout": {"type": "integer", "description": "超时秒数，默认 15"},
    },
    category="web",
)
def web_fetch(url: str, *, max_chars: int = 6000,
              timeout: int = 15, **_kw) -> dict[str, Any]:
    t0 = time.time()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": f"URL 必须以 http:// 或 https:// 开头: {url}"}

    try:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AIChat/1.0)",
                     "Accept": "text/html,application/json,*/*"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_TEXT_MAX_BYTES)
            content_type = resp.headers.get("Content-Type", "")
            status = resp.status

        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()

        try:
            text = raw.decode(encoding, errors="ignore")
        except (LookupError, UnicodeDecodeError):
            text = raw.decode("utf-8", errors="ignore")

        if "text/html" in content_type or "<html" in text.lower():
            text = _strip_html(text)

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [内容截断，原始长度 {len(text)}]"

        return {"ok": True, "url": url, "status": status,
                "content_type": content_type,
                "content": text, "length": len(text),
                "duration_ms": int((time.time() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}",
                "duration_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "duration_ms": int((time.time() - t0) * 1000)}


def _strip_html(html: str) -> str:
    """简单 HTML → 纯文本。"""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# ============================================================
# 7. python_eval — 执行 Python 代码（安全沙箱）
# ============================================================
_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "filter": filter, "float": float,
    "int": int, "len": len, "list": list, "map": map, "max": max,
    "min": min, "print": print, "range": range, "round": round,
    "set": set, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "type": type, "zip": zip, "isinstance": isinstance,
    "hasattr": hasattr, "getattr": getattr, "setattr": setattr,
    "reversed": reversed, "format": format, "chr": chr, "ord": ord,
    "hex": hex, "bin": bin, "oct": oct, "pow": pow, "divmod": divmod,
    "frozenset": frozenset, "complex": complex, "repr": repr,
}

@skill("python_eval",
    "执行 Python 表达式或代码块。内置 math/json/re 等常用模块。禁止文件/网络/系统操作。",
    {
        "code": {"type": "string", "description": "Python 代码"},
        "timeout": {"type": "integer", "description": "超时秒数，默认 10"},
    },
    category="code",
)
def python_eval(code: str, *, timeout: int = 10, **_kw) -> dict[str, Any]:
    t0 = time.time()
    if not code or not code.strip():
        return {"ok": False, "error": "code 为空"}

    dangerous = re.findall(
        r"\b(import\s+os|import\s+subprocess|import\s+shutil|import\s+sys|"
        r"__import__|open\s*\(|exec\s*\(|eval\s*\(|compile\s*\(|"
        r"globals\s*\(\s*\)|exit\s*\(|quit\s*\()" , code
    )
    if dangerous:
        return {"ok": False, "error": f"代码包含禁止的关键字: {dangerous}"}

    import math
    import json as _json
    import re as _re
    import datetime
    import collections
    import itertools
    import string
    import statistics
    import hashlib
    import io
    from contextlib import redirect_stdout

    sandbox_globals = {
        "__builtins__": _SAFE_BUILTINS,
        "math": math, "json": _json, "re": _re,
        "datetime": datetime, "collections": collections,
        "itertools": itertools, "string": string,
        "statistics": statistics, "hashlib": hashlib,
    }

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            result = eval(code, sandbox_globals, {})
        stdout_text = buf.getvalue()
        result_str = repr(result) if result is not None else ""
        return {"ok": True, "result": result_str,
                "stdout": stdout_text[:4000],
                "duration_ms": int((time.time() - t0) * 1000)}
    except SyntaxError:
        try:
            with redirect_stdout(buf):
                exec(code, sandbox_globals, {})
            stdout_text = buf.getvalue()
            return {"ok": True, "result": "",
                    "stdout": stdout_text[:4000],
                    "duration_ms": int((time.time() - t0) * 1000)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "stdout": buf.getvalue()[:2000],
                    "duration_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "stdout": buf.getvalue()[:2000],
                "duration_ms": int((time.time() - t0) * 1000)}


# ============================================================
# 8. get_system_info — 获取系统信息
# ============================================================
@skill("get_system_info",
    "获取当前系统信息：操作系统、CPU 核数、内存、磁盘使用情况。",
    {},
    category="system",
)
def get_system_info(**_kw) -> dict[str, Any]:
    t0 = time.time()
    info: dict[str, Any] = {
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "python_version": platform.python_version(),
        "hostname": platform.node(),
        "cpu_count": os.cpu_count(),
    }

    try:
        if platform.system() == "Darwin":
            mem = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True, timeout=5)
            if mem.returncode == 0:
                info["total_memory_bytes"] = int(mem.stdout.strip())
                info["total_memory_gb"] = round(int(mem.stdout.strip()) / (1024**3), 1)
        elif platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for ln in f:
                    if ln.startswith("MemTotal"):
                        info["total_memory_kb"] = int(ln.split()[1])
                        info["total_memory_gb"] = round(int(ln.split()[1]) / (1024**2), 1)
    except Exception:
        pass

    try:
        usage = shutil.disk_usage(Path.home())
        info["disk"] = {
            "total_gb": round(usage.total / (1024**3), 1),
            "used_gb": round(usage.used / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
        }
    except Exception:
        pass

    info["duration_ms"] = int((time.time() - t0) * 1000)
    return info


# ============================================================
# 系统提示生成（从注册表自动生成）
# ============================================================
def _gen_protocol_md() -> str:
    """从 SKILL_REGISTRY 自动生成工具说明，注入 system prompt。"""
    category_labels = {
        "file": "📁 文件操作",
        "shell": "🛠 Shell 命令",
        "web": "🌐 网络访问",
        "code": "💻 代码执行",
        "system": "🖥 系统信息",
        "general": "🔧 通用",
    }

    lines = [
        "",
        "---------- 可用 Skill 功能池 ----------",
        f"共 {len(SKILL_REGISTRY)} 个 skill 可调用。格式：",
        '  <|tool| name="skill_name">',
        "  {参数JSON}",
        "</|tool|>",
        "",
        "执行后我会把结果以 <|tool_result|> 返回，请根据结果继续作答。",
        "不要编造结果，必须等宿主返回再继续。",
        "",
    ]

    by_cat: dict[str, list[SkillSpec]] = {}
    for spec in SKILL_REGISTRY.values():
        by_cat.setdefault(spec.category, []).append(spec)

    idx = 0
    for cat, specs in by_cat.items():
        label = category_labels.get(cat, cat)
        lines.append(f"### {label}")
        for spec in specs:
            idx += 1
            lines.append("")
            lines.append(f"【{idx}. {spec.name}】{spec.description}")
            lines.append(f'  <|tool| name="{spec.name}">')
            params = spec.parameters
            if params:
                example = {}
                for pname, pspec in params.items():
                    ptype = pspec.get("type", "string")
                    if ptype == "integer":
                        example[pname] = 0
                    elif ptype == "boolean":
                        example[pname] = False
                    else:
                        example[pname] = f"<{pspec.get('description', pname)[:20]}>"
                lines.append(f"  {json.dumps(example, ensure_ascii=False)}")
            else:
                lines.append(f"  {{}}")
            lines.append(f"  </|tool|>")
            if params:
                for pname, pspec in params.items():
                    desc = pspec.get("description", "")
                    ptype = pspec.get("type", "any")
                    lines.append(f"    - {pname} ({ptype}): {desc}")

    lines.append("")
    lines.append("---------- Skill 结束 ----------")
    return "\n".join(lines)


TOOL_PROTOCOL_MD = _gen_protocol_md()


# ============================================================
# 工具分发：从字符串解析 <|tool|> 标签 → 从注册表分发
# ============================================================
_TOOL_RE = re.compile(
    r"<\|tool\|\s+name\s*=\s*[\"'](?P<name>\w+)[\"']\s*>"
    r"(?P<body>.*?)</\|tool\|>",
    re.DOTALL,
)


def extract_and_run(text: str) -> tuple[str, list[dict]]:
    """扫描 text 中的 <|tool|...></|tool|> 片段 → 执行 → 追加结果。"""
    if "<|tool|" not in text:
        return text, []

    results: list[dict] = []
    appendices: list[str] = []

    for m in _TOOL_RE.finditer(text):
        name = m.group("name")
        body = m.group("body").strip()
        try:
            args = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            results.append({"name": name, "args_raw": body,
                            "result": {"ok": False, "error": f"JSON 解析失败: {e}"}})
            appendices.append(
                f'<|tool_result| name="{name}"><![CDATA[JSON parse error: {e}]]></|tool_result|>'
            )
            continue

        spec = SKILL_REGISTRY.get(name)
        if spec is None:
            r = {"ok": False, "error": f"未知工具: {name}（可用: {', '.join(SKILL_REGISTRY.keys())}）"}
        else:
            try:
                r = spec.handler(**args)
            except TypeError as e:
                r = {"ok": False, "error": f"参数错误: {e}"}
            except Exception as e:
                r = {"ok": False, "error": f"{type(e).__name__}: {e}",
                     "traceback": traceback.format_exc()[:1000]}

        results.append({"name": name, "args": args, "result": r})

        # 结果渲染
        if isinstance(r, dict) and r.get("ok") is False and "blocked" in str(r.get("stderr", "")):
            txt = f"⚠️  安全拦截: {r.get('stderr')}\n请用户确认此命令。"
        elif name == "exec_shell":
            txt = (f"[exec_shell ok={r.get('ok')} exit={r.get('exit_code')} "
                   f"time_ms={r.get('duration_ms')} cwd={r.get('cwd')}]\n"
                   f"--- stdout ---\n{r.get('stdout', '')}\n"
                   f"--- stderr ---\n{r.get('stderr', '')}")
        elif name == "read_file":
            txt = (f"[read_file ok={r.get('ok')} lines={r.get('showed_lines', '?')}"
                   f" total={r.get('total_lines', '?')}]\n"
                   f"{r.get('content', r.get('error', ''))}")
        elif name == "list_directory":
            entries = r.get("entries", [])
            lines_txt = [f"[list_directory ok={r.get('ok')} count={r.get('count', 0)}]"]
            for e in entries[:50]:
                icon = "📁" if e["type"] == "dir" else "📄"
                lines_txt.append(f"  {icon} {e['name']:30s}  {e['size_bytes']:>10}  {e['modified']}")
            txt = "\n".join(lines_txt)
        else:
            try:
                txt = json.dumps(r, ensure_ascii=False, indent=2)
            except Exception:
                txt = str(r)

        appendices.append(
            f'\n<|tool_result| name="{name}"><![CDATA[\n{txt}\n]]></|tool_result|>\n'
        )

    out = text
    if appendices:
        out = out + "\n\n" + "\n".join(appendices)
    return out, results


# ============================================================
# 记忆提取协议：AI 回复里的隐藏记忆指令
# ============================================================
# AI 在正常回复中用 <|memory| type="xxx">JSON</|memory|> 携带结构化记忆。
# 这些内容对用户不可见 —— extract_memory() 会从原文中剥离，
# 返回 (干净正文, 记忆项列表)，记忆项由 agent 写入 L1/L2/L3/L4。
#
# 支持的记忆类型：
#   type="fact"       — 事实陈述（用户说过的话、对话中产生的事实）
#   type="preference" — 用户偏好（长期有效）
#   type="entity"     — 实体信息（人/项目/概念及其属性）
#   type="tree_node"  — 树形记忆节点（分支摘要）
#   type="reminder"   — 短期提醒（下次对话要记住的）
#
# 示例：
#   <|memory| type="fact">
#   {"content": "用户在开发 AIChat 项目，使用 PyQt6", "tags": ["项目", "技术栈"], "confidence": 0.95}
#   </|memory|>
#
#   <|memory| type="preference">
#   {"key": "皮肤偏好", "value": "cyberpunk", "context": "用户喜欢暗色赛博风格"}
#   </|memory|>

_MEMORY_RE = re.compile(
    r"<\|memory\|\s+type\s*=\s*[\"'](?P<type>\w+)[\"']\s*>"
    r"(?P<body>.*?)</\|memory\|>",
    re.DOTALL,
)


def extract_memory(text: str) -> tuple[str, list[dict[str, Any]]]:
    """从 AI 回复中剥离 <|memory|> 标签，返回 (干净正文, 记忆项列表)。

    记忆项列表每项: {type, data, raw}.
    干净正文 = 原文去掉所有 <|memory|> 块 + 多余空行清理。
    """
    if "<|memory|" not in text:
        return text, []

    items: list[dict[str, Any]] = []

    for m in _MEMORY_RE.finditer(text):
        mem_type = m.group("type").strip()
        body = m.group("body").strip()
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            # 不是合法 JSON，当纯文本存
            data = {"content": body}

        items.append({
            "type": mem_type,
            "data": data,
            "raw": m.group(0),
        })

    # 剥离所有 <|memory|>...</|memory|> 块
    clean = _MEMORY_RE.sub("", text)
    # 清理多余空行（剥离后可能留下连续空行）
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

    return clean, items


def _gen_memory_protocol_md() -> str:
    """生成记忆协议说明，注入 system prompt。"""
    return """
---------- 记忆写入协议 ----------
你在回复用户时，可以在正文里嵌入隐藏的记忆指令。
用户不会看到这些指令（宿主会自动剥离），但后台会把记忆写入四层记忆架构。

格式：
  <|memory| type="记忆类型">
  {JSON 数据}
  </|memory|>

可用记忆类型：

【1. fact — 事实陈述】
  用户提到的事实、对话中产生的关键信息。
  <|memory| type="fact">
  {"content": "事实内容", "tags": ["标签1", "标签2"], "confidence": 0.9}
  </|memory|>
  - content: 必填，事实的简洁描述（一句话，≤100字）
  - tags: 可选，分类标签
  - confidence: 可选，0~1 置信度，默认 0.8

【2. preference — 用户偏好】
  用户的长期偏好，跨会话有效。
  <|memory| type="preference">
  {"key": "偏好名", "value": "偏好值", "context": "为什么"}
  </|memory|>
  - key: 必填，偏好名称（如 "皮肤偏好"、"回复语言"）
  - value: 必填，偏好值
  - context: 可选，偏好来源上下文

【3. entity — 实体信息】
  记住某个实体（人/项目/概念）的属性和关系。
  <|memory| type="entity">
  {"name": "实体名", "attributes": {"属性1": "值1"}, "relations": ["关系1"]}
  </|memory|>
  - name: 必填，实体名称
  - attributes: 可选，属性键值对
  - relations: 可选，关系列表

【4. tree_node — 树形记忆节点】
  当对话进入新主题/阶段时，创建树形记忆分支。
  <|memory| type="tree_node">
  {"summary": "分支摘要", "parent": "父节点ID或root", "children": ["子主题1"]}
  </|memory|>
  - summary: 必填，这段对话的摘要（≤50字）
  - parent: 可选，父节点（默认 root）
  - children: 可选，预期子主题

【5. reminder — 短期提醒】
  下次对话需要记住的事项。
  <|memory| type="reminder">
  {"content": "提醒内容", "priority": "high/normal/low", "expire_after": "7d"}
  </|memory|>
  - content: 必填，提醒内容
  - priority: 可选，优先级（默认 normal）
  - expire_after: 可选，过期时间（如 1d/7d/30d，默认不过期）

使用规则：
  - 记忆指令必须放在回复正文的最前面或最后面，不要插在句子中间
  - 每条记忆必须是一句简洁的事实，不要写长段落
  - 只在真正有价值的信息时才写入记忆（用户偏好、关键事实、实体关系）
  - 闲聊/打招呼/客套话不需要记忆
  - 可以在一条回复里写多条记忆（不同 type）
---------- 记忆协议结束 ----------
"""


MEMORY_PROTOCOL_MD = _gen_memory_protocol_md()

# ============================================================
# NovelForge 专用 Skill（小说分析工具）
# ============================================================
# 这些 skill 在 NovelForge 适配器模式下激活。
# AI 通过 <|tool|> 标签调用，读取小说项目数据。

_NF_ADAPTER_REF = None  # 运行时由 MainWindow 注入

def set_novelforge_adapter(adapter) -> None:
    """注入 NovelForge 适配器实例，使 read_chapter 等 skill 能访问项目数据。"""
    global _NF_ADAPTER_REF
    _NF_ADAPTER_REF = adapter


def _get_nf_adapter():
    """获取已注入的 NovelForge 适配器。"""
    return _NF_ADAPTER_REF


@skill("read_chapter",
    "读取小说指定章节的完整正文。用于精确查看某章内容后再决定如何分析/改写。",
    {
        "chapter_id": {"type": "integer", "description": "章节号，如 12 表示第12章"},
        "max_chars": {"type": "integer", "description": "最多返回字符数，默认 4000"},
    },
    category="novel",
)
def read_chapter(chapter_id: int, *, max_chars: int = 4000, **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()
    nf = _get_nf_adapter()
    if nf is None:
        return {"ok": False, "error": "未激活 NovelForge 适配器（无项目上下文）"}

    path = nf.get_chapter_path(chapter_id)
    if not path:
        return {"ok": False, "error": f"第 {chapter_id} 章文件不存在",
                "chapter_id": chapter_id}

    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError as e:
        return {"ok": False, "error": f"读取失败: {e}"}

    total = len(text)
    if total > max_chars:
        text = text[:max_chars] + f"\n... [正文截断，原始 {total} 字]"
    return {"ok": True, "chapter_id": chapter_id, "path": path,
            "total_chars": total, "content": text,
            "duration_ms": int((_t.time() - t0) * 1000)}


@skill("search_novel",
    "在全本小说中检索关键词，返回含该关键词的段落（带章节号）。用于定位某设定/人物/线索在全书分布。",
    {
        "keyword": {"type": "string", "description": "要检索的关键词"},
        "limit": {"type": "integer", "description": "最多返回条数，默认 15"},
    },
    category="novel",
)
def search_novel(keyword: str, *, limit: int = 15, **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()
    nf = _get_nf_adapter()
    if nf is None:
        return {"ok": False, "error": "未激活 NovelForge 适配器"}

    if not keyword or not keyword.strip():
        return {"ok": False, "error": "keyword 不能为空"}

    kw = keyword.strip()
    kw_lower = kw.lower()
    chapters_dir = nf._project_data.get("chapters_dir", "")
    if not chapters_dir:
        root = Path(nf._project_root) / "chapters"
        chapters_dir = str(root) if root.exists() else ""

    if not chapters_dir or not Path(chapters_dir).exists():
        return {"ok": False, "error": "章节目录不存在",
                "chapters_dir": str(chapters_dir)}

    matches: list[dict] = []
    scanned = 0
    for fp in sorted(Path(chapters_dir).glob("chapter_*.md")):
        if len(matches) >= limit:
            break
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
            scanned += 1
            # 提取章节号
            name = fp.stem  # chapter_0012
            nums = re.findall(r"\d+", name)
            ch_id = int(nums[-1]) if nums else 0

            # 段落级搜索
            paragraphs = text.split("\n")
            for i, para in enumerate(paragraphs):
                if len(matches) >= limit:
                    break
                if kw_lower in para.lower():
                    # 取上下文
                    start = max(0, i - 1)
                    end = min(len(paragraphs), i + 2)
                    ctx = "\n".join(paragraphs[start:end])[:300]
                    matches.append({
                        "chapter_id": ch_id,
                        "line": i + 1,
                        "text": para.strip()[:200],
                        "context": ctx,
                    })
        except OSError:
            continue

    return {"ok": True, "keyword": kw, "scanned_chapters": scanned,
            "match_count": len(matches), "matches": matches,
            "duration_ms": int((_t.time() - t0) * 1000)}


@skill("list_characters",
    "列出小说全部人物档案：名字、角色定位、描述、当前状态。用于角色一致性分析。",
    {
        "role": {"type": "string", "description": "可选，按角色过滤: 主角/配角/反派/路人"},
    },
    category="novel",
)
def list_characters(*, role: str | None = None, **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()
    nf = _get_nf_adapter()
    if nf is None:
        return {"ok": False, "error": "未激活 NovelForge 适配器"}

    chars = nf.get_all_characters()
    if role:
        chars = [c for c in chars if c.get("role", "") == role]

    # 精简输出
    result = []
    for c in chars[:50]:
        result.append({
            "id": c.get("id", ""),
            "name": c.get("name", ""),
            "role": c.get("role", ""),
            "description": (c.get("description") or "")[:80],
            "current_state": (c.get("current_state") or "")[:60],
            "last_updated_chapter": c.get("last_updated_chapter", 0),
        })

    return {"ok": True, "count": len(result), "characters": result,
            "duration_ms": int((_t.time() - t0) * 1000)}


@skill("list_foreshadows",
    "列出小说全部伏笔：描述、埋设章节、状态、关联角色。用于伏笔追踪与一致性检查。",
    {
        "status": {"type": "string", "description": "可选，按状态过滤: planted/advanced/resolved/abandoned"},
    },
    category="novel",
)
def list_foreshadows(*, status: str | None = None, **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()
    nf = _get_nf_adapter()
    if nf is None:
        return {"ok": False, "error": "未激活 NovelForge 适配器"}

    fs = nf.get_all_foreshadows()
    if status:
        fs = [f for f in fs if f.get("status", "") == status]

    result = []
    for f in fs[:30]:
        result.append({
            "id": f.get("id", ""),
            "description": (f.get("description") or "")[:100],
            "planted_chapter": f.get("planted_chapter", 0),
            "status": f.get("status", "planted"),
            "priority": f.get("priority", 3),
            "related_characters": f.get("related_characters", []),
            "resolution_chapter": f.get("resolution_chapter"),
            "notes": (f.get("notes") or "")[:60],
        })

    return {"ok": True, "count": len(result), "foreshadows": result,
            "duration_ms": int((_t.time() - t0) * 1000)}


@skill("check_consistency",
    "检查小说设定一致性：人物状态连续性、伏笔状态、时间线逻辑。返回发现的问题列表。",
    {
        "chapter_id": {"type": "integer", "description": "可选，只检查某章相关的问题"},
    },
    category="novel",
)
def check_consistency(*, chapter_id: int | None = None, **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()
    nf = _get_nf_adapter()
    if nf is None:
        return {"ok": False, "error": "未激活 NovelForge 适配器"}

    issues: list[dict] = []

    # 1. 检查伏笔状态
    foreshadows = nf.get_all_foreshadows()
    for f in foreshadows:
        status = f.get("status", "")
        planted = f.get("planted_chapter", 0)
        resolution = f.get("resolution_chapter")
        if status == "resolved" and resolution and planted and resolution < planted:
            issues.append({
                "type": "foreshadow",
                "severity": "high",
                "message": f"伏笔「{f.get('description', '')[:30]}」解决章({resolution})早于埋设章({planted})",
                "entity_id": f.get("id", ""),
            })
        if status == "planted" and planted and chapter_id and planted > chapter_id:
            issues.append({
                "type": "foreshadow",
                "severity": "low",
                "message": f"伏笔「{f.get('description', '')[:30]}」埋设于第{planted}章，晚于检查章{chapter_id}",
                "entity_id": f.get("id", ""),
            })

    # 2. 检查人物状态连续性
    characters = nf.get_all_characters()
    for c in characters:
        last_ch = c.get("last_updated_chapter", 0)
        if chapter_id and last_ch > chapter_id:
            continue  # 跳过未来章节
        state = c.get("current_state", "")
        if not state and c.get("role") in ("主角", "配角"):
            issues.append({
                "type": "character",
                "severity": "medium",
                "message": f"角色「{c.get('name', '')}」缺少当前状态更新",
                "entity_id": c.get("id", ""),
            })

    # 3. 检查大纲完整性
    outlines = nf.get_all_outlines()
    if outlines:
        outline_ids = [o.get("chapter_id", o.get("id", 0)) for o in outlines]
        # 检查是否有缺失的章节号
        if outline_ids:
            max_ch = max(int(x) for x in outline_ids if str(x).isdigit())
            chapter_count = nf._project_data.get("chapter_count", 0)
            if chapter_count > 0 and max_ch < chapter_count:
                issues.append({
                    "type": "outline",
                    "severity": "low",
                    "message": f"大纲只到第{max_ch}章，但章节目录有{chapter_count}章",
                })

    return {"ok": True, "issues_count": len(issues), "issues": issues,
            "chapter_id": chapter_id,
            "duration_ms": int((_t.time() - t0) * 1000)}


@skill("get_outline",
    "获取章节大纲。可指定单章或范围，返回核心事件/状态变化/写作重点等。",
    {
        "chapter_id": {"type": "integer", "description": "章节号"},
        "range_end": {"type": "integer", "description": "可选，范围结束章（如查第5-10章则chapter_id=5,range_end=10）"},
    },
    category="novel",
)
def get_outline(chapter_id: int, *, range_end: int | None = None, **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()
    nf = _get_nf_adapter()
    if nf is None:
        return {"ok": False, "error": "未激活 NovelForge 适配器"}

    outlines = nf.get_all_outlines()
    if not outlines:
        return {"ok": False, "error": "无大纲数据"}

    if range_end:
        selected = [o for o in outlines
                    if range(int(chapter_id), int(range_end) + 1)
                    and o.get("chapter_id", o.get("id", 0)) in
                    range(int(chapter_id), int(range_end) + 1)]
    else:
        selected = [o for o in outlines
                    if o.get("chapter_id", o.get("id", 0)) == chapter_id]

    if not selected:
        return {"ok": False, "error": f"第 {chapter_id} 章大纲不存在"}

    result = []
    for o in selected:
        result.append({
            "chapter_id": o.get("chapter_id", o.get("id", 0)),
            "core_events": o.get("core_events", "")[:200],
            "plot_summary": o.get("plot_summary", "")[:200],
            "state_deltas": o.get("state_deltas", "")[:150],
            "writing_focus": o.get("writing_focus", "")[:100],
            "chapter_type": o.get("chapter_type_label", ""),
            "opening_hook": o.get("opening_hook", "")[:80],
            "ending_hook": o.get("ending_hook", "")[:80],
        })

    return {"ok": True, "outlines": result, "count": len(result),
            "duration_ms": int((_t.time() - t0) * 1000)}

# NovelForge skill 追加后重新生成协议文档
TOOL_PROTOCOL_MD = _gen_protocol_md()

# ============================================================
# 数据分析专用 Skill（data-analyst-tool 工作流程）
# ============================================================
_DA_ADAPTER_REF = None

def set_data_analysis_adapter(adapter) -> None:
    """注入数据分析适配器实例。"""
    global _DA_ADAPTER_REF
    _DA_ADAPTER_REF = adapter

def _get_da_adapter():
    return _DA_ADAPTER_REF


@skill("load_spreadsheet",
    "加载数据文件（Excel/CSV），返回表头、行数、列数、数据类型、样本行。用于了解数据概况后再决定分析方向。",
    {
        "sheet": {"type": "string", "description": "可选，Excel 的 sheet 名。不填=第一个 sheet"},
        "max_rows": {"type": "integer", "description": "最多返回样本行数，默认 5"},
    },
    category="data",
)
def load_spreadsheet(*, sheet: str | None = None, max_rows: int = 5, **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()
    da = _get_da_adapter()
    if da is None:
        return {"ok": False, "error": "未激活数据分析适配器（无数据文件）"}

    file_path = da.get_file_path()
    if not file_path or not os.path.exists(file_path):
        return {"ok": False, "error": f"数据文件不存在: {file_path}"}

    p = Path(file_path)
    suffix = p.suffix.lower()

    if suffix in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
            sn = sheet or wb.sheetnames[0]
            ws = wb[sn]
            rows = list(ws.iter_rows(max_row=max_rows + 1, values_only=True))
            headers = [str(c) if c is not None else "" for c in rows[0]] if rows else []
            sample = [list(r) for r in rows[1:max_rows + 1]] if len(rows) > 1 else []
            wb.close()
            return {"ok": True, "file": str(p), "sheet": sn,
                    "headers": headers, "col_count": len(headers),
                    "sample_rows": sample, "sample_count": len(sample),
                    "row_count_estimate": ws.max_row,
                    "duration_ms": int((_t.time() - t0) * 1000)}
        except ImportError:
            return {"ok": False, "error": "需要 openpyxl 库: pip install openpyxl"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    elif suffix == ".csv":
        try:
            import csv
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                rows = list(reader)
            headers = [str(c) for c in rows[0]] if rows else []
            sample = [list(r) for r in rows[1:max_rows + 1]] if len(rows) > 1 else []
            return {"ok": True, "file": str(p), "sheet": "csv",
                    "headers": headers, "col_count": len(headers),
                    "sample_rows": sample, "sample_count": len(sample),
                    "row_count": len(rows) - 1,
                    "duration_ms": int((_t.time() - t0) * 1000)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {"ok": False, "error": f"不支持的文件类型: {suffix}"}


@skill("run_pandas",
    "用 pandas 执行数据分析脚本。传入 Python 代码（使用变量 df 代表 DataFrame），返回执行结果或统计摘要。",
    {
        "code": {"type": "string", "description": "Python 代码，使用 df 代表 DataFrame。如 df.describe().to_dict()"},
        "sheet": {"type": "string", "description": "可选，Excel 的 sheet 名"},
    },
    category="data",
)
def run_pandas(code: str, *, sheet: str | None = None, **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()
    da = _get_da_adapter()
    if da is None:
        return {"ok": False, "error": "未激活数据分析适配器"}

    file_path = da.get_file_path()
    if not file_path or not os.path.exists(file_path):
        return {"ok": False, "error": f"数据文件不存在: {file_path}"}

    # 安全：禁止危险关键字
    dangerous = re.findall(
        r"\b(import\s+os|import\s+subprocess|import\s+shutil|import\s+sys|"
        r"__import__|exec\s*\(|eval\s*\(|compile\s*\(|"
        r"open\s*\(|exit\s*\(|quit\s*\()" , code
    )
    if dangerous:
        return {"ok": False, "error": f"代码包含禁止的关键字: {dangerous}"}

    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return {"ok": False, "error": "需要 pandas 库: pip install pandas openpyxl"}

    p = Path(file_path)
    suffix = p.suffix.lower()

    # 加载数据
    try:
        if suffix in (".xlsx", ".xls"):
            df = pd.read_excel(str(p), sheet_name=sheet or 0)
        elif suffix == ".csv":
            df = pd.read_csv(str(p))
        else:
            return {"ok": False, "error": f"不支持的文件类型: {suffix}"}
    except Exception as e:
        return {"ok": False, "error": f"数据加载失败: {e}"}

    # 安全沙箱
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    sandbox = {
        "__builtins__": {
            "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
            "enumerate": enumerate, "filter": filter, "float": float,
            "int": int, "len": len, "list": list, "map": map, "max": max,
            "min": min, "print": print, "range": range, "round": round,
            "set": set, "sorted": sorted, "str": str, "sum": sum,
            "tuple": tuple, "type": type, "zip": zip, "isinstance": isinstance,
        },
        "df": df, "pd": pd, "np": np,
        "json": json,
    }

    try:
        with redirect_stdout(buf):
            result = eval(code, sandbox, {})
        stdout_text = buf.getvalue()

        # 结果转 JSON 可序列化
        def _to_jsonable(obj):
            if isinstance(obj, (pd.DataFrame, pd.Series)):
                # DataFrame → dict；Series → dict
                try:
                    return json.loads(obj.to_json(orient="records" if isinstance(obj, pd.DataFrame) else "index",
                                                 force_ascii=False, default_handler=str))
                except Exception:
                    return str(obj)[:2000]
            elif isinstance(obj, dict):
                return {str(k): _to_jsonable(v) for k, v in list(obj.items())[:50]}
            elif isinstance(obj, (list, tuple)):
                return [_to_jsonable(x) for x in list(obj)[:50]]
            elif isinstance(obj, (int, float, str, bool, type(None))):
                return obj
            else:
                return str(obj)[:200]

        result_jsonable = _to_jsonable(result)
        result_str = json.dumps(result_jsonable, ensure_ascii=False, default=str)
        cap = 8000
        if len(result_str) > cap:
            result_str = result_str[:cap] + f"\n... [结果截断，原始 {len(result_str)} 字符]"

        return {"ok": True, "result": result_str,
                "stdout": stdout_text[:3000],
                "df_shape": list(df.shape),
                "duration_ms": int((_t.time() - t0) * 1000)}
    except SyntaxError:
        # 多行代码用 exec
        try:
            with redirect_stdout(buf):
                exec(code, sandbox, {})
            stdout_text = buf.getvalue()
            return {"ok": True, "result": "",
                    "stdout": stdout_text[:3000],
                    "df_shape": list(df.shape),
                    "duration_ms": int((_t.time() - t0) * 1000)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "stdout": buf.getvalue()[:1000],
                    "duration_ms": int((_t.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "stdout": buf.getvalue()[:1000],
                "duration_ms": int((_t.time() - t0) * 1000)}


@skill("data_summary",
    "生成数据统计摘要：数值列的 count/mean/std/min/max/中位数，分类列的唯一值/Top频次。用于快速了解数据分布。",
    {
        "sheet": {"type": "string", "description": "可选，Excel sheet 名"},
    },
    category="data",
)
def data_summary(*, sheet: str | None = None, **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()
    da = _get_da_adapter()
    if da is None:
        return {"ok": False, "error": "未激活数据分析适配器"}

    file_path = da.get_file_path()
    if not file_path:
        return {"ok": False, "error": "数据文件不存在"}

    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return {"ok": False, "error": "需要 pandas 库"}

    p = Path(file_path)
    suffix = p.suffix.lower()

    try:
        if suffix in (".xlsx", ".xls"):
            df = pd.read_excel(str(p), sheet_name=sheet or 0)
        elif suffix == ".csv":
            df = pd.read_csv(str(p))
        else:
            return {"ok": False, "error": f"不支持的文件类型: {suffix}"}
    except Exception as e:
        return {"ok": False, "error": f"数据加载失败: {e}"}

    result: dict[str, Any] = {
        "shape": list(df.shape),
        "columns": list(df.columns),
    }

    # 数值列统计
    numeric = df.select_dtypes(include=["number"])
    if not numeric.empty:
        desc = numeric.describe()
        result["numeric_stats"] = {}
        for col in numeric.columns:
            s = df[col]
            result["numeric_stats"][str(col)] = {
                "count": int(s.count()),
                "mean": round(float(s.mean()), 2) if s.mean() == s.mean() else None,
                "std": round(float(s.std()), 2) if s.std() == s.std() else None,
                "min": float(s.min()) if s.min() == s.min() else None,
                "max": float(s.max()) if s.max() == s.max() else None,
                "median": round(float(s.median()), 2) if s.median() == s.median() else None,
                "null_count": int(s.isnull().sum()),
            }

    # 分类列统计
    categorical = df.select_dtypes(exclude=["number"])
    if not categorical.empty:
        result["categorical_stats"] = {}
        for col in categorical.columns:
            s = df[col]
            vc = s.value_counts()
            result["categorical_stats"][str(col)] = {
                "unique_count": int(s.nunique()),
                "top_values": [{"value": str(v), "count": int(c)} for v, c in vc.head(5).items()],
                "null_count": int(s.isnull().sum()),
            }

    return {"ok": True, **result,
            "duration_ms": int((_t.time() - t0) * 1000)}


@skill("generate_report",
    "生成 HTML 分析报告。根据分析结论生成包含 KPI 卡片、图表说明和建议的 HTML 报告。",
    {
        "title": {"type": "string", "description": "报告标题"},
        "findings": {"type": "string", "description": "分析发现（JSON 数组，每项含 metric/value/insight）"},
        "conclusion": {"type": "string", "description": "核心结论与建议"},
    },
    category="data",
)
def generate_report(title: str = "数据分析报告", findings: str = "[]",
                    conclusion: str = "", **_kw) -> dict[str, Any]:
    import time as _t
    t0 = _t.time()

    # 解析 findings
    try:
        findings_data = json.loads(findings) if isinstance(findings, str) else findings
        if not isinstance(findings_data, list):
            findings_data = [findings_data] if findings_data else []
    except json.JSONDecodeError:
        findings_data = [{"metric": "数据", "value": findings[:200], "insight": ""}]

    # 生成简单 HTML 报告
    kpi_cards = ""
    for f in findings_data[:12]:
        if isinstance(f, dict):
            metric = f.get("metric", "")
            value = f.get("value", "")
            insight = f.get("insight", "")
            kpi_cards += f"""
            <div class="kpi-card">
                <div class="kpi-value">{value}</div>
                <div class="kpi-label">{metric}</div>
                {"<div class='kpi-insight'>" + insight + "</div>" if insight else ""}
            </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, 'PingFang SC', sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
  .container {{ max-width: 900px; margin: 0 auto; background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  h1 {{ color: #333; border-bottom: 2px solid #4a90d9; padding-bottom: 10px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
  .kpi-card {{ background: #f8f9fa; border-radius: 8px; padding: 15px; border-left: 4px solid #4a90d9; }}
  .kpi-value {{ font-size: 28px; font-weight: 700; color: #2c3e50; }}
  .kpi-label {{ color: #7f8c8d; font-size: 13px; margin-top: 4px; }}
  .kpi-insight {{ color: #e67e22; font-size: 12px; margin-top: 6px; }}
  .conclusion {{ background: #eaf6ff; border-radius: 8px; padding: 15px; margin: 20px 0; border-left: 4px solid #3498db; }}
  .conclusion h3 {{ margin-top: 0; color: #2980b9; }}
</style>
</head>
<body>
<div class="container">
  <h1>{title}</h1>
  <div class="kpi-grid">{kpi_cards}</div>
  <div class="conclusion">
    <h3>核心结论</h3>
    <p>{conclusion or '暂无结论'}</p>
  </div>
  <p style="text-align:center;color:#999;font-size:12px;">AIChat 数据分析 · 生成于 {_t.strftime('%Y-%m-%d %H:%M', _t.localtime())}</p>
</div>
</body>
</html>"""

    # 保存到文件
    da = _get_da_adapter()
    save_dir = Path.home() / "AIChat_Data" / "reports"
    save_dir.mkdir(parents=True, exist_ok=True)
    report_path = save_dir / f"report_{_t.strftime('%Y%m%d_%H%M%S', _t.localtime())}.html"
    try:
        report_path.write_text(html, encoding="utf-8")
    except OSError:
        pass

    return {"ok": True, "report_path": str(report_path),
            "kpi_count": len(findings_data),
            "html_length": len(html),
            "duration_ms": int((_t.time() - t0) * 1000)}


# 数据分析 skill 追加后重新生成协议文档
TOOL_PROTOCOL_MD = _gen_protocol_md()
