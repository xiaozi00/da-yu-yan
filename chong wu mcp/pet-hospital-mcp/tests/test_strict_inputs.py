"""严格入参在 **MCP 协议路径** 上的行为（回归防护）。

这里覆盖两个曾经真实发生、且单测直接调用 ``list_pets_impl`` 时**看不见**的缺陷：

1. 工具签名若把参数标注成 ``int`` / ``str | None``，SDK 2.x 生成的 ``*Arguments``
   模型会在调用我们的函数之前接管校验，于是
   - 未知字段被**静默丢弃**（返回了全量数据，而不是报错）；
   - ``page="abc"`` 之类输入把 Pydantic 原文（含 ``errors.pydantic.dev`` 链接）
     直接送到客户端。
2. 因此实现改为：签名参数宽松（``Any`` + ``WithJsonSchema``），校验全部由
   :class:`ListPetsInput` 负责，未知字段靠 ``Context`` 里的原始线缆参数判断。

测试刻意经由真实 SDK 客户端（内存连接 / 真实 HTTP）调用，而不是直接调
``list_pets_impl``——否则这两个缺陷都会溜过去。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp.types import CallToolResult

from pet_hospital_mcp import PROTOCOL_VERSION
from pet_hospital_mcp.server import MCPService
from pet_hospital_mcp.tools.list_pets import (
    MAX_PAGE_SIZE,
    MIN_PAGE_SIZE,
    PARAM_SPECS,
    SPECIES_VALUES,
    STATUS_VALUES,
    TOOL_NAME,
    ListPetsInput,
    list_pets_impl,
)

from .conftest import FakeBackend, call_list_pets, error_payload, structured
from .test_http_transport import _NO_PROXY, _decode_jsonrpc, live_server  # noqa: F401 - 复用夹具

#: 错误文本中绝不允许出现的实现细节。
FORBIDDEN_MARKERS = (
    "pydantic",
    "Pydantic",
    "ValidationError",
    "validation error for",
    "errors.pydantic.dev",
    "type=int_parsing",
    "site-packages",
    "Traceback",
    "httpx",
    "Arguments",
    "list_pets_impl",
    "rest_client",
)


def assert_no_internal_leak(result: CallToolResult) -> str:
    """断言失败结果里没有实现细节，返回正文文本。"""
    assert result.is_error is True
    text = "".join(getattr(block, "text", "") for block in result.content)
    for marker in FORBIDDEN_MARKERS:
        assert marker not in text, f"错误文本泄漏了实现细节：{marker!r} → {text[:300]}"
    return text


# --------------------------------------------------------------------------- 未知字段


@pytest.mark.parametrize(
    ("label", "arguments"),
    [
        ("字符串值", {"unknownField": "x"}),
        ("null 值", {"unknownField": None}),
        ("数字值", {"unknownField": 1}),
        ("多个未知字段", {"page": 1, "foo": 1, "bar": 2}),
        ("大小写不符", {"Page": 1}),
        ("旧写法（下划线）", {"page_size": 1}),
    ],
)
async def test_unknown_parameters_are_rejected_not_silently_ignored(
    service: MCPService,
    backend: FakeBackend,
    label: str,
    arguments: dict[str, Any],
) -> None:
    """未知字段必须报错——尤其不能像宽松参数模型那样把它丢掉后照常查询。"""
    result = await call_list_pets(service, **arguments)
    text = assert_no_internal_leak(result)

    payload = error_payload(result)
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert label  # 让 parametrize 的标签出现在测试 id 里
    assert "未知参数" in payload["error"]["message"]
    assert payload["error"]["details"]["allowedParameters"] == sorted(PARAM_SPECS)
    assert text.startswith(f'Error executing tool {TOOL_NAME}: ')
    assert backend.call_count == 0, "入参非法时不得访问上游"


async def test_unknown_field_reported_even_when_null(service: MCPService) -> None:
    """值为 ``null`` 的未知字段同样要被发现（不能因为"空值"就跳过）。"""
    payload = error_payload(await call_list_pets(service, ghost=None))
    assert payload["error"]["details"]["unknownParameters"] == ["ghost"]


async def test_valid_parameters_are_still_forwarded_after_strictness_change(
    service: MCPService,
    backend: FakeBackend,
) -> None:
    """加严之后，正常调用的转发行为必须一字不差。"""
    result = await call_list_pets(service, species="猫", page=1, pageSize=5)

    assert result.is_error is False
    assert structured(result)["items"][0]["id"] == "PET-000201"
    assert dict(backend.last_request.url.params) == {"species": "猫", "page": "1", "pageSize": "5"}


# --------------------------------------------------------------------------- 类型与取值


@pytest.mark.parametrize(
    ("label", "arguments"),
    [
        ("page 非整数", {"page": "abc"}),
        ("page 为 0", {"page": 0}),
        ("page 为负", {"page": -3}),
        ("page 为小数", {"page": 1.5}),
        ("page 为列表", {"page": [1, 2]}),
        ("pageSize 超上限", {"pageSize": MAX_PAGE_SIZE + 1}),
        ("pageSize 为 0", {"pageSize": 0}),
        ("非法 species", {"species": "恐龙"}),
        ("非法 status", {"status": "复活中"}),
        ("非法 sortBy", {"sortBy": "zzz"}),
        ("非法 order", {"order": "up"}),
        ("min 为负", {"min": -1}),
        ("min 大于 max", {"min": 100, "max": 10}),
        ("q 超长", {"q": "x" * 201}),
        # NaN / Infinity 不放这里：JSON 无法表达它们，SDK 客户端序列化时会变成
        # ``null``（等同于"未提供"）。这两类输入分别由
        # test_nan_and_infinity_are_rejected_by_the_input_model 与
        # test_literal_nan_over_raw_http_is_rejected 覆盖。
    ],
)
async def test_invalid_inputs_never_leak_framework_details(
    service: MCPService,
    backend: FakeBackend,
    label: str,
    arguments: dict[str, Any],
) -> None:
    """所有非法输入都返回同一份统一错误结构，且不含 Pydantic/SDK 原文。"""
    result = await call_list_pets(service, **arguments)
    assert_no_internal_leak(result)

    payload = error_payload(result)
    assert payload["error"]["code"] == "VALIDATION_ERROR", label
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "details"}
    assert payload["error"]["details"]["violations"], "应给出字段级的可读原因"
    assert backend.call_count == 0


async def test_nan_and_infinity_are_rejected_by_the_input_model() -> None:
    """NaN / Infinity 在输入模型层被拒绝（``allow_inf_nan=False``）。

    注：标准 JSON 无法表达 NaN/Infinity，Pydantic 序列化时会把它们变成 ``null``，
    因此这类输入在 SDK 客户端上会退化成"未提供"；但只要它真的以非标准 JSON
    到达服务端，就会在这里被拦下（见 :func:`test_literal_nan_over_raw_http_is_rejected`）。
    """
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(Exception) as excinfo:  # ToolError
            await list_pets_impl(_Client(), {"min": bad})
        assert "VALIDATION_ERROR" in str(excinfo.value)


class _Client:
    """占位上游：被调用即视为失败（NaN 校验必须在访问上游之前完成）。"""

    async def get_data(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("入参非法时不应访问上游")


async def test_explicit_null_is_treated_as_not_provided(service: MCPService, backend: FakeBackend) -> None:
    """显式 ``null`` 视为"未提供"（与 JSON Schema 的 ``default: null`` 一致）。

    这是刻意的选择：不少客户端会把未设置的字段序列化成 ``null``，
    而 NaN 经 JSON 传输也会变成 ``null``，两者在线上无法区分。
    """
    result = await call_list_pets(service, min=None, pageSize=None, species="犬")

    assert result.is_error is False
    assert dict(backend.last_request.url.params) == {"species": "犬"}


# --------------------------------------------------------------------------- JSON Schema


async def test_declared_schema_matches_param_specs_without_drift(service: MCPService) -> None:
    """工具签名的参数集合必须与 :data:`PARAM_SPECS` 完全一致（防漂移）。"""
    tool = await _get_tool(service)
    properties = tool.input_schema["properties"]

    assert set(properties) == set(PARAM_SPECS)
    assert set(ListPetsInput.model_fields) == set(PARAM_SPECS)
    assert "ctx" not in properties, "Context 参数不得出现在对外 schema 里"
    assert tool.input_schema.get("required", []) == []

    for name, (description, fragment) in PARAM_SPECS.items():
        prop = properties[name]
        assert prop["description"] == description, name
        for key, value in fragment.items():
            assert prop[key] == value, f"{name}.{key}"


async def test_schema_advertises_enums_and_bounds(service: MCPService) -> None:
    """Schema 里要能看到真实后端允许值，模型才不必靠猜。"""
    properties = (await _get_tool(service)).input_schema["properties"]

    assert properties["species"]["enum"] == list(SPECIES_VALUES)
    assert properties["status"]["enum"] == list(STATUS_VALUES)
    assert properties["order"]["enum"] == ["asc", "desc"]
    assert properties["page"]["type"] == "integer"
    assert properties["page"]["minimum"] == 1
    assert properties["pageSize"]["minimum"] == MIN_PAGE_SIZE
    assert properties["pageSize"]["maximum"] == MAX_PAGE_SIZE
    assert properties["min"]["type"] == "number"


async def _get_tool(service: MCPService) -> Any:
    """经真实 MCP 客户端拿到 ``list_pets`` 的对外工具定义。"""
    async with Client(service.server, raise_exceptions=True) as client:
        tools = await client.list_tools()
    assert [tool.name for tool in tools.tools] == [TOOL_NAME]
    return tools.tools[0]


# --------------------------------------------------------------------------- 真实 HTTP


async def _raw_call(http: httpx.AsyncClient, base_url: str, raw_arguments: str) -> httpx.Response:
    """发送**原始 JSON 文本**，以便表达标准 JSON 不允许的 NaN / Infinity 字面量。"""
    meta = (
        '"_meta":{"io.modelcontextprotocol/protocolVersion":"'
        + PROTOCOL_VERSION
        + '","io.modelcontextprotocol/clientCapabilities":{}}'
    )
    body = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_pets",'
        '"arguments":' + raw_arguments + "," + meta + "}}"
    )
    return await http.post(
        f"{base_url}/mcp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Method": "tools/call",
            "Mcp-Name": "list_pets",
        },
    )


@pytest.mark.parametrize(
    ("label", "raw_arguments"),
    [
        ("NaN 字面量", '{"min":NaN}'),
        ("Infinity 字面量", '{"max":Infinity}'),
        ("-Infinity 字面量", '{"min":-Infinity}'),
        ("未知字段", '{"bogus":1}'),
        ("类型错误", '{"page":"abc"}'),
    ],
)
async def test_raw_http_inputs_return_unified_error(
    live_server: tuple[str, Any],  # noqa: F811 - 来自 test_http_transport 的夹具
    backend: FakeBackend,
    label: str,
    raw_arguments: str,
) -> None:
    """真实 HTTP 线路上：非标准 JSON 字面量与非法入参都得到统一错误结构。"""
    base_url, _ = live_server

    async with httpx.AsyncClient(**_NO_PROXY) as http:  # type: ignore[arg-type]
        response = await _raw_call(http, base_url, raw_arguments)

    assert response.status_code == 200, label
    result = _decode_jsonrpc(response)["result"]
    assert result["isError"] is True, label

    text = result["content"][0]["text"]
    for marker in FORBIDDEN_MARKERS:
        assert marker not in text, f"{label} 泄漏了 {marker!r}"

    payload = json.loads(text.split(": ", 1)[1])
    assert payload["error"]["code"] == "VALIDATION_ERROR", label
    assert backend.call_count == 0


async def test_literal_nan_over_raw_http_is_rejected(
    live_server: tuple[str, Any],  # noqa: F811 - 来自 test_http_transport 的夹具
) -> None:
    """单独锁住 NaN：这是 JSON 传输层唯一能真正把 NaN 送进来的路径。"""
    base_url, _ = live_server

    async with httpx.AsyncClient(**_NO_PROXY) as http:  # type: ignore[arg-type]
        response = await _raw_call(http, base_url, '{"min":NaN,"pageSize":1}')

    result = _decode_jsonrpc(response)["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"].split(": ", 1)[1])
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert any(v["field"] == "min" for v in payload["error"]["details"]["violations"])
