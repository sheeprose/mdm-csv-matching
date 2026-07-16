from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from mdm_matching.preprocess import preprocess_table
from mdm_matching.service import build_candidate_pairs, load_model, score_candidates


TABLE_A_PATH = Path("structured_dn2/tableA.csv")
TABLE_B_PATH = Path("structured_dn2/tableB.csv")
REFERENCE_PATH = Path("structured_dn2/master.csv")


def load_reference_pairs() -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    with REFERENCE_PATH.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            left_id = str(row.get("source_A_id") or "").strip()
            right_id = str(row.get("source_B_id") or "").strip()
            if left_id and right_id:
                pairs.add((left_id, right_id))
    return pairs


def metrics(predicted: set[tuple[str, str]], expected: set[tuple[str, str]]) -> dict[str, float | int]:
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1, "predicted": len(predicted)}


def pair_key(row: pd.Series) -> tuple[str, str]:
    return str(row["table1.id"]), str(row["table2.id"])


def all_above(scored: pd.DataFrame, threshold: float) -> set[tuple[str, str]]:
    return {pair_key(row) for _, row in scored[scored["confidence"] >= threshold].iterrows()}


def mutual_top(scored: pd.DataFrame, threshold: float) -> set[tuple[str, str]]:
    filtered = scored[scored["confidence"] >= threshold].copy()
    if filtered.empty:
        return set()
    by_left = filtered.sort_values("confidence", ascending=False).drop_duplicates("table1.id")
    by_right = filtered.sort_values("confidence", ascending=False).drop_duplicates("table2.id")
    return {pair_key(row) for _, row in by_left.iterrows()} & {pair_key(row) for _, row in by_right.iterrows()}


def main() -> None:
    expected = load_reference_pairs()
    table_a = preprocess_table(pd.read_csv(TABLE_A_PATH))
    table_b = preprocess_table(pd.read_csv(TABLE_B_PATH))
    candidates = build_candidate_pairs(table_a, table_b)
    scored = score_candidates(candidates, load_model()).sort_values("confidence", ascending=False)
    candidate_keys = {pair_key(row) for _, row in scored.iterrows()}
    covered_expected = expected & candidate_keys

    thresholds = [0.94, 0.90, 0.85, 0.80, 0.78, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50]
    result = {
        "reference_pairs": len(expected),
        "candidate_pairs": len(scored),
        "reference_pairs_in_candidates": len(covered_expected),
        "candidate_generation_recall": len(covered_expected) / len(expected) if expected else 0.0,
        "all_above": [{"threshold": threshold, **metrics(all_above(scored, threshold), expected)} for threshold in thresholds],
        "mutual_top": [{"threshold": threshold, **metrics(mutual_top(scored, threshold), expected)} for threshold in thresholds],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
