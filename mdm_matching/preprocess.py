from __future__ import annotations

import html
import re
import string
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = ("id", "title", "manufacturer", "price")

BRAND_ALIASES = {
    "ms": "microsoft",
    "microsoft corp": "microsoft",
    "microsoft corporation": "microsoft",
    "adobe systems": "adobe",
    "adobe systems incorporated": "adobe",
    "intuit inc": "intuit",
    "symantec corporation": "symantec",
    "ca": "computer associates",
}

MANUFACTURER_ALIASES = {
    "broderbund software": "broderbund",
    "sage software inc": "sage software",
    "microsoft corporation": "microsoft",
    "adobe systems": "adobe",
    "adobe systems incorporated": "adobe",
}

PUNCT_TRANSLATION = str.maketrans({ch: " " for ch in string.punctuation})
HTML_TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str]
    columns: list[str]


def normalize_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = html.unescape(text)
    text = HTML_TAG_RE.sub(" ", text)
    text = text.lower()
    text = text.translate(PUNCT_TRANSLATION)
    text = SPACE_RE.sub(" ", text).strip()
    return text


def normalize_brand(value: Any, aliases: dict[str, str] | None = None) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    alias_map = aliases or BRAND_ALIASES
    return alias_map.get(normalized, normalized)


def normalize_manufacturer(value: Any) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    return MANUFACTURER_ALIASES.get(normalized, BRAND_ALIASES.get(normalized, normalized))


def normalize_price(value: Any) -> float | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    cleaned = re.sub(r"[^0-9.]", "", str(value))
    if cleaned == "":
        return None
    try:
        price = Decimal(cleaned)
    except InvalidOperation:
        return None
    if price < 0:
        return None
    return float(price.quantize(Decimal("0.01")))


def validate_columns(df: pd.DataFrame, required: tuple[str, ...] = REQUIRED_COLUMNS) -> ValidationResult:
    columns = list(df.columns)
    missing = [column for column in required if column not in columns]
    errors = [f"missing required column: {column}" for column in missing]
    return ValidationResult(valid=not errors, errors=errors, columns=columns)


def preprocess_table(df: pd.DataFrame) -> pd.DataFrame:
    validation = validate_columns(df)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))

    result = df.copy()
    result["title_norm"] = result["title"].map(normalize_text)
    result["manufacturer_norm"] = result["manufacturer"].map(normalize_manufacturer)
    result["brand_norm"] = result["manufacturer_norm"].map(normalize_brand)
    result["price_norm"] = result["price"].map(normalize_price)
    result["price_missing"] = result["price_norm"].isna().astype(int)
    result["title_missing"] = (result["title_norm"] == "").astype(int)
    result["manufacturer_missing"] = (result["manufacturer_norm"] == "").astype(int)
    return result


def map_input_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map common incoming field names to the canonical table schema."""
    aliases = {
        "product_id": "id",
        "sku": "id",
        "name": "title",
        "product_name": "title",
        "brand": "manufacturer",
        "maker": "manufacturer",
        "vendor": "manufacturer",
        "list_price": "price",
        "amount": "price",
    }
    renamed = {column: aliases.get(normalize_text(column), column) for column in df.columns}
    return df.rename(columns=renamed)

