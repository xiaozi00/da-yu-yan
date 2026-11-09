"""读取项目根目录下的 .mcp.json，得到 MCP Server 的连接信息。

配置文件格式与 Claude Code / MCP 生态通用的一致：

```json
{
  "mcpServers": {
    "my-server": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ${MY_TOKEN}" }
    }
  }
}
```

本项目只支持 Streamable HTTP（HTTP + SSE 流式响应）。stdio 类型的
`command` / `args` 会被跳过并给出原因，不影响其它 server 启动。

解析全程「失败不炸」：单个 server 配置有问题只进 `skipped`，
整个文件有问题只记在 `error` 上，MCP 功能整体降级，xAgent 照常可用。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

CONFIG_FILENAME = ".mcp.json"

# type 字段可接受的写法（大小写不敏感）
_HTTP_TYPES = {
    "http",
    "streamable-http",
    "streamable_http",
    "streamablehttp",
    "streamable",
}

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

DEFAULT_CALL_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0


@dataclass
class MCPServerConfig:
    """一个可用的 MCP Server。"""

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_CALL_TIMEOUT
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT


@dataclass(frozen=True)
class UnusableServer:
    """配置里存在但无法使用的 server（不启动，只记录原因）。"""

    name: str
    reason: str


@dataclass(frozen=True)
class MCPConfig:
    path: Path
    servers: tuple[MCPServerConfig, ...] = ()
    skipped: tuple[UnusableServer, ...] = ()
    error: str = ""

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def summary(self) -> str:
        if self.error:
            return "MCP 配置错误：%s" % self.error
        if not self.exists:
            return "未配置（%s 不存在）" % self.path
        if not self.servers:
            return "无可用 server"
        return "%d 个 server：%s" % (len(self.servers), "、".join(s.name for s in self.servers))


class _InvalidServer(Exception):
    """内部使用：单个 server 配置不合法。"""


def load_mcp_config(
    root: str | Path,
    env: Mapping[str, str] | None = None,
    *,
    call_timeout: float = DEFAULT_CALL_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> MCPConfig:
    """读取 `<root>/.mcp.json`。任何情况下都不抛异常。

    `call_timeout` / `connect_timeout` 是 config.ini 给的全局默认值，
    单个 server 仍可用自身配置项覆盖。
    """
    path = Path(root) / CONFIG_FILENAME
    if not path.is_file():
        return MCPConfig(path=path)

    try:
        raw_text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return MCPConfig(path=path, error="读取失败：%s" % exc)

    if not raw_text.strip():
        return MCPConfig(path=path, error="文件为空")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return MCPConfig(path=path, error="不是合法 JSON（第 %d 行第 %d 列）：%s" % (exc.lineno, exc.colno, exc.msg))

    if not isinstance(data, dict):
        return MCPConfig(path=path, error="顶层必须是 JSON 对象")

    servers_raw = data.get("mcpServers")
    if servers_raw is None:
        return MCPConfig(path=path, error="缺少 mcpServers 字段")
    if not isinstance(servers_raw, dict):
        return MCPConfig(path=path, error="mcpServers 必须是对象（server 名 → 配置）")

    environment = os.environ if env is None else env
    servers: list[MCPServerConfig] = []
    skipped: list[UnusableServer] = []

    for name, entry in servers_raw.items():
        try:
            servers.append(
                _parse_server(str(name), entry, environment, call_timeout, connect_timeout)
            )
        except _InvalidServer as exc:
            skipped.append(UnusableServer(name=str(name), reason=str(exc)))

    return MCPConfig(path=path, servers=tuple(servers), skipped=tuple(skipped))


def _parse_server(
    name: str,
    entry: Any,
    env: Mapping[str, str],
    default_call_timeout: float = DEFAULT_CALL_TIMEOUT,
    default_connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> MCPServerConfig:
    if not isinstance(entry, dict):
        raise _InvalidServer("配置必须是对象")

    if entry.get("disabled") is True:
        raise _InvalidServer("已禁用（disabled: true）")

    url = entry.get("url") or entry.get("serverUrl") or entry.get("endpoint")
    if not url:
        if entry.get("command"):
            raise _InvalidServer("只支持 Streamable HTTP；stdio 启动方式（command/args）未支持")
        raise _InvalidServer("缺少 url 字段")

    transport = entry.get("type") or entry.get("transport")
    if transport is not None:
        transport_norm = str(transport).strip().lower()
        if transport_norm not in _HTTP_TYPES:
            raise _InvalidServer(
                "只支持 Streamable HTTP（type=%s 未支持，可用值：http / streamable-http）" % transport
            )

    url = _expand(str(url), env)
    if not url.lower().startswith(("http://", "https://")):
        raise _InvalidServer("url 必须以此开头：http:// 或 https://")

    headers_raw = entry.get("headers") or {}
    if not isinstance(headers_raw, dict):
        raise _InvalidServer("headers 必须是对象（header 名 → 值）")
    headers: dict[str, str] = {}
    for key, value in headers_raw.items():
        headers[str(key)] = _expand(str(value), env)

    timeout = _positive_float(entry.get("timeout"), default_call_timeout, "timeout")
    connect_timeout = _positive_float(
        entry.get("connectTimeout", entry.get("connect_timeout")),
        default_connect_timeout,
        "connectTimeout",
    )

    return MCPServerConfig(
        name=name,
        url=url,
        headers=headers,
        timeout=timeout,
        connect_timeout=connect_timeout,
    )


def _positive_float(value: Any, default: float, field_name: str) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _InvalidServer("%s 必须是数字（秒）" % field_name) from None
    if number <= 0:
        raise _InvalidServer("%s 必须大于 0" % field_name)
    return number


def _expand(value: str, env: Mapping[str, str]) -> str:
    """支持 ${VAR} 与 $VAR，用于 header 里的密钥；变量缺失时直接报错。"""

    def replace(match: re.Match[str]) -> str:
        var = match.group(1) or match.group(2)
        if var not in env:
            raise _InvalidServer("环境变量 %s 未设置" % var)
        return env[var]

    return _ENV_PATTERN.sub(replace, value)
