"""把多个 MCP Server 的工具接进 xAgent 的工具集合。

职责：
1. 启动时连接 `.mcp.json` 里的每个 server，`list_tools()` 拿工具清单；
2. 给每个工具起一个不会冲突的暴露名 `mcp__<server>__<tool>`，并记住映射；
3. 生成普通的 `Tool` 对象，交给 ToolRegistry —— 对 Agent 来说它和本地工具没区别；
4. LLM 调用时按暴露名找回 server 和原始工具名，转发并整理返回内容。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..errors import MCPConnectionLost, MCPError
from ..tools.base import Tool, ToolResult
from .config import MCPConfig, load_mcp_config
from .connection import MCPConnection, _describe

# 单次工具返回给模型的最大字符数，超出截断，避免一条结果吃光上下文
MAX_RESULT_CHARS = 8000

_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")

__all__ = [
    "MCPToolBinding",
    "MCPManager",
    "ServerStatus",
    "create_mcp_manager",
]


@dataclass(frozen=True)
class ServerStatus:
    """一个 server 的当前状态，供 TUI / 诊断脚本展示。"""

    name: str
    url: str
    connected: bool
    tool_count: int
    error: str = ""


@dataclass(frozen=True)
class MCPToolBinding:
    """一个 MCP 工具在 xAgent 里的登记信息。"""

    exposed_name: str      # 给 LLM 看的名字：mcp__<server>__<tool>
    server: str            # 所属 server（.mcp.json 里的键）
    remote_name: str       # server 上的原始工具名
    description: str
    schema: dict[str, Any]
    connection: MCPConnection


class MCPManager:
    def __init__(self, config: MCPConfig) -> None:
        self.config = config
        self._connections: dict[str, MCPConnection] = {}
        self._bindings: dict[str, MCPToolBinding] = {}
        self._tools: list[Tool] = []
        self._failed: list[tuple[str, str]] = []
        self._started = False
        self._new_tools: list[Tool] = []

    # ---------- 启动 / 关闭 ----------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._failed.clear()
        self._new_tools = self._sync_all()

    def reload(self) -> list[Tool]:
        """重新读 `.mcp.json`：新增 server 接上、配置变了重连、断线的重连。

        返回「新增或重建的 Tool」，由调用方（Agent）注册进工具表。
        连接对象在配置未变时会被复用，已有工具绑定继续有效。
        """
        self.config = load_mcp_config(self.config.path.parent)
        wanted = {s.name for s in self.config.servers}
        for name in [n for n in self._connections if n not in wanted]:
            self._connections.pop(name).close()
            self._drop_server(name)
        self._started = False
        self.start()
        return list(self._new_tools)

    def _sync_all(self) -> list[Tool]:
        created: list[Tool] = []
        used: set[str] = set(self._bindings)  # 名字已经占住，新工具不许抢
        for server in self.config.servers:
            connection = self._connections.get(server.name)
            try:
                if connection is not None and connection.config != server:
                    # 配置变了（改端口 / 改 header）：原地换配置后重连
                    connection.config = server
                    connection.restart()
                    self._drop_server(server.name)
                elif connection is None:
                    connection = MCPConnection(server)
                    connection.start()
                elif not connection.alive:
                    connection.restart()

                remote_tools = connection.list_tools()
            except Exception as exc:
                if connection is not None and connection not in self._connections.values():
                    connection.close()
                self._failed.append((server.name, str(exc)))
                continue

            self._connections[server.name] = connection
            known = {b.remote_name for b in self._bindings.values() if b.server == server.name}
            for remote in remote_tools:
                remote_name = str(getattr(remote, "name", "") or "").strip() or "unnamed"
                if remote_name in known:
                    continue
                binding = self._make_binding(server.name, remote, connection, used)
                self._bindings[binding.exposed_name] = binding
                tool = Tool(
                    name=binding.exposed_name,
                    description=binding.description,
                    parameters=binding.schema,
                    handler=self._make_handler(binding),
                )
                self._tools.append(tool)
                created.append(tool)
        return created

    def _drop_server(self, server: str) -> None:
        """丢掉某个 server 的旧绑定与工具（配置变化后重建用）。"""
        names = [n for n, b in self._bindings.items() if b.server == server]
        for name in names:
            self._bindings.pop(name, None)
        prefix = "mcp__%s__" % _slug(server)
        self._tools = [t for t in self._tools if not t.name.startswith(prefix)]

    def close(self) -> None:
        for connection in self._connections.values():
            connection.close()
        self._connections.clear()

    # ---------- 对外状态 ----------

    @property
    def tools(self) -> list[Any]:
        return list(self._tools)

    @property
    def bindings(self) -> list[MCPToolBinding]:
        return list(self._bindings.values())

    @property
    def servers(self) -> list[str]:
        return list(self._connections)

    @property
    def server_status(self) -> list[ServerStatus]:
        """按配置顺序给出每个 server 的当前状态。"""
        statuses: list[ServerStatus] = []
        for server in self.config.servers:
            connection = self._connections.get(server.name)
            count = sum(1 for b in self._bindings.values() if b.server == server.name)
            error = ""
            if connection is None:
                error = dict(self._failed).get(server.name, "未连接")
            statuses.append(
                ServerStatus(
                    name=server.name,
                    url=server.url,
                    connected=connection is not None and connection.alive,
                    tool_count=count,
                    error=error,
                )
            )
        return statuses

    @property
    def failed(self) -> list[tuple[str, str]]:
        return list(self._failed)

    @property
    def skipped(self) -> list[tuple[str, str]]:
        return [(s.name, s.reason) for s in self.config.skipped]

    def summary(self) -> str:
        if self.config.error:
            return "配置错误"
        if not self.config.exists:
            return "未配置"
        if not self._connections:
            return "无可用 server"
        return "%d server / %d 工具" % (len(self._connections), len(self._bindings))

    def diagnostics(self) -> list[str]:
        """启动阶段的问题清单，供前端展示或排查。"""
        lines: list[str] = []
        if self.config.error:
            lines.append("%s：%s" % (self.config.path.name, self.config.error))
        for name, reason in self.skipped:
            lines.append("%s 未启用：%s" % (name, reason))
        for name, reason in self._failed:
            lines.append("%s 连接失败：%s" % (name, reason))
        return lines

    # ---------- 绑定 ----------

    def _make_binding(
        self,
        server: str,
        remote: Any,
        connection: MCPConnection,
        used: set[str],
    ) -> MCPToolBinding:
        remote_name = str(getattr(remote, "name", "") or "").strip() or "unnamed"
        exposed = _exposed_name(server, remote_name, used)

        description = str(getattr(remote, "description", "") or "").strip()
        if not description:
            description = "MCP 工具 %s（%s 提供）" % (remote_name, server)
        description = "[MCP:%s] %s" % (server, description)

        schema = getattr(remote, "input_schema", None)
        if not isinstance(schema, dict) or not schema:
            schema = {"type": "object", "properties": {}}
        else:
            # 深拷贝一份，防止后续改动影响 SDK 内部对象
            schema = json.loads(json.dumps(schema, ensure_ascii=False))
        schema.setdefault("type", "object")

        return MCPToolBinding(
            exposed_name=exposed,
            server=server,
            remote_name=remote_name,
            description=description[:2000],
            schema=schema,
            connection=connection,
        )

    def _make_handler(self, binding: MCPToolBinding):
        def handler(**arguments: Any):
            return self._invoke(binding, arguments)

        return handler

    # ---------- 调用 ----------

    def _invoke(self, binding: MCPToolBinding, arguments: dict[str, Any]) -> ToolResult:
        try:
            result = binding.connection.call_tool(binding.remote_name, arguments)
        except MCPConnectionLost as exc:
            # 连接断了（server 重启 / 空闲被掐）→ 重连后重试一次
            if not self._recover(binding.connection):
                return self._failure(binding, exc)
            try:
                result = binding.connection.call_tool(binding.remote_name, arguments)
            except Exception as again:
                return self._failure(binding, again)
        except MCPError as exc:
            return self._failure(binding, exc)
        except Exception as exc:  # 兜底，任何异常都要变成模型能读懂的文字
            return self._failure(binding, exc)

        ok, text = format_result(result)
        return ToolResult(name=binding.exposed_name, output=text, ok=ok)

    def _recover(self, connection: MCPConnection) -> bool:
        """重连一次，成功返回 True。"""
        try:
            connection.restart()
            connection.list_tools()
        except Exception:
            return False
        return True

    def _failure(self, binding: MCPToolBinding, exc: BaseException) -> ToolResult:
        detail = str(exc) if isinstance(exc, MCPError) else _describe(exc)
        return ToolResult(
            name=binding.exposed_name,
            output="调用 MCP 工具 `%s`（服务器 %s）失败：%s" % (
                binding.remote_name,
                binding.server,
                detail,
            ),
            ok=False,
        )


def format_result(result: Any) -> tuple[bool, str]:
    """把 CallToolResult 转成给模型看的文本 + 成功标志。"""
    parts: list[str] = []

    for item in getattr(result, "content", None) or []:
        text = _content_to_text(item)
        if text:
            parts.append(text)

    structured = getattr(result, "structured_content", None)
    if not structured:
        structured = getattr(result, "structuredContent", None)
    if structured and not _is_echo_of_text(structured, parts):
        try:
            rendered = json.dumps(structured, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            rendered = str(structured)
        parts.append("结构化数据（JSON）：\n%s" % rendered)

    text = "\n".join(part for part in parts if part).strip()
    is_error = bool(getattr(result, "is_error", None) or getattr(result, "isError", False))

    if not text:
        text = "（工具没有返回内容）" if not is_error else "（工具返回了空错误）"
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS].rstrip() + "\n\n（结果过长，只显示前 %d 字符）" % MAX_RESULT_CHARS

    if is_error:
        return False, "MCP 工具返回错误：\n%s" % text
    return True, text


def _is_echo_of_text(structured: Any, parts: list[str]) -> bool:
    """文本里已经是同一份数据（`{"result": ...}` 包装或 JSON 原文）就不重复贴，省 token。"""
    texts = [part.strip() for part in parts if part and part.strip()]
    if len(texts) != 1:
        return False
    if isinstance(structured, dict) and set(structured) == {"result"}:
        value = structured.get("result")
        if isinstance(value, str) and value.strip() == texts[0]:
            return True
    try:
        return json.loads(texts[0]) == structured
    except (ValueError, TypeError):
        return False


def _content_to_text(item: Any) -> str:
    """兼容文本、图片、音频、内嵌资源等各类 content。"""
    text = getattr(item, "text", None)
    if isinstance(text, str):
        return text

    resource = getattr(item, "resource", None)
    if resource is not None:
        resource_text = getattr(resource, "text", None)
        if isinstance(resource_text, str):
            uri = getattr(resource, "uri", "")
            return "[资源 %s]\n%s" % (uri, resource_text)
        uri = getattr(resource, "uri", "")
        blob = getattr(resource, "blob", None)
        size = len(blob) if isinstance(blob, str) else 0
        return "[二进制资源 %s，约 %d 字节，已省略]" % (uri, size * 3 // 4)

    data = getattr(item, "data", None)
    mime = getattr(item, "mime_type", None) or getattr(item, "mimeType", None) or "未知类型"
    if isinstance(data, str):
        return "[%s 内容，base64 约 %d 字节，已省略]" % (mime, len(data) * 3 // 4)

    return str(item)


def _exposed_name(server: str, tool: str, used: set[str]) -> str:
    """`mcp__<server>__<tool>`，非法字符替换成下划线，冲突时加短哈希。"""
    base = "mcp__%s__%s" % (_slug(server), _slug(tool))
    base = base[:64]
    if base not in used:
        used.add(base)
        return base

    digest = hashlib.sha1(("%s/%s" % (server, tool)).encode("utf-8")).hexdigest()[:6]
    candidate = "%s__%s" % (base[:57], digest)
    used.add(candidate)
    return candidate


def _slug(text: str) -> str:
    slug = _INVALID_NAME_CHARS.sub("_", text.strip())
    return slug or "tool"


def create_mcp_manager(root: str | Path) -> MCPManager:
    """读取配置并连接所有 server；任何失败都只记录，不抛异常。"""
    manager = MCPManager(load_mcp_config(root))
    manager.start()
    atexit.register(manager.close)
    return manager
