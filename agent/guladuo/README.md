# guladuo

一个可换前端的本地文件 Agent。业务逻辑（core）与界面（tui）完全分离，
同一套 core 可以被 TUI、Web、桌面端、移动端复用。

## 目录结构

```
guladuo/
├── core/                   ← 业务核心，禁止 import 任何界面框架
│   ├── config.py           ← 配置加载（路径锚定本文件，不依赖 cwd）
│   ├── errors.py           ← 领域异常
│   ├── events.py           ← 对前端输出的唯一契约（事件）
│   ├── conversation.py     ← 对话历史
│   ├── skills.py           ← Skill 发现、注册、System Prompt 注入
│   ├── agent.py            ← 编排循环：模型 ⇄ 工具
│   ├── factory.py          ← 组合根：装配出一个 Agent
│   ├── llm/
│   │   ├── port.py         ← LLMClient 协议（可替换实现）
│   │   └── openai_client.py← OpenAI 兼容实现
│   └── tools/
│       ├── base.py         ← Tool / ToolResult
│       ├── registry.py     ← 注册、schema 导出、参数解析、分发
│       ├── sandbox.py      ← 工作区路径沙箱
│       ├── system.py       ← getDate / isLeapYear / getCwd
│       ├── files.py        ← 9 个文件类工具
│       ├── search.py       ← searchweb：百度千帆 AI 搜索（联网查询）
│       ├── fetch.py        ← fetchweb：curl 抓网页 + trafilatura 提取正文转 Markdown
│       ├── runcli.py       ← runcli：subprocess 执行本地命令行程序（shell=False）
│       └── load_skill.py   ← loadSkill：按需加载 SKILL.md / 技能内附加文件
├── tui/                    ← Textual 前端
│   ├── app.py              ← App、事件消费、线程调度、斜杠命令
│   ├── widgets.py          ← 消息气泡、可折叠工具卡片、侧栏、帮助弹窗
│   └── app.tcss            ← 样式
├── run.py                  ← 统一入口
├── config.ini              ← 含密钥，不提交
└── requirements.txt
```

## 依赖方向

```
tui ──▶ core ──▶ (openai SDK)
        ▲
   web/desktop/mobile 未来同样只依赖 core
```

`core` 从不 import `tui`。新增前端不需要改动 core 一行代码。

## 运行

```bash
pip install -r requirements.txt
python run.py                          # 启动 TUI
python run.py --workspace D:/some/dir  # 指定工作区（默认当前目录）
python run.py --config other.ini       # 指定配置
```

快捷键：`Enter` 发送，`Esc` 中断生成，`Ctrl+L` 清屏（保留历史），`Ctrl+N` 新会话，
`Ctrl+B` 收起/展开侧栏，`F1` 帮助，`Ctrl+Q` 退出。

命令：`/help`、`/clear`、`/new`、`/tools`、`/cwd`、`/model`、`/quit`。

界面布局：左侧对话流（用户 / 助手气泡 + 可点击折叠的工具卡片），右侧会话信息与
工具清单侧栏，底部状态条（轮次 / 工具调用次数 / 上轮耗时）。

## 事件协议（core → 前端）

前端只需要实现「如何渲染这些事件」：

| 事件 | 含义 | 典型渲染 |
|---|---|---|
| `TurnStarted` | 一轮开始 | 显示用户消息 |
| `TextDelta` | 模型流式增量 | 追加到当前气泡 |
| `ToolCallStarted` | 即将执行工具 | 新增工具卡片 |
| `ToolCallFinished` | 工具执行完成 | 卡片追加结果与耗时 |
| `TurnFinished` | 本轮结束 | 收尾、恢复输入 |
| `AgentFailed` | 本轮异常 | 错误提示 |

## 新增一个前端（以 web 为例）

1. 新建 `web/` 目录，`web/server.py` 里调用 `core.factory.create_agent(settings)`；
2. 遍历 `agent.run(text)`，把事件序列化成 SSE 或 WebSocket 消息；
3. 前端页面按事件类型渲染，逻辑与 TUI 完全一致。

不需要复制任何工具代码、消息拼装逻辑或配置解析。

## 工具扩展

新增工具只需两步：

```python
from core.tools.base import Tool

def my_tool(expr: str) -> str:
    return str(eval(expr, {"__builtins__": {}}, {}))

tool = Tool(
    name="calc",
    description="计算简单算术表达式",
    parameters={
        "type": "object",
        "properties": {"expr": {"type": "string"}},
        "required": ["expr"],
    },
    handler=my_tool,
)
```

然后在 `core/tools/__init__.py` 的 `build_default_registry()` 里 `registry.register(tool)`。

## 安全边界

- 所有文件工具都经 `WorkspaceSandbox.resolve()`，越界路径直接拒绝；
- 读写仅限纯文本扩展名白名单；
- 单次读取上限 `max_read_chars`；
- 一轮内工具调用有轮次上限，避免模型死循环。

## 联网搜索（searchweb）

通过百度千帆 AI 搜索接口（`/v2/ai_search/web_search`）检索互联网，
把参考来源的标题、链接、内容摘要整理后交给模型。

- 密钥放在 `config.ini` 的 `[search]` 小节（`baidu-qianfan-apikey`），代码不硬编码；
- 未配置密钥时工具仍会注册，调用时返回清晰的配置提示；
- 网络异常、接口报错（含鉴权失败 401/403、欠费）、解析失败都会以
  `ok=false` 的工具结果回传给模型，不会中断整轮对话；
- 可选参数：`count`（条数，默认 5）、`recency`（时效过滤 week/month/semiyear/year）。

## 网页正文抓取（fetchweb）

用本地 `curl`（参数列表方式调用，不经 shell）抓取指定 URL 的 HTML，
再用 `trafilatura` 提取正文主内容，转成 Markdown 交给模型——
保留标题、段落、列表、表格和链接，自动去掉导航栏、广告、页眉页脚。

- 可选参数 `max_chars`（500-20000，默认 8000）控制返回长度，超长截断省 token；
- URL 非法、DNS 失败、连接失败、超时、HTTP 4xx/5xx、响应过大、
  解析失败、正文提取失败（如 SPA/验证码页）都会以清晰中文错误
  `ok=false` 回传给模型，不会中断对话；
- 与 searchweb 配合使用：先搜索拿链接，再抓正文深读。

## Skill 发现（Agent Skills 规范）

启动时以**程序的工作目录**为项目根，扫描 `.agents/skills/<skill>/SKILL.md`：

```
<项目根>/.agents/skills/
├── notice/SKILL.md          ← frontmatter: name + description
└── pdf-tools/SKILL.md
```

```markdown
---
name: notice
description: 撰写公司内部通知。触发词：通知、公告。
---

# 撰写通知规范
...
```

- 初始化阶段**只解析 frontmatter**（读到结束的 `---` 就停），不读正文、不加载脚本，
  即使某个技能正文有 5MB，发现全部技能也只需几十毫秒；
- `name` / `description` 都是必填；`name` 需符合小写字母+数字+连字符、≤64 字符；
  不合规的目录会被跳过并记录在 `registry.skipped`（含原因），不影响其它技能；
- 重名时优先保留「目录名与 name 一致」的那个；
- 结果挂在 `agent.skills`（`SkillRegistry`），提供 `names` / `get(name)` / `summary()`，
  TUI 右侧栏显示「技能 N 个」。

### 注入 System Prompt

启动时会把技能清单拼到 `config.ini` 的 `system_prompt` 之后（只有 name 和 description）：

```
你是一个本地文件助手……

You have access to the following skills:

- `notice`: 撰写公司内部通知。触发词：通知、公告。
- `pdf-tools`: 处理 PDF：拆分、合并、提取表格。

Use the `loadSkill` tool to load the full instructions of a skill when needed.
You can also load additional files referenced by a skill.
```

- 没有技能时不追加任何内容，保持原提示词不变；
- 正文不进提示词，模型只知道「有哪些技能、什么时候该加载」；
- 技能较多时这部分仍很短（2 个技能约 260 字符）。

### loadSkill 工具

模型按需加载内容，两级：

| 调用 | 返回 |
|---|---|
| `loadSkill(name="notice")` | `SKILL.md` 全文（含技能目录绝对路径） |
| `loadSkill(name="notice", filename="scripts/x.py")` | 只返回该文件，不重复返回 SKILL.md |

`filename` 解析顺序：**绝对路径 → 相对技能目录 → 相对项目根 → 递归按文件名/路径后缀搜索**。
只写文件名且目录内有多个同名文件时，返回候选相对路径清单让模型自己选。

安全：所有读取都 `resolve()` 后校验必须落在该技能目录内，`../`、指向外部的绝对路径、
符号链接逃逸一律拒绝；二进制文件、超大文件（>200k 字符）分别报错/截断。

### 动态加载原则

技能正文进入上下文**只有一个入口**：模型主动调用 `loadSkill`。

| 时机 | 上下文里有什么 |
|---|---|
| 启动 / 首次请求 | 只有 `name` + `description`（提示词里的技能清单） |
| 模型判断需要该技能 | 调用 `loadSkill`，正文作为一条 **tool 消息**进入上下文 |
| 后续轮次 | 已加载的正文随对话历史保留，无需重复加载 |

这条约定靠结构保证，不是靠提示词祈祷：

- `discover_skills()` 只解析 frontmatter（读到结束的 `---` 就停），启动开销与技能正文大小无关；
- `to_prompt()` 只输出 `- name: description` 一行，正文一个字都不进 System Prompt；
- `loadSkill` 是普通工具，走的是现有的工具调用循环，`core/agent.py` 的主流程一行没改——
  技能不需要任何特殊的调度逻辑，也不会挤占别的工具。

**与 runcli / 文件工具协同**：`loadSkill` 返回结果里带技能目录的绝对路径，
模型可以直接用 `runcli` 执行技能自带的脚本；脚本产物再用 `createFile` / `editFile`
写回工作区。三者共享同一个工具循环与同一轮次预算，可以任意组合、连续调用。

> 注意：技能目录在**项目根**（启动时 cwd）下，若 `-w` 指定的工作区是别的目录，
> 技能目录就在工作区之外，`readFile` 会被沙箱拦下——此时读技能文件请统一走 `loadSkill`，
> 执行技能脚本用 `loadSkill` 返回的绝对路径。

## 本地命令执行（runcli）

用 `subprocess` 以「程序名 + 参数列表」执行本地任意 CLI（`shell=False`，
不拼接命令字符串），把 stdout、stderr、退出码回传给模型自行判断。

- 可选参数：`args`（字符串数组）、`timeout`（1-300 秒，默认 30）、
  `max_output`（stdout/stderr 各自上限 200-20000 字符，默认 4000，超出截断并标注）；
- 编码自动识别（UTF-8 → GBK 兜底），中文程序输出不乱码；
- 超时会把进程终止，并带回超时前已产生的输出帮助模型定位；
- 非零退出码**不算工具失败**，正常回传三要素并提示模型结合 stderr 分析；
- Windows cmd 内部命令（dir、echo 等）需写 `command='cmd'、args=['/c','dir']`；
- ⚠️ 按教学要求**未做命令白名单/审批机制**，命令以运行 guladuo 的当前
  用户权限真实执行，仅适用于受控实验环境。
