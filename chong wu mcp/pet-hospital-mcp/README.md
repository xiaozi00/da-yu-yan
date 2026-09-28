# 🐾 宠物医院 MCP 服务（pet-hospital-mcp）

把本地 **Go 宠物医院 REST API** 的能力，以 **无状态 Streamable HTTP** 的方式暴露给 AI Agent。

* 官方 Python SDK：**`mcp==2.0.0`**（v2，使用 `mcp.server.MCPServer`）
* MCP 协议版本：**`2026-07-28`**
* 传输：**streamable-http**，`stateless_http=True`
* 本阶段只提供一个工具：**`list_pets`**
* 本服务是**独立进程**，只通过 HTTP 调用上游 Go 服务；**不修改** Go 服务

> ⚠️ 这是教学/本地使用的最小适配器：**没有**鉴权、权限、CORS、Origin 校验。
> 默认只监听 `127.0.0.1`，请勿直接暴露到公网。

---

## 1. 目录结构

```text
pet-hospital-mcp/
├── pyproject.toml                 # 依赖与 pytest 配置（pythonpath=src）
├── README.md                      # 本文件
├── UPGRADE_PROMPT.md              # 阶段二：新增工具的标准流程（本阶段不实现）
├── src/
│   └── pet_hospital_mcp/
│       ├── __init__.py            # 版本号 + PROTOCOL_VERSION
│       ├── __main__.py            # python -m pet_hospital_mcp 入口（可选 --host/--port/--base-url）
│       ├── config.py              # 环境变量配置（Settings）
│       ├── server.py              # MCPServer 装配、ASGI 应用、/health、启动
│       ├── rest_client.py         # 上游 HTTP 客户端（超时 + 有限重试 + 错误翻译）
│       ├── validation.py          # 入参校验共享约定（未知字段、错误收敛、脱敏原因）
│       ├── errors.py              # 统一结构化错误 + 错误码
│       ├── logging_config.py      # JSON 日志 + 敏感字段递归脱敏
│       └── tools/
│           ├── __init__.py        # register_all()
│           └── list_pets.py       # list_pets 工具：输入/输出模型 + 实现 + 注册
└── tests/                         # pytest（全部使用 httpx.MockTransport，不访问真实 Go 服务）
    ├── conftest.py
    ├── test_list_pets_tool.py
    ├── test_validation.py
    ├── test_backend_errors.py
    ├── test_registration.py
    ├── test_http_transport.py
    └── test_logging_and_errors.py
```

---

## 2. 前置条件

1. **Python 3.11+**
2. **上游 Go 宠物医院服务必须先启动**（本适配器不会启动它）

### 2.1 先启动 Go 宠物医院服务

发行包（`windows/` 目录）里已经带好了：

```bash
# Windows
cd windows
pethospital.exe                 # 默认监听 127.0.0.1:8080，数据文件 data/pet.db

# 换端口
pethospital.exe -addr 127.0.0.1:9090
```

从源码运行时：`go run .`（同样默认 `127.0.0.1:8080`）。

确认它活着：

```bash
curl -s http://127.0.0.1:8080/health
# {"code":200,"message":"ok","data":{"petCount":1008,"status":"healthy",...}}
```

### 2.2 安装本服务

```bash
cd pet-hospital-mcp

# 建议使用虚拟环境
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate

pip install -e .
# 或只装运行期依赖：pip install "mcp==2.0.0" "httpx>=0.27,<1" "pydantic>=2.7,<3"
```

---

## 3. 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | 上游 Go REST API 地址 |
| `MCP_HOST` | `127.0.0.1` | MCP 监听地址（默认只监听本机） |
| `MCP_PORT` | `8765` | MCP 监听端口 |
| `MCP_PATH` | `/mcp` | MCP Streamable HTTP 端点路径 |
| `PET_HOSPITAL_TIMEOUT` | `10` | 上游请求超时（秒） |
| `PET_HOSPITAL_MAX_RETRIES` | `2` | 上游失败后的重试次数（总尝试 = 1 + N） |
| `PET_HOSPITAL_TRUST_ENV` | `false` | 是否读取系统代理变量（`HTTP_PROXY` 等） |
| `MCP_LOG_LEVEL` | `INFO` | 日志级别 |

> `PET_HOSPITAL_TRUST_ENV` 默认 `false`：上游默认是本机地址，把本机流量交给系统代理
> 会让请求以 absolute-form 发出，在部分服务器上路由失败。需要走企业代理时再设为 `true`。

---

## 4. 启动

```bash
cd pet-hospital-mcp
python -m pet_hospital_mcp                 # 默认 127.0.0.1:8765
```

或使用安装后生成的控制台脚本：

```bash
pet-hospital-mcp
```

也可以用环境变量与命令行覆盖：

```bash
MCP_PORT=9099 PET_HOSPITAL_BASE_URL=http://127.0.0.1:9090 python -m pet_hospital_mcp
python -m pet_hospital_mcp --host 127.0.0.1 --port 9000 --base-url http://127.0.0.1:8080
```

启动后终端会打印：

```text
  pet-hospital-mcp 1.0.0
  MCP 端点     : http://127.0.0.1:8765/mcp
  健康检查     : http://127.0.0.1:8765/health
  协议版本     : 2026-07-28（无状态 Streamable HTTP，无 Mcp-Session-Id）
  上游 Go 服务 : http://127.0.0.1:8080
```

| 端点 | 说明 |
| --- | --- |
| `http://127.0.0.1:8765/mcp` | MCP Streamable HTTP 端点（唯一 MCP 端点） |
| `http://127.0.0.1:8765/health` | 健康检查（普通 HTTP，与 MCP 无关） |

`Ctrl+C` 退出。

---

## 5. 无状态模型：本服务没有的东西

MCP `2026-07-28` 修订版取消了连接握手与会话概念，本服务据此实现：

| 不做的事 | 原因 |
| --- | --- |
| ❌ `initialize` / `notifications/initialized` 握手 | 2026-07-28 用 `server/discover` 一次性探测替代 |
| ❌ `Mcp-Session-Id` 请求头 / 响应头 | 现代协议路径没有会话标识 |
| ❌ 会话存储、会话过期、`max_sessions` | 没有会话，就没有生命周期 |
| ❌ 有状态 SSE 断线恢复（`event_store`） | 同上 |
| ❌ `mcp.server.fastmcp.FastMCP` | SDK 2.x 里已彻底移除，本服务使用 `MCPServer` |

每个请求**自包含**：`Mcp-Protocol-Version` 头 + `params._meta` 里的协议版本与客户端能力，
服务端不依赖任何前置请求即可响应，因此可以随意横向扩展、由普通负载均衡器转发。

---

## 6. 工具 `list_pets`

严格适配 `GET /api/v1/pets`，14 个查询参数一一对应，**不新增任何适配器私有业务参数**。

| 参数 | 类型 | 约束 |
| --- | --- | --- |
| `q` | string | 全文关键词，空格分词 AND，跨字段且含病历全文 |
| `name` | string | 宠物姓名 |
| `ownerName` | string | 主人姓名 |
| `ownerPhone` | string | 主人电话 |
| `species` | enum | `犬` `猫` `兔` `鸟` `仓鼠` `爬宠` `其他` |
| `doctor` | string | 主治医生 |
| `disease` | string | 疾病 / 主要诊断 |
| `status` | enum | `待就诊` `就诊中` `住院中` `已康复` `慢性病随访` |
| `min` | number | 总花费下限（含），`>= 0` |
| `max` | number | 总花费上限（含），`>= 0`，且 `min <= max` |
| `sortBy` | enum | `id` `name` `ownerName` `species` `doctor` `disease` `status` `totalCost` `visitCount` `createdAt` `updatedAt` |
| `order` | enum | `asc` `desc` |
| `page` | integer | `>= 1` |
| `pageSize` | integer | `1 ~ 500` |

枚举取值全部来自上游 `GET /api/v1/meta` 的真实字典。

### 入参校验的边界

校验**只在一处**：`ListPetsInput`（`extra="forbid"` + `strict=True`）。拒绝未知字段、
类型不正确的值、NaN / Infinity、越界数值与 `min > max`，统一返回 `VALIDATION_ERROR`。

要做到这一点，工具签名里的 14 个参数刻意标注为宽松的 `Any`，只用
`WithJsonSchema` 描述对外契约。原因是一个很实际的坑：SDK 2.x 会用函数签名生成一个
`list_petsArguments` 模型，并在**调用处理函数之前**用它拦截入参。若把注释写成
`int` / `str | None`：

* `page="abc"` 会先被 Pydantic 拒绝，客户端收到
  `1 validation error for listPetsArguments ... errors.pydantic.dev` —— 实现细节直接外泄；
* 未知字段会被该模型**静默丢弃**，于是 `{"species":"犬","bogus":1}` 会照常返回数据而不是报错。

因此处理函数通过 `Context` 读取**未校验的线缆参数**
（`ctx.request_context.params["arguments"]`），与允许的参数名集合比对后再做校验。
对外 JSON Schema 依然完整保留 `type` / `enum` / `minimum` / `maximum` / `description`，
模型看到的信息量不减。

两个刻意的取舍：

* **显式 `null` 视为"未提供"**：`{"min": null}` 等同于不传 `min`。不少客户端会把未设置的
  字段序列化成 `null`；
* **NaN / Infinity 在 JSON 传输层会退化成 `null`**：标准 JSON 无法表达这两个值，
  Pydantic 序列化时会把它们变成 `null`，因此经 SDK 客户端发送时等价于"未提供"。
  但只要它们以非标准 JSON 字面量真正到达服务端，就会被 `VALIDATION_ERROR` 拦下。

---

### 成功输出

严格对应 Go 成功响应里的 `data`，不多不少：

```jsonc
{
  "items": [
    {
      "id": "PET-000201", "name": "桃子", "species": "猫", "breed": "波斯猫",
      "gender": "母", "ageMonths": 101, "color": "黑白奶牛", "chipNo": "CHIP-031279",
      "ownerName": "陈先生", "ownerPhone": "18045077845", "ownerAddr": "西安市未央区长江路247号",
      "doctor": "郑医生", "disease": "急性肠胃炎", "status": "已康复",
      "records": [ /* 历史病历，可能为 null */ ],
      "charges": [ /* 消费明细，可能为 null */ ],
      "totalCost": 917.91, "visitCount": 1,
      "createdAt": "...", "updatedAt": "..."
    }
  ],
  "total": 1008, "page": 1, "pageSize": 20, "totalPages": 51, "totalCost": 3680172.85
}
```

`records` / `charges` 兼容 Go 真实返回的 `null` 与数组两种形态。

### 错误输出

失败时工具结果为 `is_error=True`，文本内容为统一错误结构。以下是**真实捕获**的三种：

未知字段：

```json
{"error":{"code":"VALIDATION_ERROR","message":"包含未知参数：bogus","details":{"unknownParameters":["bogus"],"allowedParameters":["disease","doctor","max","min","name","order","ownerName","ownerPhone","page","pageSize","q","sortBy","species","status"]}}}
```

类型错误（`{"page":"abc"}`）：

```json
{"error":{"code":"VALIDATION_ERROR","message":"入参校验失败（page）","details":{"violations":[{"field":"page","reason":"Input should be a valid integer"}],"summary":"page: Input should be a valid integer"}}}
```

跨字段约束（`{"min":100,"max":10}`）：

```json
{"error":{"code":"VALIDATION_ERROR","message":"入参校验失败（<root>）","details":{"violations":[{"field":"<root>","reason":"min 不能大于 max"}],"summary":"<root>: min 不能大于 max"}}}
```

| 错误码 | 触发场景 |
| --- | --- |
| `VALIDATION_ERROR` | 入参不满足输入模型：未知字段、类型错误、枚举取值、区间越界、`min <= max`、NaN / Infinity |
| `BACKEND_TIMEOUT` | 上游超时，且重试耗尽 |
| `BACKEND_UNAVAILABLE` | 上游不可达（连接被拒绝 / 中断） |
| `BACKEND_API_ERROR` | 上游返回 4xx / 5xx，或信封 `code != 200` |
| `BACKEND_INVALID_RESPONSE` | 上游返回非法 JSON，或不符合约定数据模型 |
| `INTERNAL_ERROR` | 适配器自身未预期到的异常 |

**失败状态的标记方式**（SDK 2.x 的实际规定）：
适配器抛出 `mcp.server.mcpserver.exceptions.ToolError`，SDK 会把它变成
`CallToolResult(is_error=True, content=[TextContent(...)], structured_content=None)`。
其中 `content[0].text` 的形式是：

```text
Error executing tool list_pets: {"error": {"code": "...", "message": "...", "details": {...}}}
```

即 `Error executing tool <工具名>: ` 前缀 + 上面的统一错误结构。
**不会**向客户端暴露 HTTPX / Pydantic / SDK 的异常或 Python 堆栈——那些只进服务端日志。

---

## 7. 验证方式一：MCP Inspector

Inspector 是 Node 应用，需要 `npx` 在 `PATH` 上。

```bash
npx @modelcontextprotocol/inspector
```

在打开的界面里：

1. **Transport** 选 `Streamable HTTP`
2. **URL** 填 `http://127.0.0.1:8765/mcp`
3. 点 **Connect** → 应该直接连通（Inspector 与 SDK 2.x 客户端一样走 `server/discover`，
   **不会**发送 `initialize`，也**不会**出现 `Mcp-Session-Id`）
4. **Tools** 页只有 `list_pets`，参数面板会渲染出 14 个可选字段
5. 填 `species=猫`、`sortBy=totalCost`、`order=desc`、`pageSize=5`，点 **Run**

---

## 8. 验证方式二：SDK 2.x 客户端（推荐，可复制粘贴）

```python
import asyncio

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    async with httpx2.AsyncClient(trust_env=False) as http_client:
        transport = streamable_http_client("http://127.0.0.1:8765/mcp", http_client=http_client)
        async with Client(transport) as client:
            print("协议版本:", client.protocol_version)        # 2026-07-28
            print("服务身份:", client.server_info.name)        # pet-hospital-mcp

            tools = await client.list_tools()
            print("工具列表:", [t.name for t in tools.tools])  # ['list_pets']

            result = await client.call_tool(
                "list_pets",
                {"species": "猫", "status": "住院中", "sortBy": "totalCost",
                 "order": "desc", "page": 1, "pageSize": 3},
            )
            print("is_error:", result.is_error)
            print("结构化结果:", result.structured_content)

            bad = await client.call_tool("list_pets", {"species": "恐龙"})
            print("is_error:", bad.is_error)                   # True
            print("错误结构:", bad.content[0].text)


asyncio.run(main())
```

真实运行输出（对着仓库里 `windows/data/pet.db` 的 1008 条数据）：

```text
协议版本: 2026-07-28
服务身份: pet-hospital-mcp
工具列表: ['list_pets']
is_error: False
结构化结果: {'items': [{'id': 'PET-000002', 'name': '咪咪', ...}], 'total': 1, 'page': 1,
            'pageSize': 3, 'totalPages': 1, 'totalCost': 3600.0}
is_error: True
错误结构: Error executing tool list_pets: {"error":{"code":"VALIDATION_ERROR","message":"入参校验失败（species）","details":{"violations":[{"field":"species","reason":"Input should be '犬', '猫', '兔', '鸟', '仓鼠', '爬宠' or '其他'"}],"summary":"species: ..."}}}
```

### 裸 HTTP 调用（不依赖 SDK 客户端）

2026-07-28 的每个请求都是普通 POST，头部自带协议版本，`params._meta` 自带客户端能力：

```bash
curl -s http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Mcp-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: list_pets' \
  -d '{
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
          "name": "list_pets",
          "arguments": {"species": "猫", "page": 1, "pageSize": 2},
          "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}
          }
        }
      }'
```

响应（无 `Mcp-Session-Id` 头）：

```json
{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{...}"}],
 "isError":false,"resultType":"complete","structuredContent":{...},
 "_meta":{"io.modelcontextprotocol/serverInfo":{"name":"pet-hospital-mcp",...}}}}
```

---

## 9. `/health`

```bash
curl -s http://127.0.0.1:8765/health
```

```json
{
  "status": "ok",
  "service": "pet-hospital-mcp",
  "version": "1.0.0",
  "protocolVersion": "2026-07-28",
  "transport": "streamable-http",
  "stateless": true,
  "mcpEndpoint": "/mcp",
  "upstream": "http://127.0.0.1:8080",
  "tools": ["list_pets"]
}
```

`/health` 只反映 **MCP 服务自身**是否在运行，不会去探测上游；上游不可用时，
工具调用会返回 `BACKEND_UNAVAILABLE` / `BACKEND_TIMEOUT`。

---

## 10. 日志

所有日志走标准库 `logging`，输出到 **stderr**，每行一条 JSON（异常堆栈也会收敛成异常类型）。
采集到的真实日志：

```json
{"timestamp": "2026-09-17T10:38:26.540+08:00", "level": "INFO", "logger": "pet_hospital_mcp.server", "message": "service_starting", "host": "127.0.0.1", "port": 8799, "mcp_endpoint": "/mcp", "protocol_version": "2026-07-28", "transport": "streamable-http", "stateless": true, "upstream": "http://127.0.0.1:8080"}
{"timestamp": "2026-09-17T10:38:32.390+08:00", "level": "INFO", "logger": "pet_hospital_mcp.tool", "message": "tool_call", "tool_name": "list_pets", "params": {"ownerPhone": "***", "species": "犬"}, "status": "ok", "duration_ms": 45.543}
```

每次工具调用至少包含 `timestamp` / `tool_name` / `params` / `status` / `duration_ms`。

**脱敏**：`ownerPhone`、`ownerAddr`、`chipNo`（以及 snake_case 写法 `owner_phone` /
`owner_addr` / `chip_no`，大小写不敏感）在任何嵌套层级都会被递归替换成 `"***"`，
包括数组里的对象。上游返回的完整响应体**不会**写入日志。

---

## 11. 单元测试

```bash
cd pet-hospital-mcp
pytest -q
```

**预期结果：`149 passed`**（约 13 秒）。

测试**不访问真实 Go 服务**：上游一律用 `httpx.MockTransport` 拦截，
HTTP 传输层测试则在后台线程里跑一个真实 uvicorn（随机空闲端口）。

覆盖范围：

| 文件 | 覆盖内容 |
| --- | --- |
| `test_list_pets_tool.py` | 请求路径 + 全部 14 个过滤/排序/分页参数转发；未传参数不下发；成功输出字段与 `data` 一致；`records`/`charges` 为 `null` 或数组 |
| `test_validation.py` | 枚举真实取值；`page >= 1`；`1 <= pageSize <= 500`；`min/max >= 0`；`min <= max`；未知字段；NaN / Infinity；类型错误；工具级统一 `VALIDATION_ERROR` |
| `test_backend_errors.py` | 4xx / 5xx；可重试状态码的有限重试；超时；连接失败；非法 JSON；信封缺 `data`；`data` 不符合模型；内部异常兜底且不泄漏细节 |
| `test_registration.py` | 只注册一个工具；工具名 `snake_case`；输入/输出 JSON Schema；工具描述；注解；**AST 静态检查确保没有导入 `mcp.server.fastmcp`** |
| `test_http_transport.py` | `/health`；真实 HTTP 上发现并调用工具；`server/discover` 探测；**不含 `initialize`**；请求与响应**都不含 `Mcp-Session-Id`**；裸 JSON-RPC 调用；并发调用 |
| `test_logging_and_errors.py` | JSON 日志字段；递归脱敏；错误结构稳定性；错误码集合 |

---

## 12. 本阶段范围声明

* ✅ 已实现：**仅一个 MCP 工具 `list_pets`**
* ❌ **未实现**任何阶段二工具（新增 / 更新 / 删除 / 病历 / 收费 / 统计 / 导出等全部不做）
* ❌ 未实现鉴权、权限、CORS、Origin 校验
* ❌ 未实现旧协议兼容（`initialize`、会话、有状态 SSE 恢复）

后续新增工具只需在 `src/pet_hospital_mcp/tools/` 增加一个模块并复用现有的
REST 客户端、错误约定与日志约定，流程见 [`UPGRADE_PROMPT.md`](UPGRADE_PROMPT.md)。
