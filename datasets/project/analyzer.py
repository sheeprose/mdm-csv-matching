"""Core analysis logic for ER-Magellan RAG dataset cards."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from schema import DatasetAnalysis, FieldAnalysis, FieldStatistics, SplitStatistics
from utils import clean_scalar, normalize_field_name, read_csv_robust, unique_preserve_order


LOGGER = logging.getLogger(__name__)


FIELD_DESCRIPTIONS: dict[str, str] = {
    "id": "实体唯一标识符，用于在同一张表内定位一条记录。",
    "name": "实体名称，通常是判断两条记录是否指向同一实体的核心字段。",
    "title": "标题字段，常用于商品、论文、音乐或文本实体的主名称匹配。",
    "manufacturer": "制造商或生产厂商，可用于校验商品来源是否一致。",
    "brand": "品牌名称，是商品实体解析中的重要辅助字段。",
    "price": "价格字段，适合作为数值相似度或弱约束特征。",
    "description": "实体描述文本，包含补充属性和上下文，适合使用语义向量比较。",
    "content": "长文本内容，通常包含实体的综合介绍、属性和上下文信息。",
    "authors": "论文作者列表，是学术论文匹配的重要辅助字段。",
    "venue": "论文发表会议、期刊或出版场所，可辅助判断论文来源。",
    "year": "年份字段，常用于出版时间、发行时间或时间范围校验。",
    "addr": "地址字段，用于餐厅、地点或机构的地理位置匹配。",
    "address": "地址字段，用于餐厅、地点或机构的地理位置匹配。",
    "city": "城市名称，可作为地理位置匹配的辅助特征。",
    "phone": "联系电话，格式规范时可作为高置信度匹配特征。",
    "type": "实体类型或类别字段，可用于过滤和辅助判断。",
    "class": "类别或标签字段，表示实体所属分类。",
    "beer_name": "啤酒名称，是啤酒实体解析中的核心比较字段。",
    "brew_factory_name": "啤酒厂或酿造厂名称，用于判断生产来源是否一致。",
    "brewery": "啤酒厂或酿造厂名称，用于判断生产来源是否一致。",
    "style": "啤酒风格或产品风格，可作为类别相似度特征。",
    "abv": "酒精度字段，适合进行数值或区间相似度比较。",
    "song_name": "歌曲名称，是音乐实体解析中的核心匹配字段。",
    "artist_name": "艺人或歌手名称，是音乐记录匹配的重要辅助字段。",
    "album_name": "专辑名称，可用于校验歌曲来源和版本。",
    "genre": "音乐流派或类别，可作为弱匹配特征。",
    "copyright": "版权信息，通常包含发行方和年份，可作为辅助上下文。",
    "time": "歌曲时长或时间字段，适合进行数值或格式化比较。",
    "released": "发行日期，可用于校验音乐作品版本。",
}


ENTITY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("beer", "brew", "abv"), "啤酒"),
    (("dblp", "acm", "scholar", "authors", "venue"), "学术论文"),
    (("itunes", "song", "artist", "album", "music"), "音乐"),
    (("fodors", "zagats", "restaurant", "addr", "phone"), "餐厅"),
    (("company", "wiki"), "公司"),
    (("amazon", "google", "walmart", "abt", "buy", "product", "manufacturer", "brand"), "商品(Product)"),
)


TEXT_MATCH_FIELDS = {
    "name",
    "title",
    "beer_name",
    "song_name",
    "description",
    "content",
    "authors",
    "venue",
}

EXACT_OR_FUZZY_FIELDS = {
    "manufacturer",
    "brand",
    "brew_factory_name",
    "brewery",
    "artist_name",
    "album_name",
    "city",
    "phone",
    "genre",
    "style",
    "type",
    "class",
}

NUMERIC_FIELDS = {"price", "abv", "year", "time", "released"}


class DatasetAnalyzer:
    """Analyze all ER-Magellan datasets below a root directory."""

    def __init__(self, root_dir: Path, sample_size: int = 5, random_seed: int = 42) -> None:
        self.root_dir = root_dir.resolve()
        self.sample_size = sample_size
        self.random_seed = random_seed
        random.seed(random_seed)

    def discover_datasets(self) -> list[Path]:
        """Find child directories containing tableA.csv and tableB.csv."""

        if not self.root_dir.exists():
            raise FileNotFoundError(f"Dataset root does not exist: {self.root_dir}")

        datasets: list[Path] = []
        for child in sorted(self.root_dir.iterdir()):
            if not child.is_dir():
                continue
            if (child / "tableA.csv").exists() and (child / "tableB.csv").exists():
                datasets.append(child)

        if not datasets:
            raise RuntimeError(f"No datasets with tableA.csv/tableB.csv found under {self.root_dir}")
        return datasets

    def analyze_all(self) -> list[DatasetAnalysis]:
        """Analyze every discovered dataset."""

        analyses: list[DatasetAnalysis] = []
        for dataset_dir in self.discover_datasets():
            try:
                LOGGER.info("Analyzing dataset: %s", dataset_dir.name)
                analyses.append(self.analyze_dataset(dataset_dir))
            except Exception:
                LOGGER.exception("Failed to analyze dataset %s", dataset_dir)
        return analyses

    def analyze_dataset(self, dataset_dir: Path) -> DatasetAnalysis:
        """Analyze one dataset directory."""

        table_a = read_csv_robust(dataset_dir / "tableA.csv")
        table_b = read_csv_robust(dataset_dir / "tableB.csv")

        field_names = self._collect_logical_fields(table_a, table_b)
        entity_type = self.infer_entity_type(dataset_dir.name, field_names)
        split_statistics = self.analyze_split_files(dataset_dir)

        fields: list[FieldAnalysis] = []
        dataset_summary_context: list[dict[str, Any]] = []
        for field_name in field_names:
            series = self._combined_field_series(field_name, table_a, table_b)
            stats = self.profile_field(series)
            examples = self.sample_examples(series)
            description = self.describe_field(field_name, entity_type)
            match_method = self.recommend_match_method(field_name, stats)
            dataset_summary_context.append(
                {
                    "field_name": field_name,
                    "description": description,
                    "match_method": match_method,
                    "null_rate": stats.null_rate,
                    "unique_rate": stats.unique_rate,
                }
            )
            fields.append(
                FieldAnalysis(
                    dataset_name=dataset_dir.name,
                    entity_type=entity_type,
                    field_name=field_name,
                    field_description=description,
                    field_statistics=stats,
                    example_values=examples,
                    recommended_match_method=match_method,
                )
            )

        analysis = DatasetAnalysis(
            dataset_name=dataset_dir.name,
            entity_type=entity_type,
            dataset_path=str(dataset_dir),
            table_a_rows=len(table_a),
            table_b_rows=len(table_b),
            fields=fields,
            split_statistics=split_statistics,
        )
        knowledge_text = self.build_knowledge_text(analysis, dataset_summary_context)
        for field in analysis.fields:
            field.knowledge_text = knowledge_text
        return analysis

    def _collect_logical_fields(self, table_a: pd.DataFrame, table_b: pd.DataFrame) -> list[str]:
        """Collect normalized field names from both entity tables."""

        fields = [normalize_field_name(col) for col in [*table_a.columns, *table_b.columns]]
        return unique_preserve_order(fields)

    def _combined_field_series(
        self,
        logical_field: str,
        table_a: pd.DataFrame,
        table_b: pd.DataFrame,
    ) -> pd.Series:
        """Concatenate tableA/tableB columns matching one logical field."""

        pieces: list[pd.Series] = []
        for frame in (table_a, table_b):
            for column in frame.columns:
                if normalize_field_name(column) == logical_field:
                    pieces.append(frame[column])
        if not pieces:
            return pd.Series(dtype="object")
        return pd.concat(pieces, ignore_index=True)

    def infer_entity_type(self, dataset_name: str, field_names: list[str]) -> str:
        """Infer entity type using dataset name and schema hints."""

        haystack = " ".join([dataset_name.lower(), *[name.lower() for name in field_names]])
        for hints, entity_type in ENTITY_HINTS:
            if any(hint in haystack for hint in hints):
                return entity_type
        return "Unknown"

    def describe_field(self, field_name: str, entity_type: str) -> str:
        """Generate a natural-language field description."""

        normalized = field_name.lower()
        if normalized in FIELD_DESCRIPTIONS:
            return FIELD_DESCRIPTIONS[normalized]

        readable = field_name.replace("_", " ").replace("-", " ")
        return f"{readable} 字段，表示 {entity_type} 实体中的结构化属性，可用于实体解析的辅助比较。"

    def profile_field(self, series: pd.Series) -> FieldStatistics:
        """Compute null, uniqueness, type, and length statistics."""

        total_count = int(len(series))
        null_mask = series.isna() | series.map(lambda value: clean_scalar(value) == "")
        null_count = int(null_mask.sum())
        non_null = series[~null_mask]

        cleaned = non_null.map(clean_scalar)
        unique_count = int(cleaned.nunique(dropna=True))
        unique_rate = round(unique_count / len(cleaned), 6) if len(cleaned) else 0.0
        null_rate = round(null_count / total_count, 6) if total_count else 0.0

        lengths = cleaned.map(len)
        if len(lengths):
            max_length = int(lengths.max())
            min_length = int(lengths.min())
            avg_length = round(float(lengths.mean()), 3)
        else:
            max_length = min_length = 0
            avg_length = 0.0

        data_type = self.infer_data_type(cleaned)
        return FieldStatistics(
            is_nullable=bool(null_count > 0),
            null_count=null_count,
            total_count=total_count,
            null_rate=null_rate,
            unique_count=unique_count,
            unique_rate=unique_rate,
            data_type=data_type,
            max_length=max_length,
            avg_length=avg_length,
            min_length=min_length,
        )

    def infer_data_type(self, cleaned: pd.Series) -> str:
        """Infer a practical data type from cleaned non-null values."""

        if cleaned.empty:
            return "empty"

        sample = cleaned.sample(min(1000, len(cleaned)), random_state=self.random_seed)
        numeric = pd.to_numeric(
            sample.str.replace(r"[$,%]", "", regex=True).str.strip(),
            errors="coerce",
        )
        numeric_rate = float(numeric.notna().mean()) if len(sample) else 0.0

        if numeric_rate >= 0.9:
            return "numeric"
        if sample.map(lambda value: len(value) > 120).mean() >= 0.5:
            return "long_text"
        return "text"

    def sample_examples(self, series: pd.Series) -> list[str]:
        """Sample up to N distinct non-empty values."""

        cleaned = series.map(clean_scalar)
        values = [value for value in cleaned.tolist() if value]
        values = unique_preserve_order(values)
        if len(values) <= self.sample_size:
            return values
        return random.sample(values, self.sample_size)

    def recommend_match_method(self, field_name: str, stats: FieldStatistics) -> str:
        """Recommend matching methods from field semantics and profile stats."""

        normalized = field_name.lower()
        if normalized in {"id", "_id"}:
            return "Do Not Match Directly; use only as record identifier"
        if normalized in NUMERIC_FIELDS or stats.data_type == "numeric":
            return "Numeric Similarity + Range Difference"
        if normalized in TEXT_MATCH_FIELDS or stats.data_type == "long_text":
            if normalized in {"description", "content"} or stats.max_length > 120:
                return "Embedding Similarity + BM25"
            return "TF-IDF + BM25 + Embedding Similarity"
        if normalized in EXACT_OR_FUZZY_FIELDS:
            if stats.unique_rate > 0.8:
                return "Exact Match + Fuzzy Match + Edit Distance"
            return "Exact Match + Token Similarity"
        return "Embedding Similarity + Token Jaccard + Edit Distance"

    def analyze_split_files(self, dataset_dir: Path) -> list[SplitStatistics]:
        """Analyze train/valid/test label distributions if files exist."""

        result: list[SplitStatistics] = []
        for split_name in ("train", "valid", "test"):
            path = dataset_dir / f"{split_name}.csv"
            if not path.exists():
                continue
            try:
                frame = read_csv_robust(path)
                label_column = self._find_label_column(frame)
                if label_column is None:
                    LOGGER.warning("No binary label column found in %s", path)
                    continue
                labels = pd.to_numeric(frame[label_column], errors="coerce")
                positive = int((labels == 1).sum())
                negative = int((labels == 0).sum())
                total = int(labels.notna().sum())
                ratio = self._format_ratio(positive, negative)
                result.append(
                    SplitStatistics(
                        split_name=split_name,
                        total_samples=total,
                        positive_samples=positive,
                        negative_samples=negative,
                        positive_negative_ratio=ratio,
                    )
                )
            except Exception:
                LOGGER.exception("Failed to analyze split file %s", path)
        return result

    def _find_label_column(self, frame: pd.DataFrame) -> str | None:
        """Find the binary label column in a pair file."""

        preferred = ("label", "gold", "is_match", "match")
        lower_to_original = {column.lower(): column for column in frame.columns}
        for name in preferred:
            if name in lower_to_original:
                return lower_to_original[name]

        for column in frame.columns:
            values = pd.to_numeric(frame[column], errors="coerce").dropna()
            unique_values = set(values.unique().tolist())
            if unique_values and unique_values.issubset({0, 1}):
                return column
        return None

    def _format_ratio(self, positive: int, negative: int) -> str:
        if negative == 0:
            return f"{positive}:0"
        return f"{round(positive / negative, 6)}:1"

    def build_knowledge_text(
        self,
        analysis: DatasetAnalysis,
        field_context: list[dict[str, Any]],
    ) -> str:
        """Build one long natural-language chunk for Dify Knowledge."""

        field_names = [item["field_name"] for item in field_context]
        important_fields = self._rank_important_fields(field_context)
        method_parts = [
            f"{item['field_name']} 推荐使用 {item['match_method']}"
            for item in field_context
            if item["field_name"].lower() not in {"id", "_id"}
        ]
        field_explanations = [
            f"{item['field_name']}：{item['description']}"
            for item in field_context
        ]

        text = (
            f"Dataset Name: {analysis.dataset_name}。"
            f"Entity Type: {analysis.entity_type}。"
            f"该数据集包含 tableA 和 tableB 两张实体表，tableA 有 {analysis.table_a_rows} 条记录，"
            f"tableB 有 {analysis.table_b_rows} 条记录，适合用于实体解析、记录链接、数据去重和主数据治理场景。"
            f"数据集的主要字段包括 {', '.join(field_names)}。"
            f"字段解释如下：{'；'.join(field_explanations)}。"
            f"从字段语义看，{', '.join(important_fields)} 是优先比较字段，通常承担召回、排序或高置信度校验作用。"
            f"推荐匹配策略为：{'；'.join(method_parts)}。"
            f"标注集统计：{analysis.split_statistics_text()}"
            f"在构建 RAG 知识库时，本段文本可作为数据集级知识块，用于回答该数据集是什么、包含哪些字段、字段如何解释、"
            f"哪些字段应重点比较以及如何选择匹配算法。对于 {analysis.entity_type} 实体解析任务，应优先使用名称类或标题类字段建立候选召回，"
            f"再结合品牌、作者、厂商、地址、类别、年份、价格、酒精度、时长等辅助属性进行重排和冲突校验。"
            f"长文本字段更适合使用 Embedding 或 BM25 捕捉语义相关性，短文本字段适合使用 TF-IDF、编辑距离、Token Jaccard 或精确匹配，"
            f"数值字段应使用差值、相对误差或区间相似度。若字段缺失率较高，应降低其规则权重；若唯一率较高且字段稳定，则可提高其匹配贡献。"
            f"该知识块面向 Dify Knowledge 导入，能够支持数据治理人员、RAG 应用和自动化匹配服务理解 {analysis.dataset_name} 的 schema、"
            f"样本分布、字段价值和推荐匹配方法。"
        )

        if len(text) < 300:
            text += (
                "补充说明：实体解析任务的核心目标是判断来自两个来源的记录是否描述同一个真实世界实体。"
                "因此需要同时考虑字段的文本相似度、结构化属性一致性、缺失情况、数据噪声和标注样本的正负比例，"
                "并将这些信息转化为可检索、可解释、可复用的知识库内容。"
            )
        return " ".join(text.split())

    def _rank_important_fields(self, field_context: list[dict[str, Any]]) -> list[str]:
        """Rank fields by semantic importance and basic data quality."""

        priority = {
            "name": 100,
            "title": 100,
            "beer_name": 100,
            "song_name": 100,
            "content": 90,
            "description": 85,
            "manufacturer": 80,
            "brand": 80,
            "authors": 80,
            "artist_name": 80,
            "brew_factory_name": 75,
            "brewery": 75,
            "venue": 70,
            "addr": 70,
            "address": 70,
            "phone": 65,
            "price": 50,
            "year": 50,
            "abv": 50,
        }

        scored: list[tuple[float, str]] = []
        for item in field_context:
            field_name = item["field_name"]
            normalized = field_name.lower()
            if normalized in {"id", "_id"}:
                continue
            score = priority.get(normalized, 40)
            score -= float(item.get("null_rate", 0.0)) * 20
            score += min(float(item.get("unique_rate", 0.0)), 1.0) * 5
            scored.append((score, field_name))
        scored.sort(reverse=True)
        return [name for _, name in scored[:5]] or ["核心业务字段"]

