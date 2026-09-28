"""MCP 工具集合。

每个工具一个模块，统一暴露 ``register(mcp, client)`` 注册函数。
新增工具时只需要在本包内加文件，并复用
:class:`~pet_hospital_mcp.rest_client.RestClient`、
:mod:`~pet_hospital_mcp.errors` 与 :mod:`~pet_hospital_mcp.logging_config`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import list_pets

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型检查
    from mcp.server import MCPServer

    from ..rest_client import RestClient

__all__ = ["list_pets", "register_all"]


def register_all(mcp: "MCPServer", client: "RestClient") -> None:
    """把所有工具注册到 ``mcp`` 上。"""
    list_pets.register(mcp, client)
