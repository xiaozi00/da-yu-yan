"""组合根：把配置、模型客户端、工具、会话装配成一个 Agent。

前端只需要调用 create_agent(settings)，不关心内部对象怎么来的。
测试时可以传入自己的假 LLM。
"""

from __future__ import annotations

from .agent import Agent
from .config import Settings
from .conversation import Conversation
from .llm.openai_client import OpenAICompatibleClient
from .llm.port import LLMClient
from .tools import build_default_registry


def create_agent(settings: Settings, llm: LLMClient | None = None) -> Agent:
    client = llm or OpenAICompatibleClient(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        timeout=settings.request_timeout,
    )
    return Agent(
        llm=client,
        tools=build_default_registry(
            settings.workspace,
            max_read_chars=settings.max_read_chars,
            search_api_key=settings.search_api_key,
        ),
        conversation=Conversation(settings.system_prompt),
        tool_round_limit=settings.tool_round_limit,
    )
