"""日志工具。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOG = logging.getLogger("novelforge")


def setup_logger(name: str = "novelforge", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def add_file_handler(project_root: str | Path, level: int = logging.ERROR) -> None:
    """把 ERROR 级以上日志追加写到 ``{project_root}/.novelforge/pipeline_errors.log``。

    用途：让软件运行时的报错自动留痕，便于事后排查（用户要求「把报错的日志记录下来」）。
    幂等：按日志文件路径去重，重复调用不会叠加多个 handler。
    不影响 stdout 输出（桌面端仍正常可见）。
    """
    log_dir = Path(project_root) / ".novelforge"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "pipeline_errors.log"
    for h in _LOG.handlers:
        if getattr(h, "_nf_err_log", None) == str(log_path):
            return
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh._nf_err_log = str(log_path)
    fh.setLevel(level)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _LOG.addHandler(fh)


logger = setup_logger()
