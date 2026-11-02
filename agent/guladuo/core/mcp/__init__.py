"""MCP 客户端：读取 .mcp.json，连接 Streamable HTTP Server，把工具接进 xAgent。

对外只需要两个东西：
- `create_mcp_manager(项目根目录)` —— 启动连接并发现工具；
- `MCPManager.tools` —— 可以直接注册进 ToolRegistry 的普通 Tool 列表。
"""

from __future__ import annotations

from .config import MCPConfig, MCPServerConfig, UnusableServer, load_mcp_config
from .connection import MCPConnection
from .manager import MCPManager, MCPToolBinding, create_mcp_manager, format_result

__all__ = [
    "MCPConfig",
    "MCPServerConfig",
    "MCPConnection",
    "MCPManager",
    "MCPToolBinding",
    "UnusableServer",
    "create_mcp_manager",
    "format_result",
    "load_mcp_config",
]
