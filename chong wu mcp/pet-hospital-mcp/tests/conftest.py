"""测试公共夹具：假上游 + 服务实例 + 调用辅助。

**不访问真实 Go 服务**：所有上游调用都被 ``httpx.MockTransport`` 拦截。
"""

from __future__ import annotations

import copy
import json
from collections import deque
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp.types import CallToolResult

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.errors import ERROR_PREFIX
from pet_hospital_mcp.server import MCPService, create_service
from pet_hospital_mcp.tools.list_pets import TOOL_NAME

BACKEND_BASE_URL = "http://backend.test"


# --------------------------------------------------------------------------- 假上游


def envelope(data: Any, *, code: int = 200, message: str = "ok") -> dict[str, Any]:
    """构造 Go 统一响应信封。"""
    return {
        "code": code,
        "message": message,
        "data": data,
        "time": "2026-09-17T10:00:00+08:00",
    }


def sample_pet(**overrides: Any) -> dict[str, Any]:
    pet: dict[str, Any] = {
        "id": "PET-000201",
        "name": "桃子",
        "species": "猫",
        "breed": "波斯猫",
        "gender": "母",
        "ageMonths": 101,
        "color": "黑白奶牛",
        "chipNo": "CHIP-031279",
        "ownerName": "陈先生",
        "ownerPhone": "18045077845",
        "ownerAddr": "西安市未央区长江路247号",
        "doctor": "郑医生",
        "disease": "急性肠胃炎",
        "status": "已康复",
        "allergy": "无",
        "records": [
            {
                "id": "MR-SEED-000193-00",
                "visitDate": "2024-01-24",
                "doctor": "郑医生",
                "diagnosis": "急性肠胃炎",
                "symptoms": "呕吐",
                "treatment": "静脉补液 + 消炎",
                "prescription": ["蒙脱石散"],
                "weightKg": 2.88,
                "temperature": 38.65,
                "followUp": "如症状加重请及时复诊",
                "charge": 917.91,
                "createdAt": "2024-01-24T17:12:00Z",
            }
        ],
        "charges": [
            {
                "id": "CH-SEED-000193-00",
                "item": "X 光检查（两张）",
                "category": "检查",
                "amount": 356.67,
                "doctor": "郑医生",
                "date": "2024-01-24",
            }
        ],
        "totalCost": 917.91,
        "visitCount": 1,
        "createdAt": "2026-09-15T13:50:11+08:00",
        "updatedAt": "2026-09-15T13:50:11+08:00",
    }
    pet.update(overrides)
    return pet


def sample_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "items": [sample_pet()],
        "total": 1,
        "page": 1,
        "pageSize": 20,
        "totalPages": 1,
        "totalCost": 917.91,
    }
    data.update(overrides)
    return data


class FakeBackend:
    """记录请求并按队列返回预设响应（或抛出预设异常）的假上游。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._queue: deque[httpx.Response | BaseException] = deque()
        self._default: httpx.Response | BaseException = httpx.Response(200, json=envelope(sample_data()))

    # -- 队列操作 ---------------------------------------------------------

    def push_response(self, *args: Any, **kwargs: Any) -> None:
        self._queue.append(httpx.Response(*args, **kwargs))

    def push_json(self, payload: Any, *, status: int = 200) -> None:
        self._queue.append(httpx.Response(status, json=payload))

    def push_text(self, text: str, *, status: int = 200, content_type: str = "application/json") -> None:
        self._queue.append(httpx.Response(status, text=text, headers={"Content-Type": content_type}))

    def push_exception(self, exc: BaseException) -> None:
        self._queue.append(exc)

    def set_default(self, item: httpx.Response | BaseException) -> None:
        self._default = item

    # -- 断言辅助 ---------------------------------------------------------

    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def last_request(self) -> httpx.Request:
        assert self.requests, "上游尚未收到任何请求"
        return self.requests[-1]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self._queue.popleft() if self._queue else self._default
        if isinstance(item, BaseException):
            raise item
        item.request = request
        return item

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


# --------------------------------------------------------------------------- 夹具


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        pet_hospital_base_url=BACKEND_BASE_URL,
        mcp_host="127.0.0.1",
        mcp_port=8765,
        request_timeout=1.0,
        max_retries=1,
        log_level="WARNING",
    )


@pytest.fixture
def service(settings: Settings, backend: FakeBackend) -> MCPService:
    return create_service(settings, transport=backend.transport())


# --------------------------------------------------------------------------- 调用辅助

_ERROR_PREFIX_TEXT = f"{ERROR_PREFIX}{TOOL_NAME}: "


async def call_list_pets(service: MCPService, **arguments: Any) -> CallToolResult:
    """经由 MCP 协议（内存连接）调用 ``list_pets``。"""
    async with Client(service.server, raise_exceptions=True) as client:
        return await client.call_tool(TOOL_NAME, arguments)


def error_payload(result: CallToolResult) -> dict[str, Any]:
    """从失败的工具结果里取出统一错误结构。"""
    assert result.is_error is True, f"期望 is_error=True，实际 {result.is_error}"
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith(_ERROR_PREFIX_TEXT), text
    return json.loads(text[len(_ERROR_PREFIX_TEXT) :])


def structured(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error is False
    assert result.structured_content is not None
    return copy.deepcopy(result.structured_content)
