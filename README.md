# APISpotLight

APISpotLight 是一个 FastMCP stdio 服务。它根据 mock 文件和可选的页面截图，从完整 OpenAPI 文档中筛选当前页面相关的接口；先生成候选清单供人工确认，再导出精简文档。

## 输入准备

**不能直接输入 Torna 页面链接。** `http://.../#/view/...` 之类的链接不是 OpenAPI 文件，也可能依赖登录状态。请先从 Torna 导出项目级或模块级的**完整 OpenAPI 3.x 文档**，保存为本地 JSON 或 YAML 文件。单接口分享页或不完整导出可能导致接口和 `$ref` schema 缺失。

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

启动 stdio 服务（通常由 Cursor 启动，不要在普通终端里常驻运行）：

```bash
.venv/bin/python -m api_spotlight.server
```

也可以使用 console script：`api-spotlight`（安装后同样进入 stdio 等待）。

## Cursor MCP 配置

在 Cursor 的 MCP 配置中加入以下服务，并将路径改为本机仓库的**绝对路径**：

```json
{
  "mcpServers": {
    "api-spotlight": {
      "type": "stdio",
      "command": "/absolute/path/to/APISpotLight/.venv/bin/python",
      "args": ["-m", "api_spotlight.server"],
      "env": {
        "OPENAI_API_KEY": "${env:OPENAI_API_KEY}",
        "OPENAI_BASE_URL": "${env:OPENAI_BASE_URL}",
        "VISION_MODEL": "${env:VISION_MODEL}"
      }
    }
  }
}
```

`env` 从 Cursor **启动时的进程环境**映射变量。仓库里的 `.env` **不会**被本服务自动加载。请先在 shell / 系统环境中 `export` 这些变量（或通过其他方式注入 Cursor 启动环境），再启动 Cursor。如果不使用截图识别，可以省略 `env`，并在阶段 1 设置 `vision_enabled=false`。

## 两阶段调用

所有输入和输出均使用本地文件路径。

### 阶段 1：查找候选接口

调用工具 `find_page_apis`：

```json
{
  "mock_paths": ["/project/mocks", "/project/mock/users.json"],
  "screenshot_paths": ["/project/screens/users.png"],
  "api_doc_path": "/project/openapi.yaml",
  "output_dir": "/project/api-candidates/users",
  "vision_enabled": true
}
```

参数：

- `mock_paths`：mock 文件或目录列表。
- `screenshot_paths`：页面截图列表；不使用视觉识别时可传空列表。
- `api_doc_path`：从 Torna 导出的完整本地 OpenAPI JSON/YAML 文件。
- `output_dir`：写入 `candidates.json` 和 `candidates.md` 的目录。
- `vision_enabled`：是否启用 OpenAI 兼容视觉分析，默认 `true`。

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
  "api_doc_path": "/project/openapi.yaml",
  "candidates_path": "/project/api-candidates/users/candidates.json",
  "output_path": "/project/exports/users.openapi.yaml",
  "output_format": "yaml"
}
```

参数：

- `api_doc_path`：与阶段 1 对应的完整 OpenAPI 文件。
- `candidates_path`：已编辑 `selected` 的 `candidates.json`；与 `confirmed_apis` 二选一。
- `confirmed_apis`：可选的候选对象数组，结构与 `candidates.json` 中的条目相同；与 `candidates_path` 二选一。
- `output_path`：精简文档的本地输出路径，不得覆盖源 OpenAPI 文件。
- `output_format`：`json`、`yaml`、`markdown`，或默认的 `openapi`。

也可以不落盘确认文件，直接传入已确认列表：

```json
{
  "api_doc_path": "/project/openapi.yaml",
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

`api_doc_path` 和 `output_path` 必填。必须且只能提供 `candidates_path`、`confirmed_apis` 之一；两者同时提供或都不提供会失败。

导出会保留所选操作以及它们递归引用的 OpenAPI components。

## OpenAI 兼容视觉配置

`.env.example` 列出了视觉相关变量名，便于本地对照：

```bash
cp .env.example .env
```

填写后仍需自行把变量导出到 Cursor 的启动环境（见上文 MCP 配置）。本服务不会自动读取仓库 `.env`。

- `OPENAI_API_KEY`：OpenAI 或兼容服务的 API key。
- `OPENAI_BASE_URL`：兼容 `/chat/completions` 的 API 基础地址。
- `VISION_MODEL`：支持图像输入的模型名称。

不要提交 `.env` 或在日志、候选文件中写入密钥。视觉请求超时为 30 秒；超时或网络请求失败时会记录警告并继续处理其他截图。缺少视觉凭据时，如果 mock 已产生有效证据，阶段 1 会降级为仅使用 mock 并返回警告。

## 格式与扩展名建议

- 完整 OpenAPI 输入：推荐 `.json`、`.yaml` 或 `.yml`。
- 候选确认：使用自动生成的 `candidates.json`；`candidates.md` 仅供阅读。
- JSON 导出：使用 `output_format="json"` 和 `.json`。
- YAML 导出：使用 `output_format="yaml"` 和 `.yaml`。
- Markdown 导出：使用 `output_format="markdown"` 和 `.md`。
- `output_format="openapi"` 时，`.yaml`/`.yml` 输出 YAML，其他扩展名输出 JSON；为避免歧义，建议显式指定格式并使用匹配的扩展名。
