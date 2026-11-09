# guladuo

一个可换前端的本地文件 Agent。业务逻辑（core）与界面（tui）完全分离，
同一套 core 可以被 TUI、Web、桌面端、移动端复用。

## 目录结构

```
guladuo/
├── core/                   ← 业务核心，禁止 import 任何界面框架
│   ├── config.py           ← 配置加载（全量参数 + 默认值 + 自动生成模板）
│   ├── errors.py           ← 领域异常
│   ├── events.py           ← 对前端输出的唯一契约（事件）
│   ├── conversation.py     ← 对话历史
│   ├── agents_md.py        ← AGENTS.md：一次注入、全程复用
│   ├── sessions.py         ← 会话事件日志（JSONL）：保存 / 列表 / 恢复
│   ├── context.py          ← 上下文：token 估算、归档、检索、压缩 Prompt
│   ├── skills.py           ← Skill 发现、注册、System Prompt 注入
│   ├── agent.py            ← 编排循环：模型 ⇄ 工具
│   ├── factory.py          ← 组合根：装配出一个 Agent
│   ├── llm/
│   │   ├── port.py         ← LLMClient 协议（可替换实现）
│   │   └── openai_client.py← OpenAI 兼容实现
│   ├── mcp/                ← MCP 客户端（.mcp.json → 动态注册外部工具）
│   │   ├── config.py       ← .mcp.json 解析与校验（仅 Streamable HTTP）
│   │   ├── connection.py   ← 单个 server 的长连接（asyncio 线程 + 同步桥）
│   │   └── manager.py      ← 多 server 管理、异步加载、命名空间、结果整理
│   └── tools/
│       ├── base.py         ← Tool / ToolResult
│       ├── registry.py     ← 注册、schema 导出、参数解析、分发
│       ├── sandbox.py      ← 工作区路径沙箱
│       ├── system.py       ← getDate / isLeapYear / getCwd
│       ├── files.py        ← 9 个文件类工具
│       ├── search.py       ← searchweb：百度千帆 AI 搜索（联网查询）
│       ├── fetch.py        ← fetchweb：curl 抓网页 + trafilatura 提取正文转 Markdown
│       ├── runcli.py       ← runcli：subprocess 执行本地命令行程序（shell=False）
│       ├── load_skill.py   ← loadSkill：按需加载 SKILL.md / 技能内附加文件
│       ├── create_skill.py ← createSkill：按自然语言要求创建新技能
│       └── recall_context.py ← recallContext：在压缩归档里做关键词检索
├── tui/                    ← Textual 前端
│   ├── app.py              ← App、事件消费、线程调度、斜杠命令、会话管理
│   ├── widgets.py          ← 气泡、工具卡片、命令补全、侧栏、各种弹窗
│   └── app.tcss            ← 样式
├── run.py                  ← 统一入口
├── mcp_doctor.py           ← MCP 体检：连接 + 列工具 + 试调一次
├── mcp.json                ← 可直接用的 MCP 配置样例（复制成 .mcp.json）
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

### 首次运行：自动生成 config.ini

启动时按这个顺序找配置：`--config` → `./config.ini`（当前目录）→ 项目内 `config.ini`。
**三者都不存在**时，会在当前目录写一份带 mock 值的完整模板，并弹出对话框提醒：

```
文件：D:\skill\config.ini
里面的模型与 API Key 都是 mock 值，直接运行无法正常工作。
请修改 [llm] base_url / api_key / model 后重新启动程序。
```

对话框里可以选择「退出，去修改配置」（默认）或「仍然进入（只看看界面）」。

配置文件里所有参数都有内置默认值：**删掉某一项就退回默认值，写错不会崩溃**，
只会在启动时提示「哪一项不合法、已用默认值」。完整清单见
`core/config.py` 的 `CONFIG_TEMPLATE`（生成的模板自带逐项注释）。

### 快捷键

`Enter` 发送 / 执行选中的命令，`↑ ↓` 在命令面板内选择，`Tab` 补全命令，
`Esc` 关闭面板 / 中断生成，`Ctrl+N` 新会话，`Ctrl+R` 历史会话，`Ctrl+S` 保存会话，
`Ctrl+L` 清屏（保留历史），`Ctrl+B` 收起/展开侧栏，`F1` 帮助，`Ctrl+Q` 退出。

### 斜杠命令

输入 `/` 就会弹出补全引导面板（只显示选中项附近的一屏，最多 8 条）：

```
 命令 ↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 关闭  1/16
 ▸ /help         打开帮助面板
   /new          新建会话（自动保存当前会话）
   /resume       历史会话列表：↑↓ 选择，Enter 恢复，Esc 取消
   /save         保存当前会话到 .agents/sessions
   /rename <标题> 给当前会话改名
   /export <路径> 把当前会话导出为 Markdown
   /compact      压缩上下文：归档原始记录 + 生成摘要
   /clear        清空界面（保留历史与上下文）
```

| 命令 | 作用 |
|---|---|
| `/help` | 帮助面板（命令 + 快捷键） |
| `/new` | 新建会话：**先把当前会话存盘**，再重置历史与界面 |
| `/resume` | 历史会话列表：↑↓ 选择、Enter 恢复、Esc 取消 |
| `/save` | 手动保存当前会话 |
| `/rename <标题>` | 给当前会话改名（会立即落盘） |
| `/export <路径>` | 导出为 Markdown，相对路径按工作目录解析 |
| `/compact` | 压缩上下文，完成后弹双栏「压缩日志」 |
| `/clear` | 清空界面，历史与上下文保留 |
| `/tools` `/skills` | 列出已注册工具 / 已发现技能 |
| `/mcp [reload]` | MCP 连接状态；`reload` 重读 `.mcp.json` 并重连 |
| `/config` | 列出当前生效的全部配置项（含来源文件与告警） |
| `/init` | 生成 `AGENTS.md` 模板（已存在则不覆盖） |
| `/model` `/cwd` | 显示模型与接口 / 工作目录 |
| `/quit` | 退出（退出前自动保存） |

界面布局：左侧对话流（用户 / 助手气泡 + 可点击折叠的工具卡片），右侧三块侧栏
（会话信息 → **MCP 服务器与工具** → 工具清单），底部状态条（轮次 / 工具调用次数 /
上轮耗时 / 会话编号）。

## 会话持久化（/new · /resume · /save）

会话写在 `<项目根>/.agents/sessions/<session_id>.jsonl`（项目根 = 启动时的 cwd）：

```text
{"type":"meta","session_id":"20261105-114209-ddfb","title":"帮我看看这个报错","created":"...","updated":"...","model":"deepseek-chat","messages":12,"tokens":4820}
{"type":"message","kind":"agents_md","ts":"...","message":{"role":"user","content":"<!-- guladuo:agents-md --> ..."}}
{"type":"message","kind":"user","ts":"...","message":{"role":"user","content":"帮我看看这个报错"}}
{"type":"message","kind":"assistant","ts":"...","message":{"role":"assistant","content":"...","tool_calls":[...]}}
```

- **整体快照**而不是纯追加：压缩会把历史换成摘要，纯追加恢复出来会重复；
  快照每次写「当前真实历史」，`/resume` 直接可用。先写 `.tmp` 再改名，中途崩溃不留半个文件；
- 自动保存时机：每轮回答结束、`/compact` 完成、`/new`、`/quit`、进程退出；
- 标题取第一条用户消息（跳过 AGENTS.md 那条），可用 `/rename` 改；
- `[session] keep` 控制最多保留多少个会话，超出按更新时间删最旧的；
- `/resume` 恢复时会**先把当前会话存盘**，来回切换不丢东西；工具卡片会用归档里的输出还原；
- 恢复的是完整历史（含 System Prompt 与 AGENTS.md 注入），所以不会重复注入、缓存前缀不变。

## AGENTS.md：一次注入，全程复用

如果项目根有 `AGENTS.md`（文件名可用 `[agent] agents_file` 改），会话开始时读**一次**，
作为一条普通 user 消息插到 System Prompt 之后（位置固定 = index 1）：

```
<!-- guladuo:agents-md -->
以下是项目根目录 `D:\skill\AGENTS.md` 的内容，是本项目的协作约定与背景说明。
它在本会话开始时注入一次并全程有效，无需重复读取该文件……

<AGENTS.md 原文>
```

- 之后所有轮次都**直接复用历史里这条消息**，不再读文件、不再重复注入；
- 它随会话一起写进事件日志（`kind: agents_md`），`/resume` 恢复时自然带回，同样不注入第二次；
- `/compact` 替换历史时会**保留**这条消息——项目约定属于「每次都要生效」的内容，
  被摘要吃掉就等于换了个人格；
- 因为消息前缀完全稳定，服务端 prompt cache 可以一直命中；每轮重新读取拼字符串
  即使内容一样，也很容易把缓存前缀打破；
- `/new` 视为新会话，会重新读一次文件（改完 `AGENTS.md` 用 `/new` 即可生效，无需重启）；
- 没有这个文件就什么都不做，历史里不会多出空消息。

超过 `[agent] agents_max_chars` 会被截断并标注。

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
| `ContextUsageChanged` | 上下文用量变化 | 更新顶部用量条 |
| `CompactStarted` | 压缩开始 | 系统气泡：归档编号 |
| `CompactFinished` | 压缩成功 | 系统气泡：前后 token 对比 |
| `CompactFailed` | 压缩失败（上下文不变） | 错误气泡，可重试 |

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
- 单次读取上限 `[agent] max_read_chars`；
- 一轮内工具调用有轮次上限（`[agent] tool_round_limit`），避免模型死循环；
- 单个工具结果上限 `[agent] max_tool_result_chars`，防止一条结果吃光上下文。

## 配置项一览（全部在 config.ini）

代码里不出现魔法数字：所有可调参数都在 `core/config.py` 的 `DEFAULTS` 里声明默认值，
由 `config.ini` 覆盖，`/config` 可以随时查看当前生效值。

| 小节 | 关键项 |
|---|---|
| `[llm]` | `base_url` / `api_key` / `model` / `request_timeout` |
| `[agent]` | `system_prompt`、`max_read_chars`、`tool_round_limit`、`context_window`、`compact_threshold`、`max_tool_result_chars`、`agents_file`、`agents_max_chars` |
| `[search]` | `api_key`（千帆 AI 搜索） |
| `[tools]` | `fetch_timeout` / `fetch_max_chars` / `fetch_hard_limit`、`search_timeout`、`runcli_timeout` / `runcli_output_limit` / `runcli_hard_limit` |
| `[context]` | `entry_limit`、`digest_max_archives` / `digest_summary_chars` / `digest_topic_chars`、`recall_limit` / `recall_max_chars` |
| `[mcp]` | `enabled`、`async`、`call_timeout`、`connect_timeout` |
| `[skills]` | `max_frontmatter_bytes`、`max_description_chars` |
| `[session]` | `dir`、`keep` |
| `[ui]` | `sidebar_width`、`tool_output_chars` |

数值型参数带上下限保护：写 `tool_round_limit = abc` 会用默认值并告警，
写 `sidebar_width = 5` 会被夹到下限 24。

## 联网搜索（searchweb）

通过百度千帆 AI 搜索接口（`/v2/ai_search/web_search`）检索互联网，
把参考来源的标题、链接、内容摘要整理后交给模型。

- 密钥放在 `config.ini` 的 `[search]` 小节（`api_key`，旧版键名 `baidu-qianfan-apikey` 仍兼容），代码不硬编码；
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

### createSkill 工具

用户用自然语言提出「帮我做一个 XX 技能」时，模型调用 `createSkill` 落盘：

| 参数 | 说明 |
|---|---|
| `name` | 技能名，强制小写字母/数字/连字符（也是子目录名） |
| `skillContext` | SKILL.md 的完整内容，可带可不带 YAML frontmatter |

规范化规则：

- **带了 frontmatter**：`name` 强制改为与目录一致的参数值；缺 `description` 时从正文
  首行自动生成；
- **没带 frontmatter**：自动生成，`description` 取正文第一个标题或首个非空行；
- 目标 `SKILL.md` 已存在时**拒绝覆盖**并明确提示（需用户确认删除后才能重建）；
- 创建成功后立即注册进 `SkillRegistry`，**当前对话就能 `loadSkill`**，重启后由
  `discover_skills()` 正式接管；
- `references/`、`scripts/` 等附加文件不在其职责内，由模型继续调用文件工具完成。

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

> 注意：技能目录在**项目根**（启动时 cwd）下，若 `--workspace` 指定的工作区是别的目录，
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

## 上下文压缩与历史检索（/compact + recallContext）

长时间任务迟早会把上下文塞满。guladuo 的做法刻意简单：**先归档、再摘要、最后替换**，
不引入向量库或 Embedding，全部逻辑在 `core/context.py`（约 300 行）。

### 用量怎么算、怎么显示

- 优先用模型返回的 `usage.total_tokens`（`stream_options.include_usage`，
  不支持该参数的接口会被自动关掉并退回估算）；
- 拿不到 usage 时按「中日韩字符 1 token、其它字符 4 字符 1 token」估算，
  并把工具定义也算进去；
- 窗口大小来自 `config.ini` 的 `[agent] context_window`（`128k` / `131072`）。
  留空表示不知道 → 按默认 128k 计算，界面上标注「默认」。

TUI 顶部常驻一行：

```
 上下文 12.3k / 128k  9.6%  默认  usage
```

（`usage` = 模型给的数，`估算` = 本地算的；达到阈值时变红并提示执行 `/compact`。）

### 达到阈值之后会怎样

`[agent] compact_threshold` 默认 **0.7**（70%）。达到阈值时：

1. 弹一条红色提示，告诉用户该压缩了；
2. **暂停接受新的普通提问**（斜杠命令仍然可用）；
3. 只有 `/compact` 成功、或 `/new` 重开、或占用降到阈值以下才恢复输入。

### `/compact` 做了什么

1. **归档**：把除 System Prompt 外的全部消息写成
   `<项目根>/.agents/context/<YYYYMMDD-HHMMSS>.jsonl`，一行一条，
   保留 `role` / `content` / `tool_calls` / `tool_call_id` 与原始顺序。
   先写 `.tmp` 再改名，**归档失败就直接中止**，绝不丢原文。
2. **压缩**：用 `COMPACT_PROMPT` 发一次**不带工具**的模型请求
   （所以压缩期间不可能触发 Function Calling），要求产出约 5,000 tokens 的 Markdown 摘要。
3. **替换**：保留 System Prompt 与工具定义，历史换成一条摘要消息，末尾追加归档说明：

   > 此前上下文已于 2026-11-05 10:26:25 完成压缩，原始上下文保存在编号为
   > 20261105-102625 的归档文件中。如需查找历史信息，请使用 recallContext 工具。

任何一步失败（写盘失败、模型报错、摘要为空）→ 原上下文**保持不变**，直接重试 `/compact` 即可。

> 注意：压缩不保证一定变小。会话很短时（几百 token），5,000 token 的摘要反而更长；
> 这个功能是为「几万 token 的长会话」准备的，短会话没必要压。

### recallContext 工具

压缩之后模型手上只有摘要。摘要里缺细节时，它可以自己查归档：

```json
{ "text": "pethospital.exe", "id": "20261105-102625" }
```

- `text` 必填，空格分隔多个词时要求**同时命中**（AND），大小写不敏感；
- `id` 可选，不填就在 `.agents/context/` 下所有归档里搜；
- 返回「归档编号 + 第几条 + 角色 + 命中位置前后的片段」，
  默认最多 8 条 / 4000 字符，超出会提示缩小范围——避免检索结果又把上下文塞满。

它就是一个普通工具（`core/tools/recall_context.py`），和 `readFile`、`runcli` 同级注册。

## MCP 客户端（接入外部 MCP Server）

启动时读取**项目根目录**（= 启动时的 cwd，与技能目录同一个根）下的 `.mcp.json`，
连接里面声明的 MCP Server（Streamable HTTP），把它们的工具注册进 xAgent 的工具集合。
对模型来说，MCP 工具和 `runcli`、`readFile` 完全一样，都走同一套 Function Calling。

```json
{
  "mcpServers": {
    "weather": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ${WEATHER_TOKEN}" }
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| `url` | 必填，`http://` 或 `https://` 的 Streamable HTTP 端点 |
| `type` | 可选，`http` / `streamable-http`；写 `sse` 或 `stdio` 会被跳过 |
| `headers` | 可选，支持 `${ENV_VAR}` 展开，密钥不必写进文件 |
| `timeout` | 可选，单次工具调用超时秒数，默认 30 |
| `connectTimeout` | 可选，连接超时秒数，默认 10 |
| `disabled` | 可选，`true` 时跳过该 server |

### 工具命名与冲突处理

每个工具暴露给模型的名字是 `mcp__<server>__<tool>`，例如 `mcp__weather__get_forecast`。
统一加前缀有三个好处，并且内部维护了「暴露名 → (server, 原始工具名)」的映射：

- 不同 server 有同名工具也不会撞车（`mcp__a__search` / `mcp__b__search`）；
- 不可能覆盖本地工具（本地工具没有 `mcp__` 前缀）；
- 从工具名就能看出该调用会发到哪台 server，排查问题直接。

名字里的非法字符（如 `.`、空格）会替换成 `_`，总长截断到 64 字符；极端情况下
（清洗后仍重名）追加 6 位短哈希。

### 结果与错误

- 文本内容按原样拼给模型；图片/音频/二进制资源只给一行说明，不塞 base64；
- `structuredContent` 会额外渲染成 JSON 块；如果它只是文本的自动包装（`{"result": "..."}`），
  自动省略以免重复占 token；
- 单个结果超过 8000 字符会截断并标注；
- 服务端 `isError=true`、参数校验失败、连接断开、调用超时，都会作为**工具结果**
  （`ok=false` + 中文说明）回传给模型，由模型决定重试或改方案，不会中断对话。

### 启动降级与异步加载

`.mcp.json` 不存在、JSON 语法错误、缺少 `mcpServers`、某个 server 连不上，
都只记录在 `MCPManager.diagnostics()` 里并在侧栏显示，**不影响 xAgent 启动**。

**连接是异步的**（`[mcp] async = true`，默认）：`create_agent()` 立刻返回，
界面先渲染出来，后台线程再去握手；连上以后前端轮询到 `manager.loading == False`，
自动把新工具注册进工具表并刷新侧栏，同时弹一条「MCP 已就绪」的系统消息。

```
侧栏 MCP 区域（加载中 → 完成）
MCP · 2/2 已连接
● Bazi-MCP 3 工具
● pei-hospital 1 工具

工具
Bazi-MCP
  • getBaziDetail
  • getSolarTimes
  • getChineseCalendar
pei-hospital
  • list_pets
```

连不上的 server 显示 `○ 未连接` 加一行原因（「端口 8090 连不上」「HTTP 410 …」），
不需要再猜。整个过程中界面不会卡——连接 I/O 全程不持锁。

> 想让启动像以前一样同步等待，把 `[mcp] async = false` 即可（多 server 时
> 每个连不上的 server 会占一个 `connectTimeout`）。

### 示例：接入本机的 PeiHospitalMCP

`PeiHospitalMCP` 跑在 `http://127.0.0.1:8090/mcp`（Streamable HTTP）。把它写进
**启动目录**下的 `.mcp.json` 即可。仓库里的 `mcp.json` 是「两个 server 并存」的完整样例，
其中远程地址用 `${BAZI_MCP_URL}` 占位（那条 URL 本身就相当于凭据，公开仓库不写真实值），
实际使用时在环境变量里给出，或直接写死在自己的 `.mcp.json` 里：

```json
{
  "mcpServers": {
    "pei-hospital": {
      "type": "http",
      "url": "http://127.0.0.1:8090/mcp",
      "timeout": 60,
      "connectTimeout": 20
    }
  }
}
```

注意三点：

1. **位置**：读的是「启动时 cwd」下的 `.mcp.json`，不是 `--workspace`。
   从 `D:\skill` 启动就放 `D:\skill\.mcp.json`；
2. **顺序**：先启动 PeiHospitalMCP，看到它监听 8090，再启动 guladuo；
3. **工具名**：接入后模型看到的是 `mcp__pei-hospital__<原始工具名>`。
   若 server 需要鉴权，加 `"headers": { "Authorization": "Bearer ${PEI_TOKEN}" }`。

### 示例：接入远程托管的 Bazi-MCP

远程 server 只是换个 URL，写法完全一样（`type` 写作 `streamable_http` 也认）：

```json
{
  "mcpServers": {
    "Bazi-MCP": {
      "type": "streamable_http",
      "url": "https://mcp.api-inference.modelscope.net/<你的-id>/mcp",
      "timeout": 60,
      "connectTimeout": 20
    }
  }
}
```

它自带 3 个工具：`getBaziDetail`（排盘）、`getSolarTimes`（八字反推公历）、
`getChineseCalendar`（黄历）。**参数有两个坑**：

- `gender` 是**数字**：`1` 男、`0` 女，传 `"男"` 会 `Expected number, received string`；
- 时间要 ISO 格式带时区：`1998-07-31T14:00:00+08:00`，不是 `1998-07-31 14:00:00`。

远程地址**走系统代理**（与本机 server 相反）。若代理不通，`mcp_doctor.py` 会报
「握手被中断」，先确认 `$env:HTTPS_PROXY` 指向的代理是否可用。

### 本机地址自动绕开系统代理

Clash / v2ray 这类工具会把 `HTTP_PROXY` 设成 `127.0.0.1:7890`，httpx 默认会把
**连本机的请求也丢给代理**，结果是代理去连 8090 失败，只拿到 `502 Bad Gateway`。
所以 `connection.py` 对 `127.0.0.1 / localhost / ::1` 的 URL 强制 `trust_env=False`，
本机 server 永远直连；远程 server 仍然遵循系统代理。

### 体检与重连

```powershell
# 不开 TUI，直接验证配置能不能接上、有哪些工具
python mcp_doctor.py
python mcp_doctor.py --config D:\skill\.mcp.json
python mcp_doctor.py --call list_pets                 # 顺手调一次
python mcp_doctor.py --call add_pet --args "{\"name\": \"旺财\", \"species\": \"狗\"}"
```

输出会区分「端口没监听」（server 没启动）和「端口通但握手失败」（路径不是 MCP 端点），
全部 server 可用时退出码为 0。TUI 里对应两个入口：

- 启动时若接入失败，直接弹一条红色气泡说明原因，不用再猜；
- `/mcp` 看每个 server 的连接状态与工具清单，`/mcp reload` 重读 `.mcp.json` 并重连，
  改完配置不用退出程序。

调用过程中 server 重启（连接被掐断）时，`MCPConnectionLost` 会触发**一次自动重连 + 重试**，
重连成功则这次工具调用照常返回；重连失败才把中文错误交给模型。
