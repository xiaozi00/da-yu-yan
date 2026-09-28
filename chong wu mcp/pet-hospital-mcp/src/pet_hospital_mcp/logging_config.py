"""结构化 JSON 日志 + 敏感字段递归脱敏。

每次工具调用输出**恰好一行** JSON，至少包含::

    {"timestamp": ..., "tool_name": ..., "params": ..., "status": ..., "duration_ms": ...}

脱敏规则
--------
键名（大小写不敏感、忽略 ``_``）落在 :data:`SENSITIVE_KEYS` 中的字段，
其值会被递归替换为 ``"***"``，因此 ``ownerPhone`` / ``owner_phone`` /
``ownerPhone`` / ``ownerAddr`` / ``owner_addr`` / ``chipNo`` / ``chip_no``
不论嵌套在多深的对象或数组里都会被遮蔽。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "SENSITIVE_KEYS",
    "REDACTED",
    "redact",
    "setup_logging",
    "log_tool_call",
    "get_logger",
    "Timer",
]

REDACTED = "***"

#: 需要脱敏的字段名（已归一化：仅小写字母数字）。
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "ownerphone",
        "owneraddr",
        "chipno",
    }
)

#: 保留字段，避免与 ``LogRecord`` 自带属性冲突而写坏日志。
_RESERVED_RECORD_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
        "fields",
    }
)


def _normalize_key(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())


def redact(value: Any) -> Any:
    """递归脱敏：字典按键脱敏，列表/元组逐项递归，其余原样返回。"""
    if isinstance(value, dict):
        return {key: (REDACTED if _normalize_key(str(key)) in SENSITIVE_KEYS else redact(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, BaseException):
        # 异常对象本身不入日志体，只保留类型名，避免堆栈泄漏到结构化字段。
        return type(value).__name__
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


class JsonFormatter(logging.Formatter):
    """把 ``LogRecord`` 渲染成单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - 覆写标准接口
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(redact(fields))

        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_KEYS or key in payload or key.startswith("_"):
                continue
            if key in ("exc_info", "stack_info"):
                continue
            payload[key] = redact(value)

        if record.exc_info:
            # 只记录异常类型与消息（服务端排查用）；堆栈由 exc_text 提供时同样收敛。
            exc_type = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            payload["exception"] = exc_type

        return json.dumps(payload, ensure_ascii=False, default=str)


_CONFIGURED = False

#: 这些第三方 logger 的 INFO 噪音（每个请求一行）不进入结构化日志。
_NOISY_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2")


def setup_logging(level: str = "INFO", *, force: bool = False) -> None:
    """安装 JSON 日志处理器（幂等）。

    ``force=True`` 会清掉已有 handler——调用方可借此覆盖 SDK 在
    ``MCPServer(...)`` 构造时通过 ``logging.basicConfig()`` 安装的默认处理器。
    """
    global _CONFIGURED

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    if _CONFIGURED and not force:
        logging.getLogger().setLevel(level.upper())
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_tool_call(
    *,
    tool_name: str,
    params: Any,
    status: str,
    duration_ms: float,
    error: str | None = None,
) -> None:
    """输出一条工具调用日志。

    :param tool_name: 工具名（``snake_case``）
    :param params: 已归一化的入参（会被递归脱敏）
    :param status: ``"ok"`` 或某个 :class:`~pet_hospital_mcp.errors.ErrorCode`
    :param duration_ms: 耗时毫秒
    :param error: 失败时的可读原因（可选）
    """
    fields: dict[str, Any] = {
        "tool_name": tool_name,
        "params": redact(params),
        "status": status,
        "duration_ms": round(duration_ms, 3),
    }
    if error:
        fields["error"] = error

    logger = get_logger("pet_hospital_mcp.tool")
    logger.info("tool_call", extra={"fields": fields})


class Timer:
    """``with Timer() as t: ...`` → ``t.ms`` 为耗时毫秒。"""

    __slots__ = ("_start", "ms")

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.ms = (time.perf_counter() - self._start) * 1000.0
