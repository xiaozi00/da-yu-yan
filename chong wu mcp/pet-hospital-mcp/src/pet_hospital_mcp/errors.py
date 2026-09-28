"""统一结构化错误。

对外只暴露一种错误形状（MCP 客户端在 ``tools/call`` 的失败结果里看到它）::

    {"error": {"code": "ERROR_CODE", "message": "可读错误信息", "details": {}}}

设计要点
--------
* HTTPX / Pydantic / SDK / Python 堆栈**永不**出现在 ``message`` 或 ``details`` 中，
  只写进服务端日志。
* 内部用 :class:`AdapterError` 家族携带语义，再由
  :func:`AdapterError.to_tool_error` 转成 SDK 2.x 规定的 ``ToolError``，
  从而得到 ``is_error=True`` 的工具调用失败状态。
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ErrorCode",
    "ErrorDetail",
    "ErrorResponse",
    "AdapterError",
    "ValidationFailure",
    "BackendTimeout",
    "BackendUnavailable",
    "BackendApiError",
    "BackendInvalidResponse",
    "InternalFailure",
    "ERROR_PREFIX",
]


class ErrorCode(str, Enum):
    """稳定、可枚举的错误码。"""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    BACKEND_API_ERROR = "BACKEND_API_ERROR"
    BACKEND_INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    """错误正文。"""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """错误信封：``{"error": {...}}``。"""

    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail

    def to_json(self) -> str:
        """紧凑 JSON（``ensure_ascii=False``，便于日志与模型阅读中文）。"""
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))


class AdapterError(Exception):
    """适配器内部异常基类：携带一个 :class:`ErrorResponse`。"""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: ErrorCode | None = None,
    ) -> None:
        super().__init__(message)
        self.error = ErrorDetail(
            code=code or self.code,
            message=message,
            details=details or {},
        )

    @property
    def error_code(self) -> ErrorCode:
        return self.error.code

    @property
    def response(self) -> ErrorResponse:
        return ErrorResponse(error=self.error)

    def to_json(self) -> str:
        return self.response.to_json()

    def to_tool_error(self) -> Exception:
        """转换成 SDK 2.x 的 ``ToolError``。

        SDK 会把工具体内抛出的异常包成
        ``ToolError("Error executing tool <name>: <message>")``，最终形成
        ``CallToolResult(is_error=True, content=[TextContent(text=...)], structured_content=None)``。
        这里把统一错误结构（紧凑 JSON）作为 message 传出去，客户端即可在
        ``content[0].text`` 中读到它（前缀为 :data:`ERROR_PREFIX`）。
        """
        # 延迟导入：让本模块在未安装 SDK 时也能被静态分析/单测直接使用。
        from mcp.server.mcpserver.exceptions import ToolError

        return ToolError(self.to_json())


ERROR_PREFIX = "Error executing tool "


class ValidationFailure(AdapterError):
    """入参不满足 :class:`~pet_hospital_mcp.tools.list_pets.ListPetsInput` 的约束。"""

    code = ErrorCode.VALIDATION_ERROR


class BackendTimeout(AdapterError):
    """调用上游 Go REST API 超时（已在重试次数内尝试过）。"""

    code = ErrorCode.BACKEND_TIMEOUT


class BackendUnavailable(AdapterError):
    """上游 Go REST API 不可达（连接被拒绝、DNS 失败、连接中断等）。"""

    code = ErrorCode.BACKEND_UNAVAILABLE


class BackendApiError(AdapterError):
    """上游返回了明确的 HTTP 错误（4xx/5xx）或业务错误码。"""

    code = ErrorCode.BACKEND_API_ERROR


class BackendInvalidResponse(AdapterError):
    """上游返回了非法 JSON，或响应体不符合约定的数据模型。"""

    code = ErrorCode.BACKEND_INVALID_RESPONSE


class InternalFailure(AdapterError):
    """兜底：适配器自身未预期到的异常。"""

    code = ErrorCode.INTERNAL_ERROR
