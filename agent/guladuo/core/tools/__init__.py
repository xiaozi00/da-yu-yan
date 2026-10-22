"""工具层统一出口：把系统工具与文件工具组装进注册表。"""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolResult
from .files import FileTools
from .registry import ToolRegistry
from .sandbox import WorkspaceSandbox
from .system import build_system_tools

__all__ = [
    "FileTools",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "WorkspaceSandbox",
    "build_system_tools",
    "build_default_registry",
]


def build_default_registry(workspace: str | Path, max_read_chars: int = 20000) -> ToolRegistry:
    sandbox = WorkspaceSandbox(workspace)
    registry = ToolRegistry(build_system_tools(sandbox.root))
    registry.extend(FileTools(sandbox, max_read_chars=max_read_chars).tools())
    return registry
