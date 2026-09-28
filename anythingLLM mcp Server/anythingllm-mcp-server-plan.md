# AnythingLLM MCP Server — MVP 开发计划书

> 版本：v1.0 ｜ 日期：2026-09-21 ｜ 状态：待确认
> 目标目录：`D:\mcp`

## 1. 项目背景

在本机开发一个 MCP Server，通过 AnythingLLM 的 HTTP API 访问本机已安装的 AnythingLLM 实例，实现"从唯一工作区提取信息进行 AI 回答"的单一功能。

### 已验证的环境事实

| 项目 | 值 |
|---|---|
| AnythingLLM 地址 | `http://localhost:3001` |
| API 文档 | `http://localhost:3001/api/docs/` |
| API Key | 已验证有效（通过环境变量注入，不写入代码） |
| 唯一工作区 | slug: `30c051a3-a945-4dcf-a336-7828ca4627c3`（我的工作区） |

## 2. 技术选型

- **协议版本**：MCP `2026-07-28`（无状态协议核心：无 initialize 握手、无 Session-Id；Streamable HTTP 强制携带 `Mcp-Method` / `Mcp-Name` 头）
- **语言**：Python 3.13（本机受管版本）
- **SDK**：官方 `mcp` SDK v2.x（`pip install "mcp[cli]"`），原生支持 2026-07-28 规范，自带 HTTP 服务（uvicorn）
- **传输**：Streamable HTTP（明确不使用 stdio），绑定 `127.0.0.1:8000`，路径 `/mcp`
- **鉴权**：MCP Server 本身不做鉴权（规范仅对公网服务器强制 OAuth；本服务仅本机访问，MVP 省略）

## 3. 功能设计（仅 1 个工具）

```
ask_workspace(question: str) -> str
  POST http://localhost:3001/api/v1/workspace/{slug}/chat
  Header: Authorization: Bearer <ANYTHINGLLM_API_KEY>
  Body:   {"message": question, "mode": "chat"}
  返回:   响应中的 textResponse 字段（AI 回答文本）
```

## 4. 配置方式（环境变量，Key 不硬编码）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ANYTHINGLLM_BASE_URL` | `http://localhost:3001` | AnythingLLM 地址 |
| `ANYTHINGLLM_API_KEY` | 无默认，必填 | AnythingLLM API Key |
| `ANYTHINGLLM_WORKSPACE_SLUG` | `30c051a3-a945-4dcf-a336-7828ca4627c3` | 唯一工作区 slug |

## 5. 交付物

- `server.py`：单文件实现，预计 60~80 行（含工具定义 + HTTP 调用 + 启动入口）
- 无其他文件；依赖安装命令写在文件头部注释中

## 6. 验证步骤（写完即测）

1. 启动 server
2. 用 curl 按 2026-07-28 格式直接 POST `tools/list` 与 `tools/call` 请求验证（无状态协议，无需握手）
3. 用一个真实问题调用 `ask_workspace`，确认返回工作区 AI 回答文本

## 7. 明确不做（防过度设计）

- ❌ 不做多工作区支持
- ❌ 不做会话延续（reset / 线程管理）
- ❌ 不做 resources / prompts 等其他 MCP 原语
- ❌ 不做 MCP Server 鉴权（OAuth）
- ❌ 不做重试、连接池、日志框架等冗余设计
