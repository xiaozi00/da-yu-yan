"""工具注册、工具名、JSON Schema，以及"没有使用 v1 的 FastMCP"的静态检查。"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

import pet_hospital_mcp
from pet_hospital_mcp.server import MCPService
from pet_hospital_mcp.tools.list_pets import (
    MAX_PAGE_SIZE,
    MIN_PAGE_SIZE,
    TOOL_NAME,
)

from .conftest import FakeBackend, call_list_pets

EXPECTED_PARAMETERS = {
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

PACKAGE_ROOT = Path(pet_hospital_mcp.__file__).resolve().parent


def test_service_uses_sdk2_mcpserver(service: MCPService) -> None:
    assert isinstance(service.server, MCPServer)
    assert type(service.server).__module__.startswith("mcp.server.mcpserver")


def test_package_never_imports_fastmcp() -> None:
    """MCP 2026-07-28 / SDK 2.x：不得使用或导入 ``mcp.server.fastmcp.FastMCP``。"""
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("mcp.server.fastmcp"):
                offenders.append(f"{path.name}: from {node.module} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("mcp.server.fastmcp"):
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.Name) and node.id == "FastMCP":
                offenders.append(f"{path.name}: FastMCP")
    assert offenders == []

    assert "mcp.server.fastmcp" not in sys.modules


async def test_exactly_one_tool_is_registered(service: MCPService) -> None:
    """本阶段只保留一个 MCP 工具。"""
    async with Client(service.server, raise_exceptions=True) as client:
        tools = await client.list_tools()

    assert [tool.name for tool in tools.tools] == [TOOL_NAME]


async def test_tool_metadata_and_json_schema(service: MCPService) -> None:
    async with Client(service.server, raise_exceptions=True) as client:
        tools = await client.list_tools()

    tool = tools.tools[0]
    assert tool.name == "list_pets"
    assert tool.name.islower(), "工具名必须使用 snake_case"
    assert tool.title == "查询宠物医院档案列表"

    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.open_world_hint is False

    schema = tool.input_schema
    assert schema["type"] == "object"
    assert set(schema["properties"]) == EXPECTED_PARAMETERS
    assert schema.get("required", []) == [], "所有过滤/排序/分页参数都应是可选的"

    # 每个参数都带 description，模型才能看到后端允许值
    for name, prop in schema["properties"].items():
        assert prop.get("description"), f"参数 {name} 缺少 description"

    assert "犬" in schema["properties"]["species"]["description"]
    assert "慢性病随访" in schema["properties"]["status"]["description"]
    assert "totalCost" in schema["properties"]["sortBy"]["description"]
    assert "asc" in schema["properties"]["order"]["description"]
    assert str(MIN_PAGE_SIZE) in schema["properties"]["pageSize"]["description"]
    assert str(MAX_PAGE_SIZE) in schema["properties"]["pageSize"]["description"]


async def test_tool_description_documents_purpose_parameters_and_result(service: MCPService) -> None:
    async with Client(service.server, raise_exceptions=True) as client:
        tools = await client.list_tools()

    description = tools.tools[0].description or ""
    for keyword in ("用途", "适用场景", "参数", "返回值", "错误"):
        assert keyword in description, f"工具描述缺少「{keyword}」小节"
    assert "records" in description and "charges" in description
    assert "VALIDATION_ERROR" in description


async def test_output_schema_matches_go_data_envelope(service: MCPService) -> None:
    async with Client(service.server, raise_exceptions=True) as client:
        tools = await client.list_tools()

    output_schema = tools.tools[0].output_schema
    assert output_schema is not None
    assert set(output_schema["required"]) == {
        "items",
        "total",
        "page",
        "pageSize",
        "totalPages",
        "totalCost",
    }


async def test_tool_call_returns_structured_content(service: MCPService, backend: FakeBackend) -> None:
    result = await call_list_pets(service, species="猫")

    assert result.is_error is False
    assert result.structured_content is not None
    assert set(result.structured_content) == {
        "items",
        "total",
        "page",
        "pageSize",
        "totalPages",
        "totalCost",
    }


@pytest.mark.parametrize("protocol_version", ["2026-07-28"])
async def test_in_memory_client_negotiates_2026_07_28(service: MCPService, protocol_version: str) -> None:
    async with Client(service.server, raise_exceptions=True) as client:
        assert client.protocol_version == protocol_version
