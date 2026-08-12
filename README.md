# APISpotLight

APISpotLight 是一个 FastMCP stdio 服务。它根据 mock 文件和可选的页面截图，从完整 OpenAPI 文档（本地文件或 Torna 项目链接）中筛选当前页面相关的接口；先生成候选清单供人工确认，再导出精简文档。

## 文档源

支持两种 `api_doc_source`（兼容旧参数名 `api_doc_path`）：

1. **本地完整 OpenAPI 3.x** JSON/YAML 文件（从 Torna 手工导出亦可）。
2. **Torna 项目文档链接**，形如：

   `http://torna.example.com/#/project/doc/{projectId}`

   需配置环境变量 `TORNA_TOKEN`。服务会鉴权拉取项目接口树并转为 OpenAPI；阶段 1 会把缓存写到 `output_dir/full-openapi.from-torna.json`，阶段 2 应优先复用该缓存，避免二次全量拉取。

单接口分享页 `#/view/...` **不能**作为完整项目文档源。无 token 时不要指望直接抓取 Torna 页面。

## 安装与 stdio 启动

需要 Python 3.11 或更高版本。

**推荐（uv）：**

```bash
uv venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

**备选（标准 pip）：**

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

本仓库本地开发启动 stdio（通常由 Cursor 启动，不要在普通终端里常驻运行）：

```bash
.venv/bin/python -m api_spotlight.server
```

也可以使用 console script：`api-spotlight`（安装后同样进入 stdio 等待）。

## Cursor MCP 配置

### 本仓库开发（推荐绝对路径）

将路径改为本机仓库的**绝对路径**：

```json
{
  "mcpServers": {
    "api-spotlight": {
      "type": "stdio",
      "command": "/absolute/path/to/APISpotLight/.venv/bin/python",
      "args": ["-m", "api_spotlight.server"],
      "env": {
        "TORNA_TOKEN": "${env:TORNA_TOKEN}",
        "TORNA_TIMEOUT_SECONDS": "${env:TORNA_TIMEOUT_SECONDS}",
        "TORNA_DOC_CONCURRENCY": "${env:TORNA_DOC_CONCURRENCY}",
        "OPENAI_API_KEY": "${env:OPENAI_API_KEY}",
        "OPENAI_BASE_URL": "${env:OPENAI_BASE_URL}",
        "VISION_MODEL": "${env:VISION_MODEL}"
      }
    }
  }
}
```

### 插件安装

仓库根目录 `mcp.json` 与 `.cursor-plugin/plugin.json` 供插件打包使用：入口为 `python -m api_spotlight.server`，并映射 `TORNA_*` 与 `OPENAI_*` / `VISION_MODEL`。插件场景下需保证该 `python` 已安装本包；本地仓库开发请继续使用上面的 `.venv/bin/python` 绝对路径。

`env` 从 Cursor **启动时的进程环境**映射变量。仓库里的 `.env` **不会**被本服务自动加载。请先在 shell / 系统环境中 `export` 这些变量（或通过其他方式注入 Cursor 启动环境），再启动 Cursor。如果不使用截图识别，可省略视觉相关变量，并在阶段 1 设置 `vision_enabled=false`。不使用 Torna 链接时可省略 `TORNA_*`。

## 斜杠命令

安装/启用本插件后可用：

| 命令 | 作用 |
|------|------|
| `/查找页面接口` | 检查 MCP →（Torna URL 时先 `test_torna_connection`）→ `find_page_apis` → 汇报候选路径并提醒编辑 `selected` |
| `/导出确认接口` | 优先用阶段 1 的 `openapi_cache_path` → `export_confirmed_apis` → 汇报导出结果 |

命令定义见 `commands/`。

## MCP 工具一览

| 工具 | 作用 |
|------|------|
| `test_torna_connection` | 校验 Torna 项目 URL + `TORNA_TOKEN`，返回项目名、接口数、样例路径（不含 token） |
| `find_page_apis` | 阶段 1：证据匹配并写出候选 |
| `export_confirmed_apis` | 阶段 2：按确认结果导出精简文档 |

## 两阶段调用

所有输入和输出均使用本地文件路径（Torna 文档源除外，可为项目 URL）。

### 阶段 1：查找候选接口

调用工具 `find_page_apis`：

```json
{
  "mock_paths": ["/project/mocks", "/project/mock/users.json"],
  "screenshot_paths": ["/project/screens/users.png"],
  "api_doc_source": "http://torna.example.com/#/project/doc/{projectId}",
  "output_dir": "/project/api-candidates/users",
  "vision_enabled": true
}
```

本地文件示例：把 `api_doc_source` 换成 `/project/openapi.yaml` 即可（也可用旧名 `api_doc_path`）。

参数：

- `mock_paths`：mock 文件或目录列表。
- `screenshot_paths`：页面截图列表；不使用视觉识别时可传空列表。
- `api_doc_source` / `api_doc_path`：本地完整 OpenAPI，或 Torna 项目文档链接。
- `output_dir`：写入 `candidates.json` 和 `candidates.md` 的目录；Torna 源时还会写 `full-openapi.from-torna.json`。
- `vision_enabled`：是否启用 OpenAI 兼容视觉分析，默认 `true`。

若返回 `openapi_cache_path`，阶段 2 请优先使用该路径。

### 人工确认候选

打开阶段 1 生成的 `candidates.json`，检查每个候选的 `method`、`path`、`source`、`match_type` 和 `doc_summary`，然后编辑 `selected`：

```json
{
  "method": "GET",
  "path": "/users/{id}",
  "selected": true
}
```

保留的接口设为 `true`，不导出的接口设为 `false`。默认情况下，已匹配候选为 `true`，未匹配项为 `false`。要确认一个 `unmatched` 候选，必须同时把 `path` 改为完整 OpenAPI 文档中的规范模板路径（例如 `/users/{id}`），并设置 `selected=true`；仅把原始具体路径设为选中会被跳过并返回明确警告。不要只修改 `candidates.md`；使用文件模式时，阶段 2 读取的是 `candidates.json`。

### 阶段 2：导出已确认接口

调用工具 `export_confirmed_apis`：

```json
{
  "api_doc_source": "/project/api-candidates/users/full-openapi.from-torna.json",
  "candidates_path": "/project/api-candidates/users/candidates.json",
  "output_path": "/project/exports/users.openapi.yaml",
  "output_format": "yaml"
}
```

参数：

- `api_doc_source` / `api_doc_path`：完整 OpenAPI（优先阶段 1 Torna 缓存）、或同一 Torna 项目链接。
- `candidates_path`：已编辑 `selected` 的 `candidates.json`；与 `confirmed_apis` 二选一。
- `confirmed_apis`：可选的候选对象数组，结构与 `candidates.json` 中的条目相同；与 `candidates_path` 二选一。
- `output_path`：精简文档的本地输出路径，不得覆盖源 OpenAPI 文件。
- `output_format`：`json`、`yaml`、`markdown`，或默认的 `openapi`。

也可以不落盘确认文件，直接传入已确认列表：

```json
{
  "api_doc_source": "/project/openapi.yaml",
  "output_path": "/project/exports/users.openapi.yaml",
  "confirmed_apis": [
    {
      "method": "GET",
      "path": "/users/{id}",
      "source": ["mock", "screenshot"],
      "match_type": "template",
      "doc_summary": "Get user by id",
      "selected": true
    }
  ],
  "output_format": "yaml"
}
```

`api_doc_source`（或 `api_doc_path`）和 `output_path` 必填。必须且只能提供 `candidates_path`、`confirmed_apis` 之一；两者同时提供或都不提供会失败。

导出会保留所选操作以及它们递归引用的 OpenAPI components。

## Torna 与环境变量

`.env.example` 列出了相关变量名，便于本地对照：

```bash
cp .env.example .env
```

填写后仍需自行把变量导出到 Cursor 的启动环境（见上文 MCP 配置）。本服务不会自动读取仓库 `.env`。

- `TORNA_TOKEN`：Torna 鉴权 token（仅环境变量；禁止写入工具参数、日志、候选文件）。
- `TORNA_TIMEOUT_SECONDS`：请求超时秒数，默认 `30`。
- `TORNA_DOC_CONCURRENCY`：详情并发数，默认 `6`。
- `OPENAI_API_KEY`：OpenAI 或兼容服务的 API key。
- `OPENAI_BASE_URL`：兼容 `/chat/completions` 的 API 基础地址。
- `VISION_MODEL`：支持图像输入的模型名称。

连接自检示例（配置好 `TORNA_TOKEN` 后）：

```text
test_torna_connection(doc_url="http://torna.example.com/#/project/doc/{projectId}")
```

成功时返回 `ok=true`、`project_name`、`api_count`、`sample_paths` 等（不含 token）。

不要提交 `.env` 或在日志、候选文件中写入密钥。视觉请求超时为 30 秒；超时或网络请求失败时会记录警告并继续处理其他截图。缺少视觉凭据时，如果 mock 已产生有效证据，阶段 1 会降级为仅使用 mock 并返回警告。

## 格式与扩展名建议

- 完整 OpenAPI 输入：推荐 `.json`、`.yaml` 或 `.yml`。
- 候选确认：使用自动生成的 `candidates.json`；`candidates.md` 仅供阅读。
- JSON 导出：使用 `output_format="json"` 和 `.json`。
- YAML 导出：使用 `output_format="yaml"` 和 `.yaml`。
- Markdown 导出：使用 `output_format="markdown"` 和 `.md`。
- `output_format="openapi"` 时，`.yaml`/`.yml` 输出 YAML，其他扩展名输出 JSON；为避免歧义，建议显式指定格式并使用匹配的扩展名。
