"""Typed data structures used by the RAG card generator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class FieldStatistics:
    """Profiling result for one logical field across tableA and tableB."""

    is_nullable: bool
    null_count: int
    total_count: int
    null_rate: float
    unique_count: int
    unique_rate: float
    data_type: str
    max_length: int
    avg_length: float
    min_length: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SplitStatistics:
    """Positive/negative label statistics for train/valid/test files."""

    split_name: str
    total_samples: int
    positive_samples: int
    negative_samples: int
    positive_negative_ratio: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FieldAnalysis:
    """All RAG-facing information generated for one dataset field."""

    dataset_name: str
    entity_type: str
    field_name: str
    field_description: str
    field_statistics: FieldStatistics
    example_values: list[str] = field(default_factory=list)
    recommended_match_method: str = "Embedding Similarity"
    knowledge_text: str = ""

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["field_statistics"] = self.field_statistics.to_dict()
        return record


@dataclass(slots=True)
class DatasetAnalysis:
    """Dataset-level result before flattening into CSV/JSON rows."""

    dataset_name: str
    entity_type: str
    dataset_path: str
    table_a_rows: int
    table_b_rows: int
    fields: list[FieldAnalysis]
    split_statistics: list[SplitStatistics] = field(default_factory=list)

    def split_statistics_text(self) -> str:
        if not self.split_statistics:
            return "未发现 train、valid 或 test 标注文件。"

        parts: list[str] = []
        for item in self.split_statistics:
            parts.append(
                f"{item.split_name}: 总样本 {item.total_samples}, "
                f"正样本 {item.positive_samples}, 负样本 {item.negative_samples}, "
                f"正负比例 {item.positive_negative_ratio}"
            )
        return "；".join(parts) + "。"

