"""上游 Go 宠物医院 REST API 的 HTTP 客户端。

职责边界
--------
* 只做 HTTP：拼 URL、传查询参数、超时、有限重试。
* 只做**传输/协议层**的错误归一化：把 httpx 异常、HTTP 状态码、非法 JSON
  统一翻译成 :mod:`pet_hospital_mcp.errors` 里的错误。
* 不认识任何业务字段——``data`` 的建模由工具模块负责，新增工具可直接复用本客户端。

实现说明
--------
每次请求创建一个 :class:`httpx.AsyncClient`。这样连接池不会跨事件循环复用，
无状态服务与单测（每个用例一个 loop）都不会踩到"事件循环已关闭"的坑；
代价是不做 keep-alive——本地教学场景可接受，将来若要长连接，
把它换成一个由 lifespan 管理的共享 client 即可，接口不变。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from .errors import (
    BackendApiError,
    BackendInvalidResponse,
    BackendTimeout,
    BackendUnavailable,
    InternalFailure,
)

__all__ = ["RestClient", "RETRYABLE_STATUS_CODES"]

#: 视为"可重试"的 HTTP 状态码（客户端错误 4xx 一般不重试）。
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

_BACKOFF_BASE_SECONDS = 0.1
_BACKOFF_MAX_SECONDS = 1.0


class RestClient:
    """面向 ``http://host:port`` 的最小 JSON 客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 10.0,
        max_retries: int = 2,
        trust_env: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        #: 默认**不**读取 HTTP_PROXY/HTTPS_PROXY：上游默认是 127.0.0.1，
        #: 把本机流量交给系统代理会让请求以 absolute-form 发出并在部分 ASGI
        #: 服务器上路由失败。需要走企业代理时设置 PET_HOSPITAL_TRUST_ENV=true。
        self.trust_env = trust_env
        #: 仅用于测试注入（``httpx.MockTransport``）。
        self._transport = transport

    # ------------------------------------------------------------------ 公开 API

    async def get_data(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``path``，返回 Go 统一响应信封中的 ``data``。

        :raises BackendTimeout: 超时且重试耗尽
        :raises BackendUnavailable: 连接失败且重试耗尽
        :raises BackendApiError: 上游返回 4xx/5xx，或信封 ``code != 200``
        :raises BackendInvalidResponse: 非法 JSON / 不符合响应模型
        """
        attempts = self.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            is_last = attempt + 1 >= attempts
            try:
                response = await self._send(path, params)
            except httpx.TimeoutException:
                last_error = BackendTimeout(
                    f"调用上游宠物医院接口超时（{self.timeout:g}s，已尝试 {attempt + 1} 次）",
                    details={
                        "path": path,
                        "timeoutSeconds": self.timeout,
                        "attempts": attempt + 1,
                        "reason": "timeout",
                    },
                )
            except httpx.TransportError:
                last_error = BackendUnavailable(
                    f"无法连接上游宠物医院服务（已尝试 {attempt + 1} 次）",
                    details={
                        "path": path,
                        "attempts": attempt + 1,
                        "reason": "connect_failed",
                    },
                )
            else:
                if response.status_code in RETRYABLE_STATUS_CODES and not is_last:
                    last_error = None
                    await self._backoff(attempt)
                    continue
                return self._parse(response)

            if is_last:
                assert last_error is not None
                raise last_error
            await self._backoff(attempt)

        # 循环必然 return 或 raise；保底分支只用于静态检查。
        raise InternalFailure("上游调用未产生结果")

    async def aclose(self) -> None:
        """无持久连接，保留接口以便将来替换为共享 client。"""
        return None

    # ------------------------------------------------------------------ 内部实现

    async def _send(self, path: str, params: dict[str, Any] | None) -> httpx.Response:
        url = f"{self.base_url}{path}"
        timeout = httpx.Timeout(self.timeout)
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "headers": {"Accept": "application/json"},
            "trust_env": self.trust_env,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**kwargs) as client:
            return await client.get(url, params=params or None)

    @staticmethod
    async def _backoff(attempt: int) -> None:
        delay = min(_BACKOFF_BASE_SECONDS * (2**attempt), _BACKOFF_MAX_SECONDS)
        await asyncio.sleep(delay)

    def _parse(self, response: httpx.Response) -> Any:
        """校验 HTTP 状态与 Go 统一响应信封，返回 ``data``。"""
        if not 200 <= response.status_code < 300:
            upstream_code: Any = None
            upstream_message: str | None = None
            try:
                body = response.json()
            except (json.JSONDecodeError, ValueError):
                body = None
            if isinstance(body, dict):
                upstream_code = body.get("code")
                raw_message = body.get("message")
                if isinstance(raw_message, str):
                    upstream_message = raw_message[:500]

            raise BackendApiError(
                f"上游宠物医院接口返回 HTTP {response.status_code}",
                details={
                    "status": response.status_code,
                    "upstreamCode": upstream_code,
                    "upstreamMessage": upstream_message,
                },
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            raise BackendInvalidResponse(
                "上游宠物医院接口返回了非法 JSON",
                details={"status": response.status_code, "reason": "invalid_json"},
            ) from None

        if not isinstance(payload, dict):
            raise BackendInvalidResponse(
                "上游宠物医院接口的响应不是 JSON 对象",
                details={"status": response.status_code, "reason": "not_an_object"},
            )

        code = payload.get("code")
        if code != 200:
            message = payload.get("message")
            raise BackendApiError(
                f"上游宠物医院接口返回业务错误码 {code!r}",
                details={
                    "status": response.status_code,
                    "upstreamCode": code,
                    "upstreamMessage": message[:500] if isinstance(message, str) else None,
                },
            )

        if "data" not in payload:
            raise BackendInvalidResponse(
                "上游宠物医院接口响应缺少 data 字段",
                details={"status": response.status_code, "reason": "missing_data"},
            )

        return payload["data"]
