from __future__ import annotations

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
DIFY_ENDPOINT = "http://localhost/v1/workflows/run"
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "").strip()
TEST_MAX_ROWS = os.environ.get("MDM_TEST_MAX_ROWS", "150")


def run_powershell(script: str) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        cwd=str(ROOT),
    )


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
    log_path = ROOT / "artifacts" / "mdm_api_dataset_test.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    env["MDM_TEST_MAX_ROWS"] = TEST_MAX_ROWS
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "mdm_matching.service:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
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
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"service did not become healthy: {last_error}")


def reset_service() -> dict[str, Any]:
    stop_service()
    delete_sqlite()
    start_service()
    return wait_health()


def run_dify_case(name: str, table_a: Path, table_b: Path) -> dict[str, Any]:
    payload = {
        "inputs": {
            "table_a_path": str(table_a).replace("\\", "/"),
            "table_b_path": str(table_b).replace("\\", "/"),
            "incoming_csv_path": "",
        },
        "response_mode": "blocking",
        "user": "codex-dataset-test",
    }
    response = requests.post(
        DIFY_ENDPOINT,
        headers={
            "Authorization": f"Bearer {DIFY_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=900,
    )
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}
    if response.status_code >= 400:
        return {
            "name": name,
            "http_status": response.status_code,
            "error": body,
        }
    data = body.get("data") if isinstance(body, dict) else {}
    outputs = data.get("outputs") if isinstance(data, dict) else {}
    if not isinstance(outputs, dict):
        outputs = {}
    return {
        "name": name,
        "http_status": response.status_code,
        "workflow_status": data.get("status") if isinstance(data, dict) else None,
        "workflow_run_id": body.get("workflow_run_id") if isinstance(body, dict) else None,
        "outputs": outputs,
    }


def compact_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "schema_related",
        "relation_type",
        "schema_reason",
        "strict_schema_result",
        "early_ingest_result",
        "ingest_result",
        "final_quality_result",
    ):
        if key in outputs:
            value = outputs[key]
            if isinstance(value, str) and len(value) > 800:
                compact[key] = value[:800] + "...<truncated>"
            else:
                compact[key] = value
    return compact


def main() -> int:
    if not DIFY_API_KEY:
        raise SystemExit("DIFY_API_KEY is required")

    same_cases = [
        (
            "same: datasets/structured_amazon_google tableA/tableB",
            DATASETS / "structured_amazon_google" / "tableA.csv",
            DATASETS / "structured_amazon_google" / "tableB.csv",
        ),
        (
            "same: structured_beer tableA/tableB",
            DATASETS / "structured_beer" / "tableA.csv",
            DATASETS / "structured_beer" / "tableB.csv",
        ),
        (
            "same: structured_fodors_zagats tableA/tableB",
            DATASETS / "structured_fodors_zagats" / "tableA.csv",
            DATASETS / "structured_fodors_zagats" / "tableB.csv",
        ),
    ]
    cross_cases = [
        (
            "cross: amazon_google tableA vs beer tableA",
            DATASETS / "structured_amazon_google" / "tableA.csv",
            DATASETS / "structured_beer" / "tableA.csv",
        ),
        (
            "cross: amazon_google tableA vs fodors_zagats tableA",
            DATASETS / "structured_amazon_google" / "tableA.csv",
            DATASETS / "structured_fodors_zagats" / "tableA.csv",
        ),
        (
            "cross: beer tableA vs fodors_zagats tableA",
            DATASETS / "structured_beer" / "tableA.csv",
            DATASETS / "structured_fodors_zagats" / "tableA.csv",
        ),
    ]

    results = []
    for name, table_a, table_b in [*same_cases, *cross_cases]:
        print(f"\n=== {name} ===", flush=True)
        health = reset_service()
        print(f"reset_ok has_master_data={health.get('has_master_data')} test_max_rows={TEST_MAX_ROWS}", flush=True)
        result = run_dify_case(name, table_a, table_b)
        outputs = result.get("outputs") or {}
        summary = {
            "name": name,
            "http_status": result.get("http_status"),
            "workflow_status": result.get("workflow_status"),
            "workflow_run_id": result.get("workflow_run_id"),
            "outputs": compact_outputs(outputs) if isinstance(outputs, dict) else outputs,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        results.append(summary)

    print("\n=== SUMMARY_JSON ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
