from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_ROOT / "artifacts" / "mdm.sqlite3"
DEFAULT_REFERENCE_PATH = Path(r"C:\Users\86153\Desktop\a\structured_amazon_google\master.csv")
OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "comparison"


def norm(value: object) -> str:
    text = "" if value is None else str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def source_ref(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"


def read_reference(reference_path: Path) -> list[dict[str, str]]:
    with reference_path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def reference_clusters(rows: list[dict[str, str]]) -> dict[frozenset[str], dict[str, str]]:
    clusters: dict[frozenset[str], dict[str, str]] = {}
    for row in rows:
        refs: list[str] = []
        a_id = str(row.get("source_A_id") or "").strip()
        b_id = str(row.get("source_B_id") or "").strip()
        if a_id:
            refs.append(source_ref("table1", a_id))
        if b_id:
            refs.append(source_ref("table2", b_id))
        if refs:
            clusters[frozenset(refs)] = row
    return clusters


def cross_pairs(clusters: set[frozenset[str]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for cluster in clusters:
        left_ids = sorted(ref.split(":", 1)[1] for ref in cluster if ref.startswith("table1:"))
        right_ids = sorted(ref.split(":", 1)[1] for ref in cluster if ref.startswith("table2:"))
        for left_id in left_ids:
            for right_id in right_ids:
                pairs.add((left_id, right_id))
    return pairs


def read_sqlite(db_path: Path) -> tuple[dict[str, int], dict[frozenset[str], dict[str, object]], list[dict[str, object]]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]: con.execute(f"SELECT COUNT(*) AS c FROM {row['name']}").fetchone()["c"]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        links = [dict(row) for row in con.execute("SELECT entity_id, source_table, source_id FROM source_record_links")]
        entities = {
            row["id"]: dict(row)
            for row in con.execute("SELECT id, canonical_title, manufacturer, price, status, confidence FROM entities")
        }
    finally:
        con.close()

    by_entity: dict[int, list[str]] = defaultdict(list)
    duplicate_refs: dict[str, int] = defaultdict(int)
    for link in links:
        ref = source_ref(str(link["source_table"]), str(link["source_id"]))
        by_entity[int(link["entity_id"])].append(ref)
        duplicate_refs[ref] += 1

    clusters: dict[frozenset[str], dict[str, object]] = {}
    for entity_id, refs in by_entity.items():
        cluster = frozenset(refs)
        entity = entities.get(entity_id, {})
        clusters[cluster] = {"entity_id": entity_id, **entity}

    duplicate_link_rows = [
        {"source_ref": ref, "count": count}
        for ref, count in duplicate_refs.items()
        if count > 1
    ]
    tables["duplicate_source_ref_count_in_links"] = len(duplicate_link_rows)
    return tables, clusters, duplicate_link_rows


def pct(part: int, whole: int) -> float:
    return round(part / whole, 6) if whole else 0.0


def prf(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH
    reference_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_REFERENCE_PATH
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ref_rows = read_reference(reference_path)
    ref_clusters = reference_clusters(ref_rows)
    sqlite_counts, pred_clusters, duplicate_links = read_sqlite(db_path)

    ref_cluster_set = set(ref_clusters)
    pred_cluster_set = set(pred_clusters)

    ref_pairs = cross_pairs(ref_cluster_set)
    pred_pairs = cross_pairs(pred_cluster_set)

    exact_matches = ref_cluster_set & pred_cluster_set
    missing_clusters = ref_cluster_set - pred_cluster_set
    extra_clusters = pred_cluster_set - ref_cluster_set

    ref_a = {ref.split(":", 1)[1] for cluster in ref_cluster_set for ref in cluster if ref.startswith("table1:")}
    ref_b = {ref.split(":", 1)[1] for cluster in ref_cluster_set for ref in cluster if ref.startswith("table2:")}
    pred_a = {ref.split(":", 1)[1] for cluster in pred_cluster_set for ref in cluster if ref.startswith("table1:")}
    pred_b = {ref.split(":", 1)[1] for cluster in pred_cluster_set for ref in cluster if ref.startswith("table2:")}

    title_exact = 0
    title_compared = 0
    for cluster in exact_matches:
        ref_title = norm(ref_clusters[cluster].get("title"))
        pred_title = norm(pred_clusters[cluster].get("canonical_title"))
        if ref_title or pred_title:
            title_compared += 1
            if ref_title == pred_title:
                title_exact += 1

    report = {
        "inputs": {
            "reference_path": str(reference_path),
            "sqlite_path": str(db_path),
        },
        "counts": {
            "reference_master_rows": len(ref_rows),
            "reference_clusters": len(ref_cluster_set),
            "predicted_clusters_from_source_links": len(pred_cluster_set),
            "reference_A_source_count": len(ref_a),
            "reference_B_source_count": len(ref_b),
            "predicted_A_source_count": len(pred_a),
            "predicted_B_source_count": len(pred_b),
            **sqlite_counts,
        },
        "source_coverage": {
            "A_covered": len(ref_a & pred_a),
            "A_missing": len(ref_a - pred_a),
            "A_extra": len(pred_a - ref_a),
            "A_recall": pct(len(ref_a & pred_a), len(ref_a)),
            "B_covered": len(ref_b & pred_b),
            "B_missing": len(ref_b - pred_b),
            "B_extra": len(pred_b - ref_b),
            "B_recall": pct(len(ref_b & pred_b), len(ref_b)),
        },
        "cluster_exact_match": {
            "exact_match_count": len(exact_matches),
            "missing_reference_cluster_count": len(missing_clusters),
            "extra_predicted_cluster_count": len(extra_clusters),
            "precision": pct(len(exact_matches), len(pred_cluster_set)),
            "recall": pct(len(exact_matches), len(ref_cluster_set)),
        },
        "cross_source_pair_linkage": prf(
            len(ref_pairs & pred_pairs),
            len(pred_pairs - ref_pairs),
            len(ref_pairs - pred_pairs),
        ),
        "canonical_title_on_exact_clusters": {
            "compared": title_compared,
            "exact_title_matches": title_exact,
            "exact_title_match_rate": pct(title_exact, title_compared),
        },
        "duplicate_source_links": duplicate_links[:50],
    }

    def cluster_to_row(cluster: frozenset[str], side: str) -> dict[str, object]:
        entity = pred_clusters.get(cluster, {})
        ref = ref_clusters.get(cluster, {})
        return {
            "side": side,
            "refs": "|".join(sorted(cluster)),
            "reference_title": ref.get("title", ""),
            "predicted_entity_id": entity.get("entity_id", ""),
            "predicted_title": entity.get("canonical_title", ""),
        }

    db_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", db_path.stem)
    mismatch_path = OUTPUT_DIR / f"cluster_mismatches_{db_stem}.csv"
    with mismatch_path.open("w", newline="", encoding="utf-8-sig") as handle:
        fieldnames = ["side", "refs", "reference_title", "predicted_entity_id", "predicted_title"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for cluster in sorted(missing_clusters, key=lambda c: sorted(c)):
            writer.writerow(cluster_to_row(cluster, "missing_reference_cluster"))
        for cluster in sorted(extra_clusters, key=lambda c: sorted(c)):
            writer.writerow(cluster_to_row(cluster, "extra_predicted_cluster"))

    report_path = OUTPUT_DIR / f"comparison_report_{db_stem}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"REPORT_JSON={report_path}")
    print(f"MISMATCH_CSV={mismatch_path}")


if __name__ == "__main__":
    main()
