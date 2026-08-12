# Torna 链接拉取与斜杠命令（增量设计）

**日期：** 2026-08-12  
**状态：** 已确认并实现（真实拉取待配置 TORNA_TOKEN）  
**基线：** `docs/superpowers/specs/2026-08-12-extract-page-apis-design.md`  
**目标链接形态：** `http(s)://{host}/#/project/doc/{projectId}`

## 1. 目标

在现有两阶段筛选流程上，增加：

1. 从 **Torna 项目文档链接** 拉取完整项目接口（无需先手工导出 OpenAPI）。
2. 提供 **`test_torna_connection`**，验证给定链接能否鉴权并拉到接口树。
3. 提供两个 Cursor **斜杠命令**：`/查找页面接口`、`/导出确认接口`。

**成功标准：**

- 配置 `TORNA_TOKEN` 后，对项目链接调用连接测试：返回项目名、接口数量、拉取成功状态。
- `find_page_apis` 可把同一链接当作完整文档源，生成候选清单。
- 斜杠命令可驱动两阶段 MCP 工具，产物路径与既有一致。

## 2. 非目标

- 不开发浏览器插件桥接。
- 不在工具参数或聊天记录中传递 token（仅环境变量）。
- 不改变阶段 2「人工确认后再导出」的流程。
- 不做空间级 `#/space/...` 多项目合并（首版仅项目链接）。

## 3. 已确认决策

| 项 | 结论 |
|----|------|
| 鉴权 | `TORNA_TOKEN`（Cursor MCP `env` 注入） |
| 链接形态 | 支持 `#/project/doc/{projectId}`；保留本地 OpenAPI 文件能力 |
| 斜杠命令 | 两个：`/查找页面接口`、`/导出确认接口` |
| 连接测试 | 独立 MCP 工具 `test_torna_connection`，由斜杠命令在阶段 1 前置调用 |

## 4. 架构（增量）

```
api_doc_source ──┬── 本地 OpenAPI 文件 ──► openapi.load_openapi
                 └── Torna 项目链接 ──► torna.client
                                          │
                     parse URL → projects → resolve project
                                          │
                              dataByProject → doc tree
                                          │
                         /doc/view?id=... → details
                                          │
                              convert → OpenAPI dict
                                          │
                              find_page_apis / export（既有）
```

新增模块：

- `src/api_spotlight/torna.py`：URL 解析、鉴权请求、项目解析、树/详情拉取、Torna→OpenAPI 转换。
- `commands/查找页面接口.md`、`commands/导出确认接口.md`。
- Cursor 插件元数据：`.cursor-plugin/plugin.json`、`mcp.json`（对齐 TestMasterPlugin 惯例）。

## 5. Torna 拉取

### 5.1 URL 解析

支持：

- `http(s)://{host}/#/project/doc/{projectId}`
- 可选兼容已有的 `#/view/{docId}`（单接口：仅用于连接诊断提示「请使用项目链接」；阶段 1 文档源要求项目级）

解析结果：`{ origin, project_id, kind: "project" }`。

### 5.2 鉴权与请求

- Header：`token: $TORNA_TOKEN`（同时可附带 `Authorization` / `X-Token` 兼容）。
- 成功码：`code` 为 `0` / `20000`（字符串或数字）。
- `code == 1000` 或 msg 含 login → 明确报错「TORNA_TOKEN 无效或未登录」。
- 超时：默认 30 秒；不把 token 写入返回值、日志、候选文件。

### 5.3 拉取步骤

1. `GET {origin}/doc/view/projects` — 校验 token，拿到空间/项目列表。
2. 在展平后的项目列表中匹配 URL 中的 `projectId`。
3. `GET {origin}/doc/view/dataByProject?projectId=...` — 接口树。
4. 展平叶子接口，并发（有限，如 6）拉取 `GET {origin}/doc/view?id={docId}`。
5. 将详情转换为 OpenAPI 3.0 `paths`（method + url/path；summary=name；参数尽量映射到 parameters/requestBody/responses）。

首版转换以 **可匹配 method+path** 为优先；复杂嵌套 schema 可简化为宽松 object，只要路径与方法完整即可供筛选。

### 5.4 `test_torna_connection`

参数：`doc_url: str`

返回（不含 token）：

```json
{
  "ok": true,
  "origin": "http://torna.example.com",
  "project_id": "your-project-id",
  "project_name": "...",
  "api_count": 123,
  "sample_paths": ["GET /foo", "POST /bar"],
  "warnings": []
}
```

失败时 `ok=false` + 可读 `error`（登录失败 / 项目不存在 / 网络超时）。

## 6. 对现有工具的变更

### 6.1 `find_page_apis`

- 将文档参数从「仅本地路径」扩展为 **`api_doc_source`**（兼容旧名 `api_doc_path`：若仍传入则按同一语义处理，或保留别名）。
- 若源是 Torna 项目链接：先拉取并转 OpenAPI，再走既有 match/write 流程。
- 若源是本地文件：行为不变。
- 可选：把转换后的完整 OpenAPI 缓存写到 `output_dir/full-openapi.from-torna.json`，便于复查与阶段 2 复用。

### 6.2 `export_confirmed_apis`

- `api_doc_source`/`api_doc_path` 同样支持 Torna 链接或本地文件。
- 若阶段 1 已写出缓存 OpenAPI，斜杠命令应优先传该缓存路径，避免二次全量拉取。

## 7. 斜杠命令

### `/查找页面接口`

1. 确认 MCP `api-spotlight` 可用。
2. 收集：`mock_paths`、可选 `screenshot_paths`、`api_doc_source`（Torna 项目链接或本地 OpenAPI）、`output_dir`。
3. 若 `api_doc_source` 是 Torna URL：先调 `test_torna_connection`；失败则停止并提示检查 `TORNA_TOKEN`。
4. 调用 `find_page_apis`（Torna 时建议 `vision_enabled` 按用户是否提供截图决定）。
5. 汇报：连接测试结果、候选统计路径、`candidates.json`/`candidates.md`，提醒用户编辑 `selected`。

### `/导出确认接口`

1. 收集：`api_doc_source`（优先阶段 1 缓存 OpenAPI）、`candidates_path`、`output_path`、`output_format`。
2. 调用 `export_confirmed_apis`。
3. 汇报导出路径与接口数。

命令文件放在仓库 `commands/`，并通过 `.cursor-plugin/plugin.json` 的 `commands` 字段注册。

## 8. 配置

`mcp.json` / README / `.env.example` 增加：

```bash
TORNA_TOKEN=
# 可选
TORNA_TIMEOUT_SECONDS=30
TORNA_DOC_CONCURRENCY=6
```

Cursor MCP `env` 映射 `${env:TORNA_TOKEN}`。仓库 `.env` 仍不自动加载。

## 9. 错误处理

| 情况 | 行为 |
|------|------|
| 未设置 `TORNA_TOKEN` | 明确失败，提示如何配置 |
| token 无效 / code 1000 | 明确失败 |
| 项目 ID 不在列表中 | 失败并给出可选项目样例名 |
| 部分详情拉取失败 | warning + 继续；`api_count`/`fetched` 区分 |
| 全部详情失败 | 失败 |
| 本地文件与 Torna 逻辑互不干扰 | 本地路径走原 loader |

## 10. 测试策略

- 单元：URL 解析、projects 展平匹配、树展平、详情→OpenAPI、登录失败。
- 工作流：mock 匹配 + 注入假 Torna OpenAPI；连接测试工具成功/失败。
- 服务器：三工具注册与 schema（含 `test_torna_connection`）。
- **真实链路（实现后必做）：** 在已配置 `TORNA_TOKEN` 时，对实际 Torna 项目链接跑 `test_torna_connection`，断言 `ok=true` 且 `api_count > 0`。无 token 时记录为阻塞并给出配置步骤，不以假成功收场。文档示例请使用占位符，勿写入内网地址或真实项目 ID。

## 11. 与基线规格关系

- 两阶段确认、候选字段、规范 path 去重、`$ref` 闭合、vision 降级等**保持不变**。
- 仅扩展文档源：本地文件 **或** Torna 项目链接。
- 基线中「禁止直接抓 Torna hash URL」修订为：「禁止无 token 抓取；允许带 `TORNA_TOKEN` 的项目链接拉取」。
