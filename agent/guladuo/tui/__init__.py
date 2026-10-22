"""Textual 前端包。

对外只暴露 run()，入口细节（App 类、控件、样式）都留在包内部，
未来新增 web / desktop / mobile 前端时互不干扰。
"""

from .app import GuladoApp, run

__all__ = ["GuladoApp", "run"]
