"""单个 MCP Server 的连接封装。

MCP SDK 是全异步的，而 xAgent 的工具 handler 是同步函数。这里的做法是：
每个 server 独占一条后台线程 + 一个事件循环，连接建立后一直挂着，
同步调用通过 `run_coroutine_threadsafe` 投递到那条循环里执行并等待结果。

这样对上层（ToolRegistry / Agent）完全是同步语义，不污染主流程。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import socket
import threading
from contextlib import AsyncExitStack, suppress
from typing import Any
from urllib.parse import urlparse

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool as MCPTool

from ..errors import MCPConnectionLost, MCPError
from .config import MCPServerConfig

# 等待后台线程「连接就绪」时，比 server 自己的超时多留一点余量
_READY_GRACE = 5.0

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def is_loopback_url(url: str) -> bool:
    """判断是不是本机地址（本机地址必须绕开系统代理）。"""
    host = (urlparse(url).hostname or "").strip().lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


class MCPConnection:
    """一个 server 的长连接，同步接口。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._stop_event: asyncio.Event | None = None
        self._token: object | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """建立连接并握手，阻塞直到就绪或超时。"""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="mcp-%s" % self.config.name,
            daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(self.config.connect_timeout + _READY_GRACE):
            self.close()
            raise MCPConnectionLost(
                "连接超时（>%.0f 秒），server 可能没启动或还在预热"
                % (self.config.connect_timeout + _READY_GRACE)
            )
        if self._error is not None:
            raise MCPConnectionLost(_with_port_hint(self.config.url, self._error))

    def close(self) -> None:
        if self._loop is not None and self._stop_event is not None:
            with suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._session = None

    def restart(self) -> None:
        """断开后重新握手（server 重启过、或连接被中间层掐断时用）。"""
        self.close()
        self._ready = threading.Event()
        self._error = None
        self._stop_event = None
        self.start()

    @property
    def alive(self) -> bool:
        return self._session is not None and self._error is None

    # ---------- 后台线程 ----------

    def _run_loop(self) -> None:
        loop = self._loop
        assert loop is not None
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except BaseException as exc:  # 后台线程不能让异常冒出去
            if self._error is None:
                self._error = exc
        finally:
            self._ready.set()
            with suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            with suppress(Exception):
                loop.close()

    async def _serve(self) -> None:
        """建立连接后一直挂着，直到 close() 触发 stop event。"""
        self._stop_event = asyncio.Event()
        token = object()
        self._token = token
        stack = AsyncExitStack()
        try:
            timeout = httpx2.Timeout(self.config.connect_timeout, read=self.config.timeout)
            http_client = _make_http_client(self.config, timeout)
            await stack.enter_async_context(http_client)

            streams = await stack.enter_async_context(
                streamable_http_client(self.config.url, http_client=http_client)
            )
            read_stream, write_stream = streams[0], streams[1]

            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream, read_timeout_seconds=self.config.timeout)
            )
            await session.initialize()

            self._session = session
            self._error = None
            self._ready.set()

            await self._stop_event.wait()
        except BaseException as exc:
            if self._error is None:
                self._error = exc
            self._ready.set()
        finally:
            if self._token is token:  # 只由「当前这条」连接清空会话，避免重连后被旧线程抹掉
                self._session = None
            with suppress(Exception):
                await stack.aclose()

    # ---------- 同步调用 ----------

    def list_tools(self) -> list[MCPTool]:
        session = self._require_session()
        result = self._submit(session.list_tools(), self.config.timeout)
        return list(getattr(result, "tools", []) or [])

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        session = self._require_session()
        return self._submit(
            session.call_tool(tool_name, arguments, read_timeout_seconds=self.config.timeout),
            self.config.timeout + 5.0,
        )

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise MCPConnectionLost(self._describe_unavailable())
        return self._session

    def _submit(self, coro: Any, timeout: float) -> Any:
        loop = self._loop
        if loop is None:
            raise MCPConnectionLost("连接未启动")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise MCPConnectionLost("请求超时（>%.0f 秒），连接可能已失效" % timeout) from None
        except Exception as exc:
            raise _wrap(exc) from exc

    def _describe_unavailable(self) -> str:
        if self._error is not None:
            return "连接已断开：%s" % _describe(self._error)
        return "连接未建立"


def _make_http_client(config: MCPServerConfig, timeout: httpx2.Timeout) -> httpx2.AsyncClient:
    """建 HTTP 客户端。

    本机 MCP Server（127.0.0.1 / localhost）必须 trust_env=False：
    否则 HTTP_PROXY / HTTPS_PROXY 会接管请求（Clash 一类代理常常把 127.0.0.1
    也代理掉），请求根本到不了 server，只会拿到代理返回的 502 Bad Gateway。
    """
    kwargs: dict[str, Any] = {"timeout": timeout}
    if config.headers:
        kwargs["headers"] = config.headers
    if is_loopback_url(config.url):
        kwargs["trust_env"] = False
    return httpx2.AsyncClient(**kwargs)


_TRANSPORT_ERROR_NAMES = {
    "ClosedResourceError",
    "BrokenResourceError",
    "BrokenPipeError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "ReadError",
    "WriteError",
    "RemoteProtocolError",
    "EndOfStream",
    "CancelledError",
    "TimeoutError",
}


def _wrap(exc: BaseException) -> MCPError:
    """区分「连接断了」和「server 说这个操作不行」，前者可以自动重连重试。"""
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, (httpx2.TransportError, asyncio.TimeoutError, asyncio.CancelledError, ConnectionError)):
        return MCPConnectionLost(_describe(exc))
    if type(exc).__name__ in _TRANSPORT_ERROR_NAMES:
        return MCPConnectionLost(_describe(exc))
    return MCPError(_describe(exc))


def _with_port_hint(url: str, exc: BaseException) -> str:
    """连接类失败时顺手探一下端口，把「没启动」和「路径不对」分开。"""
    detail = _describe(exc)
    name = type(exc).__name__
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        name = type(exc.exceptions[0]).__name__
    if name in {"CancelledError", "ConnectError", "ConnectTimeout", "ConnectionError", "OSError"} or (
        "cancel scope" in str(exc).lower()
    ):
        return "%s；%s" % (detail, _probe_port(url))
    return detail


def _probe_port(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return "端口 %d 可连通，说明服务是活的，检查该路径是否为 MCP 端点（Streamable HTTP）" % port
    except OSError:
        return "端口 %d 连不上，确认 MCP Server 已启动、地址和端口没写错" % port


def _describe(exc: BaseException) -> str:
    """把 SDK 的异常压成一句用户能看懂的中文。"""
    if isinstance(exc, BaseExceptionGroup):  # anyio task group 会包一层
        inner = exc.exceptions
        if inner:
            return _describe(inner[0])

    # anyio 在连接失败时会抛「Cancelled via cancel scope 0x...」，对用户毫无意义
    if isinstance(exc, asyncio.CancelledError) or "cancel scope" in str(exc).lower():
        return "与 server 的握手被中断（连接没建立起来）"

    # MCP 协议错误自带错误码（mcp.shared.exceptions.MCPError）
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if code is not None and message:
        text = "MCP 错误码 %s：%s" % (code, message)
        if code == -32603:
            text += "（常见原因：服务未启动、地址/端口写错，或该 URL 不是 MCP 端点）"
        elif code == -32601:
            text += "（服务器不支持该操作）"
        return text

    if isinstance(exc, httpx2.ConnectError):
        return "无法连接：%s（检查服务是否启动、地址与端口是否正确）" % exc
    if isinstance(exc, httpx2.TimeoutException):
        return "请求超时：%s" % exc

    name = type(exc).__name__
    text = str(exc).strip()
    return "%s: %s" % (name, text) if text else name
