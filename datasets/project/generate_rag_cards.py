"""Generate Dify-ready RAG dataset cards for ER-Magellan datasets.

Run:
    python generate_rag_cards.py

Optional:
    python generate_rag_cards.py --root D:\existingDatasets --output output
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from analyzer import DatasetAnalyzer
from utils import compact_json, configure_logging, ensure_directory, make_json_safe


LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate RAG dataset cards from ER-Magellan existingDatasets.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Dataset root containing dataset subdirectories. Defaults to the parent of this project directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory. Defaults to ./output beside this script.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=5,
        help="Number of example values sampled for each field.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print debug logging.",
    )
    return parser.parse_args()


def default_dataset_root(script_dir: Path) -> Path:
    """Choose a dataset root without hard-coding machine-specific paths."""

    parent = script_dir.parent
    if (parent / "tableA.csv").exists() and (parent / "tableB.csv").exists():
        return script_dir

    sibling_dataset_markers = list(parent.glob("*/tableA.csv"))
    if sibling_dataset_markers:
        return parent

    cwd = Path.cwd()
    if list(cwd.glob("*/tableA.csv")):
        return cwd

    return parent


def flatten_records(analyses: list[Any]) -> list[dict[str, Any]]:
    """Flatten dataset analyses into CSV/JSON rows."""

    records: list[dict[str, Any]] = []
    for analysis in analyses:
        for field in analysis.fields:
            record = field.to_record()
            record["field_statistics"] = compact_json(record["field_statistics"])
            record["example_values"] = compact_json(record["example_values"])
            records.append(record)
    return records


def write_outputs(records: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    """Write CSV and JSON outputs with identical logical content."""

    ensure_directory(output_dir)
    csv_path = output_dir / "rag_dataset_cards.csv"
    json_path = output_dir / "rag_dataset_cards.json"

    frame = pd.DataFrame(
        records,
        columns=[
            "dataset_name",
            "entity_type",
            "field_name",
            "field_description",
            "field_statistics",
            "example_values",
            "recommended_match_method",
            "knowledge_text",
        ],
    )
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")

    with json_path.open("w", encoding="utf-8") as file:
        json.dump(make_json_safe(records), file, ensure_ascii=False, indent=2)

    return csv_path, json_path


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)

    script_dir = Path(__file__).resolve().parent
    root_dir = (args.root or default_dataset_root(script_dir)).resolve()
    output_dir = (args.output or (script_dir / "output")).resolve()

    LOGGER.info("Dataset root: %s", root_dir)
    LOGGER.info("Output directory: %s", output_dir)

    analyzer = DatasetAnalyzer(root_dir=root_dir, sample_size=args.sample_size)
    analyses = analyzer.analyze_all()
    records = flatten_records(analyses)

    if not records:
        raise RuntimeError("No RAG card records generated. Please check dataset files and logs.")

    csv_path, json_path = write_outputs(records, output_dir)
    LOGGER.info("Generated %s records from %s datasets.", len(records), len(analyses))
    LOGGER.info("CSV: %s", csv_path)
    LOGGER.info("JSON: %s", json_path)


if __name__ == "__main__":
    main()

