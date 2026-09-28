"""Streamable HTTP 端到端测试：/health、无状态发现与调用。

这里会真的监听一个 127.0.0.1 端口（uvicorn 跑在后台线程），因此
"无 initialize、无 Mcp-Session-Id、每个请求独立" 是在真实 HTTP 线路上被验证的：
一个 ASGI 中间件把**所有**请求/响应头与请求体记录下来，测试直接检查原始字节。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from pet_hospital_mcp import PROTOCOL_VERSION
from pet_hospital_mcp.server import SERVER_NAME, MCPService

from .conftest import FakeBackend

MCP_PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"

# 测试内一律直连（trust_env=False）：CI / 沙箱里可能有 HTTP_PROXY，
# 走代理会让请求以 absolute-form 发出，ASGI 路由就匹配不上 /mcp。
# 这是 SDK 文档支持的用法（自带 httpx2.AsyncClient）。
_NO_PROXY = {"trust_env": False}


class RecorderMiddleware:
    """纯 ASGI 记录器：把请求/响应头与请求体原样留档。"""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.requests: list[dict[str, Any]] = []

    @staticmethod
    def _headers(raw: list[tuple[bytes, bytes]]) -> dict[str, str]:
        return {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in raw}

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        record: dict[str, Any] = {
            "method": scope["method"],
            "path": scope["path"],
            "headers": self._headers(scope.get("headers", [])),
            "body": b"",
            "status": None,
            "response_headers": {},
        }
        self.requests.append(record)

        async def recording_receive() -> dict[str, Any]:
            message = await receive()
            if message.get("type") == "http.request":
                record["body"] += message.get("body", b"")
            return message

        async def recording_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                record["status"] = message["status"]
                record["response_headers"] = self._headers(message.get("headers", []))
            await send(message)

        await self.app(scope, recording_receive, recording_send)

    # -- 断言辅助 ---------------------------------------------------------

    @property
    def mcp_exchanges(self) -> list[dict[str, Any]]:
        return [item for item in self.requests if item["path"] == "/mcp"]

    @property
    def all_bodies(self) -> bytes:
        return b"".join(item["body"] for item in self.mcp_exchanges)


@asynccontextmanager
async def mcp_client(base_url: str) -> AsyncIterator[Client]:
    """用 SDK 2.x 的 Client 通过真实 HTTP 连接本服务（默认 ``mode="auto"``）。"""
    async with httpx2.AsyncClient(**_NO_PROXY) as http_client:  # type: ignore[arg-type]
        transport = streamable_http_client(f"{base_url}/mcp", http_client=http_client)
        async with Client(transport) as client:
            yield client


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_listening(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"uvicorn 未能在 {timeout}s 内监听 127.0.0.1:{port}")


@pytest.fixture
def live_server(service: MCPService) -> Iterator[tuple[str, RecorderMiddleware]]:
    """在后台线程启动真实 uvicorn，返回 (base_url, 记录器)。"""
    recorder = RecorderMiddleware(service.app())
    port = _free_port()
    config = uvicorn.Config(recorder, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        _wait_until_listening(port)
        yield f"http://127.0.0.1:{port}", recorder
    finally:
        server.should_exit = True
        thread.join(timeout=15)


# --------------------------------------------------------------------------- /health


async def test_health_endpoint(live_server: tuple[str, RecorderMiddleware]) -> None:
    base_url, recorder = live_server

    async with httpx.AsyncClient(**_NO_PROXY) as http:  # type: ignore[arg-type]
        response = await http.get(f"{base_url}/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == SERVER_NAME
    assert body["protocolVersion"] == PROTOCOL_VERSION
    assert body["mcpEndpoint"] == "/mcp"
    assert body["tools"] == ["list_pets"]

    health_calls = [item for item in recorder.requests if item["path"] == "/health"]
    assert len(health_calls) == 1


# --------------------------------------------------------------------------- MCP over HTTP


async def test_tools_are_discoverable_and_callable_over_http(
    live_server: tuple[str, RecorderMiddleware],
    backend: FakeBackend,
) -> None:
    base_url, _ = live_server

    async with mcp_client(base_url) as client:
        assert client.protocol_version == PROTOCOL_VERSION
        assert client.server_info is not None
        assert client.server_info.name == SERVER_NAME

        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ["list_pets"]

        result = await client.call_tool("list_pets", {"species": "猫", "page": 1, "pageSize": 5})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["items"][0]["id"] == "PET-000201"

    assert backend.call_count == 1
    assert dict(backend.last_request.url.params) == {"species": "猫", "page": "1", "pageSize": "5"}


async def test_connection_is_stateless_no_initialize_and_no_session_id(
    live_server: tuple[str, RecorderMiddleware],
) -> None:
    """2026-07-28 的实际发现方式：``server/discover``，且不出现任何会话标识。"""
    base_url, recorder = live_server

    async with mcp_client(base_url) as client:
        await client.list_tools()
        await client.call_tool("list_pets", {"species": "犬"})
        await client.call_tool("list_pets", {"status": "住院中"})

    exchanges = recorder.mcp_exchanges
    assert exchanges, "应至少发生一次到 /mcp 的请求"

    bodies = recorder.all_bodies
    assert b"server/discover" in bodies, "2026-07-28 客户端应先发 server/discover 探测"
    assert b'"initialize"' not in bodies, "无状态模型下不得发送旧 protocol 的 initialize"
    assert b"notifications/initialized" not in bodies
    assert b"Mcp-Session-Id" not in bodies

    for exchange in exchanges:
        assert "mcp-session-id" not in exchange["headers"], "客户端不应携带 Mcp-Session-Id"
        assert "mcp-session-id" not in exchange["response_headers"], "服务端不应返回 Mcp-Session-Id"
        assert exchange["status"] == 200

    # 每个请求都被独立处理：三次调用全部成功，其中两次是 tools/call
    assert len([item for item in exchanges if b"tools/call" in item["body"]]) == 2


async def test_each_request_is_self_contained_and_carries_protocol_meta(
    live_server: tuple[str, RecorderMiddleware],
) -> None:
    base_url, recorder = live_server

    async with mcp_client(base_url) as client:
        await client.list_tools()

    assert recorder.mcp_exchanges
    for exchange in recorder.mcp_exchanges:
        payload = json.loads(exchange["body"] or b"{}")
        meta = payload.get("params", {}).get("_meta", {})
        assert meta.get(MCP_PROTOCOL_VERSION_META_KEY) == PROTOCOL_VERSION


async def test_two_independent_connections_share_no_server_side_state(
    live_server: tuple[str, RecorderMiddleware],
) -> None:
    """两次完全独立的连接都能各自完成发现与调用（服务端不保存任何会话）。"""
    base_url, _ = live_server

    async with mcp_client(base_url) as first:
        assert first.protocol_version == PROTOCOL_VERSION
        first_tools = await first.list_tools()

    async with mcp_client(base_url) as second:
        assert second.protocol_version == PROTOCOL_VERSION
        second_result = await second.call_tool("list_pets", {"q": "猫"})

    assert [tool.name for tool in first_tools.tools] == ["list_pets"]
    assert second_result.is_error is False


async def test_raw_jsonrpc_without_session_header_is_accepted(
    live_server: tuple[str, RecorderMiddleware],
    backend: FakeBackend,
) -> None:
    """裸 HTTP 校验 2026-07-28 的无状态调用契约。

    每个请求自包含：``Mcp-Protocol-Version`` 头 + ``params._meta`` 里的协议版本与
    客户端能力，**没有任何** ``Mcp-Session-Id``；服务端照样按普通请求处理。
    """
    base_url, _ = live_server
    meta = {
        MCP_PROTOCOL_VERSION_META_KEY: PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "raw-probe", "version": "1.0"},
    }

    async with httpx.AsyncClient(**_NO_PROXY) as http:  # type: ignore[arg-type]
        discover = await _raw_post(http, base_url, "server/discover", {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": meta}})
        listed = await _raw_post(http, base_url, "tools/list", {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": meta}})
        called = await _raw_post(
            http,
            base_url,
            "tools/call",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_pets", "arguments": {"species": "猫"}, "_meta": meta},
            },
            tool_name="list_pets",
        )

    # 发现：声明支持的协议版本与能力，但绝不返回会话标识
    for response in (discover, listed, called):
        assert response.status_code == 200
        assert "mcp-session-id" not in {key.lower() for key in response.headers}

    discover_result = _decode_jsonrpc(discover)["result"]
    assert PROTOCOL_VERSION in discover_result["supportedVersions"]
    assert discover_result["capabilities"]["tools"]

    assert [tool["name"] for tool in _decode_jsonrpc(listed)["result"]["tools"]] == ["list_pets"]

    call_result = _decode_jsonrpc(called)["result"]
    assert call_result["isError"] is False
    assert call_result["structuredContent"]["items"][0]["id"] == "PET-000201"
    assert backend.call_count == 1


async def _raw_post(
    http: httpx.AsyncClient,
    base_url: str,
    method: str,
    body: dict[str, Any],
    *,
    tool_name: str | None = None,
) -> httpx.Response:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Mcp-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if tool_name is not None:
        headers["Mcp-Name"] = tool_name
    return await http.post(f"{base_url}/mcp", json=body, headers=headers)


async def test_concurrent_calls_are_handled_without_sessions(
    live_server: tuple[str, RecorderMiddleware],
    backend: FakeBackend,
) -> None:
    """并发调用彼此独立（无状态服务可以横向扩展）。"""
    base_url, _ = live_server

    async def call_once(arguments: dict[str, Any]) -> Any:
        async with mcp_client(base_url) as client:
            return await client.call_tool("list_pets", arguments)

    results = await asyncio.gather(
        call_once({"species": "犬"}),
        call_once({"species": "猫"}),
        call_once({"status": "已康复"}),
    )

    assert all(item.is_error is False for item in results)
    assert backend.call_count == 3


def _decode_jsonrpc(response: httpx.Response) -> dict[str, Any]:
    """兼容 application/json 与 text/event-stream 两种响应体。"""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise AssertionError(f"SSE 响应中没有 data 行：{response.text[:200]}")
    return response.json()
