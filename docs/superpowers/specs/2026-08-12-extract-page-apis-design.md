# 按页面从完整接口文档中筛选并导出（APISpotLight）

**日期：** 2026-08-12  
**状态：** 待用户审阅  
**参考：** [字段反查接口浏览器插件](file:///Users/tsbmacmini/Desktop/fieldReverseQueryInterfacePlugin)（候选列表 + 人工确认后再落地）

## 1. 目标

从一份**完整**接口文档中，根据项目真实 **mock** 与 **设计稿页面**（截图）找出「当前页面相关」的接口，先导出**候选清单给你确认**，再按确认结果导出精简接口文档。

不是凭空生成文档；是筛选 + 提取 + 人工确认。

**成功标准：**

1. 给定 mock、截图、完整 OpenAPI 导出文件，阶段 1 产出可审阅的候选清单（含来源、匹配类型、统计）。
2. 你确认后，阶段 2 产出只含已确认接口的 OpenAPI/Markdown，并带上所需 `$ref` schemas。
3. 未匹配项可见，便于手工补选，不静默丢弃。

## 2. 非目标（首版不做）

- 不绕过 Torna 登录直接抓 `#/view/...` 单接口分享页
- 不自动把候选当成最终文档（必须经确认）
- 不做浏览器插件 UI（MCP 工具 + 落盘文件；交互形态对齐「反查插件」的结果清单思路）
- 不做多文档源聚合、相似度模糊推荐（可作为后续扩展）

## 3. 文档来源约定

| 项 | 约定 |
|----|------|
| Torna 页面链接 | 仅作项目说明；不能当 OpenAPI URL |
| 完整文档输入 | 从 Torna **导出**的 OpenAPI 3.x JSON/YAML 本地文件 |
| 范围 | 必须是项目/模块级完整导出，而非单接口分享页 |

## 4. 交互形态（对齐字段反查插件）

字段反查插件的有效模式：

- 输入证据（字段名）→ 在完整索引中反查 → **结果列表**（方法、路径、位置、统计）→ 人再点选/跳转。

APISpotLight 对齐为：

| 反查插件 | 本工具 |
|----------|--------|
| 字段名 | mock 路径 + 截图推断接口 |
| 全量索引 / Torna 详情 | 完整 OpenAPI 导出文件 |
| 结果列表（方法、路径、匹配说明） | 阶段 1 候选确认清单 |
| 人工点选 / 跳转 | 你编辑清单勾选 / 传入确认列表 |
| （扩展）导出报告 | 阶段 2 精简 OpenAPI / Markdown |

候选清单每一项至少包含：

- `method` / `path`（文档规范路径）
- `source`：有序去重列表，只取 `["mock"]`、`["screenshot"]` 或 `["mock", "screenshot"]`
- `match_type`：`exact` | `template` | `unmatched`
- `confidence`（截图推断可偏低）
- `doc_summary`（便于确认）
- `selected`：默认已匹配为 `true`，未匹配为 `false`（你可改）

## 5. 架构

新建 Python FastMCP 服务（仓库当前为空，从零搭建）。

```
APISpotLight/
├── pyproject.toml / requirements.txt
├── README.md
├── .env.example
├── src/
│   └── api_spotlight/
│       ├── server.py       # FastMCP 入口
│       ├── workflows.py    # 两阶段编排
│       ├── evidence.py     # mock 解析与证据合并
│       ├── vision.py       # 截图分析
│       ├── openapi.py      # 加载、匹配与引用收集
│       └── exporters.py    # 候选及精简文档输出
└── tests/ + fixtures/
```

**MCP 工具：**

1. `find_page_apis` — 产出候选确认清单（写入 `candidates_path`），返回路径与统计。  
2. `export_confirmed_apis` — 读取已确认清单或 `confirmed_apis`，写出精简文档到 `output_path`。

模型配置走环境变量：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`VISION_MODEL`（OpenAI 兼容多模态）。不把密钥写入返回值或日志。

## 6. 数据流

```
mock_paths ──► mock_parser ──┐
                             ├── merge/dedupe ──► required[{method,path,source}]
screenshot_paths ──► vision ─┘
                                      │
完整 OpenAPI 文件 ──► loader ─────────┼──► matcher
                                      │       │
                                      │       ▼
                                      │  candidates.json + candidates.md
                                      │  （阶段 1，给你确认）
                                      │
你确认（改 selected / 传入列表）──────► export_confirmed_apis
                                              │
                                              ▼
                                    slim OpenAPI + 必要 schemas
                                    或 Markdown（阶段 2）
```

## 7. 匹配规则

1. **精确匹配**：method + path 完全一致。  
2. **模板匹配**：文档路径含 `{param}`，与 mock/推断中的具体路径用段级通配对齐（如 `/user/{id}` ↔ `/user/123`）。  
3. **去重键**：匹配后按 `METHOD + 规范 path` 去重（以文档中的 template path 为准）；`source` 有序去重，`confidence` 取已有最高值。  
4. **截图推断**：只作为候选来源；匹配失败则进入 `unmatched`，不写入阶段 2，除非你同时把 `path` 改成完整 OpenAPI 中的规范模板路径并设置 `selected=true`。  
5. **未匹配**：必须出现在清单中，附警告，不静默丢弃。

## 8. 导出行为

| 阶段 | 产物 | 返回值 |
|------|------|--------|
| 1 | `candidates.json` + 可读 `candidates.md` | 路径、发现数、匹配数、未匹配数、警告 |
| 2 | 精简 OpenAPI JSON/YAML 或 Markdown | `output_path`、导出接口数、遗漏 `$ref` 警告（若有） |

阶段 2 必须收集 paths 中 `$ref` 引用的 `components.schemas`（及递归依赖），保证精简文档可独立使用。

## 9. 错误处理

- mock 路径不存在 / 无法解析：记录警告，继续其他文件。  
- 截图超时（30 秒）、请求失败或无 API Key：记录警告并继续；若有 mock 结果则降级为仅 mock，若二者皆空则失败并说明原因。  
- OpenAPI 加载失败：直接失败。  
- 阶段 2 清单无任何 `selected=true`：失败，提示先确认。  
- 已选 unmatched 或确认的 path 在文档中不存在：该项跳过，并明确提示改成完整 OpenAPI 规范路径且保持 `selected=true`。

## 10. 测试策略

以 pytest 为主，夹具驱动：

- mock JSON / `METHOD path` key 解析  
- OpenAPI 精确匹配与 `{id}` 模板匹配  
- `$ref` 递归收集  
- 合并去重与候选清单字段  
- Markdown / OpenAPI 导出冒烟  
- vision 模块用假 HTTP 客户端隔离，不测真实模型

## 11. 与既有决策对照

| 决策点 | 结论 |
|--------|------|
| Torna 接入 | 先导出 OpenAPI 文件再传入 |
| 视觉模型 | OpenAI 兼容（env 配置） |
| 交付方式 | 写文件 + 返回路径与统计 |
| 确认方式 | 两阶段：候选清单 → 确认后精简文档 |
| UX 参考 | 字段反查插件的「结果列表 + 人工确认」 |

## 12. 后续可扩展（不做进首版）

- Torna Token / Cookie 在线拉取（参考 Robin `api-doc.js`）  
- 路径相似度候选建议  
- Cursor 插件壳 / 斜杠命令包装  
- 浏览器侧结果面板（更接近反查插件 UI）
