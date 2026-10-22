#!/usr/bin/env python
"""guladuo 统一启动入口。

用法：
    python run.py                    # 启动 TUI
    python run.py --workspace ..     # 指定工作区
    python run.py --config other.ini # 指定配置

新增 web / desktop / mobile 前端后，可以在这里按参数分发，
core 与各前端之间不需要任何改动。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    from tui.app import main as tui_main

    tui_main()


if __name__ == "__main__":
    main()
