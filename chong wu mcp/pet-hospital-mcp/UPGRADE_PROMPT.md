# 阶段二升级指南（新增 MCP 工具的标准流程）

> **本阶段未实现任何阶段二工具。** 本文件只描述后续新增工具时应当遵循的流程与约定，
> 不是已经完成的清单。

## 目标

保持"一个工具一个模块"的结构，让新增工具**只写业务代码**：
REST 调用、错误归一化、日志与脱敏、无状态协议装配全部复用现有实现。

## 不改动的部分

* `server.py` 的装配方式（`MCPServer` + `stateless_http=True` + `/mcp`）
* `rest_client.py`（HTTP、超时、有限重试、错误翻译）
* `errors.py`（六个错误码与统一错误结构）
* `logging_config.py`（JSON 日志与敏感字段递归脱敏）
* 协议版本与传输方式（2026-07-28 / 无状态 Streamable HTTP）

## 新增一个工具的步骤

以 `get_pet`（按 ID 查询单个宠物，`GET /api/v1/pets/{id}`）为例：

### 1. 新建模块 `src/pet_hospital_mcp/tools/get_pet.py`

```python
from collections.abc import Mapping
from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError, WithJsonSchema

from ..errors import AdapterError, BackendInvalidResponse, InternalFailure
from ..logging_config import Timer, log_tool_call
from ..validation import summarize_validation_error, validate_input
from .list_pets import _wire_arguments          # 复用原始线缆参数读取

TOOL_NAME = "get_pet"          # snake_case，与函数名一致
PATH = "/api/v1/pets/{pet_id}"

class GetPetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pet_id: str = Field(min_length=1, max_length=40)

# 允许的参数名集合：必须与上面的模型字段同源，用于拒绝未知字段
PARAM_NAMES = frozenset(GetPetInput.model_fields)

# 成功输出模型必须严格对应 Go 成功响应里的 data
class GetPetData(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    ...

async def get_pet_impl(client: RestClient, raw_args: Mapping[str, Any]) -> GetPetData:
    """校验 → 调用上游 → 校验输出；失败一律收敛成统一错误结构。"""
    with Timer() as elapsed:
        try:
            params = validate_input(raw_args, GetPetInput, PARAM_NAMES)   # 未知字段 + 严格校验
            data = await client.get_data(PATH.format(pet_id=params.pet_id))
            result = GetPetData.model_validate(data)
        except AdapterError as exc:                     # 含 ValidationFailure
            log_tool_call(tool_name=TOOL_NAME, params=raw_args,
                          status=exc.error_code.value,
                          duration_ms=elapsed.ms, error=exc.error.message)
            raise exc.to_tool_error() from None
        except ValidationError as exc:
            failure = BackendInvalidResponse("上游宠物医院接口返回的数据不符合约定模型",
                                             details={"violations": summarize_validation_error(exc)})
            log_tool_call(...); raise failure.to_tool_error() from None
        except Exception:                               # noqa: BLE001 - 兜底，不外泄内部异常
            failure = InternalFailure("MCP 适配器内部错误")
            log_tool_call(...); raise failure.to_tool_error() from None
    log_tool_call(tool_name=TOOL_NAME, params=raw_args, status="ok", duration_ms=elapsed.ms)
    return result
```

> 注意 `except AdapterError` 要排在 `except ValidationError` 之前，且 `AdapterError` 分支不能被
> 后面的兜底 `except Exception` 吃掉——否则 `VALIDATION_ERROR` 会被降级成 `INTERNAL_ERROR`。
> 校验失败时**不要**访问上游：`test_strict_inputs.py` 用 `backend.call_count == 0` 断言这一点。

同一模块里的注册函数：

```python
def register(mcp: MCPServer, client: RestClient) -> None:
    @mcp.tool(name=TOOL_NAME, title="...", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    async def get_pet(
        pet_id: Annotated[Any, WithJsonSchema({"type": "string", "description": "档案编号，例如 PET-000001。"})] = None,
        ctx: Context | None = None,
    ) -> GetPetData:
        """<用途 / 适用场景 / 参数 / 返回值 / 错误——四个小节都要写>"""
        return await get_pet_impl(client, _wire_arguments(ctx) or {"pet_id": pet_id})
```

> ⚠️ **参数必须标注为宽松的 `Any`（外面只包 `WithJsonSchema`），不能写成 `str` / `int`。**
>
> SDK 2.x 会用函数签名生成一个 `<工具名>Arguments` 模型，并在调用处理函数**之前**用它校验入参。
> 一旦把注释写成真实类型，会同时坏掉两件事：
>
> 1. `{"pet_id": 123}` 这类输入由该模型抛出 Pydantic 原文
>    （`1 validation error for getPetArguments ... errors.pydantic.dev`），实现细节直接给到客户端；
> 2. **未知字段被静默丢弃**，`{"pet_id":"PET-1","bogus":1}` 会照常返回数据而不是报错。
>
> 正确做法（`list_pets` 已是范例）：
>
> * 对外契约用 `WithJsonSchema` 描述（`type` / `enum` / `minimum` / `maximum` / `description` 一个都不能少），
>   模型看到的信息量不减；
> * 校验全部交给 `XxxInput`（`extra="forbid"` + `strict=True`），统一产出 `VALIDATION_ERROR`；
> * 未知字段通过 `Context` 读取**未校验的线缆参数**来发现：
>   `_wire_arguments(ctx)` 已在 `list_pets.py` 里实现，直接复用（返回 `None` 表示拿不到线缆参数）。
> * 校验辅助全部复用 `pet_hospital_mcp.validation`：
>   `validate_input(raw_args, XxxInput, ALLOWED_NAMES)` 一次搞定未知字段 + 严格校验并抛出
>   统一 `VALIDATION_ERROR`；`summarize_validation_error(exc)` 用于输出模型校验失败；
>   参数名集合与签名必须同源，参照 `PARAM_SPECS` + `PARAM_NAMES` 的写法。
>
> 别忘了照抄 `test_strict_inputs.py` 里的回归用例——**这两个坑单测直接调 `xxx_impl` 时完全看不出来**，
> 必须经由真实 MCP 客户端（`Client(service.server)`）调用才能暴露。

写操作工具（`POST` / `PUT` / `PATCH` / `DELETE`）需要先在 `rest_client.py` 增加
对应的 `post_data` / `patch_data` / `delete_data`，复用同一套超时与重试策略；
写操作的注解应改为 `read_only_hint=False`，并按语义设置
`destructive_hint` / `idempotent_hint`。

### 2. 在 `tools/__init__.py` 的 `register_all()` 里登记

```python
def register_all(mcp: MCPServer, client: RestClient) -> None:
    list_pets.register(mcp, client)
    get_pet.register(mcp, client)     # 新增
```

### 3. 补测试 `tests/test_get_pet_tool.py`

沿用 `tests/conftest.py` 的 `FakeBackend` / `service` 夹具，至少覆盖：

1. 请求路径与全部查询参数正确转发（断言 `backend.last_request.url`）；
2. 入参校验失败返回 `VALIDATION_ERROR` 统一结构，且**不触发**上游请求；
3. 上游 4xx / 5xx / 超时 / 连接失败 / 非法 JSON / 不符合模型 → 对应错误码；
4. 工具注册（工具名、JSON Schema、描述、注解）；
5. 若涉及新参数的枚举取值，补 `ListPetsInput` 那样的逐值参数化用例。

### 4. 更新 `README.md`

* 工具表里补一行；
* 重跑 `pytest -q` 并更新"预期结果"里的用例数；
* 若新增了环境变量或上游端点，一并补进配置表。

## 必须遵守的约定

| 约定 | 说明 |
| --- | --- |
| 工具名 `snake_case` | 与 Python 函数名一致 |
| 不新增适配器私有业务参数 | 只暴露上游 API 真正的查询/路径/请求体字段 |
| 输入模型严格校验 | `extra="forbid"`、`strict=True`、`allow_inf_nan=False`，枚举用**上游真实取值** |
| 输出严格对应 `data` | 成功输出模型的字段与 Go 信封里的 `data` 一一对应 |
| 容忍 `null` 数组 | `records` / `charges` 这类字段允许 `null` 或数组 |
| 统一错误结构 | 所有失败都必须走 `AdapterError` → `ToolError`，结果 `is_error=True` |
| 不泄漏实现细节 | HTTPX / Pydantic / SDK / Python 堆栈只进日志，不进错误消息 |
| 日志必带五字段 | `timestamp` / `tool_name` / `params` / `status` / `duration_ms` |
| 敏感字段脱敏 | `ownerPhone`、`ownerAddr`、`chipNo` 及其 snake_case 写法的递归脱敏已经内建，`params` 直接交给 `log_tool_call` 即可 |
| 不引入旧协议 | 不得添加 `initialize`、`Mcp-Session-Id`、会话存储、`max_sessions`、有状态 SSE 恢复 |
| 不用 FastMCP | 只用 SDK 2.x 的 `mcp.server.MCPServer` |

## 验收

```bash
cd pet-hospital-mcp
pytest -q          # 全部通过
```

再用 **MCP Inspector** 或 SDK 2.x 客户端跑一遍新工具的真实调用，
确认：工具可被 `tools/list` 发现、可被 `tools/call` 调用、失败时 `is_error=True`
且错误结构符合第 6 节的约定。
