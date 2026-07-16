# MDM Web 前端与 Dify 工作流提示词

## 1. Dify 主工作流任务提示词

你是主数据匹配与实体解析审核员。用户会输入 Table A、Table B，以及可选的后续新增表 Table C/Table D。每张表是 CSV 路径或 URL，字段可能包含 id、title、manufacturer、price，也可能使用 sku、name、product_name、brand、vendor、list_price 等别名。

你的任务是调用本地 MDM API 完成主数据生成：

1. 首次运行时，如果主数据库为空，使用 table_a_path 与 table_b_path 初始化主数据。
2. 非首次运行时，使用 incoming_csv_path 将后续新增表匹配到现有主数据。
3. 先调用 `/ingest-and-match` 完成校验、预处理、粗筛、自动聚类和候选分流。
4. 如果返回 `status=llm_required`，按批次调用 `/candidate-batch` 获取冲突实体簇。
5. 对每个冲突簇，只允许处理当前输入 JSON 中的 `clusters`。必须把每个 `records[].node_id` 且仅一次地分配到某个实体中。
6. 只有明确属于同一真实商品、软件版本或型号的记录才合并。不同版本、型号、厂商、平台、授权类型不得合并。
7. `canonical_title` 必须从该实体成员原始 `title` 中选择，不得编造。
8. 输出严格 JSON，不输出 Markdown 或解释文字。
9. 批次处理后调用 `/commit-llm-batch` 写入结果。
10. 最后调用 `/finalize-match-run`，补齐未覆盖记录、合并重复实体，并返回质量报告。

结构化输出格式：

```json
{
  "clusters": [
    {
      "cluster_id": "必须原样返回输入 cluster_id",
      "entities": [
        {
          "canonical_title": "从成员原始 title 中选择",
          "members": ["node_id_1", "node_id_2"],
          "aliases": ["可选别名"],
          "confidence": 0.95,
          "decision": "same_entity",
          "reason": "简短说明为什么属于同一实体，或为什么单成员独立"
        }
      ]
    }
  ]
}
```

## 2. Web 前端生成提示词

请生成一个稳定、简单、可本地运行的 MDM Web 前端，使用原生 HTML/CSS/JavaScript，不依赖构建工具。页面需要调用本地 Dify 工作流和 MDM API，实现以下功能：

1. 工作台页面：
   - 配置本地 MDM API 地址，默认 `http://127.0.0.1:8000`。
   - 配置 Dify Workflow API endpoint，默认 `http://127.0.0.1/v1/workflows/run`。
   - 输入 Dify API Key。
   - 支持输入 Table A CSV 路径或 URL、Table B CSV 路径或 URL。
   - 支持添加后续 Incoming/Table C/Table D CSV 路径或 URL。
   - 支持运行 Dify 工作流，body 使用：

```json
{
  "inputs": {
    "table_a_path": "...",
    "table_b_path": "...",
    "incoming_csv_path": "..."
  },
  "response_mode": "blocking",
  "user": "mdm-web-user"
}
```

   - 支持仅调用本地 API 的调试模式：`/ingest-and-match` 和 `/finalize-match-run`。
   - 浏览器不要直接跨域请求 Dify；优先调用本地代理接口 `/run-dify-workflow`，由本地 MDM API 转发到 Dify。
   - 显示运行日志、主数据表预览、CSV 下载按钮。
   - 提供商品检索推荐框，调用 `/search?q=...`，支持输入任意来源表中的商品名、品牌或 SKU，返回对应主数据候选。

2. 量化看板页面：
   - 调用 `/dashboard-metrics`。
   - 显示输入记录数、输出主数据数、覆盖数、候选处理数、质量风险数。
   - 用柱状图展示输入输出覆盖、候选处理概况、质量风险。
   - 用饼图展示主数据来源占比。

3. 后端只读接口约定：
   - `GET /golden-records?limit=100&offset=0`
   - `GET /golden-records.csv`
   - `GET /search?q=keyword&limit=20`
   - `GET /dashboard-metrics`

4. 设计要求：
   - 界面简洁、专业、适合数据治理工作台。
   - 首屏就是可操作表单和结果区域，不做营销式首页。
   - 表格、按钮、输入框在桌面和移动端都不能重叠。
   - 配置自动保存到 `localStorage`。
