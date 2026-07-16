from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = Path(r"C:\Users\86153\Desktop\Dn2")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "structured_dn2"


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    # The Dn2 files contain an extra literal quote layer in many fields.
    changed = True
    while changed and len(text) >= 2:
        changed = False
        if (text[0] == text[-1] == '"') or (text[0] == text[-1] == "'"):
            text = text[1:-1].strip()
            changed = True
    return text.replace('""', '"').strip()


def clean_price(value: Any) -> str:
    text = clean_text(value)
    if text == "":
        return ""
    try:
        number = float(text)
    except ValueError:
        return ""
    if number <= 0:
        return ""
    return f"{number:.2f}".rstrip("0").rstrip(".")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalize_table(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        normalized.append(
            {
                "id": clean_text(row.get("id")),
                "title": clean_text(row.get("title")),
                "manufacturer": clean_text(row.get("manufacturer")),
                "price": clean_price(row.get("price")),
            }
        )
    return normalized


def normalize_pair_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        normalized.append(
            {
                "_id": clean_text(row.get("id")) or str(index),
                "label": "1" if clean_text(row.get("label")) == "1" else "0",
                "table1.id": clean_text(row.get("left_id")),
                "table2.id": clean_text(row.get("right_id")),
                "table1.title": clean_text(row.get("left_title")),
                "table2.title": clean_text(row.get("right_title")),
                "table1.manufacturer": clean_text(row.get("left_manufacturer")),
                "table2.manufacturer": clean_text(row.get("right_manufacturer")),
                "table1.price": clean_price(row.get("left_price")),
                "table2.price": clean_price(row.get("right_price")),
            }
        )
    return normalized


def load_positive_pairs(source_dir: Path) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for split in ("train_set.csv", "valid_set.csv", "test_set.csv"):
        for row in read_rows(source_dir / split):
            if clean_text(row.get("label")) == "1":
                left_id = clean_text(row.get("left_id"))
                right_id = clean_text(row.get("right_id"))
                if left_id and right_id:
                    pairs.add((left_id, right_id))
    return pairs


def choose_title(a_row: dict[str, str] | None, b_row: dict[str, str] | None) -> str:
    if a_row and a_row.get("title"):
        return a_row["title"]
    if b_row and b_row.get("title"):
        return b_row["title"]
    return ""


def choose_manufacturer(a_row: dict[str, str] | None, b_row: dict[str, str] | None) -> str:
    if a_row and a_row.get("manufacturer"):
        return a_row["manufacturer"]
    if b_row and b_row.get("manufacturer"):
        return b_row["manufacturer"]
    return ""


def choose_price(a_row: dict[str, str] | None, b_row: dict[str, str] | None) -> str:
    if a_row and a_row.get("price"):
        return a_row["price"]
    if b_row and b_row.get("price"):
        return b_row["price"]
    return ""


def build_master(
    table_a: list[dict[str, str]], table_b: list[dict[str, str]], positive_pairs: set[tuple[str, str]]
) -> list[dict[str, str]]:
    by_a = {row["id"]: row for row in table_a}
    by_b = {row["id"]: row for row in table_b}
    matched_a = {left_id for left_id, _ in positive_pairs}
    matched_b = {right_id for _, right_id in positive_pairs}

    master: list[dict[str, str]] = []
    entity_index = 1

    for left_id, right_id in sorted(positive_pairs, key=lambda item: (int(item[0]), int(item[1]))):
        a_row = by_a.get(left_id)
        b_row = by_b.get(right_id)
        master.append(
            {
                "entity_id": f"DN2-{entity_index:05d}",
                "title": choose_title(a_row, b_row),
                "manufacturer": choose_manufacturer(a_row, b_row),
                "price": choose_price(a_row, b_row),
                "source_A_id": left_id,
                "source_B_id": right_id,
            }
        )
        entity_index += 1

    for row in table_a:
        if row["id"] in matched_a:
            continue
        master.append(
            {
                "entity_id": f"DN2-{entity_index:05d}",
                "title": row["title"],
                "manufacturer": row["manufacturer"],
                "price": row["price"],
                "source_A_id": row["id"],
                "source_B_id": "",
            }
        )
        entity_index += 1

    for row in table_b:
        if row["id"] in matched_b:
            continue
        master.append(
            {
                "entity_id": f"DN2-{entity_index:05d}",
                "title": row["title"],
                "manufacturer": row["manufacturer"],
                "price": row["price"],
                "source_A_id": "",
                "source_B_id": row["id"],
            }
        )
        entity_index += 1

    return master


def summarize(
    table_a: list[dict[str, str]], table_b: list[dict[str, str]], positive_pairs: set[tuple[str, str]], master: list[dict[str, str]]
) -> dict[str, Any]:
    left_counts = Counter(left_id for left_id, _ in positive_pairs)
    right_counts = Counter(right_id for _, right_id in positive_pairs)
    matched_a = {left_id for left_id, _ in positive_pairs}
    matched_b = {right_id for _, right_id in positive_pairs}
    return {
        "tableA_rows": len(table_a),
        "tableB_rows": len(table_b),
        "positive_pairs": len(positive_pairs),
        "master_rows": len(master),
        "matched_A_rows": len(matched_a),
        "matched_B_rows": len(matched_b),
        "unmatched_A_rows": len(table_a) - len(matched_a),
        "unmatched_B_rows": len(table_b) - len(matched_b),
        "duplicate_positive_left_ids": sum(1 for count in left_counts.values() if count > 1),
        "duplicate_positive_right_ids": sum(1 for count in right_counts.values() if count > 1),
    }


def prepare(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    table_a = normalize_table(read_rows(source_dir / "tableA.csv"))
    table_b = normalize_table(read_rows(source_dir / "tableB.csv"))
    positive_pairs = load_positive_pairs(source_dir)
    master = build_master(table_a, table_b, positive_pairs)

    table_fields = ["id", "title", "manufacturer", "price"]
    pair_fields = [
        "_id",
        "label",
        "table1.id",
        "table2.id",
        "table1.title",
        "table2.title",
        "table1.manufacturer",
        "table2.manufacturer",
        "table1.price",
        "table2.price",
    ]
    master_fields = ["entity_id", "title", "manufacturer", "price", "source_A_id", "source_B_id"]

    write_rows(output_dir / "tableA.csv", table_fields, table_a)
    write_rows(output_dir / "tableB.csv", table_fields, table_b)
    for source_name, output_name in [
        ("train_set.csv", "train.csv"),
        ("valid_set.csv", "valid.csv"),
        ("test_set.csv", "test.csv"),
    ]:
        write_rows(output_dir / output_name, pair_fields, normalize_pair_rows(read_rows(source_dir / source_name)))
    write_rows(output_dir / "master.csv", master_fields, master)

    summary = summarize(table_a, table_b, positive_pairs, master)
    summary["source_dir"] = str(source_dir)
    summary["output_dir"] = str(output_dir)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare the Dn2 entity-matching dataset for the MDM Dify workflow.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    summary = prepare(args.source_dir, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
