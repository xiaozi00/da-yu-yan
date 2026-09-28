"""``list_pets`` 工具：适配 Go 的 ``GET /api/v1/pets``。

设计要点
--------
1. **工具签名扁平**：14 个查询参数一一对应，不新增任何适配器私有业务参数。
   每个参数都在 JSON Schema 里带 ``type`` / ``enum`` / 边界与 ``description``，
   模型能直接看到后端允许值。
2. **校验真相只在一处**：:class:`ListPetsInput` 是唯一的校验来源
   （枚举、区间、``min <= max``、拒绝未知字段 / NaN / Infinity / 类型错误），
   统一转成 ``VALIDATION_ERROR`` 错误结构。
3. **为什么工具参数标注为 ``Any``**：SDK 2.x 会用函数签名生成一个 ``*Arguments``
   模型，并在**调用我们的函数之前**用它拦截参数。若注释写成 ``int`` / ``str | None``，
   ``page="abc"`` 这类输入会先被 Pydantic 拒绝，客户端收到
   ``1 validation error for listPetsArguments ... errors.pydantic.dev`` 这种
   泄漏实现细节的原文；同时未知字段会被该模型**静默丢弃**，我们再也看不到。
   因此这里把签名参数声明为宽松的 ``Any``（仅用 ``WithJsonSchema`` 描述对外契约），
   让所有入参原样到达 :func:`list_pets_impl`，由它给出统一错误结构。
4. **未知字段靠原始入参检测**：宽松模型会丢掉未知字段，所以处理函数通过
   ``Context`` 读取**未校验的线缆参数**（``ctx.request_context.params["arguments"]``），
   与 :data:`PARAM_SPECS` 的键集合比对后拒绝。
5. **成功输出严格对应 Go 响应的 ``data``**：``items`` / ``total`` / ``page`` /
   ``pageSize`` / ``totalPages`` / ``totalCost``，不多不少。
6. **``records`` / ``charges`` 允许 ``null``**：Go 侧真实返回可能是 ``null`` 或数组。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, Mapping

from mcp.server.mcpserver.context import Context
from mcp.types import ToolAnnotations
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    WithJsonSchema,
    model_validator,
)

from ..errors import (
    AdapterError,
    BackendInvalidResponse,
    InternalFailure,
)
from ..logging_config import Timer, log_tool_call
from ..validation import summarize_validation_error, validate_input

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型检查
    from mcp.server import MCPServer

    from ..rest_client import RestClient

__all__ = [
    "TOOL_NAME",
    "PATH",
    "PARAM_SPECS",
    "PARAM_NAMES",
    "SPECIES_VALUES",
    "STATUS_VALUES",
    "SORT_BY_VALUES",
    "ORDER_VALUES",
    "MAX_PAGE_SIZE",
    "ListPetsInput",
    "ListPetsData",
    "list_pets_impl",
    "register",
]

TOOL_NAME = "list_pets"
PATH = "/api/v1/pets"

#: 真实后端允许值（来自 ``GET /api/v1/meta``）。
SPECIES_VALUES = ("犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他")
STATUS_VALUES = ("待就诊", "就诊中", "住院中", "已康复", "慢性病随访")
SORT_BY_VALUES = (
    "id",
    "name",
    "ownerName",
    "species",
    "doctor",
    "disease",
    "status",
    "totalCost",
    "visitCount",
    "createdAt",
    "updatedAt",
)
ORDER_VALUES = ("asc", "desc")

MIN_PAGE_SIZE = 1
MAX_PAGE_SIZE = 500

Species = Literal["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
Status = Literal["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
SortBy = Literal[
    "id",
    "name",
    "ownerName",
    "species",
    "doctor",
    "disease",
    "status",
    "totalCost",
    "visitCount",
    "createdAt",
    "updatedAt",
]
Order = Literal["asc", "desc"]


# --------------------------------------------------------------------------- 对外参数契约

#: 工具允许的**全部**参数：``名字 -> (说明, JSON Schema 片段)``。
#:
#: 这是唯一的口径来源——工具签名（用于生成 JSON Schema）与
#: :func:`list_pets_impl` 的未知字段校验都读它，因此不可能出现
#: "签名里加了参数但校验没跟上" 的漂移。
PARAM_SPECS: dict[str, tuple[str, dict[str, Any]]] = {
    "q": ("全文关键词，空格分词 AND，跨字段检索且包含病历全文，例如 \"猫瘟 疫苗\"。", {"type": "string"}),
    "name": ("按宠物姓名精确/模糊匹配，例如 \"旺财\"。", {"type": "string"}),
    "ownerName": ("按主人姓名查询，例如 \"张三\"。", {"type": "string"}),
    "ownerPhone": ("按主人电话查询，例如 \"13800001111\"。", {"type": "string"}),
    "species": (
        "按种类过滤，只能是：" + "、".join(SPECIES_VALUES) + "。",
        {"type": "string", "enum": list(SPECIES_VALUES)},
    ),
    "doctor": ("按主治医生过滤，例如 \"李医生\"。", {"type": "string"}),
    "disease": ("按疾病/主要诊断过滤，例如 \"急性肠胃炎\"。", {"type": "string"}),
    "status": (
        "按就诊状态过滤，只能是：" + "、".join(STATUS_VALUES) + "。",
        {"type": "string", "enum": list(STATUS_VALUES)},
    ),
    "min": ("总花费下限（元，含），非负；必须不大于 max。", {"type": "number", "minimum": 0}),
    "max": ("总花费上限（元，含），非负；必须不小于 min。", {"type": "number", "minimum": 0}),
    "sortBy": (
        "排序字段，只能是：" + "、".join(SORT_BY_VALUES) + "。",
        {"type": "string", "enum": list(SORT_BY_VALUES)},
    ),
    "order": (
        "排序方向，只能是：asc（升序）、desc（降序）。",
        {"type": "string", "enum": list(ORDER_VALUES)},
    ),
    "page": ("页码，从 1 开始，默认 1。", {"type": "integer", "minimum": 1}),
    "pageSize": (
        f"每页条数，取值 {MIN_PAGE_SIZE}~{MAX_PAGE_SIZE}，默认 20。",
        {"type": "integer", "minimum": MIN_PAGE_SIZE, "maximum": MAX_PAGE_SIZE},
    ),
}

#: 允许的参数名集合（用于拒绝未知字段）。
PARAM_NAMES: frozenset[str] = frozenset(PARAM_SPECS)


def _schema(name: str) -> WithJsonSchema:
    """把 :data:`PARAM_SPECS` 里的一条转成 Pydantic 的 JSON Schema 覆盖标记。"""
    description, fragment = PARAM_SPECS[name]
    return WithJsonSchema({"description": description, **fragment})


# --------------------------------------------------------------------------- 输入模型


class ListPetsInput(BaseModel):
    """``list_pets`` 的输入模型。

    严格模式（``strict=True``）+ ``extra="forbid"``：拒绝未知字段、
    拒绝类型不正确、拒绝 ``NaN`` / ``Infinity``（``allow_inf_nan=False``）。
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    q: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=100)
    ownerName: str | None = Field(default=None, max_length=100)
    ownerPhone: str | None = Field(default=None, max_length=40)
    species: Species | None = None
    doctor: str | None = Field(default=None, max_length=100)
    disease: str | None = Field(default=None, max_length=100)
    status: Status | None = None
    min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sortBy: SortBy | None = None
    order: Order | None = None
    page: int | None = Field(default=None, ge=1)
    pageSize: int | None = Field(default=None, ge=MIN_PAGE_SIZE, le=MAX_PAGE_SIZE)

    @model_validator(mode="after")
    def _check_cost_range(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min 不能大于 max")
        return self

    def to_query_params(self) -> dict[str, Any]:
        """转成上游查询参数：丢弃 ``None`` 与空白字符串，整数值去掉多余小数位。"""
        params: dict[str, Any] = {}
        for key, value in self.model_dump(exclude_none=True).items():
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            elif isinstance(value, float) and value.is_integer():
                value = int(value)
            params[key] = value
        return params


# --------------------------------------------------------------------------- 输出模型


class PetRecord(BaseModel):
    """历史病历。字段与 Go 侧保持一致，未知字段容忍（``extra="allow"``）。"""

    model_config = ConfigDict(extra="allow")

    id: str
    visitDate: str | None = None
    doctor: str | None = None
    diagnosis: str | None = None
    symptoms: str | None = None
    treatment: str | None = None
    prescription: list[str] | None = None
    weightKg: float | None = None
    temperature: float | None = None
    followUp: str | None = None
    charge: float | None = None
    createdAt: str | None = None


class PetCharge(BaseModel):
    """消费明细。"""

    model_config = ConfigDict(extra="allow")

    id: str
    item: str | None = None
    category: str | None = None
    amount: float | None = None
    doctor: str | None = None
    date: str | None = None


class Pet(BaseModel):
    """宠物档案主档。"""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    species: str
    breed: str | None = None
    gender: str | None = None
    ageMonths: int | None = None
    color: str | None = None
    chipNo: str | None = None
    ownerName: str
    ownerPhone: str | None = None
    ownerAddr: str | None = None
    doctor: str
    disease: str
    status: str
    allergy: str | None = None
    note: str | None = None
    records: list[PetRecord] | None = None
    charges: list[PetCharge] | None = None
    totalCost: float
    visitCount: int
    createdAt: str | None = None
    updatedAt: str | None = None


class ListPetsData(BaseModel):
    """成功输出：严格对应 Go 成功响应中的 ``data``。"""

    model_config = ConfigDict(extra="ignore")

    items: list[Pet]
    total: int
    page: int
    pageSize: int
    totalPages: int
    totalCost: float


# --------------------------------------------------------------------------- 校验辅助


def _validate_input(raw_args: Mapping[str, Any]) -> ListPetsInput:
    """未知字段 + 全部严格约束；实现复用 :mod:`pet_hospital_mcp.validation`。"""
    return validate_input(raw_args, ListPetsInput, PARAM_NAMES)


# --------------------------------------------------------------------------- 核心实现


async def list_pets_impl(client: "RestClient", raw_args: Mapping[str, Any]) -> ListPetsData:
    """把一组原始入参跑完全流程：校验 → 调用上游 → 校验输出。

    该函数与 MCP 无关，便于单测直接在任意调用方里复用；所有失败都收敛成
    统一错误结构（``AdapterError`` 家族），并同时写一条结构化日志。
    """
    with Timer() as elapsed:
        try:
            params = _validate_input(raw_args)
            data = await client.get_data(PATH, params=params.to_query_params())
            result = ListPetsData.model_validate(data)
        except AdapterError as exc:
            log_tool_call(
                tool_name=TOOL_NAME,
                params=raw_args,
                status=exc.error_code.value,
                duration_ms=elapsed.ms,
                error=exc.error.message,
            )
            raise exc.to_tool_error() from None
        except ValidationError as exc:
            failure = BackendInvalidResponse(
                "上游宠物医院接口返回的数据不符合约定模型",
                details={"violations": summarize_validation_error(exc)},
            )
            log_tool_call(
                tool_name=TOOL_NAME,
                params=raw_args,
                status=failure.error_code.value,
                duration_ms=elapsed.ms,
                error=failure.error.message,
            )
            raise failure.to_tool_error() from None
        except Exception:  # noqa: BLE001 - 兜底，避免任何内部异常泄漏到客户端
            failure = InternalFailure("MCP 适配器内部错误")
            log_tool_call(
                tool_name=TOOL_NAME,
                params=raw_args,
                status=failure.error_code.value,
                duration_ms=elapsed.ms,
                error=failure.error.message,
            )
            raise failure.to_tool_error() from None

    log_tool_call(
        tool_name=TOOL_NAME,
        params=raw_args,
        status="ok",
        duration_ms=elapsed.ms,
    )
    return result


# --------------------------------------------------------------------------- 原始入参获取


def _wire_arguments(ctx: "Context[Any, Any] | None") -> dict[str, Any] | None:
    """读取**未校验**的线缆参数；取不到时返回 ``None``。

    ``tools/call`` 的 ``params`` 形如 ``{"name": ..., "arguments": {...}}``，
    其中 ``arguments`` 是客户端原样发来的对象——未被 SDK 参数模型过滤，
    因此可以据此发现未知字段。
    """
    if ctx is None:
        return None
    try:
        params = ctx.request_context.params
    except ValueError:  # pragma: no cover - 脱离请求上下文调用时（如直接单测）
        return None
    if not isinstance(params, Mapping):
        return None
    if "arguments" not in params:
        return None
    arguments = params["arguments"]
    if arguments is None:
        return {}
    if not isinstance(arguments, Mapping):
        # 协议层已保证是对象；这里只做防御，交给统一错误结构处理。
        return {"__invalid_arguments__": arguments}
    return dict(arguments)


# --------------------------------------------------------------------------- 注册


def register(mcp: "MCPServer", client: "RestClient") -> None:
    """把 ``list_pets`` 注册到 ``mcp``。

    参数类型刻意**宽松**（``Any`` + :func:`_schema`）：对外 JSON Schema 由
    :data:`PARAM_SPECS` 描述得足够精确，而所有校验（含未知字段）都留给
    :func:`list_pets_impl`，这样任何非法输入都得到同一份 ``VALIDATION_ERROR``
    结构，而不是 SDK 参数模型抛出的 Pydantic 原文。
    """

    @mcp.tool(
        name=TOOL_NAME,
        title="查询宠物医院档案列表",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    async def list_pets(  # noqa: PLR0913 - 参数个数由上游 API 决定
        q: Annotated[Any, _schema("q")] = None,
        name: Annotated[Any, _schema("name")] = None,
        ownerName: Annotated[Any, _schema("ownerName")] = None,
        ownerPhone: Annotated[Any, _schema("ownerPhone")] = None,
        species: Annotated[Any, _schema("species")] = None,
        doctor: Annotated[Any, _schema("doctor")] = None,
        disease: Annotated[Any, _schema("disease")] = None,
        status: Annotated[Any, _schema("status")] = None,
        min: Annotated[Any, _schema("min")] = None,
        max: Annotated[Any, _schema("max")] = None,
        sortBy: Annotated[Any, _schema("sortBy")] = None,
        order: Annotated[Any, _schema("order")] = None,
        page: Annotated[Any, _schema("page")] = None,
        pageSize: Annotated[Any, _schema("pageSize")] = None,
        ctx: Context | None = None,
    ) -> ListPetsData:
        """查询宠物医院的宠物档案列表，支持关键词全文检索、多字段过滤、排序与分页。

        用途
        ----
        从本地 Go 宠物医院服务读取档案：宠物（姓名/种类/品种/性别/月龄/毛色/芯片号）、
        主人（姓名/电话/住址）、主治医生、疾病、就诊状态、过敏史，以及每条档案的
        历史病历 ``records`` 与消费明细 ``charges``。

        适用场景
        --------
        - 按主人电话或姓名查这家人有哪些宠物、花了多少钱；
        - 按种类 / 医生 / 疾病 / 就诊状态盘点病例，例如"住院中的犬"；
        - 用 ``q`` 做跨字段全文检索，例如"猫瘟""肠胃炎 2024"；
        - 用 ``min`` / ``max`` 圈出高消费客户，再用 ``sortBy=totalCost``、``order=desc`` 排序；
        - 分页浏览全量档案。

        参数
        ----
        全部为可选过滤/排序/分页参数，未传即不生效，不会新增任何适配器私有参数。
        各类取值必须使用上游真实允许值（见每个参数的说明），否则返回
        ``VALIDATION_ERROR``。``page`` 从 1 开始，``pageSize`` 取值 1~500；
        ``min`` / ``max`` 非负且 ``min <= max``。

        返回值
        ------
        上游 ``data`` 字段，且仅包含：
        ``items``（宠物档案数组，含 ``records`` / ``charges``，二者可能为 ``null``）、
        ``total``（满足条件的总条数）、``page``、``pageSize``、``totalPages``、
        ``totalCost``（当前结果集的总花费之和，元）。

        错误
        ----
        失败时工具结果为 ``is_error=True``，文本内容为统一错误结构
        ``{"error": {"code": ..., "message": ..., "details": {...}}}``，
        ``code`` 取值为 ``VALIDATION_ERROR`` / ``BACKEND_TIMEOUT`` /
        ``BACKEND_UNAVAILABLE`` / ``BACKEND_API_ERROR`` /
        ``BACKEND_INVALID_RESPONSE`` / ``INTERNAL_ERROR`` 之一。
        """
        raw = _wire_arguments(ctx)
        if raw is None:
            # 拿不到线缆参数（例如脱离请求上下文直接调用）时，退回到签名参数。
            raw = {
                "q": q,
                "name": name,
                "ownerName": ownerName,
                "ownerPhone": ownerPhone,
                "species": species,
                "doctor": doctor,
                "disease": disease,
                "status": status,
                "min": min,
                "max": max,
                "sortBy": sortBy,
                "order": order,
                "page": page,
                "pageSize": pageSize,
            }
        return await list_pets_impl(client, raw)
