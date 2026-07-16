from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"
DB_BASE = ROOT / "artifacts" / "mdm.sqlite3"
API_BASE = "http://127.0.0.1:8000"
DIFY_ENDPOINT = os.environ.get("DIFY_ENDPOINT", "http://localhost/v1/workflows/run")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "").strip()
TEST_MAX_ROWS = int(os.environ.get("MDM_TEST_MAX_ROWS", "150"))


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
    log_path = ROOT / "artifacts" / "mdm_api_dataset_metrics.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    env = os.environ.copy()
    env["MDM_TEST_MAX_ROWS"] = str(TEST_MAX_ROWS)
    env["DIFY_API_KEY"] = DIFY_API_KEY
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


def executed_count(original_count: int) -> int:
    if TEST_MAX_ROWS <= 0:
        return original_count
    return min(original_count, TEST_MAX_ROWS)


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)) or value is None:
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def run_dify_case(table_a: Path, table_b: Path) -> tuple[dict[str, Any], float]:
    payload = {
        "inputs": {
            "table_a_path": str(table_a).replace("\\", "/"),
            "table_b_path": str(table_b).replace("\\", "/"),
            "incoming_csv_path": "",
        },
        "response_mode": "blocking",
        "user": "codex-dataset-metrics",
    }
    started = time.perf_counter()
    response = requests.post(
        DIFY_ENDPOINT,
        headers={"Authorization": f"Bearer {DIFY_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=900,
    )
    elapsed = time.perf_counter() - started
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}
    if response.status_code >= 400:
        return {"http_status": response.status_code, "error": body}, elapsed
    return body, elapsed


def get_quality(expected_a: int, expected_b: int) -> dict[str, Any]:
    try:
        response = requests.get(
            f"{API_BASE}/admin/entity-quality",
            params={"expected_table1": expected_a, "expected_table2": expected_b},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        return {"error": str(exc)}


def get_dashboard_metrics() -> dict[str, Any]:
    for path in ("/admin/dashboard-metrics", "/admin/metrics"):
        try:
            response = requests.get(f"{API_BASE}{path}", timeout=20)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            return response.json()
        except Exception:
            continue
    return {}


def output_value(outputs: dict[str, Any], key: str) -> Any:
    return parse_jsonish(outputs.get(key))


def count_items(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    if value is None:
        return None
    return 0


def summarize_case(name: str, expected_positive: bool, table_a: Path, table_b: Path) -> dict[str, Any]:
    original_a = row_count(table_a)
    original_b = row_count(table_b)
    executed_a = executed_count(original_a)
    executed_b = executed_count(original_b)
    health = reset_service()
    body, elapsed = run_dify_case(table_a, table_b)
    data = body.get("data") if isinstance(body, dict) else {}
    outputs = data.get("outputs") if isinstance(data, dict) else {}
    if not isinstance(outputs, dict):
        outputs = {}

    strict = output_value(outputs, "strict_schema_result")
    ingest = output_value(outputs, "ingest_result") or output_value(outputs, "early_ingest_result")
    final_quality = output_value(outputs, "final_quality_result") or {}
    quality = final_quality.get("quality") if isinstance(final_quality, dict) else None
    if not isinstance(quality, dict):
        quality = get_quality(executed_a, executed_b)
    dashboard = get_dashboard_metrics()

    relation_type = outputs.get("relation_type")
    schema_related = outputs.get("schema_related")
    status = data.get("status") if isinstance(data, dict) else body.get("http_status")
    workflow_run_id = body.get("workflow_run_id") if isinstance(body, dict) else None
    strict_status = strict.get("status") if isinstance(strict, dict) else None
    ingest_status = ingest.get("status") if isinstance(ingest, dict) else None
    final_status = final_quality.get("status") if isinstance(final_quality, dict) else None
    rejected = relation_type == "dataset_type_mismatch" or ingest_status == "reupload_required"
    passed = (not rejected and bool(quality.get("valid"))) if expected_positive else rejected

    table1_links = int(quality.get("table1_source_link_count") or 0)
    table2_links = int(quality.get("table2_source_link_count") or 0)
    coverage_a = table1_links / executed_a if executed_a else 0.0
    coverage_b = table2_links / executed_b if executed_b else 0.0

    auto_clusters = ingest.get("auto_merged_clusters") if isinstance(ingest, dict) else None
    llm_clusters = ingest.get("llm_conflict_clusters") if isinstance(ingest, dict) else None
    human_clusters = ingest.get("human_review_clusters") if isinstance(ingest, dict) else None

    return {
        "case": name,
        "expected": "positive" if expected_positive else "negative",
        "passed": passed,
        "workflow_status": status,
        "workflow_run_id": workflow_run_id,
        "elapsed_seconds": round(elapsed, 3),
        "service_reset_has_master_data": health.get("has_master_data"),
        "table_a_rows_original": original_a,
        "table_b_rows_original": original_b,
        "table_a_rows_executed": executed_a,
        "table_b_rows_executed": executed_b,
        "strict_status": strict_status,
        "schema_related": schema_related,
        "relation_type": relation_type,
        "ingest_status": ingest_status,
        "final_status": final_status,
        "quality_valid": quality.get("valid"),
        "master_data_rows": quality.get("golden_records_count"),
        "entity_count": quality.get("entity_count"),
        "table_a_covered": table1_links,
        "table_b_covered": table2_links,
        "table_a_coverage": round(coverage_a, 6),
        "table_b_coverage": round(coverage_b, 6),
        "duplicate_golden_records": quality.get("duplicate_golden_records"),
        "duplicate_entities": quality.get("duplicate_entities"),
        "orphan_golden_records": quality.get("orphan_golden_records"),
        "orphan_source_links": quality.get("orphan_source_links"),
        "table1_coverage_ok": quality.get("table1_coverage_ok"),
        "table2_coverage_ok": quality.get("table2_coverage_ok"),
        "auto_merged_cluster_count": count_items(auto_clusters),
        "llm_conflict_cluster_count": count_items(llm_clusters),
        "human_review_cluster_count": count_items(human_clusters),
        "dashboard_input_flow": dashboard.get("input_flow") if isinstance(dashboard, dict) else None,
        "schema_reason": outputs.get("schema_reason"),
    }


def main() -> int:
    if not DIFY_API_KEY:
        raise SystemExit("DIFY_API_KEY is required")

    default_cases = [
        ("same_amazon_google", True, DATASETS / "structured_amazon_google" / "tableA.csv", DATASETS / "structured_amazon_google" / "tableB.csv"),
        ("same_beer", True, DATASETS / "structured_beer" / "tableA.csv", DATASETS / "structured_beer" / "tableB.csv"),
        ("same_fodors_zagats", True, DATASETS / "structured_fodors_zagats" / "tableA.csv", DATASETS / "structured_fodors_zagats" / "tableB.csv"),
        ("cross_amazon_vs_beer", False, DATASETS / "structured_amazon_google" / "tableA.csv", DATASETS / "structured_beer" / "tableA.csv"),
        ("cross_amazon_vs_fodors", False, DATASETS / "structured_amazon_google" / "tableA.csv", DATASETS / "structured_fodors_zagats" / "tableA.csv"),
        ("cross_beer_vs_fodors", False, DATASETS / "structured_beer" / "tableA.csv", DATASETS / "structured_fodors_zagats" / "tableA.csv"),
    ]
    new_positive_cases = [
        (name, True, DATASETS / name / "tableA.csv", DATASETS / name / "tableB.csv")
        for name in [
            "dirty_dblp_acm",
            "dirty_dblp_scholar",
            "dirty_itunes_amazon",
            "dirty_walmart_amazon",
            "structured_dblp_acm",
            "structured_dblp_scholar",
            "structured_itunes_amazon",
            "structured_walmart_amazon",
            "textual_abt_buy",
            "textual_company",
        ]
    ]
    new_negative_cases = [
        ("cross_new_dblp_vs_itunes", False, DATASETS / "structured_dblp_acm" / "tableA.csv", DATASETS / "structured_itunes_amazon" / "tableA.csv"),
        ("cross_new_abt_vs_dblp", False, DATASETS / "textual_abt_buy" / "tableA.csv", DATASETS / "structured_dblp_scholar" / "tableA.csv"),
        ("cross_new_walmart_vs_abt", False, DATASETS / "dirty_walmart_amazon" / "tableA.csv", DATASETS / "textual_abt_buy" / "tableA.csv"),
    ]
    small_new_cases = [
        ("dirty_dblp_acm", True, DATASETS / "dirty_dblp_acm" / "tableA.csv", DATASETS / "dirty_dblp_acm" / "tableB.csv"),
        ("structured_dblp_acm", True, DATASETS / "structured_dblp_acm" / "tableA.csv", DATASETS / "structured_dblp_acm" / "tableB.csv"),
        ("textual_abt_buy", True, DATASETS / "textual_abt_buy" / "tableA.csv", DATASETS / "textual_abt_buy" / "tableB.csv"),
        ("cross_small_dblp_vs_abt", False, DATASETS / "structured_dblp_acm" / "tableA.csv", DATASETS / "textual_abt_buy" / "tableA.csv"),
        ("cross_small_dirty_dblp_vs_abt", False, DATASETS / "dirty_dblp_acm" / "tableA.csv", DATASETS / "textual_abt_buy" / "tableA.csv"),
    ]
    case_set = os.environ.get("MDM_DATASET_CASE_SET", "default").strip().lower()
    if case_set == "new":
        cases = [*new_positive_cases, *new_negative_cases]
    elif case_set == "new_positive":
        cases = new_positive_cases
    elif case_set == "new_no_company":
        cases = [case for case in [*new_positive_cases, *new_negative_cases] if "textual_company" not in str(case[2]) and "textual_company" not in str(case[3])]
    elif case_set == "small_new":
        cases = small_new_cases
    elif case_set == "new_negative":
        cases = new_negative_cases
    else:
        cases = default_cases
    results = []
    for name, expected_positive, table_a, table_b in cases:
        print(f"RUN {name}", flush=True)
        result = summarize_case(name, expected_positive, table_a, table_b)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        results.append(result)

    print("SUMMARY_JSON")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
