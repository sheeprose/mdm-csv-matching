# Dify 主数据 CSV 匹配工作流

## 可实施性结论

完整流程可以落地，但建议把 Dify 定位为“编排层”，把匹配模型和数据库写入放在外部 API 服务中。

Dify 的代码节点不适合直接运行 XGBoost、MLP、SBERT、向量器或数据库写入逻辑，因为依赖安装、模型加载、模型文件存储和长时间推理都会受到限制。更稳妥的部署方式是：

1. 使用 Python/FastAPI 训练并提供匹配服务。
2. 由 Dify 负责接收 CSV 文件路径、URL 或文件对象。
3. Dify 通过 HTTP 请求节点调用上传校验、摄入匹配和写库接口。
4. 高置信候选交给 Dify 的大模型节点选择黄金记录。分数只能说明两条记录可能匹配，不能说明应该选哪一个标题作为主标题。
5. 低置信候选进入匹配服务自带的人工审核页面。这比 Dify Human Input、飞书、Slack 或独立审批系统更简单。

## 已实现模块

- `mdm_matching.preprocess`：Unicode NFKC 规范化、转小写、移除标点、移除 HTML、合并重复空格、制造商/品牌别名归一化、价格归一化、缺失值标记和字段映射。
- `mdm_matching.features`：标题相似度、TF-IDF 余弦相似度、词集合 Jaccard、制造商相似度、价格相似度和标题长度比例特征。
- `mdm_matching.train`：使用 `structured_amazon_google/train.csv` 训练 XGBoost 和 MLP，并在 `valid.csv` 上验证。
- `mdm_matching.service`：供 Dify 调用的 FastAPI 服务，支持 MySQL/SQLite 自动建表和一个简单的浏览器人工审核页面。
- `dify/mdm_workflow.yml`：可导入 Dify 的工作流 DSL，覆盖上传校验、匹配、置信度分支、大模型选择、人工审核任务、黄金记录写入和最终输出。

## 模型选择

第一版可落地基线使用三类信号：

- XGBoost：基于结构化配对特征。
- MLP：基于同一组归一化特征向量。
- TF-IDF 词法相似度：作为语义模型不可用时的稳定兜底。

SBERT 可以作为第四个评分器后续加入，但应部署在同一个 FastAPI 服务里，而不是放在 Dify 代码节点里。对产品标题而言，SBERT 对改写和近义表达有帮助，但短软件标题容易误匹配，所以仍需要制造商和价格特征约束。

## RRF 决策融合

服务使用“排名归一化融合 + 模型概率”的方式。每批候选中，各评分器先对候选配对排序；排名一致性占最终置信度的 45%，XGBoost 占 35%，MLP 占 10%，词法相似度占 10%。这样既保留 RRF 风格的多模型一致性，又避免把 `0.9` 变成“本批前 10%”这种相对分数。

推荐阈值策略：

- `confidence >= 0.9`：候选足够可信，交给大模型选择黄金记录；大模型必须返回选中的记录/标题后，才能写入 MySQL。
- `confidence < 0.9`：进入人工审核页面。
- 粗筛阶段没有匹配候选的记录：视为新记录，直接写入主数据表。

## Dify 调用接口

启动服务：

```bash
python3 -m mdm_matching.train --data-dir structured_amazon_google --output-dir artifacts
export MDM_DATABASE_URL='mysql+pymysql://mdm_user:mdm_password@127.0.0.1:3306/mdm_data?charset=utf8mb4'
export PUBLIC_REVIEW_BASE_URL='http://127.0.0.1:8000'
python3 -m uvicorn mdm_matching.service:app --host 0.0.0.0 --port 8000
```

Dify 环境变量：

```text
MDM_API_BASE=http://host.docker.internal:8000
```

如果 Dify 不是 Docker 本地部署，可以使用：

```text
MDM_API_BASE=http://127.0.0.1:8000
```

推荐的 MySQL 初始化语句：

```sql
CREATE DATABASE mdm_data CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'mdm_user'@'%' IDENTIFIED BY 'mdm_password';
GRANT ALL PRIVILEGES ON mdm_data.* TO 'mdm_user'@'%';
FLUSH PRIVILEGES;
```

服务会自动创建以下表：

- `golden_records`
- `action_log`
- `review_tasks`

接口清单：

- `GET /health`：检查数据库状态和模型加载状态。
- `POST /validate-upload`：校验必须上传的 CSV 文件和字段结构。
- `POST /ingest-and-match`：执行 CSV 加载、预处理、粗筛、模型评分、排名融合，并自动写入明显不相关的新记录。
- `POST /create-review-task`：保存低置信候选，并返回 `review_url`。
- `GET /review/{task_id}`：人工选择主标题的浏览器页面。
- `POST /commit-golden-record`：写入大模型或人工选择后的黄金记录，并记录操作日志。

## Dify 1.15.0 本地 Docker 文件处理

本地 Docker 部署 Dify 时，MDM 服务通常运行在宿主机上。Dify 容器访问宿主机 FastAPI 服务时，建议使用 `host.docker.internal`。

Dify 文件上传节点在不同配置下可能输出文件对象、文件 URL 或文件 id。当前服务兼容以下形式：

- 本地路径：`/path/to/tableA.csv`
- HTTP URL：`http://.../tableA.csv`
- Dify 文件对象 JSON，其中包含 `url`、`source_url`、`preview_url`、`original_url` 或 `id`
- Dify 文件 id，但需要配置以下环境变量：

```bash
export DIFY_API_BASE='http://127.0.0.1/v1'
export DIFY_API_KEY='app-xxxxxxxx'
```

当只传入 Dify 文件 id 时，服务会从以下地址下载文件：

```text
{DIFY_API_BASE}/files/{file_id}/preview
```

## 仍需确认的事项

1. MySQL 是宿主机服务还是 Docker 服务。如果是宿主机 MySQL，`MDM_DATABASE_URL` 里使用 `127.0.0.1`；如果是 Docker Compose 中的 MySQL，使用 Compose 服务名。
2. Dify 开始节点最终使用文本路径/URL 输入，还是原生文件输入。当前 DSL 保留文本输入，因为导入兼容性最高；拿到你当前 Dify 实例导出的样例 DSL 后，可以改成原生文件变量。
3. `0.9` 阈值仍需按业务容忍度校准。当前验证结果是高精度、低召回，因此较多记录会进入人工审核。
