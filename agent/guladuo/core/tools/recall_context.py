"""recallContext 工具：在压缩归档里做关键词全文检索。

只做「子串匹配 + 片段截取」，不依赖任何外部服务；
模型自己决定要不要查、查哪个归档。
"""

from __future__ import annotations

from ..context import ContextStore
from .base import Tool

DESCRIPTION = (
    "在历史上下文归档中检索原始对话内容。"
    "当上下文压缩摘要里缺少某个细节（具体文件路径、命令、报错原文、之前的结论）时使用。"
    "提供 text 关键词即可；如果知道归档编号，可以同时给 id 缩小范围。"
)


def build_recall_context_tool(
    store: ContextStore,
    limit: int = 8,
    max_chars: int = 4000,
) -> Tool:
    def recall_context(text: str, id: str | None = None) -> str:
        return store.search(text, id, limit=limit, max_chars=max_chars)

    return Tool(
        name="recallContext",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要检索的关键词或文本片段；多个词用空格分隔，会要求同时命中。",
                },
                "id": {
                    "type": "string",
                    "description": "可选。归档编号，形如 20261022-153000；不填则在所有归档中检索。",
                },
            },
            "required": ["text"],
        },
        handler=recall_context,
    )
