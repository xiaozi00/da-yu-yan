"""入参校验的共享约定。

阶段二新增工具时复用本模块，就不必重新踩 SDK 2.x 的两个坑（详见
:mod:`pet_hospital_mcp.tools.list_pets` 的模块文档）：

* **校验只在 ``XxxInput`` 一处**：工具签名里的参数保持宽松（``Any`` +
  ``WithJsonSchema``），否则 SDK 生成的 ``*Arguments`` 模型会抢在校验之前抛出
  Pydantic 原文；
* **未知字段必须自己发现**：宽松模型会把未知字段丢掉，所以要拿
  **原始线缆参数**（``Context`` 里的 ``params["arguments"]``）与允许的参数名集合比对。

因此每个工具的"入参契约"由两部分组成，且必须同源：

* ``PARAM_SPECS``：``参数名 -> (说明, JSON Schema 片段)``，既用于生成对外 schema，
  也用于推导允许的参数名集合；
* ``XxxInput``：``extra="forbid"`` + ``strict=True`` 的 Pydantic 模型，承载全部语义约束。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .errors import ValidationFailure

__all__ = [
    "clean_reason",
    "summarize_validation_error",
    "as_validation_failure",
    "reject_unknown_params",
    "validate_input",
]

ModelT = TypeVar("ModelT", bound=BaseModel)

#: Pydantic 给自定义校验器（``raise ValueError(...)``）加的前缀，属于噪音。
_VALUE_ERROR_PREFIX = "Value error, "


def clean_reason(reason: str) -> str:
    """去掉 Pydantic 附加的 ``Value error, `` 前缀，让原因直接可读。"""
    return reason.removeprefix(_VALUE_ERROR_PREFIX)


def summarize_validation_error(exc: ValidationError) -> list[dict[str, Any]]:
    """只保留字段位置与可读原因。

    ``include_input=False`` / ``include_url=False`` 是关键：错误结构会原样送到
    MCP 客户端，不能把原始输入值（可能含手机号、住址）或 Pydantic 文档链接带出去。
    """
    return [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())) or "<root>",
            "reason": clean_reason(error.get("msg", "invalid value")),
        }
        for error in exc.errors(include_url=False, include_input=False)
    ]


def as_validation_failure(exc: ValidationError) -> ValidationFailure:
    """把 Pydantic 的校验错误收敛成统一的 ``VALIDATION_ERROR``。"""
    violations = summarize_validation_error(exc)
    fields = [item["field"] for item in violations]
    pretty = "；".join(f"{item['field']}: {item['reason']}" for item in violations)
    return ValidationFailure(
        f"入参校验失败（{'、'.join(fields)}）",
        details={"violations": violations, "summary": pretty},
    )


def reject_unknown_params(raw_args: Mapping[str, Any], allowed_names: Collection[str]) -> None:
    """拒绝 ``allowed_names`` 之外的参数名。

    注意入参必须是**原始线缆参数**，而不是经 SDK 参数模型处理后的结果——后者已经把
    未知字段丢掉了。值为 ``null`` 的未知字段同样要被拒绝，所以这里只看键名。
    """
    unknown = sorted(set(raw_args) - set(allowed_names))
    if unknown:
        raise ValidationFailure(
            f"包含未知参数：{'、'.join(unknown)}",
            details={
                "unknownParameters": unknown,
                "allowedParameters": sorted(allowed_names),
            },
        )


def validate_input(
    raw_args: Mapping[str, Any],
    model: type[ModelT],
    allowed_names: Collection[str],
) -> ModelT:
    """先查未知字段，再用 ``model`` 跑全部严格约束。

    ``None`` 视为"未提供"：不少客户端会把未设置的字段序列化成 ``null``，
    而 JSON 也无法表达 NaN/Infinity（序列化时同样退化成 ``null``）。
    """
    reject_unknown_params(raw_args, allowed_names)
    provided = {key: value for key, value in raw_args.items() if value is not None}
    try:
        return model.model_validate(provided)
    except ValidationError as exc:
        raise as_validation_failure(exc) from None
