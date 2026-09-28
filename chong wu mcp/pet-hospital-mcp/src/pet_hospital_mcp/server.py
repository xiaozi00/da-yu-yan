"""MCP 服务装配：``MCPServer`` + 工具注册 + ``/health`` 自定义路由。

协议事实（SDK 2.x / MCP 2026-07-28）
-----------------------------------
* 使用官方 SDK ``mcp==2.0.0`` 的高层服务类 ``mcp.server.MCPServer``
  （**不是** v1 的 ``mcp.server.fastmcp.FastMCP``）。
* 传输方式为 **无状态 Streamable HTTP**：``stateless_http=True``，
  端点默认 ``/mcp``。
* 2026-07-28 修订版没有 ``initialize`` 握手、没有 ``Mcp-Session-Id``、
  没有会话存储/过期，也没有可恢复的有状态 SSE——本模块**不实现**它们。
* 发现流程是普通请求 ``server/discover``，由 SDK 负责应答。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp.server import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import PROTOCOL_VERSION, __version__
from .config import Settings
from .logging_config import get_logger, setup_logging
from .rest_client import RestClient
from .tools import register_all

__all__ = [
    "SERVER_NAME",
    "SERVICE_DESCRIPTION",
    "SERVICE_INSTRUCTIONS",
    "MCPService",
    "create_service",
]

SERVER_NAME = "pet-hospital-mcp"

SERVICE_DESCRIPTION = "宠物医院 MCP 适配器（对接本地 Go Pet Hospital REST API）"

SERVICE_INSTRUCTIONS = (
    "本服务把本地 Go 宠物医院 REST API 暴露为 MCP 工具。\n"
    "上游地址由 PET_HOSPITAL_BASE_URL 配置，默认 http://127.0.0.1:8080，必须先启动它。\n"
    "当前仅提供一个只读工具 list_pets，用于查询宠物档案列表"
    "（关键词检索、多字段过滤、排序、分页）。\n"
    "所有取值必须使用上游真实允许值，非法入参统一返回 "
    '{"error": {"code": ..., "message": ..., "details": {...}}} 结构。'
)

_logger = get_logger("pet_hospital_mcp.server")


@dataclass
class MCPService:
    """把配置、REST 客户端与 MCP 服务打包在一起，便于测试与复用。"""

    settings: Settings
    client: RestClient
    server: MCPServer
    _app: Starlette | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------ 装配

    def app(self) -> Starlette:
        """返回 ASGI 应用（Streamable HTTP + ``/health``），只构建一次。"""
        if self._app is None:
            self._app = self.server.streamable_http_app(
                streamable_http_path=self.settings.mcp_path,
                stateless_http=True,
            )
        return self._app

    # ------------------------------------------------------------------ 运行

    def run(self) -> None:
        """以无状态 Streamable HTTP 方式启动（阻塞）。"""
        _logger.info(
            "service_starting",
            extra={
                "fields": {
                    "host": self.settings.mcp_host,
                    "port": self.settings.mcp_port,
                    "mcp_endpoint": self.settings.mcp_endpoint,
                    "health_endpoint": "/health",
                    "protocol_version": PROTOCOL_VERSION,
                    "transport": "streamable-http",
                    "stateless": True,
                    "upstream": self.settings.pet_hospital_base_url,
                }
            },
        )
        print(
            f"\n  {SERVER_NAME} {__version__}\n"
            f"  MCP 端点     : http://{self.settings.mcp_host}:{self.settings.mcp_port}"
            f"{self.settings.mcp_endpoint}\n"
            f"  健康检查     : http://{self.settings.mcp_host}:{self.settings.mcp_port}/health\n"
            f"  协议版本     : {PROTOCOL_VERSION}（无状态 Streamable HTTP，无 Mcp-Session-Id）\n"
            f"  上游 Go 服务 : {self.settings.pet_hospital_base_url}\n",
            flush=True,
        )
        self.server.run(
            transport="streamable-http",
            host=self.settings.mcp_host,
            port=self.settings.mcp_port,
            streamable_http_path=self.settings.mcp_path,
            stateless_http=True,
        )


def create_service(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MCPService:
    """构造服务实例。

    :param settings: 运行期配置；``None`` 表示从环境变量读取。
    :param transport: **仅用于测试**，注入 ``httpx.MockTransport`` 以隔离真实上游。
    """
    resolved = settings or Settings.from_env()

    # 必须在 MCPServer(...) 之前安装 JSON 日志处理器：
    # MCPServer 构造时会调用 logging.basicConfig()，若 root 已有 handler 则是空操作。
    setup_logging(resolved.log_level, force=True)

    client = RestClient(
        base_url=resolved.pet_hospital_base_url,
        timeout=resolved.request_timeout,
        max_retries=resolved.max_retries,
        trust_env=resolved.trust_env,
        transport=transport,
    )

    server = MCPServer(
        name=SERVER_NAME,
        title="宠物医院 MCP 服务",
        description=SERVICE_DESCRIPTION,
        instructions=SERVICE_INSTRUCTIONS,
        version=__version__,
        log_level=resolved.log_level,  # type: ignore[arg-type]
    )

    register_all(server, client)
    _register_health_route(server, resolved)

    return MCPService(settings=resolved, client=client, server=server)


def _register_health_route(server: MCPServer, settings: Settings) -> None:
    """``GET /health``：与 MCP 无关的普通健康检查端点。"""

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> Response:
        payload: dict[str, Any] = {
            "status": "ok",
            "service": SERVER_NAME,
            "version": __version__,
            "protocolVersion": PROTOCOL_VERSION,
            "transport": "streamable-http",
            "stateless": True,
            "mcpEndpoint": settings.mcp_endpoint,
            "upstream": settings.pet_hospital_base_url,
            "tools": ["list_pets"],
        }
        return JSONResponse(payload)
