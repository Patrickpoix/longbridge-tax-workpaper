"""统一命令行文本流编码，确保中文结果可被管道读取。"""
from __future__ import annotations

import sys


def configure_text_io() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure):
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except (OSError, ValueError):
                pass
