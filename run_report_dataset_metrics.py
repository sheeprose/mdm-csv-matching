from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score


ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"
DB_BASE = ROOT / "artifacts" / "mdm.sqlite3"
API_BASE = "http://127.0.0.1:8000"
TEST_THRESHOLD = float(os.environ.get("MDM_REPORT_THRESHOLD", "0.59"))


def run_powershell(script: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], check=True, cwd=str(ROOT))


def stop_service() -> None:
    script = r"""
$targets = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'uvicorn mdm_matching\.service:app' }
foreach ($p in $targets) {
  Stop-Process -Id $p.ProcessId -Force
}
"""
    run_powershell(script)
    time.sleep(1)


def delete_sqlite() -> None:
    for path in (DB_BASE, Path(str(DB_BASE) + "-wal"), Path(str(DB_BASE) + "-shm")):
        if path.exists():
            path.unlink()


def start_service() -> subprocess.Popen:
    log_path = ROOT / "artifacts" / "mdm_api_report_metrics.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    env = os.environ.copy()
    env["MDM_TEST_MAX_ROWS"] = "0"
    env.setdefault("MDM_MAX_SCHEMA_CANDIDATES_PER_LEFT", "40")
    env.setdefault("MDM_MAX_SCHEMA_TOKEN_POSTINGS_RATIO", "0.08")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mdm_matching.service:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(ROOT),
        stdout=log,
        stderr=log,
        env=env,
        creationflags=creationflags,
    )


def wait_health(timeout_seconds: int = 60) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            response = requests.get(f"{API_BASE}/health", timeout=3)
            response.raise_for_status()
            body = response.json()
            if body.get("ok"):
                return body
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"service did not become healthy: {last_error}")


def reset_service() -> dict[str, Any]:
    stop_service()
    delete_sqlite()
    start_service()
    return wait_health()


def row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return max(sum(1 for _ in csv.reader(handle)) - 1, 0)


def post_json(path: str, payload: dict[str, Any], timeout: int = 3600) -> dict[str, Any]:
    response = requests.post(f"{API_BASE}{path}", json=payload, timeout=timeout)
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(f"{path} failed: {response.status_code} {body}")
    return body


def get_quality(expected_a: int, expected_b: int) -> dict[str, Any]:
    response = requests.get(
        f"{API_BASE}/admin/entity-quality",
        params={"expected_table1": expected_a, "expected_table2": expected_b},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def run_positive_dataset(name: str) -> dict[str, Any]:
    table_a = DATASETS / name / "tableA.csv"
    table_b = DATASETS / name / "tableB.csv"
    count_a = row_count(table_a)
    count_b = row_count(table_b)
    reset_service()
    started = time.perf_counter()
    request = {"table_a_path": str(table_a).replace("\\", "/"), "table_b_path": str(table_b).replace("\\", "/"), "threshold": TEST_THRESHOLD}
    strict = post_json("/strict-schema-match", request, timeout=120)
    ingest = post_json("/ingest-and-match", request, timeout=7200)
    final = post_json(
        "/finalize-match-run",
        {
            "match_run_id": ingest.get("match_run_id"),
            "ingest_payload": ingest,
            "table_a_path": str(table_a).replace("\\", "/"),
            "table_b_path": str(table_b).replace("\\", "/"),
        },
        timeout=7200,
    )
    elapsed = round(time.perf_counter() - started, 3)
    quality = final.get("quality") if isinstance(final.get("quality"), dict) else get_quality(count_a, count_b)
    report_metrics = evaluate_test_pairs(name)
    return {
        "case": name,
        "expected": "positive",
        "passed": bool(quality.get("valid")),
        "elapsed_seconds": elapsed,
        "table_a_rows": count_a,
        "table_b_rows": count_b,
        "input_rows_sum": count_a + count_b,
        "master_data_rows": quality.get("golden_records_count"),
        "entity_count": quality.get("entity_count"),
        "master_le_input_sum": (quality.get("golden_records_count") or 0) <= count_a + count_b,
        "strict_status": strict.get("status"),
        "ingest_status": ingest.get("status"),
        "final_status": final.get("status"),
        "quality_valid": quality.get("valid"),
        **report_metrics,
    }


def run_negative_case(name: str, table_a: Path, table_b: Path) -> dict[str, Any]:
    count_a = row_count(table_a)
    count_b = row_count(table_b)
    reset_service()
    started = time.perf_counter()
    request = {"table_a_path": str(table_a).replace("\\", "/"), "table_b_path": str(table_b).replace("\\", "/")}
    strict = post_json("/strict-schema-match", request, timeout=120)
    analyze = post_json("/analyze-schema", request, timeout=600)
    rejected = not bool((analyze.get("schema_profile") or {}).get("related"))
    quality = get_quality(count_a, count_b)
    return {
        "case": name,
        "expected": "negative",
        "passed": rejected and int(quality.get("golden_records_count") or 0) == 0,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "table_a_rows": count_a,
        "table_b_rows": count_b,
        "input_rows_sum": count_a + count_b,
        "master_data_rows": quality.get("golden_records_count"),
        "entity_count": quality.get("entity_count"),
        "strict_status": strict.get("status"),
        "relation_type": (analyze.get("schema_profile") or {}).get("relation_type"),
        "schema_reason": (analyze.get("schema_profile") or {}).get("reason"),
        "quality_valid": quality.get("valid"),
        "f1_score": None,
        "roc_auc": None,
        "accuracy": None,
        "recall": None,
        "precision": None,
    }


def evaluate_test_pairs(name: str) -> dict[str, Any]:
    from mdm_matching.schema_profile import exact_header_schema_profile
    from mdm_matching.service import load_csv, apply_schema_profile, generic_pair_features

    dataset = DATASETS / name
    test_path = dataset / "test.csv"
    if not test_path.exists():
        return {"f1_score": None, "roc_auc": None, "accuracy": None, "recall": None, "precision": None, "test_pairs": 0}
    table_a = load_csv(dataset / "tableA.csv")
    table_b = load_csv(dataset / "tableB.csv")
    profile = exact_header_schema_profile(list(table_a.columns))
    left = apply_schema_profile(table_a, profile, "tableA").set_index("id", drop=False)
    right = apply_schema_profile(table_b, profile, "tableB").set_index("id", drop=False)
    test = pd.read_csv(test_path)
    rows: list[dict[str, Any]] = []
    labels: list[int] = []
    for _, pair in test.iterrows():
        left_id = pair.get("table1.id")
        right_id = pair.get("table2.id")
        if left_id not in left.index or right_id not in right.index:
            continue
        lrow = left.loc[left_id]
        rrow = right.loc[right_id]
        row = {
            "table1.id": lrow["id"],
            "table2.id": rrow["id"],
            "table1.title": lrow["title"],
            "table2.title": rrow["title"],
            "table1.manufacturer": lrow["manufacturer"],
            "table2.manufacturer": rrow["manufacturer"],
            "table1.price": lrow["price"],
            "table2.price": rrow["price"],
        }
        for column in profile.get("weights") or {}:
            row[f"table1.__match_{column}"] = lrow.get(f"__match_{column}")
            row[f"table2.__match_{column}"] = rrow.get(f"__match_{column}")
        rows.append(row)
        labels.append(int(pair.get("label")))
    if not rows:
        return {"f1_score": None, "roc_auc": None, "accuracy": None, "recall": None, "precision": None, "test_pairs": 0}
    candidates = pd.DataFrame(rows)
    scores = generic_pair_features(candidates, profile)["schema_weighted_similarity"].fillna(0.0).to_numpy()
    y_true = pd.Series(labels).astype(int).to_numpy()
    y_pred = (scores >= TEST_THRESHOLD).astype(int)
    roc_auc = roc_auc_score(y_true, scores) if len(set(y_true.tolist())) > 1 else None
    return {
        "f1_score": round(float(f1_score(y_true, y_pred, zero_division=0)), 6),
        "roc_auc": round(float(roc_auc), 6) if roc_auc is not None else None,
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 6),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 6),
        "test_pairs": int(len(y_true)),
        "positive_pairs": int(y_true.sum()),
    }


def main() -> int:
    positive_names = [
        name.strip()
        for name in os.environ.get(
            "MDM_REPORT_POSITIVES",
            "structured_dblp_acm,structured_dblp_scholar,structured_itunes_amazon,structured_walmart_amazon,textual_abt_buy",
        ).split(",")
        if name.strip()
    ]
    negative_cases = [
        ("cross_dblp_vs_itunes", DATASETS / "structured_dblp_acm" / "tableA.csv", DATASETS / "structured_itunes_amazon" / "tableA.csv"),
        ("cross_abt_vs_dblp", DATASETS / "textual_abt_buy" / "tableA.csv", DATASETS / "structured_dblp_scholar" / "tableA.csv"),
        ("cross_walmart_vs_itunes", DATASETS / "structured_walmart_amazon" / "tableA.csv", DATASETS / "structured_itunes_amazon" / "tableA.csv"),
    ]
    results: list[dict[str, Any]] = []
    for name in positive_names:
        print(f"RUN {name}", flush=True)
        result = run_positive_dataset(name)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        results.append(result)
    for name, table_a, table_b in negative_cases:
        print(f"RUN {name}", flush=True)
        result = run_negative_case(name, table_a, table_b)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        results.append(result)
    print("SUMMARY_JSON")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item.get("passed") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
