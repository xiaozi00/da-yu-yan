"""配置加载。

配置路径基于本文件定位（而不是当前工作目录），
这样无论从哪个目录启动、换哪个前端，都能读到同一份 config.ini。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.ini"


@dataclass(frozen=True)
class Settings:
    """一次运行所需的全部静态配置。"""

    base_url: str
    api_key: str
    model: str
    workspace: Path
    system_prompt: str = ""
    max_read_chars: int = 20000
    tool_round_limit: int = 8
    request_timeout: float = 90.0

    @classmethod
    def load(
        cls,
        config_path: str | os.PathLike[str] | None = None,
        workspace: str | os.PathLike[str] | None = None,
    ) -> "Settings":
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        if not path.is_file():
            raise ConfigError("找不到配置文件：%s" % path)

        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        if "llm" not in parser:
            raise ConfigError("配置文件缺少 [llm] 段：%s" % path)

        llm = parser["llm"]
        try:
            base_url = llm["base_url"].strip()
            api_key = llm["api_key"].strip()
            model = llm["model"].strip()
        except KeyError as exc:
            raise ConfigError("[llm] 缺少字段 %s" % exc) from exc

        if not base_url or not api_key or not model:
            raise ConfigError("[llm] 的 base_url / api_key / model 不能为空")

        agent_section = parser["agent"] if "agent" in parser else {}
        system_prompt = (agent_section.get("system_prompt", "") or "").strip()
        max_read_chars = int(agent_section.get("max_read_chars", 20000))
        tool_round_limit = int(agent_section.get("tool_round_limit", 8))

        root = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            workspace=root,
            system_prompt=system_prompt,
            max_read_chars=max_read_chars,
            tool_round_limit=tool_round_limit,
        )
