# ER-Magellan RAG Dataset Cards

这个项目会自动扫描 ER-Magellan `existingDatasets` 目录下的所有数据集，为每个数据集的每个字段生成可导入 Dify Knowledge 的 RAG 知识库卡片。

## 项目结构

```text
project/
  generate_rag_cards.py
  analyzer.py
  schema.py
  utils.py
  requirements.txt
  README.md
  output/
    rag_dataset_cards.csv
    rag_dataset_cards.json
```

## 功能

- 自动发现包含 `tableA.csv` 和 `tableB.csv` 的数据集目录，不写死具体数据集名称。
- 自动读取 `train.csv`、`valid.csv`、`test.csv`，如果文件存在则统计正负样本。
- 自动推断实体类型，例如商品、啤酒、学术论文、音乐、餐厅、公司。
- 自动抽取 schema，并为字段生成中文解释。
- 自动计算字段统计：是否为空、空值率、唯一值数量、唯一率、数据类型、最大长度、平均长度、最小长度。
- 每个字段随机抽取 5 个示例值。
- 自动推荐匹配方法，例如 BM25、TF-IDF、Embedding、Exact Match、Edit Distance、Numeric Similarity。
- 生成不少于 300 字的 `knowledge_text`，适合作为 Dify Knowledge 的 RAG Chunk。
- 同时输出 CSV 和 JSON。

## 环境

Python 3.11

安装依赖：

```bash
pip install -r requirements.txt
```

## 运行

在 `project` 目录执行：

```bash
python generate_rag_cards.py
```

默认情况下，脚本会把 `project` 的上一级目录作为数据集根目录。因此如果目录结构如下，可以直接运行：

```text
existingDatasets/
  structured_amazon_google/
  structured_beer/
  ...
  project/
```

也可以显式指定数据目录和输出目录：

```bash
python generate_rag_cards.py --root D:\existingDatasets --output output
```

## 输出字段

`rag_dataset_cards.csv` 和 `rag_dataset_cards.json` 都包含以下字段：

- `dataset_name`
- `entity_type`
- `field_name`
- `field_description`
- `field_statistics`
- `example_values`
- `recommended_match_method`
- `knowledge_text`

其中 `field_statistics` 和 `example_values` 在 CSV 中使用 JSON 字符串保存，便于 Dify 或其他下游系统解析。

## 说明

本项目不依赖任何付费 API，也不会调用外部模型。所有实体类型推断、字段解释、统计分析、匹配策略建议和知识库文本生成都在本地完成。

