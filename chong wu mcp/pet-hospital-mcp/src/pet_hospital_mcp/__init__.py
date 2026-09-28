"""宠物医院 MCP 服务。

把 Go 宠物医院 REST API（``GET /api/v1/pets``）以 **无状态 Streamable HTTP** 的
方式暴露给 AI Agent。本包不包含任何业务逻辑，只做参数校验、协议适配与错误归一化。

关键约束：

* 官方 Python SDK ``mcp==2.0.0``（v2），协议版本 ``2026-07-28``
* 使用 :class:`mcp.server.MCPServer`，**不使用** ``mcp.server.fastmcp.FastMCP``
* 无状态：没有 ``initialize`` 握手、没有 ``Mcp-Session-Id``、没有会话存储
"""

from __future__ import annotations

__all__ = ["__version__", "PROTOCOL_VERSION"]

__version__ = "1.0.0"

#: 本服务对外声明的 MCP 协议版本（不向前兼容 1.x / 2025 版本的无状态模型）。
PROTOCOL_VERSION = "2026-07-28"
