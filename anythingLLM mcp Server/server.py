#!/usr/bin/env python3
"""AnythingLLM MCP Server (MVP) — MCP 2026-07-28 / Streamable HTTP

通过 AnythingLLM HTTP API 向本机唯一工作区提问。
绑定 127.0.0.1:8000，路径 /mcp，stateless 模式（对应 2026-07-28 无状态协议核心）。

启动:
    C:/Users/34925/.workbuddy/binaries/python/envs/default/Scripts/python.exe server.py

环境变量:
    ANYTHINGLLM_API_KEY          必填，AnythingLLM API Key（不写入代码）
    ANYTHINGLLM_BASE_URL         默认 http://localhost:3001
    ANYTHINGLLM_WORKSPACE_SLUG   默认 30c051a3-a945-4dcf-a336-7828ca4627c3

明确不做: 多工作区 / 会话延续 / resources、prompts / OAuth / 重试与连接池
"""

import os

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

DEFAULT_BASE_URL = "http://localhost:3001"
DEFAULT_SLUG = "30c051a3-a945-4dcf-a336-7828ca4627c3"

server = MCPServer("anythingllm", version="0.1.0")


@server.tool(description="向本机 AnythingLLM 工作区提问，返回 AI 基于工作区知识库的回答文本。")
def ask_workspace(question: str) -> str:
    """向本机 AnythingLLM 工作区提问。"""
    api_key = os.environ.get("ANYTHINGLLM_API_KEY")
    if not api_key:
        raise ToolError("缺少环境变量 ANYTHINGLLM_API_KEY，请先注入后重试。")

    base_url = os.environ.get("ANYTHINGLLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    slug = os.environ.get("ANYTHINGLLM_WORKSPACE_SLUG", DEFAULT_SLUG)
    try:
        resp = httpx.post(
            f"{base_url}/api/v1/workspace/{slug}/chat",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"message": question, "mode": "chat"},
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json().get("textResponse")
        if not text:
            raise ToolError(f"响应中缺少 textResponse: {resp.text[:300]}")
        return text
    except httpx.HTTPStatusError as e:
        raise ToolError(f"AnythingLLM 返回 HTTP {e.response.status_code}: {e.response.text[:300]}") from e
    except (httpx.HTTPError, ValueError) as e:
        raise ToolError(f"调用 AnythingLLM 失败: {e}") from e


if __name__ == "__main__":
    server.run(
        "streamable-http",
        host="127.0.0.1",
        port=8000,
        streamable_http_path="/mcp",
        stateless_http=True,
    )
