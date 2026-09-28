"""输入参数校验：模型级约束 + 工具级统一错误结构。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pet_hospital_mcp.server import MCPService
from pet_hospital_mcp.tools.list_pets import (
    MAX_PAGE_SIZE,
    ORDER_VALUES,
    SORT_BY_VALUES,
    SPECIES_VALUES,
    STATUS_VALUES,
    ListPetsInput,
)

from .conftest import FakeBackend, call_list_pets, error_payload


# --------------------------------------------------------------------------- 模型级


@pytest.mark.parametrize(
    ("payload", "because"),
    [
        ({"page": 0}, "page 必须 >= 1"),
        ({"page": -3}, "page 必须 >= 1"),
        ({"pageSize": 0}, "pageSize 必须 >= 1"),
        ({"pageSize": MAX_PAGE_SIZE + 1}, "pageSize 必须 <= 500"),
        ({"min": -1}, "min 必须非负"),
        ({"max": -0.01}, "max 必须非负"),
        ({"min": 500, "max": 100}, "min 不能大于 max"),
        ({"species": "恐龙"}, "species 必须是真实种类"),
        ({"species": "dog"}, "species 必须是中文真实种类"),
        ({"status": "已出院"}, "status 必须是真实状态"),
        ({"sortBy": "ownerPhone"}, "sortBy 必须是后端支持的排序字段"),
        ({"sortBy": "records"}, "sortBy 必须是后端支持的排序字段"),
        ({"order": "up"}, "order 只能是 asc/desc"),
        ({"order": "升序"}, "order 只能是 asc/desc"),
        ({"page": "3"}, "拒绝字符串形式的整数（strict）"),
        ({"pageSize": 10.5}, "拒绝小数形式的整数（strict）"),
        ({"q": 123}, "拒绝非字符串关键词（strict）"),
        ({"min": float("nan")}, "拒绝 NaN"),
        ({"min": float("inf")}, "拒绝 Infinity"),
        ({"max": float("-inf")}, "拒绝 -Infinity"),
        ({"unknownField": 1}, "拒绝未知字段"),
        ({"owneRname": "张三"}, "拒绝拼写错误的字段名"),
    ],
)
def test_input_model_rejects_invalid_payloads(payload: dict[str, object], because: str) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate(payload)


@pytest.mark.parametrize("species", SPECIES_VALUES)
def test_input_model_accepts_every_backend_species(species: str) -> None:
    assert ListPetsInput.model_validate({"species": species}).species == species


@pytest.mark.parametrize("status", STATUS_VALUES)
def test_input_model_accepts_every_backend_status(status: str) -> None:
    assert ListPetsInput.model_validate({"status": status}).status == status


@pytest.mark.parametrize("sort_by", SORT_BY_VALUES)
def test_input_model_accepts_every_backend_sort_field(sort_by: str) -> None:
    assert ListPetsInput.model_validate({"sortBy": sort_by}).sortBy == sort_by


@pytest.mark.parametrize("order", ORDER_VALUES)
def test_input_model_accepts_every_backend_order(order: str) -> None:
    assert ListPetsInput.model_validate({"order": order}).order == order


def test_input_model_has_no_adapter_private_parameters() -> None:
    """只允许上游 API 的 14 个查询参数，不得新增适配器私有业务参数。"""
    assert set(ListPetsInput.model_fields) == {
        "q",
        "name",
        "ownerName",
        "ownerPhone",
        "species",
        "doctor",
        "disease",
        "status",
        "min",
        "max",
        "sortBy",
        "order",
        "page",
        "pageSize",
    }


def test_min_equal_to_max_is_allowed() -> None:
    assert ListPetsInput.model_validate({"min": 100.0, "max": 100.0}).min == 100.0


def test_zero_bounds_and_float_precision_are_preserved() -> None:
    model = ListPetsInput.model_validate({"min": 0, "max": 12.25})
    assert model.to_query_params() == {"min": 0, "max": 12.25}


def test_nan_and_infinity_never_survive_validation() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            ListPetsInput.model_validate({"max": bad})


# --------------------------------------------------------------------------- 工具级


@pytest.mark.parametrize(
    ("arguments", "expected_field"),
    [
        ({"page": 0}, "page"),
        ({"pageSize": 501}, "pageSize"),
        ({"min": -5}, "min"),
        ({"min": 900, "max": 100}, ""),
        ({"species": "恐龙"}, "species"),
        ({"status": "已出院"}, "status"),
        ({"sortBy": "ownerPhone"}, "sortBy"),
        ({"order": "up"}, "order"),
    ],
)
async def test_tool_returns_unified_validation_error(
    service: MCPService,
    backend: FakeBackend,
    arguments: dict[str, object],
    expected_field: str,
) -> None:
    result = await call_list_pets(service, **arguments)

    payload = error_payload(result)
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["message"]
    assert isinstance(payload["error"]["details"], dict)
    assert backend.call_count == 0, "非法入参不应触发任何上游请求"

    if expected_field:
        assert expected_field in payload["error"]["details"]["summary"]


async def test_validation_error_shape_is_stable(service: MCPService) -> None:
    payload = error_payload(await call_list_pets(service, pageSize=9999))

    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "details"}
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    violations = payload["error"]["details"]["violations"]
    assert violations and violations[0]["field"] == "pageSize"
    assert "500" in violations[0]["reason"]
