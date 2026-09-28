"""日志格式与敏感字段脱敏。"""

from __future__ import annotations

import json
import logging

import pytest

from pet_hospital_mcp.errors import (
    AdapterError,
    ErrorCode,
    ErrorResponse,
    InternalFailure,
    ValidationFailure,
)
from pet_hospital_mcp.logging_config import JsonFormatter, log_tool_call, redact
from pet_hospital_mcp.server import MCPService

from .conftest import FakeBackend, call_list_pets, error_payload

# --------------------------------------------------------------------------- 脱敏


def test_redact_hides_camel_case_and_snake_case_sensitive_keys() -> None:
    payload = {
        "ownerPhone": "18045077845",
        "owner_phone": "18045077845",
        "ownerAddr": "西安市未央区长江路247号",
        "owner_addr": "西安市未央区长江路247号",
        "chipNo": "CHIP-031279",
        "chip_no": "CHIP-031279",
        "name": "桃子",
    }

    cleaned = redact(payload)

    assert cleaned["ownerPhone"] == "***"
    assert cleaned["owner_phone"] == "***"
    assert cleaned["ownerAddr"] == "***"
    assert cleaned["owner_addr"] == "***"
    assert cleaned["chipNo"] == "***"
    assert cleaned["chip_no"] == "***"
    assert cleaned["name"] == "桃子"


def test_redact_recurses_into_nested_structures() -> None:
    payload = {
        "items": [
            {
                "ownerPhone": "18045077845",
                "records": [{"chipNo": "CHIP-1", "diagnosis": "急性肠胃炎"}],
                "charges": [{"item": "X 光检查", "ownerAddr": "北京市"}],
            }
        ],
        "meta": {"nested": {"CHIP_NO": "CHIP-2", "OWNERPHONE": "13800001111"}},
    }

    cleaned = redact(payload)
    rendered = json.dumps(cleaned, ensure_ascii=False)

    assert "18045077845" not in rendered
    assert "CHIP-1" not in rendered
    assert "CHIP-2" not in rendered
    assert "北京市" not in rendered
    assert "13800001111" not in rendered
    assert "急性肠胃炎" in rendered
    assert cleaned["items"][0]["ownerPhone"] == "***"


def test_redact_leaves_non_sensitive_values_untouched() -> None:
    assert redact({"species": "猫", "page": 1, "flag": True, "nothing": None}) == {
        "species": "猫",
        "page": 1,
        "flag": True,
        "nothing": None,
    }


def test_redact_replaces_exception_objects_with_their_type_name() -> None:
    assert redact({"boom": ValueError("secret")}) == {"boom": "ValueError"}


# --------------------------------------------------------------------------- 日志行


def test_tool_call_log_line_contains_required_fields(caplog: pytest.LogCaptureFixture) -> None:
    params = {
        "species": "猫",
        "ownerPhone": "18045077845",
        "ownerAddr": "西安市未央区长江路247号",
        "chipNo": "CHIP-031279",
    }

    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tool"):
        log_tool_call(tool_name="list_pets", params=params, status="ok", duration_ms=12.3456)

    assert caplog.records, "应产生一条工具调用日志"
    payload = json.loads(JsonFormatter().format(caplog.records[-1]))

    for key in ("timestamp", "tool_name", "params", "status", "duration_ms"):
        assert key in payload, f"日志缺少字段 {key}"

    assert payload["tool_name"] == "list_pets"
    assert payload["status"] == "ok"
    assert payload["duration_ms"] == 12.346
    assert payload["params"]["species"] == "猫"
    assert payload["params"]["ownerPhone"] == "***"
    assert payload["params"]["ownerAddr"] == "***"
    assert payload["params"]["chipNo"] == "***"


def test_tool_call_log_line_is_single_line_json(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tool"):
        log_tool_call(tool_name="list_pets", params={"q": "多行\n内容"}, status="ok", duration_ms=1.0)

    rendered = JsonFormatter().format(caplog.records[-1])
    assert "\n" not in rendered
    assert json.loads(rendered)["params"]["q"] == "多行\n内容"


async def test_successful_call_writes_one_redacted_log_line(
    service: MCPService, backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tool"):
        await call_list_pets(service, ownerPhone="18045077845", species="猫")

    lines = [json.loads(JsonFormatter().format(record)) for record in caplog.records]
    tool_lines = [line for line in lines if line.get("tool_name") == "list_pets"]
    assert len(tool_lines) == 1

    line = tool_lines[0]
    assert line["status"] == "ok"
    assert line["params"]["species"] == "猫"
    assert line["params"]["ownerPhone"] == "***"
    assert "18045077845" not in json.dumps(line, ensure_ascii=False)


async def test_failed_call_logs_the_error_code(service: MCPService, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tool"):
        await call_list_pets(service, page=0)

    line = json.loads(JsonFormatter().format(caplog.records[-1]))
    assert line["status"] == "VALIDATION_ERROR"
    assert line["error"]


async def test_backend_payload_is_never_logged_verbatim(
    service: MCPService, caplog: pytest.LogCaptureFixture
) -> None:
    """成功响应里的敏感字段不得整段落到日志。"""
    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tool"):
        await call_list_pets(service)

    rendered = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert "18045077845" not in rendered
    assert "西安市未央区长江路247号" not in rendered
    assert "CHIP-031279" not in rendered


# --------------------------------------------------------------------------- 错误结构


def test_error_response_serializes_to_the_agreed_shape() -> None:
    failure = ValidationFailure("入参校验失败", details={"violations": [{"field": "page"}]})

    payload = json.loads(failure.response.to_json())

    assert payload == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "入参校验失败",
            "details": {"violations": [{"field": "page"}]},
        }
    }


def test_error_codes_cover_the_required_set() -> None:
    assert {code.value for code in ErrorCode} == {
        "VALIDATION_ERROR",
        "BACKEND_TIMEOUT",
        "BACKEND_UNAVAILABLE",
        "BACKEND_API_ERROR",
        "BACKEND_INVALID_RESPONSE",
        "INTERNAL_ERROR",
    }


def test_details_default_to_an_empty_object() -> None:
    payload = json.loads(ErrorResponse(error=InternalFailure("boom").error).to_json())
    assert payload["error"]["details"] == {}


def test_adapter_error_keeps_code_and_message() -> None:
    failure = InternalFailure("MCP 适配器内部错误")
    assert isinstance(failure, AdapterError)
    assert failure.error_code is ErrorCode.INTERNAL_ERROR
    assert failure.response.error.message == "MCP 适配器内部错误"


async def test_upstream_pii_never_leaks_into_error_details(
    service: MCPService, backend: FakeBackend
) -> None:
    import httpx

    backend.set_default(httpx.Response(500, text="<html>bad gateway</html>"))

    payload = error_payload(await call_list_pets(service, ownerPhone="18045077845"))

    assert payload["error"]["code"] == "BACKEND_API_ERROR"
    assert "18045077845" not in json.dumps(payload, ensure_ascii=False)
