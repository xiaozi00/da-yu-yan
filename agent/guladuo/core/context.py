"""上下文管理：token 估算、原始上下文归档、全文检索、压缩提示词。

设计上刻意保持简单（不引入向量库 / Embedding）：
- 压缩前把会话原样写成 JSONL 归档，一个会话一次压缩 = 一个文件；
- 检索就是「关键词子串匹配 + 上下文片段」，够用且可读；
- 压缩只调用一次 LLM（不带工具），前端仍然只认识事件。

目录结构：

    <项目根>/.agents/context/20261022-153000.jsonl
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

CONTEXT_DIRNAME = ".agents/context"

# 窗口未知时的展示兜底（真实值与阈值都在 config.ini 里配）
DEFAULT_WINDOW = 128_000

# 估算时给每条消息留的结构开销（role / 分隔符等）
_MESSAGE_OVERHEAD = 4
# 单条消息写进压缩请求时的默认截断长度（归档里仍然是完整内容）
_ENTRY_LIMIT = 3000
# digest / 检索的默认上限（真实值由 Settings 传入）
_DIGEST_ARCHIVES = 5
_DIGEST_SUMMARY = 600
_DIGEST_TOPIC = 120
_SEARCH_LIMIT = 8
_SEARCH_CHARS = 4000

ARCHIVE_NOTE = (
    "此前上下文已于 {time} 完成压缩，原始上下文保存在编号为 {archive_id} 的归档文件中。"
    "如需查找历史信息，请使用 recallContext 工具。"
)

COMPACT_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 `recallContext` 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""


# ---------------------------------------------------------------- token 估算


def estimate_tokens(text: str) -> int:
    """粗估 token 数：中日韩字符按 1 个 token，其它字符按 4 个字符 1 个 token。

    不追求精确，只用于「API 没给 usage 时」的兜底估算。
    """
    if not text:
        return 0
    wide = 0
    narrow = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af":
            wide += 1
        else:
            narrow += 1
    return wide + (narrow + 3) // 4


def estimate_messages_tokens(
    messages: Sequence[dict[str, Any]],
    tool_schemas: Sequence[dict[str, Any]] | None = None,
) -> int:
    """估算「整段上下文」的 token：消息 + 工具定义。"""
    total = 0
    for message in messages:
        total += _MESSAGE_OVERHEAD
        total += estimate_tokens(str(message.get("content") or ""))
        if message.get("tool_calls"):
            total += estimate_tokens(json.dumps(message["tool_calls"], ensure_ascii=False))
        if message.get("name"):
            total += estimate_tokens(str(message["name"]))
    if tool_schemas:
        total += estimate_tokens(json.dumps(list(tool_schemas), ensure_ascii=False))
    return total


# ---------------------------------------------------------------- 归档


@dataclass(frozen=True)
class ArchiveRecord:
    archive_id: str
    path: Path
    messages: int
    size: int


def new_archive_id(now: datetime | None = None) -> str:
    """归档编号：20261022-153000。"""
    return (now or datetime.now()).strftime("%Y%m%d-%H%M%S")


class ContextStore:
    """`.agents/context/` 下的原始上下文归档与检索。"""

    def __init__(self, root: str | Path, entry_limit: int = _ENTRY_LIMIT) -> None:
        self.root = Path(root)
        self.entry_limit = max(200, int(entry_limit))

    # ---------- 写入 ----------

    def save(self, messages: Iterable[dict[str, Any]], archive_id: str | None = None) -> ArchiveRecord:
        """把消息逐行写成 JSONL。失败会抛 OSError，调用方必须据此中止压缩。"""
        self.root.mkdir(parents=True, exist_ok=True)
        archive_id = archive_id or new_archive_id()
        path = self.root / ("%s.jsonl" % archive_id)

        count = 0
        lines: list[str] = []
        for index, message in enumerate(messages):
            record = {"index": index}
            record.update(message)
            lines.append(json.dumps(record, ensure_ascii=False))
            count += 1

        # 先写临时文件再改名，避免压缩中途失败留下半个归档
        temp = path.with_suffix(".jsonl.tmp")
        temp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        temp.replace(path)
        return ArchiveRecord(archive_id=archive_id, path=path, messages=count, size=path.stat().st_size)

    def save_summary(self, archive_id: str, summary: str) -> Path:
        """把压缩摘要也落盘（`<archive_id>.summary.md`），供重启后的会话注入提示词。

        写盘失败抛 OSError，由调用方决定是否忽略 —— 摘要丢了不影响归档本身。
        """
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ("%s.summary.md" % archive_id)
        temp = path.with_suffix(".summary.md.tmp")
        temp.write_text(summary, encoding="utf-8")
        temp.replace(path)
        return path

    # ---------- 读取 ----------

    def archives(self) -> list[ArchiveRecord]:
        if not self.root.is_dir():
            return []
        records: list[ArchiveRecord] = []
        for path in sorted(self.root.glob("*.jsonl"), reverse=True):
            records.append(self._record(path))
        return records

    def find(self, archive_id: str) -> ArchiveRecord | None:
        name = archive_id.strip()
        if name.endswith(".jsonl"):
            name = name[: -len(".jsonl")]
        path = self.root / ("%s.jsonl" % name)
        if not path.is_file():
            return None
        return self._record(path)

    def summary_of(self, archive_id: str) -> str | None:
        """读取某个归档的持久化摘要；没有则返回 None。"""
        path = self.root / ("%s.summary.md" % archive_id)
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return text or None

    def load_transcript(self, archive_id: str) -> str:
        """读取归档原文并渲染成可读文本（给 TUI 的压缩日志面板用）。"""
        record = self.find(archive_id)
        if record is None:
            return "（找不到归档 %s）" % archive_id
        messages: list[dict[str, Any]] = []
        try:
            with record.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    message = _safe_json(line)
                    if message is not None:
                        messages.append(message)
        except OSError as exc:
            return "（归档读取失败：%s）" % exc
        if not messages:
            return "（归档为空）"
        return render_transcript(messages, self.entry_limit)

    def digest(
        self,
        *,
        max_archives: int = _DIGEST_ARCHIVES,
        summary_chars: int = _DIGEST_SUMMARY,
        topic_chars: int = _DIGEST_TOPIC,
    ) -> str | None:
        """生成给 System Prompt 注入的归档清单；没有任何归档时返回 None。

        有持久化摘要的归档注入「摘要开头」，没有的退化为「第一条用户消息当话题」。
        细节一律引导模型用 recallContext 检索，避免把整个归档塞回上下文。
        """
        records = self.archives()
        if not records or max_archives <= 0:
            return None

        blocks: list[str] = []
        for record in records[:max_archives]:
            summary = self.summary_of(record.archive_id)
            if summary:
                head = summary[:summary_chars].strip()
                if len(summary) > summary_chars:
                    head += "…（完整摘要见归档，可用 recallContext 检索细节）"
                body = "摘要：%s" % head
            else:
                body = "话题：%s" % self._first_topic(record, topic_chars)
            blocks.append("### 归档 %s（%d 条消息）\n%s" % (record.archive_id, record.messages, body))

        header = (
            "## 历史会话归档\n\n"
            "下面是此前会话经 /compact 压缩后的存档。当用户提到「之前 / 上次 / 刚才」"
            "聊过的内容而当前对话里没有时，先看这里的摘要；\n"
            "需要具体细节（原文、命令、报错、文件路径）时，用 recallContext 工具检索，"
            "可传 id 参数限定某个归档。\n\n" + "\n\n".join(blocks)
        )
        if len(records) > max_archives:
            header += "\n\n（另有 %d 个更早的归档未列出，可用 recallContext 直接检索。）" % (len(records) - max_archives)
        return header

    def _first_topic(self, record: ArchiveRecord, limit: int) -> str:
        """没有持久化摘要时，取第一条用户消息当话题。"""
        try:
            with record.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    message = _safe_json(line)
                    if message and message.get("role") == "user":
                        text = str(message.get("content") or "").strip().replace("\n", " ")
                        if text:
                            return text[:limit] + ("…" if len(text) > limit else "")
        except OSError:
            pass
        return "（无文本记录）"

    def _record(self, path: Path) -> ArchiveRecord:
        messages = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for _ in handle:
                    messages += 1
        except OSError:
            pass
        return ArchiveRecord(
            archive_id=path.stem,
            path=path,
            messages=messages,
            size=path.stat().st_size if path.exists() else 0,
        )

    # ---------- 检索 ----------

    def search(
        self,
        text: str,
        archive_id: str | None = None,
        *,
        limit: int = _SEARCH_LIMIT,
        max_chars: int = _SEARCH_CHARS,
    ) -> str:
        """关键词全文检索，返回给模型看的文本（不是异常）。"""
        keywords = [word for word in (text or "").split() if word]
        if not keywords:
            return "检索失败：text 参数不能为空，请给出要查找的关键词。"

        if archive_id:
            record = self.find(archive_id)
            if record is None:
                known = "、".join(item.archive_id for item in self.archives()) or "（暂无归档）"
                return "找不到编号为 %s 的归档。现有归档：%s" % (archive_id, known)
            targets = [record]
        else:
            targets = self.archives()

        if not targets:
            return "还没有任何上下文归档（%s 为空）。只有在执行过 /compact 之后才会有归档。" % self.root

        lowered = [word.lower() for word in keywords]
        hits: list[str] = []
        truncated = False
        used = 0

        for record in targets:
            try:
                with record.path.open("r", encoding="utf-8") as handle:
                    for line_no, line in enumerate(handle, start=1):
                        message = _safe_json(line)
                        if message is None:
                            continue
                        haystack = json.dumps(message, ensure_ascii=False).lower()
                        if not all(word in haystack for word in lowered):
                            continue
                        snippet = _snippet(message, lowered)
                        block = "【归档 %s】第 %d 条 · %s\n%s" % (
                            record.archive_id,
                            message.get("index", line_no - 1) + 1 if isinstance(message.get("index"), int) else line_no,
                            message.get("role", "?"),
                            snippet,
                        )
                        if len(hits) >= limit or used + len(block) > max_chars:
                            truncated = True
                            break
                        hits.append(block)
                        used += len(block) + 2
            except OSError as exc:
                hits.append("【归档 %s】读取失败：%s" % (record.archive_id, exc))
            if len(hits) >= limit or truncated:
                break

        if not hits:
            scope = "归档 %s" % archive_id if archive_id else "全部 %d 个归档" % len(targets)
            return "在%s中没有找到包含「%s」的内容。" % (scope, " ".join(keywords))

        head = "命中 %d 条（关键词：%s）：" % (len(hits), " ".join(keywords))
        body = "\n\n".join(hits)
        if truncated:
            body += "\n\n（结果已截断，可用更具体的关键词，或指定 id 缩小范围。）"
        return head + "\n\n" + body


def _safe_json(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _content_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        parts.append(content)
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        parts.append("[调用 %s] %s" % (function.get("name", "?"), function.get("arguments", "")))
    return "\n".join(parts)


def _snippet(message: dict[str, Any], keywords: list[str], span: int = 160) -> str:
    """截取命中位置前后的一段文字，避免把整条长消息塞回上下文。"""
    text = _content_text(message)
    if not text:
        return "（无文本内容）"
    lowered = text.lower()
    position = -1
    for word in keywords:
        found = lowered.find(word)
        if found != -1 and (position == -1 or found < position):
            position = found
    if position == -1:
        return text[: span * 2].strip()
    start = max(0, position - span)
    end = min(len(text), position + span)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return (prefix + text[start:end] + suffix).strip()


# ---------------------------------------------------------------- 压缩


def render_transcript(messages: Sequence[dict[str, Any]], limit: int = _ENTRY_LIMIT) -> str:
    """把消息渲染成给压缩模型看的纯文本，保留角色、工具调用与顺序。"""
    call_names = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            call_id = call.get("id")
            function = call.get("function") or {}
            if call_id:
                call_names[call_id] = function.get("name", "?")

    blocks: list[str] = []
    for index, message in enumerate(messages, start=1):
        role = message.get("role", "?")
        label = role
        if role == "tool":
            label = "tool → %s" % call_names.get(message.get("tool_call_id"), "unknown")
        text = _content_text(message)
        if len(text) > limit:
            text = text[:limit] + "\n…（本条过长已截断，完整内容见归档）"
        blocks.append("[%d] %s\n%s" % (index, label, text or "（空）"))
    return "\n\n".join(blocks)
