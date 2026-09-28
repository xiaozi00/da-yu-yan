"""运行期配置。

全部通过环境变量注入，默认值面向"本地教学"场景：

===========================  ==============================  ==========================
环境变量                      默认值                           说明
===========================  ==============================  ==========================
``PET_HOSPITAL_BASE_URL``     ``http://127.0.0.1:8080``        上游 Go REST API 地址
``MCP_HOST``                  ``127.0.0.1``                    MCP 监听地址（默认只监听本机）
``MCP_PORT``                  ``8765``                         MCP 监听端口
``MCP_PATH``                  ``/mcp``                         MCP Streamable HTTP 端点路径
``PET_HOSPITAL_TIMEOUT``      ``10``                           上游请求超时（秒）
``PET_HOSPITAL_MAX_RETRIES``  ``2``                            上游失败后的重试次数（总尝试 = 1 + N）
``PET_HOSPITAL_TRUST_ENV``    ``false``                        是否读取系统代理环境变量（HTTP_PROXY 等）
``MCP_LOG_LEVEL``             ``INFO``                         日志级别
===========================  ==============================  ==========================
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8765
DEFAULT_MCP_PATH = "/mcp"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_TRUST_ENV = False
DEFAULT_LOG_LEVEL = "INFO"

ENV_BASE_URL = "PET_HOSPITAL_BASE_URL"
ENV_MCP_HOST = "MCP_HOST"
ENV_MCP_PORT = "MCP_PORT"
ENV_MCP_PATH = "MCP_PATH"
ENV_TIMEOUT = "PET_HOSPITAL_TIMEOUT"
ENV_MAX_RETRIES = "PET_HOSPITAL_MAX_RETRIES"
ENV_TRUST_ENV = "PET_HOSPITAL_TRUST_ENV"
ENV_LOG_LEVEL = "MCP_LOG_LEVEL"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})
_ALLOWED_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


class Settings(BaseModel):
    """MCP 服务的不可变配置快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pet_hospital_base_url: str = DEFAULT_BASE_URL
    mcp_host: str = DEFAULT_MCP_HOST
    mcp_port: int = Field(default=DEFAULT_MCP_PORT, ge=1, le=65535)
    mcp_path: str = DEFAULT_MCP_PATH
    request_timeout: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    max_retries: int = Field(default=DEFAULT_MAX_RETRIES, ge=0, le=10)
    trust_env: bool = DEFAULT_TRUST_ENV
    log_level: str = DEFAULT_LOG_LEVEL

    @field_validator("pet_hospital_base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("PET_HOSPITAL_BASE_URL 不能为空")
        if not value.startswith(("http://", "https://")):
            raise ValueError("PET_HOSPITAL_BASE_URL 必须以 http:// 或 https:// 开头")
        return value

    @field_validator("mcp_path")
    @classmethod
    def _check_mcp_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("MCP_PATH 不能为空")
        if not value.startswith("/"):
            value = "/" + value
        return value if value == "/" else value.rstrip("/")

    @field_validator("trust_env", mode="before")
    @classmethod
    def _check_trust_env(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _TRUE_VALUES:
                return True
            if lowered in _FALSE_VALUES:
                return False
            raise ValueError(f"PET_HOSPITAL_TRUST_ENV 必须是布尔值，收到 {value!r}")
        return value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in _ALLOWED_LOG_LEVELS:
            raise ValueError(f"MCP_LOG_LEVEL 必须是 {_ALLOWED_LOG_LEVELS} 之一")
        return value

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """从环境变量构造配置；缺省时回落到默认值。"""
        source = os.environ if env is None else env
        return cls(
            pet_hospital_base_url=source.get(ENV_BASE_URL, DEFAULT_BASE_URL),
            mcp_host=source.get(ENV_MCP_HOST, DEFAULT_MCP_HOST),
            mcp_port=source.get(ENV_MCP_PORT, DEFAULT_MCP_PORT),
            mcp_path=source.get(ENV_MCP_PATH, DEFAULT_MCP_PATH),
            request_timeout=source.get(ENV_TIMEOUT, DEFAULT_TIMEOUT_SECONDS),
            max_retries=source.get(ENV_MAX_RETRIES, DEFAULT_MAX_RETRIES),
            trust_env=source.get(ENV_TRUST_ENV, DEFAULT_TRUST_ENV),
            log_level=source.get(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL),
        )

    @property
    def mcp_endpoint(self) -> str:
        """对外暴露的 MCP 端点路径。"""
        return self.mcp_path
