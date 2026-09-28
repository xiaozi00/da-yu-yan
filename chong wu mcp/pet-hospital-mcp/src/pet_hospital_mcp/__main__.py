"""``python -m pet_hospital_mcp`` 入口。

也支持最小化的命令行覆盖（环境变量仍然是首选配置方式）::

    python -m pet_hospital_mcp --host 127.0.0.1 --port 8765 --base-url http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
from typing import Sequence

from .config import Settings
from .server import create_service


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pet-hospital-mcp",
        description="宠物医院 MCP 服务（无状态 Streamable HTTP / MCP 2026-07-28）",
    )
    parser.add_argument("--host", default=None, help="MCP 监听地址，覆盖 MCP_HOST")
    parser.add_argument("--port", type=int, default=None, help="MCP 监听端口，覆盖 MCP_PORT")
    parser.add_argument("--base-url", default=None, help="上游 Go REST API 地址，覆盖 PET_HOSPITAL_BASE_URL")
    parser.add_argument("--path", default=None, help="MCP 端点路径，覆盖 MCP_PATH（默认 /mcp）")
    parser.add_argument("--log-level", default=None, help="日志级别，覆盖 MCP_LOG_LEVEL")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)

    settings = Settings.from_env()
    overrides = {
        "mcp_host": args.host,
        "mcp_port": args.port,
        "pet_hospital_base_url": args.base_url,
        "mcp_path": args.path,
        "log_level": args.log_level,
    }
    clean_overrides = {key: value for key, value in overrides.items() if value is not None}
    if clean_overrides:
        settings = settings.model_copy(update=clean_overrides)
        # 重新触发校验，避免命令行传入非法值。
        settings = Settings.model_validate(settings.model_dump())

    create_service(settings).run()


if __name__ == "__main__":  # pragma: no cover
    main()
