# Mock 字段反查定位 API（混合策略）

**日期：** 2026-08-12  
**状态：** 已确认  
**基线：** extract-page-apis + torna-link 规格

## 1. 目标

在现有 path 匹配之上，从 mock 提取字段名（及可选描述），到完整接口文档的参数名/描述中打分，补全未匹配或仅有字段证据的候选 API；仍经人工 `selected` 确认。

## 2. 策略（已确认：混合 A）

1. Path 精确/模板匹配（现有）优先。  
2. 对 **unmatched** path 证据，以及 **无 path、仅有字段** 的 mock 体，做字段反查。  
3. 命中接口写入候选：`match_type` = `field` 或 `path+field`。  
4. 与 path 已匹配项按规范 path 去重合并；字段命中信息写入 `hit_fields` / `score`。

## 3. Mock 字段提取

- 从 mock JSON / `METHOD path` 对应的响应或请求对象递归收集键名。  
- 跳过纯数字键、空键；可选保留同级描述字段（如 `xxxDesc`、`description` 邻近注释——首版仅键名，描述字符串若值为短中文也可作辅助，但不强制）。  
- 返回：`[{ name, path_hint?, source_file? }]` 及 warnings。

## 4. 文档字段索引

对 OpenAPI（含 Torna 转换结果）每个 operation：

- 收集 parameters[].name / description  
- requestBody / responses schema 属性名与 description（含简单 `$ref` 一层解析到 components.schemas）  
- 索引：`field_name_lower -> [{ method, path, param_location, description }]`

Torna 原生详情若未进 OpenAPI：依赖既有 `details_to_openapi`；首版不另建 Torna 参数树索引。

## 5. 打分

对一组 mock 字段：

- 参数名精确匹配（忽略大小写）：+3  
- 参数描述包含字段名：+1  
- 接口得分 = 命中累加；同分按命中字段数  
- 每组证据保留 top 3 接口；全页合并去重

过滤：得分 < 3（至少一个精确字段名）的不进入候选，避免描述误伤。

## 6. 候选字段扩展

在既有候选上增加可选：

- `hit_fields: string[]`  
- `score: number`（字段匹配时）  
- `match_type`: `exact` | `template` | `field` | `path+field` | `unmatched`

path 已匹配且字段也命中同一接口 → `path+field`，保留 selected=true。

## 7. 工作流

`find_page_apis`：

1. parse mock paths + bodies → requirements + field bags  
2. load document（本地 / Torna）  
3. path match  
4. field index + score for unmatched / field-only bags  
5. merge/dedupe → write candidates  

斜杠命令说明补充：候选可能含字段命中来源。

## 8. 测试

- 字段提取自嵌套 JSON  
- 索引含 parameters 与 schema properties  
- path 未匹配但字段命中 → `match_type=field`  
- path 已匹配 + 字段 → `path+field`  
- 低分不进入候选  
- 全流程 fixture：mock 无正确 path、仅字段能命中文档接口

## 9. 非目标

- 向量/LLM 语义对齐  
- 修改阶段 2  
- 扫描全仓库无指定 mock_paths
