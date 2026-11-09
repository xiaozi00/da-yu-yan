"""配置加载。

一条规则：**代码里不出现魔法数字**。所有可调参数都在这里声明默认值，
再从 config.ini 覆盖；config.ini 里写错/写漏不会让程序起不来，
只会退回默认值并记一条 warning，启动时由前端提示。

配置文件查找顺序（前者优先）：

1. 命令行 `--config` 指定的路径；
2. 当前工作目录下的 `config.ini`；
3. 本项目根目录（core 的上一层）下的 `config.ini`。

三者都不存在时，会在**当前工作目录**写一份带 mock 值的完整模板，
并把 `Settings.created` 置为 True，前端据此弹出「请修改后重启」的对话框。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILENAME = "config.ini"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / CONFIG_FILENAME

# ---------------------------------------------------------------- 默认值
# 这些常量就是「代码默认值」，config.ini 里的同名项覆盖它们。
DEFAULTS = {
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "request_timeout": 90.0,
    },
    "agent": {
        "system_prompt": "",
        "max_read_chars": 20000,
        "tool_round_limit": 8,
        "context_window": None,
        "compact_threshold": 0.70,
        "max_tool_result_chars": 8000,
        "agents_file": "AGENTS.md",
        "agents_max_chars": 20000,
    },
    "search": {"api_key": ""},
    "tools": {
        "fetch_timeout": 20.0,
        "fetch_max_chars": 8000,
        "fetch_hard_limit": 20000,
        "search_timeout": 30.0,
        "runcli_timeout": 60,
        "runcli_output_limit": 4000,
        "runcli_hard_limit": 20000,
    },
    "context": {
        "entry_limit": 3000,
        "digest_max_archives": 5,
        "digest_summary_chars": 600,
        "digest_topic_chars": 120,
        "recall_limit": 8,
        "recall_max_chars": 4000,
    },
    "mcp": {
        "enabled": True,
        "async": True,
        "call_timeout": 30.0,
        "connect_timeout": 10.0,
    },
    "skills": {
        "max_frontmatter_bytes": 8 * 1024,
        "max_description_chars": 1024,
    },
    "session": {"dir": ".agents/sessions", "keep": 100},
    "ui": {"sidebar_width": 36, "tool_output_chars": 1200},
}


@dataclass(frozen=True)
class Settings:
    """一次运行所需的全部静态配置（含所有可调参数）。"""

    # [llm]
    config_path: Path
    base_url: str
    api_key: str
    model: str
    workspace: Path
    project_root: Path
    # [agent]
    system_prompt: str = ""
    max_read_chars: int = 20000
    tool_round_limit: int = 8
    request_timeout: float = 90.0
    context_window: int | None = None
    compact_threshold: float = 0.70
    max_tool_result_chars: int = 8000
    agents_file: str = "AGENTS.md"
    agents_max_chars: int = 20000
    # [search]
    search_api_key: str = ""
    # [tools]
    fetch_timeout: float = 20.0
    fetch_max_chars: int = 8000
    fetch_hard_limit: int = 20000
    search_timeout: float = 30.0
    runcli_timeout: int = 60
    runcli_output_limit: int = 4000
    runcli_hard_limit: int = 20000
    # [context]
    context_entry_limit: int = 3000
    digest_max_archives: int = 5
    digest_summary_chars: int = 600
    digest_topic_chars: int = 120
    recall_limit: int = 8
    recall_max_chars: int = 4000
    # [mcp]
    mcp_enabled: bool = True
    mcp_async: bool = True
    mcp_call_timeout: float = 30.0
    mcp_connect_timeout: float = 10.0
    # [skills]
    skill_max_frontmatter_bytes: int = 8 * 1024
    skill_max_description_chars: int = 1024
    # [session]
    session_dir: Path = field(default_factory=lambda: Path(".agents") / "sessions")
    session_keep: int = 100
    # [ui]
    sidebar_width: int = 36
    tool_output_chars: int = 1200
    # 运行期状态（不是配置项）
    created: bool = False
    warnings: tuple[str, ...] = ()

    @classmethod
    def load(
        cls,
        config_path: str | os.PathLike[str] | None = None,
        workspace: str | os.PathLike[str] | None = None,
        project_root: str | os.PathLike[str] | None = None,
    ) -> "Settings":
        path, created = resolve_config_path(config_path)
        if created:
            write_default_config(path)

        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(path, encoding="utf-8-sig")
        except (OSError, configparser.Error) as exc:
            raise ConfigError("配置文件读取失败：%s（%s）" % (path, exc)) from exc

        if "llm" not in parser and not created:
            raise ConfigError("配置文件缺少 [llm] 段：%s" % path)

        warnings: list[str] = []
        read = _Reader(parser, warnings)

        base_url = read.text("llm", "base_url", DEFAULTS["llm"]["base_url"])
        api_key = read.text("llm", "api_key", DEFAULTS["llm"]["api_key"])
        model = read.text("llm", "model", DEFAULTS["llm"]["model"])
        # 只有「用户自己的配置」才强制要求填 key；自动生成的模板里带 mock 值
        if not created and not (base_url and api_key and model):
            raise ConfigError("[llm] 的 base_url / api_key / model 不能为空（%s）" % path)

        root = Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
        work = Path(workspace).expanduser().resolve() if workspace else root

        session_dir_raw = read.text("session", "dir", DEFAULTS["session"]["dir"])
        session_dir = Path(session_dir_raw).expanduser()
        if not session_dir.is_absolute():
            session_dir = root / session_dir

        return cls(
            config_path=path,
            base_url=base_url,
            api_key=api_key,
            model=model,
            workspace=work,
            project_root=root,
            system_prompt=read.text("agent", "system_prompt", DEFAULTS["agent"]["system_prompt"]),
            max_read_chars=read.integer("agent", "max_read_chars", DEFAULTS["agent"]["max_read_chars"], minimum=200),
            tool_round_limit=read.integer("agent", "tool_round_limit", DEFAULTS["agent"]["tool_round_limit"], minimum=1),
            request_timeout=read.number("llm", "request_timeout", DEFAULTS["llm"]["request_timeout"], minimum=1.0),
            context_window=_parse_window(read.text("agent", "context_window", "")),
            compact_threshold=read.number(
                "agent", "compact_threshold", DEFAULTS["agent"]["compact_threshold"], minimum=0.1, maximum=1.0
            ),
            max_tool_result_chars=read.integer(
                "agent", "max_tool_result_chars", DEFAULTS["agent"]["max_tool_result_chars"], minimum=200
            ),
            agents_file=read.text("agent", "agents_file", DEFAULTS["agent"]["agents_file"]) or "AGENTS.md",
            agents_max_chars=read.integer(
                "agent", "agents_max_chars", DEFAULTS["agent"]["agents_max_chars"], minimum=1000
            ),
            search_api_key=(
                read.text("search", "api_key", DEFAULTS["search"]["api_key"])
                # 兼容旧版配置里的键名
                or read.text("search", "baidu-qianfan-apikey", "")
            ),
            fetch_timeout=read.number("tools", "fetch_timeout", DEFAULTS["tools"]["fetch_timeout"], minimum=1.0),
            fetch_max_chars=read.integer("tools", "fetch_max_chars", DEFAULTS["tools"]["fetch_max_chars"], minimum=200),
            fetch_hard_limit=read.integer(
                "tools", "fetch_hard_limit", DEFAULTS["tools"]["fetch_hard_limit"], minimum=500
            ),
            search_timeout=read.number("tools", "search_timeout", DEFAULTS["tools"]["search_timeout"], minimum=1.0),
            runcli_timeout=read.integer("tools", "runcli_timeout", DEFAULTS["tools"]["runcli_timeout"], minimum=1),
            runcli_output_limit=read.integer(
                "tools", "runcli_output_limit", DEFAULTS["tools"]["runcli_output_limit"], minimum=200
            ),
            runcli_hard_limit=read.integer(
                "tools", "runcli_hard_limit", DEFAULTS["tools"]["runcli_hard_limit"], minimum=500
            ),
            context_entry_limit=read.integer(
                "context", "entry_limit", DEFAULTS["context"]["entry_limit"], minimum=200
            ),
            digest_max_archives=read.integer(
                "context", "digest_max_archives", DEFAULTS["context"]["digest_max_archives"], minimum=0
            ),
            digest_summary_chars=read.integer(
                "context", "digest_summary_chars", DEFAULTS["context"]["digest_summary_chars"], minimum=50
            ),
            digest_topic_chars=read.integer(
                "context", "digest_topic_chars", DEFAULTS["context"]["digest_topic_chars"], minimum=20
            ),
            recall_limit=read.integer("context", "recall_limit", DEFAULTS["context"]["recall_limit"], minimum=1),
            recall_max_chars=read.integer(
                "context", "recall_max_chars", DEFAULTS["context"]["recall_max_chars"], minimum=200
            ),
            mcp_enabled=read.boolean("mcp", "enabled", DEFAULTS["mcp"]["enabled"]),
            mcp_async=read.boolean("mcp", "async", DEFAULTS["mcp"]["async"]),
            mcp_call_timeout=read.number("mcp", "call_timeout", DEFAULTS["mcp"]["call_timeout"], minimum=1.0),
            mcp_connect_timeout=read.number(
                "mcp", "connect_timeout", DEFAULTS["mcp"]["connect_timeout"], minimum=1.0
            ),
            skill_max_frontmatter_bytes=read.integer(
                "skills", "max_frontmatter_bytes", DEFAULTS["skills"]["max_frontmatter_bytes"], minimum=512
            ),
            skill_max_description_chars=read.integer(
                "skills", "max_description_chars", DEFAULTS["skills"]["max_description_chars"], minimum=64
            ),
            session_dir=session_dir,
            session_keep=read.integer("session", "keep", DEFAULTS["session"]["keep"], minimum=1),
            sidebar_width=read.integer("ui", "sidebar_width", DEFAULTS["ui"]["sidebar_width"], minimum=24, maximum=80),
            tool_output_chars=read.integer(
                "ui", "tool_output_chars", DEFAULTS["ui"]["tool_output_chars"], minimum=200
            ),
            created=created,
            warnings=tuple(warnings),
        )

    # ---------- 展示 ----------

    def masked_key(self, key: str) -> str:
        key = (key or "").strip()
        if len(key) <= 8:
            return "（未配置）" if not key else "****"
        return "%s…%s" % (key[:4], key[-4:])

    def describe(self) -> list[tuple[str, str, str]]:
        """(分组, 参数名, 当前值) —— 给 /config 命令展示。"""
        window = "未知（按 128k 默认显示）" if self.context_window is None else str(self.context_window)
        rows = [
            ("llm", "base_url", self.base_url),
            ("llm", "api_key", self.masked_key(self.api_key)),
            ("llm", "model", self.model),
            ("llm", "request_timeout", "%gs" % self.request_timeout),
            ("agent", "max_read_chars", str(self.max_read_chars)),
            ("agent", "tool_round_limit", str(self.tool_round_limit)),
            ("agent", "context_window", window),
            ("agent", "compact_threshold", "%.0f%%" % (self.compact_threshold * 100)),
            ("agent", "max_tool_result_chars", str(self.max_tool_result_chars)),
            ("agent", "agents_file", "%s（上限 %d 字符）" % (self.agents_file, self.agents_max_chars)),
            ("tools", "fetch", "timeout %gs / 正文 %d–%d" % (self.fetch_timeout, self.fetch_max_chars, self.fetch_hard_limit)),
            ("tools", "search_timeout", "%gs" % self.search_timeout),
            ("tools", "runcli", "timeout %ds / 输出 %d（上限 %d）" % (self.runcli_timeout, self.runcli_output_limit, self.runcli_hard_limit)),
            ("context", "entry_limit", str(self.context_entry_limit)),
            ("context", "digest", "%d 个归档 / 摘要 %d 字符" % (self.digest_max_archives, self.digest_summary_chars)),
            ("context", "recall", "%d 条 / %d 字符" % (self.recall_limit, self.recall_max_chars)),
            ("mcp", "enabled", "是" if self.mcp_enabled else "否"),
            ("mcp", "async", "是（先进界面，后台连接）" if self.mcp_async else "否（启动时阻塞连接）"),
            ("mcp", "timeout", "调用 %gs / 连接 %gs" % (self.mcp_call_timeout, self.mcp_connect_timeout)),
            ("session", "dir", str(self.session_dir)),
            ("session", "keep", str(self.session_keep)),
            ("ui", "sidebar_width", str(self.sidebar_width)),
            ("ui", "tool_output_chars", str(self.tool_output_chars)),
        ]
        if self.search_api_key:
            rows.insert(9, ("search", "api_key", self.masked_key(self.search_api_key)))
        return rows


# ---------------------------------------------------------------- 查找 / 写模板


def resolve_config_path(config_path: str | os.PathLike[str] | None = None) -> tuple[Path, bool]:
    """返回 (配置文件路径, 是否需要新建)。"""
    if config_path:
        path = Path(config_path).expanduser()
        return path.resolve(), not path.is_file()

    for candidate in (Path.cwd() / CONFIG_FILENAME, DEFAULT_CONFIG_PATH):
        if candidate.is_file():
            return candidate.resolve(), False

    return (Path.cwd() / CONFIG_FILENAME).resolve(), True


CONFIG_TEMPLATE = """\
; guladuo (xAgent) 配置文件
; 由程序自动生成于 {time}
;
; ⚠ 当前是 mock 配置：api_key 是假的，直接运行会在第一次提问时报鉴权错误。
;   请把下面的 base_url / api_key / model 改成你自己的，然后**重启程序**。
;
; 编码要求：UTF-8（不要 BOM）。本文件含密钥，请勿提交到公开仓库。
; 所有参数都有内置默认值，删掉某一项就退回默认值；写错不会崩溃，启动时会提示。

[llm]

; 接口基础地址：填到 /v1 为止，不要带 /chat/completions
; OpenAI 官方   -> https://api.openai.com/v1
; DeepSeek      -> https://api.deepseek.com/v1
; 阿里百炼兼容  -> https://dashscope.aliyuncs.com/compatible-mode/v1
; 本地 Ollama   -> http://localhost:11434/v1
base_url = https://api.deepseek.com/v1

; API Key：填服务商后台申请到的密钥。下面这个是 mock，必须修改。
api_key = sk-mock-0000000000000000000000000000

; 模型名称
; DeepSeek 可选 deepseek-chat（V3 通用）/ deepseek-reasoner（R1 推理）
model = deepseek-chat

; 单次模型请求超时（秒）
request_timeout = 90

[agent]

; 系统提示词，可留空
system_prompt =

; 单次 readFile 最多读取的字符数，防止大文件撑爆上下文
max_read_chars = 20000

; 一轮提问内允许的最大模型调用轮次（含工具往返），防止死循环
tool_round_limit = 8

; 模型上下文窗口，用于计算「上下文已占用百分比」
; 可写 128k / 64k / 131072；留空表示不知道，界面按默认 128k 显示并标注「默认」
context_window = 128k

; 上下文占用达到该比例就暂停提问并提示 /compact（0.1–1.0）
compact_threshold = 0.7

; 单个工具返回给模型的最大字符数（MCP 结果等），超出截断
max_tool_result_chars = 8000

; 项目说明文件名：启动时若当前目录存在该文件，会作为一条消息注入会话历史（只注入一次）
agents_file = AGENTS.md

; AGENTS.md 注入上限（字符），超出部分截断
agents_max_chars = 20000

[search]

; 百度千帆 AI 搜索 key（searchweb 工具用），留空则该工具返回提示
api_key =

[tools]

; fetchweb 抓网页
fetch_timeout = 20
fetch_max_chars = 8000
fetch_hard_limit = 20000

; searchweb 百度千帆接口超时（秒）
search_timeout = 30

; runcli 执行本地 CLI
runcli_timeout = 60
runcli_output_limit = 4000
runcli_hard_limit = 20000

[context]

; 压缩时单条消息写进摘要请求的截断长度（归档里仍是完整内容）
entry_limit = 3000

; System Prompt 里注入的归档清单
digest_max_archives = 5
digest_summary_chars = 600
digest_topic_chars = 120

; recallContext 检索
recall_limit = 8
recall_max_chars = 4000

[mcp]

; 是否启用 .mcp.json
enabled = true

; 是否异步加载：true = 先进入界面，连接完成后动态刷新侧栏（推荐）
async = true

; 单次工具调用超时 / 建立连接超时（秒）
call_timeout = 30
connect_timeout = 10

[skills]

; SKILL.md frontmatter 读取上限与 description 长度上限
max_frontmatter_bytes = 8192
max_description_chars = 1024

[session]

; 会话事件日志目录，相对路径按项目根（启动目录）解析
dir = .agents/sessions

; 最多保留多少个会话文件，超出后删除最旧的
keep = 100

[ui]

; 右侧栏宽度（列）
sidebar_width = 36

; 工具卡片展开时最多显示的字符数
tool_output_chars = 1200
"""


def write_default_config(path: Path) -> Path:
    """写一份带 mock 值的完整模板。已存在则不覆盖。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return path
    from datetime import datetime

    text = CONFIG_TEMPLATE.replace("{time}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    temp = path.with_suffix(".ini.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
    return path


# ---------------------------------------------------------------- 读取辅助


class _Reader:
    """带默认值与告警的配置读取器。写错一项不影响其它项。"""

    def __init__(self, parser: configparser.ConfigParser, warnings: list[str]) -> None:
        self._parser = parser
        self._warnings = warnings

    def _raw(self, section: str, key: str) -> str | None:
        if section not in self._parser:
            return None
        if key not in self._parser[section]:
            return None
        return self._parser[section][key]

    def text(self, section: str, key: str, default: str) -> str:
        raw = self._raw(section, key)
        if raw is None:
            return default
        return raw.strip()

    def integer(
        self,
        section: str,
        key: str,
        default: int,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        raw = self._raw(section, key)
        if raw is None or not raw.strip():
            return default
        try:
            value = int(float(raw.strip()))
        except ValueError:
            self._warn(section, key, raw, default)
            return default
        return self._clamp(section, key, value, default, minimum, maximum)

    def number(
        self,
        section: str,
        key: str,
        default: float,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        raw = self._raw(section, key)
        if raw is None or not raw.strip():
            return default
        try:
            value = float(raw.strip())
        except ValueError:
            self._warn(section, key, raw, default)
            return default
        return self._clamp(section, key, value, default, minimum, maximum)

    def boolean(self, section: str, key: str, default: bool) -> bool:
        raw = self._raw(section, key)
        if raw is None or not raw.strip():
            return default
        text = raw.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "是"}:
            return True
        if text in {"0", "false", "no", "n", "off", "否"}:
            return False
        self._warn(section, key, raw, default)
        return default

    def _clamp(self, section, key, value, default, minimum, maximum):
        if minimum is not None and value < minimum:
            self._warning("[%s] %s = %s 小于下限 %s，已按下限处理" % (section, key, value, minimum))
            return type(default)(minimum)
        if maximum is not None and value > maximum:
            self._warning("[%s] %s = %s 大于上限 %s，已按上限处理" % (section, key, value, maximum))
            return type(default)(maximum)
        return value

    def _warn(self, section: str, key: str, raw: str, default) -> None:
        self._warning("[%s] %s = %r 不是合法取值，已用默认值 %s" % (section, key, raw, default))

    def _warning(self, message: str) -> None:
        self._warnings.append(message)


def _parse_window(raw: str | None) -> int | None:
    """解析 context_window：支持 `131072`、`128k`、`1m`；留空表示未知。"""
    text = (raw or "").strip().lower()
    if not text:
        return None
    try:
        if text.endswith("k"):
            value = int(float(text[:-1]) * 1024)
        elif text.endswith("m"):
            value = int(float(text[:-1]) * 1024 * 1024)
        else:
            value = int(float(text))
    except ValueError:
        raise ConfigError(
            "[agent] context_window 写法不对：%r（示例：128k / 131072；留空表示未知）" % raw
        ) from None
    if value <= 0:
        raise ConfigError("[agent] context_window 必须大于 0")
    return value
