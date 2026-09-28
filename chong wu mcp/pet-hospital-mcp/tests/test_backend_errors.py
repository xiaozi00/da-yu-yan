"""上游异常：4xx/5xx、超时、连接失败、非法 JSON、不符合数据模型。"""

from __future__ import annotations

import json

import httpx
import pytest

from pet_hospital_mcp.server import MCPService

from .conftest import FakeBackend, call_list_pets, envelope, error_payload, sample_data, sample_pet

# 断言错误信息绝不泄漏实现细节
_FORBIDDEN_IN_TEXT = (
    "Traceback",
    "httpx",
    "HTTPX",
    "pydantic",
    "ValidationError",
    "site-packages",
    "rest_client",
    "list_pets.py",
)


def assert_no_internals(payload: dict[str, object]) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for needle in _FORBIDDEN_IN_TEXT:
        assert needle not in text, f"错误结构泄漏了实现细节：{needle}"


# --------------------------------------------------------------------------- HTTP 状态


async def test_backend_4xx_is_reported_as_api_error_without_retry(
    service: MCPService, backend: FakeBackend
) -> None:
    backend.set_default(
        httpx.Response(404, json={"code": 404, "message": "记录不存在: id=PET-999999", "time": "x"})
    )

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_API_ERROR"
    assert payload["error"]["details"]["status"] == 404
    assert payload["error"]["details"]["upstreamMessage"] == "记录不存在: id=PET-999999"
    assert backend.call_count == 1, "4xx 属于客户端错误，不应重试"
    assert_no_internals(payload)


async def test_backend_5xx_is_retried_then_reported(service: MCPService, backend: FakeBackend) -> None:
    backend.set_default(httpx.Response(500, json={"code": 500, "message": "内部错误", "time": "x"}))

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_API_ERROR"
    assert payload["error"]["details"]["status"] == 500
    assert backend.call_count == 2, "max_retries=1 → 共 2 次尝试"
    assert_no_internals(payload)


async def test_retryable_status_succeeds_on_second_attempt(service: MCPService, backend: FakeBackend) -> None:
    backend.push_response(503, json={"code": 503, "message": "服务暂不可用", "time": "x"})

    result = await call_list_pets(service)

    assert result.is_error is False
    assert backend.call_count == 2


async def test_business_error_code_inside_http_200(service: MCPService, backend: FakeBackend) -> None:
    backend.set_default(
        httpx.Response(200, json={"code": 1001, "message": "业务校验失败", "data": None, "time": "x"})
    )

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_API_ERROR"
    assert payload["error"]["details"]["upstreamCode"] == 1001
    assert_no_internals(payload)


# --------------------------------------------------------------------------- 传输异常


async def test_timeout_is_reported_as_backend_timeout(service: MCPService, backend: FakeBackend) -> None:
    backend.set_default(httpx.ReadTimeout("read timed out"))

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_TIMEOUT"
    assert payload["error"]["details"]["reason"] == "timeout"
    assert payload["error"]["details"]["attempts"] == 2
    assert backend.call_count == 2
    assert_no_internals(payload)


async def test_connect_timeout_is_reported_as_backend_timeout(
    service: MCPService, backend: FakeBackend
) -> None:
    backend.set_default(httpx.ConnectTimeout("connect timed out"))

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_TIMEOUT"
    assert_no_internals(payload)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.RemoteProtocolError("server disconnected"),
        httpx.ReadError("network is unreachable"),
    ],
)
async def test_connection_failures_are_reported_as_backend_unavailable(
    service: MCPService, backend: FakeBackend, exc: Exception
) -> None:
    backend.set_default(exc)

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_UNAVAILABLE"
    assert payload["error"]["details"]["reason"] == "connect_failed"
    assert backend.call_count == 2
    assert_no_internals(payload)


# --------------------------------------------------------------------------- 非法响应


async def test_invalid_json_is_reported_as_invalid_response(
    service: MCPService, backend: FakeBackend
) -> None:
    backend.set_default(httpx.Response(200, text="<html>502 Bad Gateway</html>"))

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_INVALID_RESPONSE"
    assert payload["error"]["details"]["reason"] == "invalid_json"
    assert_no_internals(payload)


async def test_json_array_instead_of_object_is_reported_as_invalid_response(
    service: MCPService, backend: FakeBackend
) -> None:
    backend.set_default(httpx.Response(200, json=[1, 2, 3]))

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_INVALID_RESPONSE"
    assert payload["error"]["details"]["reason"] == "not_an_object"


async def test_envelope_without_data_is_reported_as_invalid_response(
    service: MCPService, backend: FakeBackend
) -> None:
    backend.set_default(httpx.Response(200, json={"code": 200, "message": "ok", "time": "x"}))

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_INVALID_RESPONSE"
    assert payload["error"]["details"]["reason"] == "missing_data"


@pytest.mark.parametrize(
    "data",
    [
        {"total": 1, "page": 1, "pageSize": 20, "totalPages": 1, "totalCost": 0.0},  # 缺 items
        sample_data(items=[sample_pet(totalCost="很多")]),  # totalCost 类型不对
        sample_data(items=[{"id": "PET-1", "name": "旺财"}]),  # item 缺必填字段
        sample_data(items="不是数组"),  # items 不是数组
    ],
)
async def test_schema_mismatch_is_reported_as_invalid_response(
    service: MCPService, backend: FakeBackend, data: dict[str, object]
) -> None:
    backend.set_default(httpx.Response(200, json=envelope(data)))

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "BACKEND_INVALID_RESPONSE"
    assert payload["error"]["details"]["violations"]
    assert_no_internals(payload)


# --------------------------------------------------------------------------- 兜底


async def test_unexpected_exception_becomes_internal_error_without_leaking_details(
    service: MCPService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("super-secret-internal-detail")

    monkeypatch.setattr(service.client, "get_data", boom)

    payload = error_payload(await call_list_pets(service))

    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "super-secret-internal-detail" not in str(payload)
    assert_no_internals(payload)
