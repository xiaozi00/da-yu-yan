"""工作区沙箱：所有文件工具的路径都必须落在这个根目录内。"""

from __future__ import annotations

from pathlib import Path

from ..errors import PathOutsideWorkspace


class WorkspaceSandbox:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, raw: str | Path | None) -> Path:
        """解析路径；越界时抛 PathOutsideWorkspace。"""
        candidate = Path(raw) if raw not in (None, "") else Path(".")
        if candidate.is_absolute():
            full = candidate.resolve()
        else:
            full = (self.root / candidate).resolve()

        if full != self.root and self.root not in full.parents:
            raise PathOutsideWorkspace("路径超出工作区：%s" % full)
        return full

    def ensure_not_root(self, full: Path) -> None:
        if full == self.root:
            raise PathOutsideWorkspace("不能对工作区根目录本身执行该操作")
