"""基于 openai SDK 的流式客户端（适配所有 OpenAI 兼容接口）。"""

from __future__ import annotations

from typing import Iterator, Sequence

from openai import OpenAI

from ..errors import LLMError
from .port import StreamDelta, ToolCallDelta


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 90.0,
    ) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def stream(
        self,
        messages: Sequence[dict],
        tools: Sequence[dict] | None = None,
    ) -> Iterator[StreamDelta]:
        kwargs = {
            "model": self._model,
            "messages": list(messages),
            "stream": True,
        }
        if tools:
            kwargs["tools"] = list(tools)

        try:
            stream = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # SDK 的异常类型太多，统一收敛成领域异常
            raise LLMError("请求模型失败：%s" % exc) from exc

        try:
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                calls = []
                for tc in delta.tool_calls or []:
                    function = tc.function
                    calls.append(
                        ToolCallDelta(
                            index=tc.index,
                            id=tc.id,
                            name=function.name if function else None,
                            arguments=function.arguments if function else None,
                        )
                    )

                yield StreamDelta(content=delta.content or "", tool_calls=tuple(calls))
        except Exception as exc:
            raise LLMError("读取模型流式响应失败：%s" % exc) from exc
