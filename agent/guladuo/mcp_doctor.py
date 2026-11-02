"""MCP 体检工具：不启动 TUI，直接验证 `.mcp.json` 里的 server 能不能接上。

用法（在项目根目录、用项目自带的 venv 跑）：

    python mcp_doctor.py                       # 读 ./.mcp.json，连接并列出工具
    python mcp_doctor.py --config D:/skill/.mcp.json
    python mcp_doctor.py --call list_pets      # 顺手调一个工具
    python mcp_doctor.py --call add_pet --args "{\"name\": \"旺财\"}"

退出码：0 表示全部 server 可用；1 表示有 server 连不上或调用失败。
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from core.mcp import create_mcp_manager, load_mcp_config
from core.mcp.connection import is_loopback_url


def _fix_stdout() -> None:
    """Windows 控制台默认 GBK，遇到生僻字符会崩，统一改成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _port_state(url: str) -> str:
    """连之前先看端口有没有人听，报错时能一眼看出是「没启动」还是「路径不对」。"""
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return "%s:%d 端口已监听" % (host, port)
    except OSError as exc:
        return "%s:%d 端口无响应（%s）" % (host, port, exc.strerror or exc)


def _required(schema: dict) -> list[str]:
    required = schema.get("required") or []
    return [str(x) for x in required if isinstance(x, str)]


def main(argv: list[str] | None = None) -> int:
    _fix_stdout()

    parser = argparse.ArgumentParser(prog="mcp_doctor", description="检查 MCP Server 接入情况")
    parser.add_argument("--config", default=".mcp.json", help="配置文件路径，默认 ./.mcp.json")
    parser.add_argument("--call", metavar="TOOL", help="连接后调用某个工具（暴露名或原始名）")
    parser.add_argument("--args", default="{}", metavar="JSON", help="调用工具时的参数（JSON 对象）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，便于脚本处理")
    args = parser.parse_args(argv)

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.is_file():
        print("找不到配置文件：%s" % config_path)
        print("提示：xAgent 读取的是「启动目录」下的 .mcp.json，所以要放在你 cd 进去的那个目录。")
        return 1

    config = load_mcp_config(config_path.parent)
    if config.error:
        print("配置有误：%s\n文件：%s" % (config.error, config.path))
        return 1

    print("MCP 配置：%s" % config.path)
    if not config.servers:
        print("没有可用的 server（检查 mcpServers 字段与 type/url）。")
        for name, reason in [(s.name, s.reason) for s in config.skipped]:
            print("  - %s 已跳过：%s" % (name, reason))
        return 1

    for server in config.servers:
        note = "本机地址（已自动绕过系统代理）" if is_loopback_url(server.url) else "远程地址（遵循系统代理）"
        print("  待连接：%s → %s    [%s]" % (server.name, server.url, note))

    manager = create_mcp_manager(config_path.parent)
    try:
        statuses = manager.server_status
        for status in statuses:
            head = "[%s] %s" % (status.name, status.url)
            if status.connected:
                print("\n%s\n  连接成功，%d 个工具" % (head, status.tool_count))
            else:
                print("\n%s\n  连接失败：%s" % (head, status.error))
                if "端口" not in status.error:
                    print("  端口探测：%s" % _port_state(status.url))
                continue

            for binding in manager.bindings:
                if binding.server != status.name:
                    continue
                required = _required(binding.schema)
                first_line = binding.description.splitlines()[0] if binding.description else ""
                print("  - %s" % binding.exposed_name)
                print("      %s" % first_line)
                print("      原始名：%s%s" % (binding.remote_name, "；必填：" + "、".join(required) if required else ""))

        for name, reason in manager.skipped:
            print("\n[%s] 已跳过：%s" % (name, reason))

        ok_count = sum(1 for s in statuses if s.connected)
        print("\n结果：%d/%d 个 server 可用，共 %d 个 MCP 工具。" % (ok_count, len(statuses), len(manager.bindings)))

        if args.call:
            try:
                payload = json.loads(args.args or "{}")
            except json.JSONDecodeError as exc:
                print("--args 不是合法 JSON：%s" % exc)
                return 1
            if not isinstance(payload, dict):
                print("--args 必须是 JSON 对象")
                return 1

            tool = _pick_tool(manager.tools, args.call)
            if tool is None:
                print("找不到工具：%s" % args.call)
                return 1
            print("\n调用 %s  参数 %s" % (tool.name, json.dumps(payload, ensure_ascii=False)))
            result = tool.handler(**payload)
            print("返回：\n%s" % result.output)
            if not result.ok:
                return 1

        return 0 if ok_count == len(statuses) else 1
    finally:
        manager.close()


def _pick_tool(tools, name: str):
    for tool in tools:
        if tool.name == name:
            return tool
    for tool in tools:  # 允许用原始名（不带 mcp__server__ 前缀）
        if tool.name.endswith("__" + name):
            return tool
    return None


if __name__ == "__main__":
    raise SystemExit(main())
