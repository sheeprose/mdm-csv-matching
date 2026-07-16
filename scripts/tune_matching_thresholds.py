from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from mdm_matching.preprocess import preprocess_table
from mdm_matching.service import (
    AMBIGUOUS_MARGIN,
    AUTO_MERGE_CONFIDENCE,
    build_candidate_pairs,
    has_field_conflict,
    load_model,
    safe_float,
    score_candidates,
)


REFERENCE_PATH = Path(r"C:\Users\86153\Desktop\a\structured_amazon_google\master.csv")
TABLE_A_PATH = Path("datasets/structured_amazon_google/tableA.csv")
TABLE_B_PATH = Path("datasets/structured_amazon_google/tableB.csv")


def reference_pairs() -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    with REFERENCE_PATH.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            a_id = str(row.get("source_A_id") or "").strip()
            b_id = str(row.get("source_B_id") or "").strip()
            if a_id and b_id:
                pairs.add((a_id, b_id))
    return pairs


def metrics(predicted: set[tuple[str, str]], expected: set[tuple[str, str]]) -> dict[str, float | int]:
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "predicted": len(predicted),
    }


def pair_key(row: pd.Series) -> tuple[str, str]:
    return str(row["table1.id"]), str(row["table2.id"])


def top_one_to_one(scored: pd.DataFrame, threshold: float) -> set[tuple[str, str]]:
    filtered = scored[scored["confidence"] >= threshold].copy()
    if filtered.empty:
        return set()
    best_by_left = filtered.sort_values("confidence", ascending=False).drop_duplicates("table1.id")
    best_by_right = filtered.sort_values("confidence", ascending=False).drop_duplicates("table2.id")
    left_keys = {pair_key(row) for _, row in best_by_left.iterrows()}
    right_keys = {pair_key(row) for _, row in best_by_right.iterrows()}
    return left_keys & right_keys


def top_one_to_one_with_margin(scored: pd.DataFrame, threshold: float) -> set[tuple[str, str]]:
    rows = scored.to_dict(orient="records")
    best_by_left = rank_best_rows(rows, "table1.id")
    best_by_right = rank_best_rows(rows, "table2.id")
    selected: set[tuple[str, str]] = set()
    used_left: set[str] = set()
    used_right: set[str] = set()

    for row in sorted(rows, key=lambda item: safe_float(item.get("confidence")), reverse=True):
        left_id = str(row.get("table1.id"))
        right_id = str(row.get("table2.id"))
        confidence = safe_float(row.get("confidence"))
        if confidence < threshold or confidence >= AUTO_MERGE_CONFIDENCE:
            continue
        if left_id in used_left or right_id in used_right or has_field_conflict(row):
            continue

        left_best, left_margin = best_by_left.get(left_id, (None, 0.0))
        right_best, right_margin = best_by_right.get(right_id, (None, 0.0))
        if left_best is row and right_best is row and left_margin >= AMBIGUOUS_MARGIN and right_margin >= AMBIGUOUS_MARGIN:
            selected.add((left_id, right_id))
            used_left.add(left_id)
            used_right.add(right_id)

    return selected


def rank_best_rows(rows: list[dict[str, object]], id_column: str) -> dict[str, tuple[dict[str, object], float]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(id_column)), []).append(row)

    ranked: dict[str, tuple[dict[str, object], float]] = {}
    for source_id, group_rows in grouped.items():
        group_rows = sorted(group_rows, key=lambda item: safe_float(item.get("confidence")), reverse=True)
        top_score = safe_float(group_rows[0].get("confidence"))
        second_score = safe_float(group_rows[1].get("confidence")) if len(group_rows) > 1 else -1.0
        ranked[source_id] = (group_rows[0], top_score - second_score)
    return ranked


def all_above(scored: pd.DataFrame, threshold: float) -> set[tuple[str, str]]:
    return {pair_key(row) for _, row in scored[scored["confidence"] >= threshold].iterrows()}


def main() -> None:
    expected = reference_pairs()
    table_a = preprocess_table(pd.read_csv(TABLE_A_PATH))
    table_b = preprocess_table(pd.read_csv(TABLE_B_PATH))
    candidates = build_candidate_pairs(table_a, table_b)
    scored = score_candidates(candidates, load_model()).sort_values("confidence", ascending=False)
    candidate_keys = {pair_key(row) for _, row in scored.iterrows()}
    covered_expected = expected & candidate_keys

    print(f"reference_merged_pairs={len(expected)}")
    print(f"candidate_pairs={len(scored)}")
    print(f"reference_pairs_in_candidates={len(covered_expected)}")
    print(f"candidate_generation_recall={len(covered_expected) / len(expected):.6f}")
    print()
    print("strategy,threshold,tp,fp,fn,precision,recall,f1,predicted")
    for strategy_name, strategy in [
        ("all_above", all_above),
        ("mutual_top_1to1", top_one_to_one),
        ("mutual_top_margin_1to1", top_one_to_one_with_margin),
    ]:
        for threshold in [0.95, 0.94, 0.92, 0.90, 0.88, 0.85, 0.82, 0.80, 0.78, 0.75, 0.72, 0.70, 0.68, 0.65, 0.60]:
            result = metrics(strategy(scored, threshold), expected)
            print(
                f"{strategy_name},{threshold},"
                f"{result['tp']},{result['fp']},{result['fn']},"
                f"{result['precision']},{result['recall']},{result['f1']},{result['predicted']}"
            )

    expected_scores = [
        float(row["confidence"])
        for _, row in scored.iterrows()
        if pair_key(row) in expected
    ]
    expected_scores.sort()
    print()
    print("expected_pair_score_quantiles")
    for quantile in [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]:
        value = expected_scores[int((len(expected_scores) - 1) * quantile)] if expected_scores else None
        print(f"{quantile},{value}")


if __name__ == "__main__":
    main()
