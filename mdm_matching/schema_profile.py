from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

import pandas as pd

from mdm_matching.preprocess import normalize_price, normalize_text


TEXT_ROLES = {"primary_name", "creator_or_brand", "location_or_context", "category"}
ID_ROLE_NAMES = {"id", "row_id", "uuid", "guid", "class"}
PRIMARY_NAME_NAMES = {"title", "name", "product_name", "paper_title", "restaurant_name", "beer_name", "company_name"}
CREATOR_BRAND_NAMES = {
    "authors",
    "author",
    "manufacturer",
    "brand",
    "vendor",
    "maker",
    "brew_factory_name",
    "brewery",
    "brewery_name",
    "brewer",
}
LOCATION_CONTEXT_NAMES = {"addr", "address", "city", "venue", "journal", "conference"}
NUMERIC_AUX_NAMES = {"year", "price", "list_price", "amount", "abv", "alcohol_by_volume"}
PHONE_NAMES = {"phone", "telephone", "tel"}
PRIMARY_NAME_TOKENS = {"title", "name", "label"}
CREATOR_BRAND_TOKENS = {
    "author",
    "authors",
    "brand",
    "brew",
    "brewery",
    "brewer",
    "factory",
    "manufacturer",
    "maker",
    "vendor",
}
LOCATION_CONTEXT_TOKENS = {"addr", "address", "city", "venue", "journal", "conference", "state", "country", "zip"}
NUMERIC_AUX_TOKENS = {"year", "price", "amount", "abv", "volume", "rating", "score"}
CATEGORY_TOKENS = {"type", "category", "genre", "style", "class"}


DOMAIN_RULES = {
    "publication": {"required": {"primary_name"}, "signals": {"authors", "author", "venue", "journal", "conference", "year"}},
    "restaurant": {"required": {"primary_name"}, "signals": {"addr", "address", "city", "phone", "telephone", "type"}},
    "product": {"required": {"primary_name"}, "signals": {"manufacturer", "brand", "vendor", "maker", "price", "list_price"}},
    "beer": {"required": {"primary_name"}, "signals": {"beer_name", "brew_factory_name", "brewery", "brewery_name", "style", "abv"}},
}


def table_profile(df: pd.DataFrame, sample_size: int = 5) -> dict[str, Any]:
    return {
        "columns": list(df.columns),
        "row_count": int(len(df)),
        "sample_rows": df.head(sample_size).fillna("").astype(str).to_dict(orient="records"),
        "column_stats": {column: column_profile(df[column], column) for column in df.columns},
    }


def column_profile(series: pd.Series, column_name: str) -> dict[str, Any]:
    non_null = series.dropna()
    as_text = non_null.astype(str).map(str.strip)
    non_empty = as_text[as_text != ""]
    total = max(len(series), 1)
    normalized_name = normalize_column_name(column_name)
    samples = non_empty.head(5).tolist()
    unique_ratio = float(non_empty.nunique() / max(len(non_empty), 1)) if len(non_empty) else 0.0
    numeric_ratio = float(non_empty.map(is_number_like).mean()) if len(non_empty) else 0.0
    year_ratio = float(non_empty.map(is_year_like).mean()) if len(non_empty) else 0.0
    phone_ratio = float(non_empty.map(is_phone_like).mean()) if len(non_empty) else 0.0
    avg_length = float(non_empty.map(len).mean()) if len(non_empty) else 0.0
    return {
        "name": str(column_name),
        "normalized_name": normalized_name,
        "role": infer_column_role(normalized_name, unique_ratio, numeric_ratio, year_ratio, phone_ratio),
        "null_ratio": float(1.0 - len(non_empty) / total),
        "unique_ratio": unique_ratio,
        "numeric_ratio": numeric_ratio,
        "year_ratio": year_ratio,
        "phone_ratio": phone_ratio,
        "avg_length": avg_length,
        "samples": samples,
    }


def analyze_table_pair(table_a: pd.DataFrame, table_b: pd.DataFrame, llm_profile: Any | None = None) -> dict[str, Any]:
    if isinstance(llm_profile, dict) and llm_profile:
        return normalize_schema_profile(llm_profile, table_a, table_b)

    profile_a = table_profile(table_a)
    profile_b = table_profile(table_b)
    roles_a = {column: profile_a["column_stats"][column]["role"] for column in table_a.columns}
    roles_b = {column: profile_b["column_stats"][column]["role"] for column in table_b.columns}
    roles_a, roles_b = refine_pair_roles(profile_a, profile_b, roles_a, roles_b)
    domain_a = infer_domain(profile_a)
    domain_b = infer_domain(profile_b)
    column_mapping = infer_column_mapping(table_a, table_b, roles_a, roles_b)
    ignored_columns = sorted(
        {column for column, role in roles_a.items() if role == "identifier"}
        | {column for column, role in roles_b.items() if role == "identifier"}
    )
    comparable = comparable_mapping(column_mapping, roles_a)
    related, reason, confidence = judge_related(domain_a, domain_b, comparable, roles_a, roles_b)
    if has_exact_column_schema(table_a, table_b):
        related = True
        reason = "Table A and Table B have exactly the same column headers in the same order; treated as the same entity schema."
        confidence = max(confidence, 0.98)
    if (
        not related
        and not has_known_domain_mismatch(domain_a, domain_b)
        and is_structurally_compatible(table_a, table_b, column_mapping, roles_a, roles_b)
    ):
        related = True
        reason = "Tables have compatible mapped non-identifier attributes; treated as a new generic entity schema."
        confidence = 0.82 if domain_a == "unknown" or domain_b == "unknown" else 0.88
    weights = normalize_weights(default_weights(comparable, roles_a))
    blocking_columns = choose_blocking_columns(comparable, roles_a)
    master_columns = build_master_columns(column_mapping, roles_a, ignored_columns, weights, blocking_columns)
    return {
        "related": related,
        "relation_type": "same_entity_table" if related else "schema_mismatch",
        "entity_type": domain_a if domain_a == domain_b else "unknown",
        "domain_a": domain_a,
        "domain_b": domain_b,
        "confidence": confidence,
        "reason": reason,
        "column_mapping": column_mapping,
        "column_roles": roles_a,
        "table_b_column_roles": roles_b,
        "ignored_columns": ignored_columns,
        "low_relevance_columns": [column for column, role in roles_a.items() if role in {"numeric_auxiliary", "category"}],
        "blocking_columns": blocking_columns,
        "weights": weights,
        "master_columns": master_columns,
        "table_a_profile": profile_a,
        "table_b_profile": profile_b,
        "source": "heuristic",
    }


def normalize_schema_profile(profile: dict[str, Any], table_a: pd.DataFrame, table_b: pd.DataFrame) -> dict[str, Any]:
    heuristic = analyze_table_pair(table_a, table_b, llm_profile=None)
    merged = dict(heuristic)
    merged.update({key: value for key, value in profile.items() if value is not None})
    mapping = {str(k): str(v) for k, v in (merged.get("column_mapping") or {}).items() if k in table_a.columns and v in table_b.columns}
    roles = dict(heuristic.get("column_roles") or {})
    for column in table_a.columns:
        roles.setdefault(column, heuristic["table_a_profile"]["column_stats"][column]["role"])
    comparable = comparable_mapping(mapping, roles)
    if not comparable:
        merged["related"] = False
        merged["reason"] = "No comparable non-identifier columns remain after validating schema profile."
    merged["column_mapping"] = mapping
    merged["column_roles"] = roles
    provided_weights = {
        column: safe_float((merged.get("weights") or {}).get(column), default=0.0)
        for column in comparable
    }
    if sum(provided_weights.values()) <= 0:
        provided_weights = default_weights(comparable, roles)
    merged["weights"] = normalize_weights(provided_weights)
    merged["blocking_columns"] = [column for column in (merged.get("blocking_columns") or []) if column in comparable]
    if not merged["blocking_columns"]:
        merged["blocking_columns"] = choose_blocking_columns(comparable, roles)
    merged["ignored_columns"] = sorted(set(merged.get("ignored_columns") or []) | {c for c, r in roles.items() if r == "identifier"})
    merged["master_columns"] = build_master_columns(
        mapping,
        roles,
        merged["ignored_columns"],
        merged["weights"],
        merged["blocking_columns"],
    )
    merged["source"] = profile.get("source") or "llm_or_external"
    return merged


def apply_schema_profile(df: pd.DataFrame, profile: dict[str, Any], side: str) -> pd.DataFrame:
    result = df.copy()
    mapping = profile.get("column_mapping") or {}
    if side == "tableA":
        side_columns = {left: left for left in mapping}
    else:
        side_columns = {left: right for left, right in mapping.items()}
    primary = first_column_with_role(profile, side, "primary_name")
    creator = first_column_with_role(profile, side, "creator_or_brand")
    price = first_column_named_or_role(profile, side, {"price", "list_price", "amount"}, "numeric_auxiliary")

    result["id"] = result["id"] if "id" in result.columns else result.index.astype(str)
    result["title"] = result[primary].fillna("").astype(str) if primary in result.columns else result.apply(
        lambda row: " ".join(str(row.get(column, "")) for column in side_columns.values() if column in row and str(row.get(column, "")).strip())[:512],
        axis=1,
    )
    result["manufacturer"] = result[creator].fillna("").astype(str) if creator in result.columns else ""
    result["price"] = result[price] if price in result.columns else ""
    result["title_norm"] = result["title"].map(normalize_text)
    result["manufacturer_norm"] = result["manufacturer"].map(normalize_text)
    result["brand_norm"] = result["manufacturer_norm"]
    result["price_norm"] = result["price"].map(normalize_price)
    result["price_missing"] = result["price_norm"].isna().astype(int)
    result["title_missing"] = (result["title_norm"] == "").astype(int)
    result["manufacturer_missing"] = (result["manufacturer_norm"] == "").astype(int)

    for left_column, side_column in side_columns.items():
        if side_column in result.columns:
            result[f"__match_{left_column}"] = result[side_column]
            result[f"__match_{left_column}_norm"] = result[side_column].map(normalize_text)
    return result


def generic_pair_features(candidate_pairs: pd.DataFrame, profile: dict[str, Any]) -> pd.DataFrame:
    weights = profile.get("weights") or {}
    roles = profile.get("column_roles") or {}
    rows: list[dict[str, Any]] = []
    for _, row in candidate_pairs.iterrows():
        feature_row: dict[str, Any] = {}
        weighted_score = 0.0
        used_weight = 0.0
        for column, weight in weights.items():
            if weight <= 0:
                continue
            role = roles.get(column, "text")
            left = row.get(f"table1.__match_{column}")
            right = row.get(f"table2.__match_{column}")
            score = column_similarity(left, right, role)
            feature_row[f"schema_{column}_similarity"] = score
            weighted_score += weight * score
            used_weight += weight
        feature_row["schema_weighted_similarity"] = weighted_score / used_weight if used_weight else 0.0
        rows.append(feature_row)
    return pd.DataFrame(rows)


def column_similarity(left: Any, right: Any, role: str) -> float:
    if role == "identifier":
        return 0.0
    if role == "numeric_auxiliary":
        return numeric_similarity(left, right)
    if role == "phone":
        left_digits = digits_only(left)
        right_digits = digits_only(right)
        if not left_digits or not right_digits:
            return 0.5
        return 1.0 if left_digits == right_digits else token_jaccard(left_digits, right_digits)
    left_text = normalize_text(left)
    right_text = normalize_text(right)
    return token_jaccard(left_text, right_text)


def infer_column_mapping(table_a: pd.DataFrame, table_b: pd.DataFrame, roles_a: dict[str, str], roles_b: dict[str, str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    unused_b = set(table_b.columns)
    for left in table_a.columns:
        left_norm = normalize_column_name(left)
        exact = next((right for right in unused_b if normalize_column_name(right) == left_norm), None)
        if exact:
            mapping[left] = exact
            unused_b.remove(exact)
            continue
        same_role = [right for right in unused_b if roles_a.get(left) == roles_b.get(right) and roles_a.get(left) != "identifier"]
        if same_role:
            best = max(same_role, key=lambda right: name_similarity(left, right))
            mapping[left] = best
            unused_b.remove(best)
    return mapping


def infer_domain(profile: dict[str, Any]) -> str:
    names = {profile["column_stats"][column]["normalized_name"] for column in profile["columns"]}
    roles = {profile["column_stats"][column]["role"] for column in profile["columns"]}
    best_domain = "unknown"
    best_score = 0
    for domain, rule in DOMAIN_RULES.items():
        if not rule["required"].issubset(roles):
            continue
        score = len(names & rule["signals"])
        if score > best_score:
            best_domain = domain
            best_score = score
    return best_domain


def judge_related(
    domain_a: str,
    domain_b: str,
    comparable: dict[str, str],
    roles_a: dict[str, str],
    roles_b: dict[str, str],
) -> tuple[bool, str, float]:
    non_id_roles = {roles_a.get(column) for column in comparable if roles_a.get(column) != "identifier"}
    if domain_a != "unknown" and domain_b != "unknown" and domain_a != domain_b:
        return False, f"Schema mismatch: Table A looks like {domain_a}, Table B looks like {domain_b}.", 0.15
    if "primary_name" not in non_id_roles:
        return False, "No comparable primary name/title field was found.", 0.25
    if len(non_id_roles) < 2 and len(comparable) < 2:
        return False, "Only one weak comparable field was found; matching was cancelled.", 0.45
    if all(roles_a.get(column) == "identifier" for column in comparable):
        return False, "Only identifier columns can be mapped; identifiers are not used for entity matching.", 0.1
    confidence = 0.9 if domain_a == domain_b and domain_a != "unknown" else 0.75
    return True, "Tables have compatible entity schema and comparable non-identifier attributes.", confidence


def has_exact_column_schema(table_a: pd.DataFrame, table_b: pd.DataFrame) -> bool:
    return list(table_a.columns) == list(table_b.columns)


def has_exact_column_headers(columns_a: list[Any], columns_b: list[Any]) -> bool:
    return [str(column) for column in columns_a] == [str(column) for column in columns_b]


def exact_header_schema_profile(columns: list[Any]) -> dict[str, Any]:
    column_names = [str(column) for column in columns]
    roles = {column: infer_column_role(normalize_column_name(column), 0.0, 0.0, 0.0, 0.0) for column in column_names}
    mapping = {column: column for column in column_names}
    ignored_columns = sorted(column for column, role in roles.items() if role == "identifier")
    comparable = comparable_mapping(mapping, roles)
    weights = normalize_weights(default_weights(comparable, roles))
    blocking_columns = choose_blocking_columns(comparable, roles)
    master_columns = build_master_columns(mapping, roles, ignored_columns, weights, blocking_columns)
    profile = header_only_table_profile(column_names, roles)
    domain = infer_domain(profile)
    return {
        "related": True,
        "relation_type": "same_entity_table",
        "entity_type": domain,
        "domain_a": domain,
        "domain_b": domain,
        "confidence": 1.0,
        "reason": "Table A and Table B column headers are exactly identical in order, characters, and case.",
        "column_mapping": mapping,
        "column_roles": roles,
        "table_b_column_roles": dict(roles),
        "ignored_columns": ignored_columns,
        "low_relevance_columns": [column for column, role in roles.items() if role in {"numeric_auxiliary", "category"}],
        "blocking_columns": blocking_columns,
        "weights": weights,
        "master_columns": master_columns,
        "table_a_profile": profile,
        "table_b_profile": dict(profile),
        "source": "strict_header",
    }


def header_only_table_profile(columns: list[str], roles: dict[str, str]) -> dict[str, Any]:
    return {
        "columns": columns,
        "row_count": 0,
        "sample_rows": [],
        "column_stats": {
            column: {
                "name": column,
                "normalized_name": normalize_column_name(column),
                "role": roles.get(column, "text"),
                "null_ratio": 0.0,
                "unique_ratio": 0.0,
                "numeric_ratio": 0.0,
                "year_ratio": 0.0,
                "phone_ratio": 0.0,
                "avg_length": 0.0,
                "samples": [],
            }
            for column in columns
        },
    }


def refine_pair_roles(
    profile_a: dict[str, Any],
    profile_b: dict[str, Any],
    roles_a: dict[str, str],
    roles_b: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    refined_a = dict(roles_a)
    refined_b = dict(roles_b)
    exact_pairs = exact_normalized_column_pairs(profile_a, profile_b)

    for left, right in exact_pairs:
        left_role = refined_a.get(left)
        right_role = refined_b.get(right)
        if left_role == "text" and right_role == "text" and looks_categorical(profile_a, left) and looks_categorical(profile_b, right):
            refined_a[left] = "category"
            refined_b[right] = "category"

    has_primary = any(refined_a.get(left) == "primary_name" and refined_b.get(right) == "primary_name" for left, right in exact_pairs)
    if not has_primary:
        primary_pair = choose_generic_primary_pair(profile_a, profile_b, exact_pairs, refined_a, refined_b)
        if primary_pair:
            left, right = primary_pair
            refined_a[left] = "primary_name"
            refined_b[right] = "primary_name"

    return refined_a, refined_b


def exact_normalized_column_pairs(profile_a: dict[str, Any], profile_b: dict[str, Any]) -> list[tuple[str, str]]:
    right_by_name = {
        profile_b["column_stats"][column]["normalized_name"]: column
        for column in profile_b["columns"]
    }
    pairs: list[tuple[str, str]] = []
    for left in profile_a["columns"]:
        normalized = profile_a["column_stats"][left]["normalized_name"]
        right = right_by_name.get(normalized)
        if right is not None:
            pairs.append((left, right))
    return pairs


def choose_generic_primary_pair(
    profile_a: dict[str, Any],
    profile_b: dict[str, Any],
    pairs: list[tuple[str, str]],
    roles_a: dict[str, str],
    roles_b: dict[str, str],
) -> tuple[str, str] | None:
    candidates: list[tuple[float, str, str]] = []
    for left, right in pairs:
        if roles_a.get(left) == "identifier" or roles_b.get(right) == "identifier":
            continue
        if roles_a.get(left) not in {"text", "category"} or roles_b.get(right) not in {"text", "category"}:
            continue
        score = generic_primary_score(profile_a, left) + generic_primary_score(profile_b, right)
        if score > 0:
            candidates.append((score, left, right))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    score, left, right = candidates[0]
    return (left, right) if score >= 0.8 else None


def generic_primary_score(profile: dict[str, Any], column: str) -> float:
    stats = profile["column_stats"][column]
    name = stats["normalized_name"]
    if stats["numeric_ratio"] > 0.5 or stats["phone_ratio"] > 0.5:
        return 0.0
    name_score = 0.0
    if any(token in name for token in ("name", "title", "label", "description", "desc")):
        name_score += 0.45
    if any(token in name for token in ("id", "code", "num", "number")):
        name_score -= 0.25
    unique_score = min(max(stats["unique_ratio"], 0.0), 1.0) * 0.3
    length_score = min(max(stats["avg_length"], 0.0) / 24.0, 1.0) * 0.2
    completeness_score = (1.0 - min(max(stats["null_ratio"], 0.0), 1.0)) * 0.15
    return max(0.0, name_score + unique_score + length_score + completeness_score)


def looks_categorical(profile: dict[str, Any], column: str) -> bool:
    stats = profile["column_stats"][column]
    name = stats["normalized_name"]
    if name in {"type", "category", "genre", "style", "class"}:
        return True
    return stats["unique_ratio"] <= 0.25 and stats["avg_length"] <= 40 and stats["numeric_ratio"] < 0.5


def has_known_domain_mismatch(domain_a: str, domain_b: str) -> bool:
    return domain_a != "unknown" and domain_b != "unknown" and domain_a != domain_b


def is_structurally_compatible(
    table_a: pd.DataFrame,
    table_b: pd.DataFrame,
    mapping: dict[str, str],
    roles_a: dict[str, str],
    roles_b: dict[str, str],
) -> bool:
    comparable = comparable_mapping(mapping, roles_a)
    if len(comparable) < 2:
        return False
    left_non_id = [column for column in table_a.columns if roles_a.get(column) != "identifier"]
    right_non_id = [column for column in table_b.columns if roles_b.get(column) != "identifier"]
    denominator = max(len(left_non_id), len(right_non_id), 1)
    coverage = len(comparable) / denominator
    comparable_roles = {roles_a.get(column) for column in comparable}
    has_entity_text = bool(comparable_roles & {"primary_name", "creator_or_brand", "text"})
    return coverage >= 0.5 and has_entity_text


def has_identical_non_identifier_schema(
    table_a: pd.DataFrame,
    table_b: pd.DataFrame,
    roles_a: dict[str, str],
    roles_b: dict[str, str],
) -> bool:
    left = [normalize_column_name(column) for column in table_a.columns if roles_a.get(column) != "identifier"]
    right = [normalize_column_name(column) for column in table_b.columns if roles_b.get(column) != "identifier"]
    return len(left) >= 2 and left == right


def comparable_mapping(mapping: dict[str, str], roles: dict[str, str]) -> dict[str, str]:
    return {left: right for left, right in mapping.items() if roles.get(left) != "identifier"}


def default_weights(mapping: dict[str, str], roles: dict[str, str]) -> dict[str, float]:
    base = {
        "primary_name": 0.45,
        "creator_or_brand": 0.3,
        "location_or_context": 0.18,
        "phone": 0.25,
        "numeric_auxiliary": 0.1,
        "category": 0.08,
        "text": 0.12,
    }
    return {column: base.get(roles.get(column, "text"), 0.1) for column in mapping}


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    cleaned = {column: max(0.0, safe_float(weight)) for column, weight in weights.items()}
    total = sum(cleaned.values())
    if total <= 0:
        return cleaned
    return {column: round(weight / total, 6) for column, weight in cleaned.items()}


def build_master_columns(
    mapping: dict[str, str],
    roles: dict[str, str],
    ignored_columns: list[str] | set[str],
    weights: dict[str, float],
    blocking_columns: list[str],
) -> list[dict[str, Any]]:
    ignored = {str(column) for column in ignored_columns}
    blocking = {str(column) for column in blocking_columns}
    important_roles = {
        "primary_name",
        "creator_or_brand",
        "location_or_context",
        "phone",
        "category",
        "numeric_auxiliary",
        "text",
    }
    master_columns: list[dict[str, Any]] = []
    for column in mapping:
        column = str(column)
        role = str(roles.get(column) or "text")
        weight = safe_float(weights.get(column), default=0.0)
        if column in ignored or role == "identifier":
            continue
        if weight <= 0.0 and column not in blocking:
            continue
        if role not in important_roles and column not in blocking:
            continue
        include_reason = "blocking_column" if column in blocking else f"matching_weight={weight:.6f}"
        master_columns.append(
            {
                "source_column": column,
                "output_column": column,
                "role": role,
                "weight": round(weight, 6),
                "reason": include_reason,
            }
        )
    return master_columns


def choose_blocking_columns(mapping: dict[str, str], roles: dict[str, str]) -> list[str]:
    priority = ["primary_name", "creator_or_brand", "phone", "location_or_context"]
    selected: list[str] = []
    for role in priority:
        selected.extend([column for column in mapping if roles.get(column) == role])
        if len(selected) >= 2:
            break
    return selected[:2] or list(mapping)[:1]


def first_column_with_role(profile: dict[str, Any], side: str, role: str) -> str | None:
    roles = profile.get("column_roles") if side == "tableA" else profile.get("table_b_column_roles")
    roles = roles or {}
    for column, column_role in roles.items():
        if column_role == role:
            if side == "tableA":
                return column
            return (profile.get("column_mapping") or {}).get(column)
    return None


def first_column_named_or_role(profile: dict[str, Any], side: str, names: set[str], role: str) -> str | None:
    roles = profile.get("column_roles") if side == "tableA" else profile.get("table_b_column_roles")
    roles = roles or {}
    for column, column_role in roles.items():
        if column_role == role or normalize_column_name(column) in names:
            if side == "tableA":
                return column
            return (profile.get("column_mapping") or {}).get(column)
    return None


def infer_column_role(name: str, unique_ratio: float, numeric_ratio: float, year_ratio: float, phone_ratio: float) -> str:
    if name in ID_ROLE_NAMES or (name.endswith("_id") and unique_ratio > 0.8):
        return "identifier"
    if name in PRIMARY_NAME_NAMES:
        return "primary_name"
    if name in CREATOR_BRAND_NAMES:
        return "creator_or_brand"
    if name in LOCATION_CONTEXT_NAMES:
        return "location_or_context"
    if name in PHONE_NAMES or phone_ratio > 0.6:
        return "phone"
    if name in NUMERIC_AUX_NAMES or numeric_ratio > 0.8 or year_ratio > 0.6:
        return "numeric_auxiliary"
    if name in {"type", "category", "genre", "style"}:
        return "category"
    return "text"


def normalize_column_name(value: Any) -> str:
    text = normalize_text(value)
    return re.sub(r"\s+", "_", text)


def token_jaccard(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def numeric_similarity(left: Any, right: Any) -> float:
    left_value = parse_float(left)
    right_value = parse_float(right)
    if left_value is None or right_value is None:
        return 0.5
    denominator = max(abs(left_value), abs(right_value), 1.0)
    return max(0.0, 1.0 - abs(left_value - right_value) / denominator)


def parse_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def is_number_like(value: Any) -> bool:
    return parse_float(value) is not None


def is_year_like(value: Any) -> bool:
    parsed = parse_float(value)
    return parsed is not None and 1400 <= parsed <= 2200 and float(parsed).is_integer()


def is_phone_like(value: Any) -> bool:
    digits = digits_only(value)
    return len(digits) >= 7


def digits_only(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return "".join(re.findall(r"\d", str(value)))


def name_similarity(left: str, right: str) -> float:
    left_counts = Counter(normalize_column_name(left))
    right_counts = Counter(normalize_column_name(right))
    intersection = sum((left_counts & right_counts).values())
    union = sum((left_counts | right_counts).values())
    return intersection / union if union else 0.0


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        result = float(value)
        if math.isnan(result):
            return default
        return result
    except (TypeError, ValueError):
        return default
