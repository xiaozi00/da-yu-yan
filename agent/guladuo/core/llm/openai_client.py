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
        # 少数 OpenAI 兼容接口不认 stream_options，被拒绝一次后就永久关掉
        self._send_usage = True

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

        stream = self._create(kwargs)

        try:
            for chunk in stream:
                usage = getattr(chunk, "usage", None)
                total = getattr(usage, "total_tokens", None) if usage is not None else None
                total = int(total) if total else None

                if not chunk.choices:
                    # 开了 include_usage 时，最后一个 chunk 只有 usage、没有 choices
                    if total:
                        yield StreamDelta(usage_tokens=total)
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

                yield StreamDelta(
                    content=delta.content or "",
                    tool_calls=tuple(calls),
                    usage_tokens=total,
                )
        except Exception as exc:
            raise LLMError("读取模型流式响应失败：%s" % exc) from exc

    def _create(self, kwargs: dict):
        """发请求；接口不支持 stream_options 时自动退回一次。"""
        if self._send_usage:
            kwargs["stream_options"] = {"include_usage": True}
        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # SDK 的异常类型太多，统一收敛成领域异常
            if not self._send_usage:
                raise LLMError("请求模型失败：%s" % exc) from exc

            self._send_usage = False
            kwargs.pop("stream_options", None)
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as retry_exc:
                raise LLMError("请求模型失败：%s" % retry_exc) from retry_exc
