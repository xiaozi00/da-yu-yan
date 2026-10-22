"""模型接入层：端口 + 具体实现。"""

from .openai_client import OpenAICompatibleClient
from .port import LLMClient, StreamDelta, ToolCallDelta

__all__ = ["LLMClient", "OpenAICompatibleClient", "StreamDelta", "ToolCallDelta"]
