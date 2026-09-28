# AnythingLLM MCP Server

只做一件事的 MCP Server：把问题转给本机 AnythingLLM 的唯一工作区，返回 AI 基于该工作区知识库的回答。

- **协议**：MCP `2026-07-28`（无状态核心：无需 initialize 握手、无 Session-Id）
- **传输**：Streamable HTTP，绑定 `127.0.0.1:8000`，路径 `/mcp`
- **工具**：`ask_workspace(question: str) -> str`

## 目录结构

```
anythingLLM mcp Server/
├─ server.py                       服务本体（单文件）
├─ run.bat                         启动（双击）
├─ stop.bat                        停止（双击）
├─ cleanup-old-folder.bat          一次性：清理旧路径残留
├─ mcp-config.json                 MCP 客户端配置示例
├─ README.md                       本文件
└─ anythingllm-mcp-server-plan.md  MVP 计划书
```

## 前置条件

1. **AnythingLLM 桌面端已启动**，监听 3001
   （Electron GUI 程序无法由脚本/命令行可靠拉起，必须手动启动；未启动时工具调用会返回连接错误）
2. **Python 3.10+**，并安装依赖：`pip install "mcp==2.0.0" httpx`
3. **环境变量 `ANYTHINGLLM_API_KEY` 已设置**（见下方「环境变量」）

## 启动

**方式一：双击 `run.bat`（推荐）**
脚本会校验环境变量并启动服务，窗口保持打开即服务运行中。
（若脚本提示找不到 Python，先执行 `setx MCP_PYTHON "你的 python.exe 完整路径"`）

**方式二：命令行手动启动**

```bat
set ANYTHINGLLM_API_KEY=<你的 Key>
set ANYTHINGLLM_BASE_URL=http://localhost:3001
python server.py
```

**启动成功的标志**：终端输出 `Uvicorn running on http://127.0.0.1:8000`。

## 停止

| 方式 | 操作 |
|---|---|
| 关闭运行窗口 | 在服务窗口按 `Ctrl+C`，或直接关掉窗口 |
| 双击 `stop.bat` | 自动按端口 8000 找到进程并结束 |
| 确认是否还在运行 | 命令行执行 `netstat -ano \| findstr ":8000"`，无 LISTENING 行即已停止 |

> 启动脚本窗口关掉后服务即结束；8000 被占用时新实例会启动失败，先执行 `stop.bat`。

## 验证服务是否正常

服务启动后，用下面这段（仅标准库）直接打 MCP 接口 —— `tools/list` 返回工具定义即正常：

```python
import json, urllib.request
URL, PROTO = "http://127.0.0.1:8000/mcp", "2026-07-28"
body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
    "io.modelcontextprotocol/protocolVersion": PROTO,
    "io.modelcontextprotocol/clientCapabilities": {}}}}
req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": PROTO,
    "Mcp-Method": "tools/list"})
print(urllib.request.urlopen(req, timeout=20).read().decode())
```

调用工具（`Mcp-Name` 头必填，`params.name` 为工具名）：

```python
body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
    "name": "ask_workspace", "arguments": {"question": "工作区里有什么资料？"},
    "_meta": {"io.modelcontextprotocol/protocolVersion": PROTO,
              "io.modelcontextprotocol/clientCapabilities": {}}}}
# headers 同上，另加 "Mcp-Name": "ask_workspace"
```

> 注意：`MCP-Protocol-Version`、`Mcp-Method` 头与 body 的 `method` 必须一致，否则返回 `-32020`；
> `params._meta` 缺少协议版本/客户端能力时返回 `-32602`。响应体可能是 JSON，也可能是 SSE 帧（`data: {...}`）。

## 在 WorkBuddy 中接入（项目级）

`.workbuddy/mcp.json` 已随项目配置好：

```json
{
  "mcpServers": {
    "anythingllm": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

生效步骤：

1. 在 WorkBuddy 中**以本文件夹为工作区打开**（配置随项目走，不写用户级配置）；
2. 首次需在 连接器管理页 → 自定义连接器 中，对该服务器点 **信任** 才会启用；
3. 服务需先 `run.bat` 启动；未启动时客户端连接失败，属正常现象。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ANYTHINGLLM_API_KEY` | 无（必填） | AnythingLLM API Key |
| `ANYTHINGLLM_BASE_URL` | `http://localhost:3001` | AnythingLLM 地址 |
| `ANYTHINGLLM_WORKSPACE_SLUG` | （代码内默认值） | 唯一工作区 slug |
| `MCP_PYTHON` | 自动探测 | `run.bat` 使用的 Python 解释器完整路径 |

## 常见问题

| 现象 | 处理 |
|---|---|
| 启动报端口占用 | 先 `stop.bat`；或 `netstat -ano \| findstr ":8000"` 找到 PID 后 `taskkill /F /PID <PID>` |
| 工具返回 `缺少环境变量 ANYTHINGLLM_API_KEY` | 该服务进程启动时未注入 Key，用 `run.bat` 重启 |
| 工具返回 HTTP 403 `No valid api key found` | Key 失效，到 AnythingLLM 设置里重新生成 |
| 工具返回连接失败 | AnythingLLM 桌面端没开，先启动它 |
| `D:\mcp` 旧文件夹删不掉 | 会话占用锁，关闭 WorkBuddy 后运行 `cleanup-old-folder.bat` |

## 安全提醒

API Key **不写入仓库**，通过环境变量 `ANYTHINGLLM_API_KEY` 注入：

```bat
setx ANYTHINGLLM_API_KEY "<你的 Key>"     REM 设置一次，重开命令行后长期生效
```

请勿把 Key 提交到版本库。若历史上曾提交过明文 Key，应立即在 AnythingLLM 中重新生成该 Key，
并从 Git 历史中清除，仅删除文件是不够的。

## 明确不做

多工作区支持 / 会话延续（reset、线程）/ resources、prompts 等其它 MCP 原语 / MCP 层鉴权（OAuth）/ 重试与连接池。
