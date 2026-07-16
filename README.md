# MDM CSV 主数据匹配系统

主数据管理（Master Data Management）系统，使用机器学习 + Dify 工作流 + LLM 的方式，将多张 CSV 表中的商品/软件记录自动整合为一张唯一的实体主数据表。

## 项目定位

- **输入**：多张 CSV（如 Amazon / Google 商品数据）
- **输出**：一张唯一实体主数据库（`entities` + `golden_records` + `source_record_links`）
- **核心流程**：粗筛 → 多模型评分 → 实体聚类 → LLM 判歧 → 人工审核 → 质量验收

## 系统架构

```
Dify 工作流（编排层）
    │ HTTP: host.docker.internal:8000
    ▼
FastAPI 服务（计算层）
    │ SQLAlchemy
    ▼
SQLite / MySQL（存储层）
```

- **Dify**：工作流编排、知识库检索、LLM 结构化输出
- **FastAPI**：CSV 校验、预处理、ML 评分、实体聚类、写库、质量收尾
- **数据库**：实体表、黄金记录表、来源映射表、别名表、操作日志

## 评分模型

| 评分器 | 权重 | 说明 |
|--------|------|------|
| 排名一致性（RRF） | 45% | 多模型排名归一化融合 |
| XGBoost | 35% | 结构化特征二分类概率 |
| MLP | 10% | 结构化特征神经网络 |
| TF-IDF 词法相似度 | 10% | 稳定兜底信号 |

置信度路由：
- `≥0.94`：后端自动合并
- `≥0.78` 但 < 0.94：交给 LLM 判歧
- `<0.78`：进入人工审核页面

## 目录结构

```
mdm_data/
├── mdm_matching/          # Python 核心模块
│   ├── preprocess.py      # 数据预处理（NFKC、归一化）
│   ├── features.py        # 特征工程（相似度特征）
│   ├── train.py           # XGBoost + MLP 训练
│   ├── service.py         # FastAPI 服务（供 Dify 调用）
│   ├── smoke_test.py      # 本地自检脚本
│   └── ...
├── dify/                  # Dify 工作流 DSL
│   ├── mdm_workflow.yml   # 主工作流
│   └── schema_analysis_prompt.md
├── frontend/              # 人工审核页面
│   ├── index.html
│   ├── dashboard.html
│   ├── app.js / dashboard.js
│   └── styles.css
├── docs/                  # 技术文档
│   ├── 快速跑通指南.md
│   ├── implementation_plan.md
│   ├── 技术报告.md
│   ├── 跨设备部署运营方案.md
│   └── schema_rag_knowledge.md
├── structured_amazon_google/  # 样例数据集
├── artifacts/              # 训练产出（模型、数据库）
├── requirements.txt        # Python 依赖
└── README.md               # 本文件
```

## 快速开始

### 1. 安装依赖

```bash
cd /path/to/mdm_data
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 训练模型

```bash
python3 -m mdm_matching.train --data-dir structured_amazon_google --output-dir artifacts
```

### 3. 启动服务

```bash
export PUBLIC_REVIEW_BASE_URL='http://127.0.0.1:8000'
python3 -m uvicorn mdm_matching.service:app --host 0.0.0.0 --port 8000
```

### 4. 运行自检

```bash
python3 -m mdm_matching.smoke_test
```

### 5. 导入 Dify 工作流

在 Dify 中导入 `dify/mdm_workflow.yml`，创建知识库并填入知识库 ID，设置环境变量：

```
MDM_API_BASE=http://host.docker.internal:8000
```

然后运行工作流，传入 `structured_amazon_google/tableA.csv` + `tableB.csv`。

## API 清单

| 端点 | 功能 |
|------|------|
| `GET /health` | 服务健康检查 |
| `POST /validate-upload` | CSV 上传校验 |
| `POST /ingest-and-match` | 核心：摄入 + 匹配 + 粗筛 |
| `POST /candidate-batch` | Dify 拉取冲突簇批次 |
| `POST /commit-llm-batch` | 写入 LLM 结构化结果 |
| `POST /finalize-match-run` | 最终质量收尾 |
| `GET /admin/entity-quality` | 质量验收报告 |

## 质量验收标准

```json
{
  "valid": true,
  "entity_count": 4200,
  "golden_records_count": 4200,
  "duplicate_source_link_count": 0,
  "uncertain_decision_count": 0
}
```

## 技术栈

- **Python 3.10+**：FastAPI, XGBoost, scikit-learn, SQLAlchemy
- **Dify 1.15.0**：工作流编排 + LLM 判歧
- **SQLite / MySQL**：数据存储
- **大模型**：温度 0、结构化输出、支持重试
