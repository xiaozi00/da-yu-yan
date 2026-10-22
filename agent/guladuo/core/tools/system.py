"""系统类工具：日期时间、闰年判断、当前目录。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .base import Tool


def get_date() -> str:
    """调用本机 date 命令取日期时间，不走 Python 的 datetime。"""
    if os.name == "nt":
        # Windows：裸跑 date 会进入"输入新日期"的交互模式并阻塞，
        # 必须 /t 才是只读；cmd 输出是 GBK。
        result = subprocess.run(["cmd", "/c", "date /t & time /t"], capture_output=True)
        text = result.stdout.decode("gbk", errors="replace")
    else:
        result = subprocess.run(["date"], capture_output=True)
        text = result.stdout.decode("utf-8", errors="replace")

    text = " ".join(text.split())
    if not text:
        return "获取日期失败，date 命令返回码 %d" % result.returncode
    return text


def is_leap_year(year: int) -> str:
    year = int(year)
    if year < 1:
        return "参数错误：年份必须为正整数，收到 %d" % year
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return "%d 年%s闰年" % (year, "是" if leap else "不是")


def build_system_tools(workspace: Path) -> list[Tool]:
    def get_cwd() -> str:
        return str(workspace)

    return [
        Tool(
            name="getDate",
            description="获取当前电脑的日期和时间。用户询问今天几号、星期几、现在几点时调用。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=get_date,
        ),
        Tool(
            name="isLeapYear",
            description="判断某一年是否是闰年。用户询问某个年份是不是闰年时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "要判断的年份，例如 2024"},
                },
                "required": ["year"],
            },
            handler=is_leap_year,
        ),
        Tool(
            name="getCwd",
            description="获取当前工作区的完整路径。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=get_cwd,
        ),
    ]
