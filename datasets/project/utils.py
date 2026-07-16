"""Utility helpers for CSV reading, profiling, and output serialization."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


LOGGER = logging.getLogger(__name__)


CSV_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "latin1")


def configure_logging(verbose: bool = False) -> None:
    """Configure a compact console logger."""

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def read_csv_robust(path: Path) -> pd.DataFrame:
    """Read a CSV file with several common encodings.

    ER-Magellan datasets are usually comma-separated and small enough to fit in
    memory. We keep values as strings where possible so IDs and prices are not
    accidentally reformatted.
    """

    last_error: Exception | None = None
    for encoding in CSV_ENCODINGS:
        try:
            return pd.read_csv(
                path,
                encoding=encoding,
                low_memory=False,
                keep_default_na=True,
                na_values=["", " ", "nan", "NaN", "NULL", "None"],
            )
        except UnicodeDecodeError as exc:
            last_error = exc
        except pd.errors.ParserError as exc:
            last_error = exc
            LOGGER.warning("Parser error for %s with %s: %s", path, encoding, exc)

    raise RuntimeError(f"Failed to read CSV file {path}: {last_error}") from last_error


def normalize_field_name(field_name: str) -> str:
    """Normalize prefixes used in pair files and return the logical field."""

    text = field_name.strip()
    for prefix in ("table1.", "table2.", "ltable_", "rtable_", "left_", "right_"):
        if text.lower().startswith(prefix):
            return text[len(prefix) :]
    return text


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    """Return unique values while preserving their first occurrence order."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def clean_scalar(value: Any) -> str:
    """Convert one scalar value into a compact text representation."""

    if pd.isna(value):
        return ""
    text = str(value).strip()
    return " ".join(text.split())


def compact_json(value: Any) -> str:
    """Serialize nested values as compact, Dify-friendly JSON text."""

    return json.dumps(make_json_safe(value), ensure_ascii=False, separators=(",", ":"))


def make_json_safe(value: Any) -> Any:
    """Convert numpy/pandas values into JSON-serializable native objects."""

    if isinstance(value, dict):
        return {str(k): make_json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [make_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def ensure_directory(path: Path) -> None:
    """Create a directory and all parents if needed."""

    path.mkdir(parents=True, exist_ok=True)

