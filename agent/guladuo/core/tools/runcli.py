"""runcli 工具：让 agent 调用本地已安装的任意命令行程序（教学实验用）。

用 subprocess 以「程序名 + 参数列表」执行（shell=False），把
stdout / stderr / 退出码整理后回传给模型，由模型自行判断结果并
决定下一步。按教学要求暂不做命令白名单/审批机制，命令以运行
guladuo 的当前用户权限执行。

错误处理约定：程序不存在、执行超时、目录无效等都抛 ToolError，
由 registry 转成 ok=False 回传模型；命令本身的非零退出码不算
工具失败——stdout/stderr/exit code 正常回传，让模型自己分析。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..errors import ToolError
from .base import Tool

DEFAULT_TIMEOUT = 30      # 默认超时（秒）
MAX_TIMEOUT = 300         # 允许的最长超时
DEFAULT_OUTPUT_LIMIT = 4000   # 单个流的默认输出上限（字符）
MAX_OUTPUT_LIMIT = 20000


def build_runcli_tool(workspace: str | Path | None = None) -> Tool:
    """构造 runcli 工具。workspace 非空时作为命令的执行目录。"""

    cwd = str(Path(workspace).resolve()) if workspace else None

    def run_cli(
        command: str,
        args: list[str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_output: int = DEFAULT_OUTPUT_LIMIT,
    ) -> str:
        command = (command or "").strip()
        if not command:
            raise ToolError("command 不能为空，请提供要执行的程序名，如 git / python / ping")

        if args is None:
            args = []
        if isinstance(args, str):
            # 模型偶尔会把参数拼成字符串，拒绝并提示正确格式
            raise ToolError(
                "args 必须是字符串数组（如 [\"status\", \"-s\"]），收到的是字符串，"
                "请改用列表传参"
            )
        args = [str(a) for a in args]

        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        timeout = max(1, min(timeout, MAX_TIMEOUT))

        try:
            max_output = int(max_output)
        except (TypeError, ValueError):
            max_output = DEFAULT_OUTPUT_LIMIT
        max_output = max(200, min(max_output, MAX_OUTPUT_LIMIT))

        try:
            completed = subprocess.run(
                [command, *args],
                capture_output=True,
                timeout=timeout,
                cwd=cwd,
                shell=False,          # 明确不用 shell，参数列表直接传给程序
            )
        except subprocess.TimeoutExpired as exc:
            # 超时也尽力带回已产生的输出，帮助模型判断卡在哪
            partial_out = _decode(exc.stdout)[:600]
            partial_err = _decode(exc.stderr)[:600]
            detail = "执行超时（超过 %d 秒），进程已终止" % timeout
            if partial_out:
                detail += "；超时前的 stdout：%s" % partial_out
            if partial_err:
                detail += "；超时前的 stderr：%s" % partial_err
            raise ToolError(detail) from exc
        except FileNotFoundError as exc:
            raise ToolError(
                "找不到程序 %r：请确认已安装且在 PATH 中（Windows 下"
                "内部命令如 dir 需写 command='cmd'、args=['/c', 'dir']）" % command
            ) from exc
        except OSError as exc:
            raise ToolError("执行命令失败：%s" % exc) from exc

        stdout = _decode(completed.stdout)
        stderr = _decode(completed.stderr)
        exit_code = completed.returncode

        out_shown, out_note = _truncate(stdout, max_output)
        err_shown, err_note = _truncate(stderr, max_output)

        lines = ["退出码: %d" % exit_code]
        lines.append("stdout:%s" % out_note)
        lines.append(out_shown if out_shown else "（空）")
        lines.append("stderr:%s" % err_note)
        lines.append(err_shown if err_shown else "（空）")
        if exit_code != 0:
            lines.append("（命令以非零退出码结束，请结合 stderr 判断失败原因）")
        return "\n".join(lines)

    return Tool(
        name="runcli",
        description=(
            "执行本地已安装的命令行程序并返回 stdout、stderr 和退出码"
            "（如 git、python、ping、npm 等）。适合查询系统信息、编译运行代码、"
            "调用其他 CLI 工具。注意：命令以当前用户权限真实执行，请只运行"
            "用户明确要求或对完成当前任务有必要的命令。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "程序名称，如 git / python / ping。"
                        "Windows cmd 内部命令（dir、echo 等）需写 command='cmd'、"
                        "args=['/c', 'dir']",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "参数列表（字符串数组），如 [\"status\", \"-s\"]，可不传",
                },
                "timeout": {
                    "type": "integer",
                    "description": "执行超时秒数（1-300），默认 30",
                },
                "max_output": {
                    "type": "integer",
                    "description": "stdout/stderr 各自返回的最大字符数（200-20000），默认 4000，"
                        "超出部分截断并标注",
                },
            },
            "required": ["command"],
        },
        handler=run_cli,
    )


def _decode(data: bytes | None) -> str:
    """把子进程输出解成文本：先试 UTF-8，失败退 GBK（Windows 中文终端常见），再不行替换坏字节。"""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _truncate(text: str, limit: int) -> tuple[str, str]:
    """按上限截断文本，返回（展示内容，截断说明后缀）。"""
    if len(text) <= limit:
        return text, ""
    dropped = len(text) - limit
    return (
        text[:limit],
        "（已截断：仅显示前 %d 字符，还有 %d 字符未显示）" % (limit, dropped),
    )
