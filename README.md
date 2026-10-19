# da-yu-yan · 大语言模型 & MCP 实验工作区

把「大模型调用」和「MCP 服务」两类东西跑通的本地实验仓库：一边是从零手写的 LLM 对话 / 工具调用脚本，一边是可直接接入 AI 客户端的 MCP Server（AnythingLLM 知识库问答、宠物医院业务系统）。

所有服务都只监听 `127.0.0.1`，面向本地学习与演示。

---

## 项目一览

| 目录 | 是什么 | 端口 | 技术栈 |
|---|---|---|---|
| [`agent/`](agent) | LLM 调用练习：流式对话 → Function Calling | — | Python · OpenAI SDK |
| [`anythingLLM mcp Server/`](anythingLLM%20mcp%20Server) | 把 AnythingLLM 知识库包装成 MCP 工具 | `8000` | Python · `mcp` · httpx |
| [`chong wu mcp/`](chong%20wu%20mcp) | 宠物医院系统 + 它的 MCP 适配器 | `8080` / `8765` | Go 编译产物 · Python · `mcp` |
| [`anything-LLM/`](anything-LLM) | 文档批量上传到 AnythingLLM 的单页工具 | — | 单文件 HTML |

---

## 1. `agent/` — LLM 调用练习

从最简对话逐级加功能，每个目录一个独立可运行的脚本。

| 目录 | 内容 | 关键点 |
|---|---|---|
| `practice01/` | `chat.py`：流式对话循环 | `stream=True` 逐字输出，手动维护 `messages` 上下文 |
| `practice02/` | `chat.py`：最简工具调用<br>`chat-stream-tool-getDate-isLeapYear.py`：日期 + 闰年两个工具<br>`chat-stream-tool-file.py`：12 个文件操作工具 | Function Calling 完整闭环：声明 → 调用 → 回填结果 → 二次生成 |

`practice02/` 还带一张架构图：`chat-stream-tool-file-architecture.mmd`（Mermaid 源文件）与渲染好的 `architecture-preview.png`。

### 运行

```bat
:: 首次先建虚拟环境并装依赖
cd agent\practice01
python -m venv .venv
.venv\Scripts\pip install openai

:: 之后双击 run.bat 即可（practice02 复用 practice01 的 .venv）
```

### 配置

脚本从**当前目录**的 `config.ini` 读取模型配置。该文件被 `.gitignore` 排除，需要自己创建：

```ini
[llm]
base_url = https://api.deepseek.com/v1
api_key  = sk-你的密钥
model    = deepseek-chat
```

> 兼容任何 OpenAI 协议的服务（DeepSeek、通义、本地 Ollama / vLLM 等），换 `base_url` 和 `model` 即可。

---

## 2. `anythingLLM mcp Server/` — 知识库问答 MCP

只做一件事：把问题转给本机 AnythingLLM 的工作区，返回基于其知识库的回答。

- **协议**：MCP `2026-07-28`（无状态核心，无 initialize 握手）
- **传输**：Streamable HTTP → `http://127.0.0.1:8000/mcp`
- **工具**：`ask_workspace(question: str) -> str`

```bat
:: 前置：AnythingLLM 桌面端已启动（监听 3001），并设置好 API Key
setx ANYTHINGLLM_API_KEY "你的 Key"
:: 然后双击 run.bat
```

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ANYTHINGLLM_API_KEY` | 无（必填） | AnythingLLM API Key |
| `ANYTHINGLLM_BASE_URL` | `http://localhost:3001` | AnythingLLM 地址 |
| `ANYTHINGLLM_WORKSPACE_SLUG` | `30c051a3-a945-4dcf-a336-7828ca4627c3` | 唯一工作区 slug |
| `MCP_PYTHON` | 自动探测 | 指定 Python 解释器路径 |

MCP 客户端配置见 [`mcp-config.json`](anythingLLM%20mcp%20Server/mcp-config.json)，细节见该目录的 [README](anythingLLM%20mcp%20Server/README.md)。

---

## 3. `chong wu mcp/` — 宠物医院系统 + MCP 适配器

分两层，职责不混：

### `windows/` — 业务后端（Go 编译产物）

用 **Go 标准库**写的单机宠物医院管理系统，网页界面直接内嵌进可执行文件。
仓库内**只提供编译好的 `pethospital.exe`，不含 Go 源码**。

- 浏览器打开 `http://127.0.0.1:8080/` 即可增删改查（档案、病历、收费）
- 零第三方依赖，单文件数据库 `data/pet.db`（追加日志 + CRC32 + 崩溃自愈）
- 迁移只需复制 **`pethospital.exe` + `data/pet.db`** 两个文件

```bat
cd "chong wu mcp\windows"
pethospital.exe
```

自带模拟数据，可直接体验。详见 [README-Windows.md](chong%20wu%20mcp/windows/README-Windows.md)。

### `pet-hospital-mcp/` — MCP 适配器（Python）

把上面那套 REST API 以 MCP 工具形式暴露给 AI Agent，**独立进程、只调 HTTP、不改后端**。

- **协议**：MCP `2026-07-28`，`stateless_http=True`
- **传输**：Streamable HTTP → `http://127.0.0.1:8765/mcp`
- **工具**：`list_pets`
- 自带 pytest 测试（全部用 `httpx.MockTransport`，不依赖真实后端）

```bash
cd "chong wu mcp/pet-hospital-mcp"
pip install -e ".[dev]"
python -m pet_hospital_mcp --port 8765 --base-url http://127.0.0.1:8080
```

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | 上游 Go 服务地址 |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8765` | MCP 监听地址 |
| `PET_HOSPITAL_TIMEOUT` | `10` | 上游超时（秒） |
| `PET_HOSPITAL_MAX_RETRIES` | `2` | 上游失败重试次数 |

新增工具的标准流程写在 [UPGRADE_PROMPT.md](chong%20wu%20mcp/pet-hospital-mcp/UPGRADE_PROMPT.md)。

---

## 4. `anything-LLM/anythingllm-upload.html` — 文档上传页

单文件网页，双击即用。把文档 `POST /api/v1/document/upload` 到指定工作区并触发向量化。

- API Key 只存在浏览器 `localStorage`，不写死在代码里
- 工作区列表自动从 `localhost:3001` 拉取
- 支持多文件，日志区实时显示每份文档的处理结果

---

## 端口占用

| 端口 | 服务 |
|---|---|
| `3001` | AnythingLLM 本体（需自行启动桌面端） |
| `8000` | AnythingLLM MCP Server |
| `8080` | 宠物医院 Web / REST API |
| `8765` | 宠物医院 MCP Server |

---

## 环境要求

| 组件 | 版本 |
|---|---|
| Python | 3.10+（宠物医院 MCP 需 3.11+） |
| AnythingLLM | 桌面版，监听 3001 |

> 宠物医院后端是预编译的 `pethospital.exe`（Windows amd64），跑后端不需要装 Go。

Python 依赖：

```bash
pip install "mcp==2.0.0" httpx openai
# 宠物医院 MCP 额外：pydantic、pytest、pytest-asyncio（见 pyproject.toml）
```

---

## 安全说明

本仓库是**公开**的，因此：

- **所有密钥走环境变量或本地 `config.ini`**，两者都不入库（见 [`.gitignore`](.gitignore)）
- `.workbuddy/`（AI 助手的记忆笔记与调试产物）已排除
- 所有 MCP 服务默认只绑定 `127.0.0.1`，**没有鉴权**，请勿暴露到公网
- 宠物医院数据为**模拟数据**，不含真实个人信息

如果你 fork 后要跑 `agent/` 下的脚本，记得自己创建 `config.ini` 并填入密钥。

---

## 相关文档

- [AnythingLLM MCP Server 说明](anythingLLM%20mcp%20Server/README.md) · [MVP 计划书](anythingLLM%20mcp%20Server/anythingllm-mcp-server-plan.md)
- [宠物医院 MCP 说明](chong%20wu%20mcp/pet-hospital-mcp/README.md) · [Windows 版说明](chong%20wu%20mcp/windows/README-Windows.md)
