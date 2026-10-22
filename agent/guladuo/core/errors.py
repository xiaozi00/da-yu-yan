"""领域异常。

core 内部只抛这些异常，前端可以按类型决定如何提示用户。
"""


class GuLaduoError(Exception):
    """所有业务异常的基类。"""


class ConfigError(GuLaduoError):
    """配置文件缺失或字段不合法。"""


class LLMError(GuLaduoError):
    """调用模型服务失败。"""


class ToolError(GuLaduoError):
    """工具执行失败。"""


class ToolNotFound(ToolError):
    """模型请求了未注册的工具。"""


class PathOutsideWorkspace(ToolError):
    """路径越出工作区，属于安全拦截。"""
