from __future__ import annotations

import csv
import json
from pathlib import Path


REFERENCE_PATH = Path(r"C:\Users\86153\Desktop\a\structured_amazon_google\master.csv")
MATCH_RUN_DIR = Path("artifacts/match_runs")


def main() -> None:
    ref_pairs: set[tuple[str, str]] = set()
    with REFERENCE_PATH.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            a_id = str(row.get("source_A_id") or "").strip()
            b_id = str(row.get("source_B_id") or "").strip()
            if a_id and b_id:
                ref_pairs.add((a_id, b_id))

    paths = sorted(MATCH_RUN_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not paths:
        raise SystemExit("no match_runs found")
    path = paths[0]
    data = json.loads(path.read_text(encoding="utf-8"))

    pairs: list[dict] = []
    pairs.extend(data.get("high_confidence_candidates") or [])
    for cluster in data.get("ignored_low_confidence_clusters") or []:
        if isinstance(cluster, dict):
            pairs.extend(item for item in cluster.get("candidates") or [] if isinstance(item, dict))
    for cluster in data.get("llm_conflict_clusters") or []:
        if isinstance(cluster, dict):
            pairs.extend(item for item in cluster.get("candidates") or [] if isinstance(item, dict))
    for cluster in data.get("auto_merged_clusters") or []:
        if isinstance(cluster, dict):
            pairs.extend(item for item in cluster.get("candidates") or [] if isinstance(item, dict))

    seen = set()
    unique_pairs = []
    for pair in pairs:
        key = (str(pair.get("table1.id") or ""), str(pair.get("table2.id") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique_pairs.append(pair)

    true_scores = []
    false_scores = []
    for pair in unique_pairs:
        key = (str(pair.get("table1.id") or ""), str(pair.get("table2.id") or ""))
        score = float(pair.get("confidence") or 0)
        if key in ref_pairs:
            true_scores.append(score)
        else:
            false_scores.append(score)

    print(f"match_run={path.name}")
    print(f"all_candidate_pairs_count={data.get('all_candidate_pairs_count')}")
    print(f"unique_pairs_in_payload={len(unique_pairs)}")
    print(f"reference_merged_pairs={len(ref_pairs)}")
    print(f"reference_pairs_present_in_candidates={len(true_scores)}")
    print("threshold,tp,fp,precision_among_candidates,recall_vs_reference_merged")
    for threshold in [0.94, 0.9, 0.85, 0.78, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5, 0.4, 0.3]:
        tp = sum(1 for score in true_scores if score >= threshold)
        fp = sum(1 for score in false_scores if score >= threshold)
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / len(ref_pairs) if ref_pairs else 0
        print(f"{threshold},{tp},{fp},{precision:.6f},{recall:.6f}")

    scores = sorted(true_scores)
    print("true_score_quantiles")
    for q in [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]:
        if not scores:
            value = None
        else:
            value = scores[int((len(scores) - 1) * q)]
        print(f"{q},{value}")


if __name__ == "__main__":
    main()
