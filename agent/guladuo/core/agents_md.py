"""AGENTS.md 支持：一次注入，全程复用。

策略（很重要，直接影响缓存命中率）：

- 会话开始时若项目根存在 `AGENTS.md`，读一次，把它作为**一条普通 user 消息**
  插到 System Prompt 之后（位置固定、内容固定）；
- 之后所有轮次都直接复用历史里这条消息，**不再重读文件**，也不再重复注入；
- 这条消息随会话一起持久化到事件日志，`/resume` 恢复时自然带回，同样不重复注入。

因为消息前缀完全稳定，服务端的 prompt cache 可以一直命中；
如果每轮都重新读取、重新拼字符串（哪怕内容一样），缓存前缀也很容易被打破。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_AGENTS_FILE = "AGENTS.md"

# 注入内容的第一行，用来识别「这条消息是 AGENTS.md」，从而保证只注入一次
AGENTS_PREFIX = "<!-- guladuo:agents-md -->"

AGENTS_TEMPLATE = """{prefix}
以下是项目根目录 `{path}` 的内容，是本项目的协作约定与背景说明。
它在本会话开始时注入一次并全程有效，无需重复读取该文件；若用户后来说明与它冲突，以用户最新的要求为准。

{content}"""


@dataclass(frozen=True)
class AgentsDoc:
    path: Path
    content: str
    truncated: bool = False

    def as_message(self) -> dict:
        body = AGENTS_TEMPLATE.format(
            prefix=AGENTS_PREFIX,
            path=self.path,
            content=self.content,
        )
        return {"role": "user", "content": body}


def load_agents_doc(
    root: str | Path,
    filename: str = DEFAULT_AGENTS_FILE,
    max_chars: int = 20000,
) -> AgentsDoc | None:
    """读取项目根的 AGENTS.md；不存在 / 读不了 / 是空文件都返回 None。"""
    name = (filename or DEFAULT_AGENTS_FILE).strip() or DEFAULT_AGENTS_FILE
    path = Path(root) / name
    try:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            return None
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    text = text.strip()
    if not text:
        return None

    truncated = False
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars] + "\n\n…（AGENTS.md 过长，已截断到 %d 字符）" % max_chars
        truncated = True
    return AgentsDoc(path=resolved, content=text, truncated=truncated)


def is_agents_message(message: dict) -> bool:
    """判断一条消息是不是 AGENTS.md 注入（用于去重与日志打标）。"""
    if message.get("role") != "user":
        return False
    content = message.get("content")
    return isinstance(content, str) and content.lstrip().startswith(AGENTS_PREFIX)
