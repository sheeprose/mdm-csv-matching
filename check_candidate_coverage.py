from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from mdm_matching.preprocess import preprocess_table
from mdm_matching.service import build_candidate_pairs


REFERENCE_PATH = Path(r"C:\Users\86153\Desktop\a\structured_amazon_google\master.csv")


def main() -> None:
    reference_pairs: set[tuple[str, str]] = set()
    with REFERENCE_PATH.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            a_id = str(row.get("source_A_id") or "").strip()
            b_id = str(row.get("source_B_id") or "").strip()
            if a_id and b_id:
                reference_pairs.add((a_id, b_id))

    table_a = preprocess_table(pd.read_csv("structured_amazon_google/tableA.csv"))
    table_b = preprocess_table(pd.read_csv("structured_amazon_google/tableB.csv"))
    candidate_pairs = build_candidate_pairs(table_a, table_b)
    candidate_set = {
        (str(row["table1.id"]), str(row["table2.id"]))
        for _, row in candidate_pairs.iterrows()
    }

    covered = reference_pairs & candidate_set
    missing = reference_pairs - candidate_set
    print(f"reference_merged_pairs={len(reference_pairs)}")
    print(f"candidate_pairs={len(candidate_set)}")
    print(f"covered_reference_merged={len(covered)}")
    print(f"missing_reference_merged={len(missing)}")
    print(f"coverage={len(covered) / len(reference_pairs):.6f}")
    print("sample_missing=" + repr(sorted(missing)[:20]))


if __name__ == "__main__":
    main()
