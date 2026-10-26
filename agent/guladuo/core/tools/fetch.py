"""fetchweb 工具：抓取指定网页并提取正文，转成 Markdown 交给 LLM。

流程：
1. 用 subprocess 以参数列表方式调用本地 curl（不经过 shell）抓取 HTML；
2. 用 trafilatura 提取正文主内容（自动去掉导航栏、广告、页眉页脚）；
3. 转成 Markdown（保留标题、段落、列表、表格、链接）返回。

错误处理约定：URL 非法、网络错误、超时、HTTP 非 2xx、解析失败、
正文提取失败，都抛 ToolError，由 registry 转成 ok=False 回传给模型，
给出清晰的中文原因，不中断对话。
"""

from __future__ import annotations

import subprocess

import trafilatura

from ..errors import ToolError
from .base import Tool

CURL_TIMEOUT = 25        # curl 整体超时（秒）
MAX_HTML_BYTES = 8_000_000  # 拒绝过大的响应，防止把上下文撑爆

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 常见 curl 退出码 → 中文说明（其余码统一给原始消息）
CURL_ERROR_HINTS = {
    3: "URL 格式不合法",
    6: "域名解析失败（可能是网络不通或域名错误）",
    7: "无法连接到目标服务器",
    22: "服务器返回了错误状态码",
    28: "连接或传输超时",
}


def build_fetch_tool(timeout: float = CURL_TIMEOUT) -> Tool:
    """构造 fetchweb 工具。"""

    def fetch_web(url: str, max_chars: int = 8000) -> str:
        url = (url or "").strip().strip('"\'')
        if not url:
            raise ToolError("url 不能为空，请提供要抓取的网页地址")
        if not url.lower().startswith(("http://", "https://")):
            raise ToolError(
                "url 必须以 http:// 或 https:// 开头，收到的是 %r" % url[:100]
            )

        try:
            max_chars = int(max_chars)
        except (TypeError, ValueError):
            max_chars = 8000
        max_chars = max(500, min(max_chars, 20000))

        html = _curl_get(url, timeout)
        if len(html.strip()) < 80:
            raise ToolError("网页内容过短（%d 字符），可能是空页面或需要登录" % len(html.strip()))

        markdown = _extract_markdown(html, url)
        if not markdown or not markdown.strip():
            raise ToolError(
                "无法从该网页提取正文：页面可能是纯脚本渲染（SPA）、验证码页或非文章内容"
            )

        if len(markdown) > max_chars:
            markdown = markdown[:max_chars].rstrip() + "\n\n（正文过长，已截断至 %d 字符）" % max_chars

        # 正文自带标题时不额外加头，只补一行来源
        stripped = markdown.lstrip()
        if stripped.startswith("# "):
            first, rest = stripped.split("\n", 1)
            return "%s\n\n来源: %s\n\n%s" % (first, url, rest.lstrip("\n"))
        return "来源: %s\n\n%s" % (url, markdown)

    return Tool(
        name="fetchweb",
        description=(
            "抓取指定 URL 网页的正文主内容，转成 Markdown 返回"
            "（保留标题、段落、列表、表格和链接，自动去掉导航栏/广告/页眉页脚）。"
            "适合阅读 searchweb 搜到的具体网页、或用户直接给出的链接。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的完整网页地址，以 http:// 或 https:// 开头",
                },
                "max_chars": {
                    "type": "integer",
                    "description": (
                        "返回正文的最大字符数（500-20000），默认 8000，"
                        "超长会截断，可按需调大"
                    ),
                },
            },
            "required": ["url"],
        },
        handler=fetch_web,
    )


def _curl_get(url: str, timeout: float) -> str:
    """用参数列表方式调用本地 curl 抓取 HTML，不经过 shell。"""
    command = [
        "curl",
        "-sS",                       # 静默但保留错误信息
        "-L",                        # 跟随重定向
        "--max-time", str(int(timeout)),
        "--compressed",              # 自动解压 gzip/deflate
        "--max-filesize", str(MAX_HTML_BYTES),
        "-A", USER_AGENT,
        "-H", "Accept: text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
        "-w", "\n__HTTP_CODE__:%{http_code}",   # 末尾追加真实状态码
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("抓取网页超时（超过 %d 秒）：%s" % (timeout, url)) from exc
    except FileNotFoundError as exc:
        raise ToolError("本地找不到 curl 程序，请确认已安装 curl 并在 PATH 中") from exc
    except OSError as exc:
        raise ToolError("调用 curl 失败：%s" % exc) from exc

    raw = completed.stdout.decode("utf-8", "replace")
    stderr = completed.stderr.decode("utf-8", "replace").strip()

    http_code, body = _split_http_code(raw)

    if completed.returncode != 0:
        hint = CURL_ERROR_HINTS.get(completed.returncode)
        detail = stderr or hint or ("curl 退出码 %d" % completed.returncode)
        # 22 = HTTP 错误，此时 stderr 只有 "curl: (22) ..."，补上状态码更清楚
        if completed.returncode == 22 and http_code:
            detail += "（HTTP %s）" % http_code
        raise ToolError("抓取网页失败：%s，链接: %s" % (detail, url))

    if not http_code:
        raise ToolError("抓取网页失败：curl 未返回 HTTP 状态码，链接: %s" % url)
    if not http_code.startswith("2"):
        message = "HTTP %s" % http_code
        if http_code == "404":
            message += "（页面不存在）"
        elif http_code in ("401", "403"):
            message += "（无权限，可能需要登录或被反爬拦截）"
        elif http_code.startswith("5"):
            message += "（服务器内部错误）"
        raise ToolError("抓取网页失败：%s，链接: %s" % (message, url))

    if not body.strip():
        raise ToolError("抓取网页失败：服务器返回了空内容，链接: %s" % url)
    return body


def _split_http_code(raw: str) -> tuple[str, str]:
    """把 curl -w 追加的状态码从输出里拆出来。"""
    marker = "__HTTP_CODE__:"
    idx = raw.rfind(marker)
    if idx < 0:
        return "", raw
    tail = raw[idx + len(marker):].strip()
    code = tail.split()[0] if tail.split() else ""
    return code, raw[:idx]


def _extract_markdown(html: str, url: str) -> str:
    """用 trafilatura 提取正文并转 Markdown，失败返回空串。"""
    try:
        markdown = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            include_images=False,      # 图片链接对对话价值低，省 token
            include_comments=False,    # 评论区通常不是正文
            include_formatting=True,
        )
    except Exception:  # trafilatura 偶发解析崩溃，一律按提取失败处理
        return ""
    return (markdown or "").strip()
