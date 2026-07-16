from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


DB_PATH = Path("models/main/mdm.sqlite3")
REFERENCE_PATH = Path("data/dn2/master.csv")


def load_reference_pairs(path: Path) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            a_id = str(row.get("source_A_id") or "").strip()
            b_id = str(row.get("source_B_id") or "").strip()
            if a_id and b_id:
                pairs.add((a_id, b_id))
    return pairs


def main() -> None:
    reference_pairs = load_reference_pairs(REFERENCE_PATH)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        links_by_entity: dict[int, list[tuple[str, str]]] = defaultdict(list)
        for row in con.execute("SELECT entity_id, source_table, source_id FROM source_record_links"):
            links_by_entity[int(row["entity_id"])].append((str(row["source_table"]), str(row["source_id"])))

        entities = {
            int(row["id"]): dict(row)
            for row in con.execute("SELECT id, canonical_title, status, confidence FROM entities")
        }
    finally:
        con.close()

    predicted_pairs: set[tuple[str, str]] = set()
    composition = Counter()
    complex_examples: list[dict[str, object]] = []

    for entity_id, links in links_by_entity.items():
        left_ids = [source_id for source_table, source_id in links if source_table == "table1"]
        right_ids = [source_id for source_table, source_id in links if source_table == "table2"]
        composition[(len(left_ids), len(right_ids))] += 1
        for left_id in left_ids:
            for right_id in right_ids:
                predicted_pairs.add((left_id, right_id))

        if (len(left_ids) > 1 or len(right_ids) > 1) and len(complex_examples) < 12:
            entity = entities.get(entity_id, {})
            complex_examples.append(
                {
                    "entity_id": entity_id,
                    "table1_ids": left_ids,
                    "table2_ids": right_ids,
                    "canonical_title": entity.get("canonical_title", ""),
                    "status": entity.get("status", ""),
                    "confidence": entity.get("confidence", ""),
                }
            )

    true_positive = reference_pairs & predicted_pairs
    false_positive = predicted_pairs - reference_pairs
    false_negative = reference_pairs - predicted_pairs

    result = {
        "reference_pairs": len(reference_pairs),
        "predicted_pairs": len(predicted_pairs),
        "tp": len(true_positive),
        "fp": len(false_positive),
        "fn": len(false_negative),
        "precision": len(true_positive) / len(predicted_pairs) if predicted_pairs else 0,
        "recall": len(true_positive) / len(reference_pairs) if reference_pairs else 0,
        "f1": (
            2 * len(true_positive) / (2 * len(true_positive) + len(false_positive) + len(false_negative))
            if true_positive
            else 0
        ),
        "cluster_composition_top": [
            {"table1_count": key[0], "table2_count": key[1], "cluster_count": value}
            for key, value in composition.most_common(20)
        ],
        "complex_cluster_count": sum(value for key, value in composition.items() if key[0] > 1 or key[1] > 1),
        "complex_examples": complex_examples,
        "sample_false_positive_pairs": sorted(false_positive)[:25],
        "sample_missing_pairs": sorted(false_negative)[:25],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
