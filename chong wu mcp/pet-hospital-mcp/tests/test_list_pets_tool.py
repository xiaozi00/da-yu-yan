"""正常调用：请求路径、参数转发、成功输出结构。"""

from __future__ import annotations

import json

import httpx
import pytest

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.server import MCPService
from pet_hospital_mcp.tools.list_pets import TOOL_NAME

from .conftest import FakeBackend, call_list_pets, envelope, sample_data, sample_pet, structured


async def test_forwards_path_and_all_query_parameters(service: MCPService, backend: FakeBackend) -> None:
    """全部 14 个过滤/排序/分页参数都必须原样转发到 GET /api/v1/pets。"""
    result = await call_list_pets(
        service,
        q="肠胃炎",
        name="桃子",
        ownerName="陈先生",
        ownerPhone="18045077845",
        species="猫",
        doctor="郑医生",
        disease="急性肠胃炎",
        status="已康复",
        min=100,
        max=2000,
        sortBy="totalCost",
        order="desc",
        page=2,
        pageSize=50,
    )

    assert result.is_error is False
    assert backend.call_count == 1

    request = backend.last_request
    assert request.method == "GET"
    assert request.url.path == "/api/v1/pets"
    assert dict(request.url.params) == {
        "q": "肠胃炎",
        "name": "桃子",
        "ownerName": "陈先生",
        "ownerPhone": "18045077845",
        "species": "猫",
        "doctor": "郑医生",
        "disease": "急性肠胃炎",
        "status": "已康复",
        "min": "100",
        "max": "2000",
        "sortBy": "totalCost",
        "order": "desc",
        "page": "2",
        "pageSize": "50",
    }


async def test_omits_parameters_that_were_not_provided(service: MCPService, backend: FakeBackend) -> None:
    await call_list_pets(service, species="猫")

    assert dict(backend.last_request.url.params) == {"species": "猫"}


async def test_empty_query_parameters_are_dropped(service: MCPService, backend: FakeBackend) -> None:
    await call_list_pets(service, q="", doctor="   ", species="犬")

    assert dict(backend.last_request.url.params) == {"species": "犬"}


async def test_integral_cost_bounds_are_sent_without_decimal_point(service: MCPService, backend: FakeBackend) -> None:
    await call_list_pets(service, min=0, max=1500.5)

    assert dict(backend.last_request.url.params) == {"min": "0", "max": "1500.5"}


async def test_success_output_contains_exactly_the_backend_data_fields(
    service: MCPService, backend: FakeBackend
) -> None:
    """成功输出必须严格对应 Go 成功响应中的 data，不多不少。"""
    result = await call_list_pets(service)
    payload = structured(result)

    assert set(payload) == {"items", "total", "page", "pageSize", "totalPages", "totalCost"}
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["pageSize"] == 20
    assert payload["totalPages"] == 1
    assert payload["totalCost"] == 917.91

    item = payload["items"][0]
    assert item["id"] == "PET-000201"
    assert item["name"] == "桃子"
    assert item["species"] == "猫"
    assert item["status"] == "已康复"
    assert item["totalCost"] == 917.91
    assert item["visitCount"] == 1
    assert item["records"][0]["diagnosis"] == "急性肠胃炎"
    assert item["charges"][0]["item"] == "X 光检查（两张）"


async def test_text_content_channel_is_json_for_the_model(service: MCPService) -> None:
    result = await call_list_pets(service)

    assert result.content, "工具结果必须带 content"
    text = result.content[0].text  # type: ignore[union-attr]
    assert "PET-000201" in text
    assert json.loads(text)["total"] == 1


@pytest.mark.parametrize("field", ["records", "charges"])
@pytest.mark.parametrize("null_value", [None, []])
async def test_null_or_array_records_and_charges_are_supported(
    settings: Settings,
    backend: FakeBackend,
    field: str,
    null_value: object,
) -> None:
    """Go 的 records / charges 可能是 null 或数组，两种形态都必须兼容。"""
    from pet_hospital_mcp.server import create_service

    backend.set_default(
        httpx.Response(
            200,
            json=envelope(sample_data(items=[sample_pet(**{field: null_value})])),
        )
    )
    service = create_service(settings, transport=backend.transport())

    payload = structured(await call_list_pets(service))

    assert payload["items"][0][field] == null_value


async def test_tool_is_read_only_and_named_in_snake_case(service: MCPService) -> None:
    assert TOOL_NAME == "list_pets"
    assert TOOL_NAME.islower()
    assert "_" in TOOL_NAME
