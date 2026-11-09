"""会话持久化：事件日志（JSONL）+ 会话列表。

一个会话一个文件，放在 `<项目根>/.agents/sessions/<session_id>.jsonl`：

```text
{"type": "meta",  "session_id": "...", "title": "...", "created": "...", ...}
{"type": "message", "kind": "user",  "ts": "...", "message": {"role": "user", "content": "..."}}
{"type": "message", "kind": "agents_md", "ts": "...", "message": {...}}
...
```

设计取舍：**整体快照**而不是纯追加。原因很简单——上下文压缩会把历史整段换成摘要，
纯追加的日志恢复出来会重复；快照每次都是「当前真实历史」，`/resume` 直接可用。
写入先落 `.tmp` 再 rename，中途崩溃不会留下半个文件。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .agents_md import is_agents_message

META_TYPE = "meta"
MESSAGE_TYPE = "message"

_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_TITLE_LIMIT = 48


@dataclass(frozen=True)
class SessionRecord:
    """一个会话的元信息（不含消息正文）。"""

    session_id: str
    path: Path
    title: str
    created: str
    updated: str
    messages: int
    model: str = ""
    tokens: int = 0

    @property
    def user_messages(self) -> int:
        return self.messages


def new_session_id(now: datetime | None = None) -> str:
    """时间可排序 + 4 位随机：`20261105-113313-4f2a`。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return "%s-%s" % (stamp, uuid.uuid4().hex[:4])


def title_from_messages(messages: Iterable[dict[str, Any]], limit: int = _TITLE_LIMIT) -> str:
    """用第一条用户消息当标题（跳过 AGENTS.md 注入那条）。"""
    for message in messages:
        if message.get("role") != "user" or is_agents_message(message):
            continue
        text = str(message.get("content") or "").strip().replace("\n", " ")
        text = " ".join(text.split())
        if text:
            return text[:limit] + ("…" if len(text) > limit else "")
    return ""


class SessionStore:
    """会话事件日志的读写。任何操作都不抛异常（读失败返回空列表/None）。"""

    def __init__(self, root: str | Path, keep: int = 100) -> None:
        self.root = Path(root)
        self.keep = max(1, int(keep))

    # ---------- 写 ----------

    def save(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        title: str = "",
        model: str = "",
        tokens: int = 0,
        created: str = "",
    ) -> SessionRecord | None:
        session_id = self._checked_id(session_id)
        if session_id is None:
            return None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        now = _now()
        path = self.path_of(session_id)
        existing = self._read_meta(path)
        created = created or (existing.get("created") if existing else "") or now
        title = (title or "").strip() or title_from_messages(messages) or (existing.get("title") if existing else "") or "新会话"

        lines: list[str] = []
        meta = {
            "type": META_TYPE,
            "session_id": session_id,
            "title": title,
            "created": created,
            "updated": now,
            "model": model,
            "messages": len(messages),
            "tokens": int(tokens or 0),
        }
        lines.append(json.dumps(meta, ensure_ascii=False))
        for message in messages:
            lines.append(
                json.dumps(
                    {
                        "type": MESSAGE_TYPE,
                        "kind": _kind_of(message),
                        "ts": now,
                        "message": message,
                    },
                    ensure_ascii=False,
                )
            )

        temp = path.with_suffix(".jsonl.tmp")
        try:
            temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            temp.replace(path)
        except OSError:
            return None

        self.prune()
        return SessionRecord(
            session_id=session_id,
            path=path,
            title=title,
            created=created,
            updated=now,
            messages=len(messages),
            model=model,
            tokens=int(tokens or 0),
        )

    def rename(self, session_id: str, title: str) -> bool:
        record, messages = self.load(session_id)
        if record is None:
            return False
        return (
            self.save(
                session_id,
                messages,
                title=title.strip() or record.title,
                model=record.model,
                tokens=record.tokens,
                created=record.created,
            )
            is not None
        )

    def delete(self, session_id: str) -> bool:
        session_id = self._checked_id(session_id)
        if session_id is None:
            return False
        path = self.path_of(session_id)
        try:
            path.unlink()
            return True
        except OSError:
            return False

    def prune(self) -> None:
        """超出 keep 时删掉最旧的会话（按 updated 时间）。"""
        records = self.list()
        for record in records[self.keep :]:
            try:
                record.path.unlink()
            except OSError:
                pass

    # ---------- 读 ----------

    def path_of(self, session_id: str) -> Path:
        return self.root / ("%s.jsonl" % session_id)

    def exists(self, session_id: str) -> bool:
        session_id = self._checked_id(session_id)
        return bool(session_id) and self.path_of(session_id).is_file()

    def list(self, limit: int | None = None) -> list[SessionRecord]:
        """按最近更新倒序列出会话。"""
        if not self.root.is_dir():
            return []
        records: list[SessionRecord] = []
        for path in self.root.glob("*.jsonl"):
            meta = self._read_meta(path)
            records.append(
                SessionRecord(
                    session_id=path.stem,
                    path=path,
                    title=str(meta.get("title") or "（无标题）"),
                    created=str(meta.get("created") or ""),
                    updated=str(meta.get("updated") or ""),
                    messages=int(meta.get("messages") or 0),
                    model=str(meta.get("model") or ""),
                    tokens=int(meta.get("tokens") or 0),
                )
            )
        records.sort(key=lambda item: (item.updated, item.session_id), reverse=True)
        return records[:limit] if limit else records

    def load(self, session_id: str) -> tuple[SessionRecord | None, list[dict[str, Any]]]:
        session_id = self._checked_id(session_id)
        if session_id is None:
            return None, []
        path = self.path_of(session_id)
        if not path.is_file():
            return None, []

        messages: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    data = _safe_json(line)
                    if not data or data.get("type") != MESSAGE_TYPE:
                        continue
                    message = data.get("message")
                    if isinstance(message, dict) and message.get("role"):
                        messages.append(message)
        except OSError:
            return None, []

        meta = self._read_meta(path)
        record = SessionRecord(
            session_id=session_id,
            path=path,
            title=str(meta.get("title") or "（无标题）"),
            created=str(meta.get("created") or ""),
            updated=str(meta.get("updated") or ""),
            messages=len(messages),
            model=str(meta.get("model") or ""),
            tokens=int(meta.get("tokens") or 0),
        )
        return record, messages

    def latest(self) -> tuple[SessionRecord | None, list[dict[str, Any]]]:
        records = self.list(limit=1)
        if not records:
            return None, []
        return self.load(records[0].session_id)

    # ---------- 内部 ----------

    def _checked_id(self, session_id: str) -> str | None:
        name = (session_id or "").strip()
        if name.endswith(".jsonl"):
            name = name[: -len(".jsonl")]
        return name if _ID_PATTERN.match(name) else None

    def _read_meta(self, path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                first = handle.readline()
        except OSError:
            return {}
        data = _safe_json(first)
        if not data or data.get("type") != META_TYPE:
            return {}
        return data


def _kind_of(message: dict[str, Any]) -> str:
    if is_agents_message(message):
        return "agents_md"
    return str(message.get("role") or "message")


def _safe_json(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
