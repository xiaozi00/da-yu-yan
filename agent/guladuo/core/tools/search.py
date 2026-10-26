"""searchweb 工具：通过百度千帆 AI 搜索接口检索互联网信息。

调用千帆 `/v2/ai_search/web_search`（纯搜索，不依赖 ERNIE 模型），
把参考来源的标题、链接、内容摘要整理成文本交给 LLM 使用。

错误处理约定：所有失败（缺配置、网络异常、接口报错、解析失败）
都以 ToolError 抛出，由 registry 转成 ok=False 的工具结果回传给模型，
让模型知道"搜索失败了、为什么失败"，而不是让整轮对话崩掉。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..errors import ToolError
from .base import Tool

API_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"

# 允许的时效过滤值（与千帆文档对齐）
RECENCY_CHOICES = ("week", "month", "semiyear", "year")

DEFAULT_COUNT = 5
MAX_COUNT = 10
MAX_SNIPPET_CHARS = 400


def build_search_tool(
    api_key: str,
    timeout: float = 30.0,
) -> Tool:
    """构造 searchweb 工具。api_key 为空时工具仍注册，调用时返回清晰错误。"""

    def search_web(query: str, count: int = DEFAULT_COUNT, recency: str = "") -> str:
        query = (query or "").strip()
        if not query:
            raise ToolError("query 不能为空，请提供要搜索的关键词或问题")

        if not api_key:
            raise ToolError(
                "搜索功能未配置：请在 config.ini 的 [search] 小节填写 "
                "baidu-qianfan-apikey（百度千帆 AppBuilder API Key）"
            )

        try:
            count = int(count)
        except (TypeError, ValueError):
            count = DEFAULT_COUNT
        count = max(1, min(count, MAX_COUNT))

        recency = (recency or "").strip()
        if recency and recency not in RECENCY_CHOICES:
            raise ToolError(
                "recency 只支持 %s，收到的是 %r" % ("/".join(RECENCY_CHOICES), recency)
            )

        body: dict = {
            "messages": [{"role": "user", "content": query}],
            "resource_type_filter": [{"type": "web", "top_k": count}],
        }
        if recency:
            body["search_recency_filter"] = recency

        request = urllib.request.Request(
            API_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer %s" % api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            code, message = _extract_error(detail)
            hint = "（HTTP %d）" % exc.code
            if code or message:
                hint += " 错误码 %s：%s" % (code or "未知", message)
            if exc.code in (401, 403):
                raise ToolError(
                    "搜索接口鉴权失败%s，请检查 [search] baidu-qianfan-apikey 是否有效" % hint
                ) from exc
            raise ToolError("搜索接口返回错误%s" % hint) from exc
        except urllib.error.URLError as exc:
            raise ToolError("搜索接口网络错误：%s" % getattr(exc, "reason", exc)) from exc
        except TimeoutError as exc:  # socket.timeout 的别名
            raise ToolError("搜索接口请求超时（%d 秒）" % timeout) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolError("搜索接口返回的不是合法 JSON：%s" % raw[:200]) from exc

        if data.get("code") or data.get("message"):
            # HTTP 200 但业务报错（少数情况）
            raise ToolError(
                "搜索接口业务错误，错误码 %s：%s" % (data.get("code"), data.get("message"))
            )

        references = data.get("references") or []
        if not references:
            return "搜索「%s」没有返回任何结果，可以换个关键词再试。" % query

        return _format(query, references, count)

    return Tool(
        name="searchweb",
        description=(
            "联网搜索互联网信息（百度千帆 AI 搜索）。"
            "适合查询时事新闻、实时数据、最新动态等训练语料之外的信息。"
            "返回多条参考来源的标题、链接和内容摘要。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要搜索的关键词或问题",
                },
                "count": {
                    "type": "integer",
                    "description": "返回的结果条数，默认 5，最大 %d" % MAX_COUNT,
                },
                "recency": {
                    "type": "string",
                    "description": (
                        "可选，按网页发布时间过滤：week(7天内)/month(30天内)/"
                        "semiyear(180天内)/year(365天内)。不传则不限"
                    ),
                },
            },
            "required": ["query"],
        },
        handler=search_web,
    )


def _extract_error(raw: str) -> tuple[str, str]:
    """从错误响应里尽量抠出 code / message。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "", raw[:200]
    return str(data.get("code") or ""), str(data.get("message") or "")


def _format(query: str, references: list, count: int) -> str:
    lines = ["搜索「%s」共返回 %d 条结果：" % (query, len(references))]
    for i, ref in enumerate(references[:count], start=1):
        title = (ref.get("title") or "").strip() or "(无标题)"
        url = (ref.get("url") or "").strip()
        snippet = (ref.get("content") or ref.get("snippet") or "").strip()
        website = (ref.get("website") or "").strip()
        date = (ref.get("date") or "").strip()

        lines.append("")
        lines.append("[%d] %s" % (i, title))
        meta = []
        if website:
            meta.append("来源: %s" % website)
        if date:
            meta.append("日期: %s" % date.split(" ")[0])
        if meta:
            lines.append("    " + " | ".join(meta))
        if url:
            lines.append("    链接: %s" % url)
        if snippet:
            if len(snippet) > MAX_SNIPPET_CHARS:
                snippet = snippet[:MAX_SNIPPET_CHARS] + "…"
            lines.append("    摘要: %s" % snippet)
    return "\n".join(lines)
