from __future__ import annotations

import html
import csv
import json
import os
import re
import time
import uuid
import warnings
from collections import defaultdict
from io import StringIO
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs

import joblib
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, create_engine, func, insert, inspect, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from mdm_matching.features import PairFeatureBuilder, token_jaccard
from mdm_matching.preprocess import map_input_columns, normalize_manufacturer, normalize_price, normalize_text, preprocess_table, validate_columns
from mdm_matching.schema_profile import (
    analyze_table_pair,
    apply_schema_profile,
    exact_header_schema_profile,
    generic_pair_features,
    has_exact_column_headers,
)


MODEL_PATH = Path("models/main/mdm_matcher.joblib")
DEFAULT_DATABASE_URL = "sqlite:///models/main/mdm.sqlite3"
MATCH_RUN_DIR = Path("models/main/match_runs")
MAX_DIFY_LLM_CANDIDATES = int(os.getenv("MDM_MAX_DIFY_LLM_CANDIDATES", "50"))
MAX_DIFY_REVIEW_PREVIEW = int(os.getenv("MDM_MAX_DIFY_REVIEW_PREVIEW", "0"))
MAX_DIFY_PAIR_SAMPLE = int(os.getenv("MDM_MAX_DIFY_PAIR_SAMPLE", "10"))
MAX_SCHEMA_CANDIDATES_PER_LEFT = int(os.getenv("MDM_MAX_SCHEMA_CANDIDATES_PER_LEFT", "40"))
MAX_SCHEMA_TOKEN_POSTINGS_RATIO = float(os.getenv("MDM_MAX_SCHEMA_TOKEN_POSTINGS_RATIO", "0.08"))
TEST_MAX_ROWS = int(os.getenv("MDM_TEST_MAX_ROWS", "0"))
AUTO_MERGE_CONFIDENCE = float(os.getenv("MDM_AUTO_MERGE_CONFIDENCE", "0.94"))
LLM_CLUSTER_MIN_CONFIDENCE = float(os.getenv("MDM_LLM_CLUSTER_MIN_CONFIDENCE", "0.59"))
MUTUAL_TOP_AUTO_MERGE_CONFIDENCE = float(os.getenv("MDM_MUTUAL_TOP_AUTO_MERGE_CONFIDENCE", "0.59"))
AMBIGUOUS_MARGIN = float(os.getenv("MDM_AMBIGUOUS_MARGIN", "0.04"))
DATASET_MISMATCH_MESSAGE = "TableA，B不是同一类数据，无法进行主数据匹配"

app = FastAPI(title="MDM Matching Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("MDM_CORS_ORIGINS", "*").split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
metadata = MetaData()

golden_records = Table(
    "golden_records",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entity_id", Integer),
    Column("title", String(512), nullable=False),
    Column("manufacturer", String(255)),
    Column("price", Float),
    Column("selected_source_table", String(32)),
    Column("source_payload", Text, nullable=False),
    Column("source", String(32), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("created_at", Float, nullable=False),
)

action_log = Table(
    "action_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("operation", String(128), nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)

review_tasks = Table(
    "review_tasks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("status", String(32), nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

entities = Table(
    "entities",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("canonical_title", String(512), nullable=False),
    Column("manufacturer", String(255)),
    Column("price", Float),
    Column("status", String(32), nullable=False),
    Column("selected_source_table", String(32)),
    Column("confidence", Float, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

entity_aliases = Table(
    "entity_aliases",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entity_id", Integer, nullable=False),
    Column("alias_title", String(512), nullable=False),
    Column("normalized_title", String(512), nullable=False),
    Column("source_table", String(32), nullable=False),
    Column("source_id", String(255)),
    Column("created_at", Float, nullable=False),
)

source_record_links = Table(
    "source_record_links",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entity_id", Integer, nullable=False),
    Column("source_table", String(32), nullable=False),
    Column("source_id", String(255), nullable=False),
    Column("raw_payload", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)

match_decisions = Table(
    "match_decisions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("match_run_id", String(64)),
    Column("cluster_id", String(64)),
    Column("left_id", String(255)),
    Column("right_id", String(255)),
    Column("decision", String(32), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("reason", Text),
    Column("model_scores", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)

_engine: Engine | None = None
_engine_url: str | None = None


class CsvIngestRequest(BaseModel):
    table_a_path: Any | None = Field(default=None, description="Required when database has no master data.")
    table_b_path: Any | None = Field(default=None, description="Required when database has no master data.")
    incoming_csv_path: Any | None = Field(default=None, description="Required when database already has master data.")
    threshold: float = 0.59
    schema_profile: dict[str, Any] | None = None


class CommitRequest(BaseModel):
    chosen_title: str
    chosen_record: dict[str, Any] | str
    source: Literal["llm", "human", "auto"]
    confidence: float
    reason: str = ""


class CreateReviewTaskRequest(BaseModel):
    review_payload: Any
    source: str = "human"


class CandidateBatchRequest(BaseModel):
    match_run_id: Any | None = None
    ingest_payload: Any | None = None
    route: Literal["llm", "llm_cluster", "human_review"] = "llm_cluster"
    offset: int = 0
    limit: int = 50


class CommitLlmBatchRequest(BaseModel):
    match_run_id: Any | None = None
    ingest_payload: Any | None = None
    offset: int = 0
    limit: int = 50
    llm_result: Any = None
    source: str = "llm"
    reason: str = "高置信候选由大模型批量选择"


class ProcessAllHighConfidenceRequest(BaseModel):
    match_run_id: Any | None = None
    ingest_payload: Any | None = None
    batch_size: int = 30


class FinalizeMatchRunRequest(BaseModel):
    match_run_id: Any | None = None
    ingest_payload: Any | None = None
    table_a_path: Any | None = None
    table_b_path: Any | None = None
    incoming_csv_path: Any | None = None
    fallback_remaining: bool = True
    merge_duplicates: bool = True


class DifyWorkflowProxyRequest(BaseModel):
    dify_endpoint: str
    dify_api_key: str
    inputs: dict[str, Any]
    user: str = "mdm-web-user"
    response_mode: str = "blocking"


class RepairMissingSourceLinksRequest(BaseModel):
    table_a_path: Any | None = None
    table_b_path: Any | None = None


def database_url() -> str:
    return os.getenv("MDM_DATABASE_URL", DEFAULT_DATABASE_URL)


def get_engine() -> Engine:
    global _engine, _engine_url
    current_url = database_url()
    if _engine is None or _engine_url != current_url:
        if current_url.startswith("sqlite:///"):
            Path(current_url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(current_url, future=True, pool_pre_ping=True)
        _engine_url = current_url
        metadata.create_all(_engine)
        ensure_schema_migrations(_engine)
    return _engine


def ensure_schema_migrations(engine: Engine) -> None:
    inspector = inspect(engine)
    golden_columns = {column["name"] for column in inspector.get_columns("golden_records")}
    entity_columns = {column["name"] for column in inspector.get_columns("entities")}
    with engine.begin() as connection:
        if "entity_id" not in golden_columns:
            connection.execute(text("ALTER TABLE golden_records ADD COLUMN entity_id INTEGER"))
        if "selected_source_table" not in golden_columns:
            connection.execute(text("ALTER TABLE golden_records ADD COLUMN selected_source_table VARCHAR(32)"))
        if "write_source" in golden_columns:
            drop_column_if_supported(connection, "golden_records", "write_source")
        if "selected_source_table" not in entity_columns:
            connection.execute(text("ALTER TABLE entities ADD COLUMN selected_source_table VARCHAR(32)"))
        if "write_source" in entity_columns:
            drop_column_if_supported(connection, "entities", "write_source")
        backfill_lineage_fields(connection)
        ensure_quality_indexes(connection, engine.dialect.name)


def ensure_quality_indexes(connection, dialect_name: str) -> None:
    statements = [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_source_record_link ON source_record_links (source_table, source_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_golden_record_entity ON golden_records (entity_id)",
        "CREATE INDEX IF NOT EXISTS ix_entity_alias_normalized ON entity_aliases (normalized_title)",
        "CREATE INDEX IF NOT EXISTS ix_match_decisions_run_cluster ON match_decisions (match_run_id, cluster_id)",
    ]
    for statement in statements:
        try:
            connection.execute(text(statement))
        except Exception as exc:
            connection.execute(
                insert(action_log).values(
                    operation="create_quality_index_skipped",
                    payload=json.dumps({"dialect": dialect_name, "statement": statement, "error": str(exc)}, ensure_ascii=False),
                    created_at=time.time(),
                )
            )


def log_action(operation: str, payload: Any) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            insert(action_log).values(operation=operation, payload=json.dumps(payload, ensure_ascii=False), created_at=time.time())
        )


def drop_column_if_supported(connection, table_name: str, column_name: str) -> None:
    try:
        connection.execute(text(f"ALTER TABLE {table_name} DROP COLUMN {column_name}"))
    except Exception as exc:
        connection.execute(
            insert(action_log).values(
                operation="drop_column_skipped",
                payload=json.dumps({"table": table_name, "column": column_name, "error": str(exc)}, ensure_ascii=False),
                created_at=time.time(),
            )
        )


def backfill_lineage_fields(connection) -> None:
    if not has_lineage_columns(connection):
        return
    now = time.time()
    entity_rows = connection.execute(select(entities)).mappings().all()
    updated_entities = 0
    updated_golden = 0
    for row in entity_rows:
        entity_id = int(row["id"])
        selected_source_table = (
            row.get("selected_source_table")
            if is_valid_selected_source_table(row.get("selected_source_table"))
            else infer_entity_selected_source_table(connection, entity_id, str(row["canonical_title"] or ""))
        )
        selected_source_table = selected_source_table if is_valid_selected_source_table(selected_source_table) else "unknown"
        status = row.get("status") if is_valid_entity_status(row.get("status")) else infer_entity_status(connection, entity_id)
        if not is_valid_selected_source_table(row.get("selected_source_table")) or not is_valid_entity_status(row.get("status")):
            connection.execute(
                update(entities)
                .where(entities.c.id == entity_id)
                .values(selected_source_table=selected_source_table, status=status, updated_at=now)
            )
            updated_entities += 1
        golden_row = connection.execute(select(golden_records).where(golden_records.c.entity_id == entity_id)).mappings().first()
        if golden_row and not is_valid_selected_source_table(golden_row.get("selected_source_table")):
            connection.execute(
                update(golden_records)
                .where(golden_records.c.id == int(golden_row["id"]))
                .values(selected_source_table=selected_source_table)
            )
            updated_golden += 1
    if updated_entities or updated_golden:
        connection.execute(
            insert(action_log).values(
                operation="backfill_lineage_fields",
                payload=json.dumps({"entities": updated_entities, "golden_records": updated_golden}, ensure_ascii=False),
                created_at=now,
            )
        )


def is_valid_selected_source_table(value: Any) -> bool:
    return value in {"tableA", "tableB", "incoming", "unknown"}


def is_valid_entity_status(value: Any) -> bool:
    return value in {"backend_active", "llm_active", "uncertain"}


def has_lineage_columns(connection) -> bool:
    entity_column_names = {column["name"] for column in inspect(connection).get_columns("entities")}
    golden_column_names = {column["name"] for column in inspect(connection).get_columns("golden_records")}
    return "selected_source_table" in entity_column_names and "selected_source_table" in golden_column_names


def infer_entity_selected_source_table(connection, entity_id: int, title: str) -> str:
    normalized_title = normalize_text(title)
    alias_rows = connection.execute(select(entity_aliases).where(entity_aliases.c.entity_id == entity_id)).mappings().all()
    for alias in alias_rows:
        if normalized_title and normalized_title == normalize_text(alias["alias_title"]):
            label = source_table_label(str(alias["source_table"] or ""))
            if label:
                return label
    source_rows = connection.execute(select(source_record_links).where(source_record_links.c.entity_id == entity_id)).mappings().all()
    for source_row in source_rows:
        try:
            raw_payload = json.loads(source_row["raw_payload"] or "{}")
        except json.JSONDecodeError:
            raw_payload = {}
        raw_title = raw_payload.get("title") or raw_payload.get(f"{source_row['source_table']}.title")
        if normalized_title and normalized_title == normalize_text(raw_title):
            return source_table_label(str(source_row["source_table"] or "")) or "unknown"
    if source_rows:
        return source_table_label(str(source_rows[0]["source_table"] or "")) or "unknown"
    return "unknown"


def infer_entity_status(connection, entity_id: int) -> str:
    entity_row = connection.execute(select(entities.c.status).where(entities.c.id == entity_id)).mappings().first()
    if entity_row and entity_row.get("status") == "uncertain":
        return "uncertain"
    golden_row = connection.execute(select(golden_records.c.source).where(golden_records.c.entity_id == entity_id)).mappings().first()
    if golden_row and golden_row.get("source") == "llm":
        return "llm_active"
    return "backend_active"


def save_match_run(payload: dict[str, Any]) -> str:
    MATCH_RUN_DIR.mkdir(parents=True, exist_ok=True)
    match_run_id = uuid.uuid4().hex
    payload = dict(payload)
    payload["match_run_id"] = match_run_id
    payload["created_at"] = time.time()
    match_run_path = MATCH_RUN_DIR / f"{match_run_id}.json"
    match_run_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    log_action(
        "save_match_run",
        {
            "match_run_id": match_run_id,
            "all_candidate_pairs_count": payload.get("all_candidate_pairs_count", 0),
            "high_confidence_count": len(payload.get("high_confidence_candidates", [])),
            "llm_conflict_cluster_count": len(payload.get("llm_conflict_clusters", [])),
            "auto_merged_cluster_count": len(payload.get("auto_merged_clusters", [])),
            "human_review_count": len(payload.get("human_review_candidates", [])),
        },
    )
    return match_run_id


def load_match_run(match_run_id: str) -> dict[str, Any]:
    match_run_path = MATCH_RUN_DIR / f"{match_run_id}.json"
    if not match_run_path.exists():
        raise HTTPException(status_code=404, detail=f"match run not found: {match_run_id}")
    return json.loads(match_run_path.read_text(encoding="utf-8"))


def latest_match_run_id() -> str | None:
    if not MATCH_RUN_DIR.exists():
        return None
    paths = sorted(MATCH_RUN_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return paths[0].stem if paths else None


def resolve_existing_match_run_id(match_run_id: Any | None, payload: Any | None = None, allow_latest: bool = False) -> str:
    try:
        resolved = resolve_match_run_id(match_run_id, payload)
    except HTTPException:
        resolved = ""
    if resolved and (MATCH_RUN_DIR / f"{resolved}.json").exists():
        return resolved
    if allow_latest:
        latest = latest_match_run_id()
        if latest:
            return latest
    if resolved:
        raise HTTPException(status_code=404, detail=f"match run not found: {resolved}")
    raise HTTPException(status_code=400, detail="match_run_id is required")


def resolve_match_run_id(match_run_id: Any | None, payload: Any | None = None) -> str:
    if match_run_id:
        value = str(match_run_id).strip()
        if value.startswith("{"):
            parsed = parse_record_payload(value)
            if parsed.get("match_run_id"):
                return str(parsed["match_run_id"])
        return value
    parsed_payload = parse_record_payload(payload)
    if parsed_payload.get("match_run_id"):
        return str(parsed_payload["match_run_id"])
    raise HTTPException(status_code=400, detail="match_run_id is required")


def update_match_run(match_run_id: str, payload: dict[str, Any]) -> None:
    MATCH_RUN_DIR.mkdir(parents=True, exist_ok=True)
    match_run_path = MATCH_RUN_DIR / f"{match_run_id}.json"
    if not match_run_path.exists():
        raise HTTPException(status_code=404, detail=f"match run not found: {match_run_id}")
    match_run_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def quote_sql_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def json_path(*parts: str) -> str:
    escaped = [str(part).replace("\\", "\\\\").replace('"', '\\"') for part in parts]
    return "$" + "".join(f'."{part}"' for part in escaped)


def master_output_column_name(column: str, role: str | None = None, entity_type: str | None = None) -> str:
    normalized = str(column)
    if role == "primary_name" and normalized == "title" and entity_type == "restaurant":
        return "name"
    return normalized


def schema_master_field_specs(schema_profile: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not schema_profile:
        return []
    master_columns = schema_profile.get("master_columns")
    if isinstance(master_columns, list):
        specs: list[tuple[str, str]] = []
        for item in master_columns:
            if not isinstance(item, dict):
                continue
            source_column = str(item.get("source_column") or item.get("column") or "").strip()
            output_column = str(item.get("output_column") or source_column).strip()
            if not source_column or not output_column:
                continue
            if output_column not in {existing[0] for existing in specs}:
                specs.append((output_column, source_column))
        if specs:
            return specs
    mapping = schema_profile.get("column_mapping") or {}
    roles = schema_profile.get("column_roles") or {}
    ignored = set(schema_profile.get("ignored_columns") or [])
    entity_type = str(schema_profile.get("entity_type") or "")
    specs: list[tuple[str, str]] = []
    for column in mapping:
        column = str(column)
        role = roles.get(column)
        if role == "identifier" or column in ignored:
            continue
        output_column = master_output_column_name(column, str(role or ""), entity_type)
        if output_column not in {existing[0] for existing in specs}:
            specs.append((output_column, column))
    return specs


def inferred_master_field_specs(candidate: dict[str, Any], schema_profile: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    specs = schema_master_field_specs(schema_profile)
    if specs:
        return specs
    fields: set[str] = set()
    for key in candidate:
        if isinstance(key, str) and ".__match_" in key and not key.endswith("_norm"):
            fields.add(key.split(".__match_", 1)[1])
    attributes = candidate.get("attributes")
    if isinstance(attributes, dict):
        fields.update(str(key) for key in attributes if not str(key).endswith("_norm"))
    for member in candidate.get("member_records", []):
        if isinstance(member, dict) and isinstance(member.get("attributes"), dict):
            fields.update(str(key) for key in member["attributes"] if not str(key).endswith("_norm"))
    fields.discard("id")
    fields.discard("class")
    if not fields:
        fields.update(["title", "manufacturer", "price"])
    priority = ["title", "name", "addr", "address", "city", "phone", "type", "manufacturer", "price"]
    ordered = sorted(fields, key=lambda item: (priority.index(item) if item in priority else len(priority), item))
    entity_type = str(candidate.get("schema_entity_type") or "")
    return [
        (master_output_column_name(field, "primary_name" if field in {"title", "name"} else None, entity_type), field)
        for field in ordered
    ]


def candidate_field_value(candidate: dict[str, Any], selected_side: str | None, field: str) -> Any:
    side_candidates = [selected_side] if selected_side else []
    side_candidates.extend(side for side in ("table2", "table1") if side not in side_candidates)
    for side in side_candidates:
        if not side:
            continue
        for key in (f"{side}.__match_{field}", f"{side}.{field}"):
            value = candidate.get(key)
            if value not in (None, ""):
                return value
    attributes = candidate.get("attributes")
    if isinstance(attributes, dict) and attributes.get(field) not in (None, ""):
        return attributes.get(field)
    for member in candidate.get("member_records", []):
        if not isinstance(member, dict):
            continue
        attributes = member.get("attributes")
        if isinstance(attributes, dict) and attributes.get(field) not in (None, ""):
            return attributes.get(field)
        if member.get(field) not in (None, ""):
            return member.get(field)
    if field in {"title", "name"}:
        return candidate.get("title") or candidate.get("table2.title") or candidate.get("table1.title")
    return candidate.get(field)


def build_master_record(title: str, candidate: dict[str, Any], selected_side: str | None, schema_profile: dict[str, Any] | None = None) -> tuple[list[str], dict[str, Any]]:
    specs = inferred_master_field_specs(candidate, schema_profile)
    record: dict[str, Any] = {}
    for output_column, source_column in specs:
        value = candidate_field_value(candidate, selected_side, source_column)
        if output_column in {"price", "amount", "list_price"}:
            value = normalize_price(value)
        record[output_column] = value
    first_column = specs[0][0] if specs else "title"
    if not record.get(first_column):
        record[first_column] = title
    return [output_column for output_column, _source_column in specs], record


def refresh_master_data_view(schema_profile: dict[str, Any] | None = None) -> None:
    specs = schema_master_field_specs(schema_profile)
    if not specs:
        latest_payload = load_latest_match_run_payload()
        specs = schema_master_field_specs(latest_payload.get("schema_profile") if latest_payload else None)
    if not specs:
        specs = [("title", "title"), ("manufacturer", "manufacturer"), ("price", "price")]

    select_columns = ["g.entity_id AS id"]
    for output_column, source_column in specs:
        fallback = "g.title" if output_column in {"name", "title"} else f"g.{source_column}" if source_column in {"manufacturer", "price"} else "NULL"
        candidate_fallbacks = [
            f"CASE WHEN json_valid(g.source_payload) THEN json_extract(g.source_payload, '{json_path('master_record', output_column)}') END",
            f"CASE WHEN json_valid(g.source_payload) THEN json_extract(g.source_payload, '{json_path('candidate', 'attributes', source_column)}') END",
            f"CASE WHEN json_valid(g.source_payload) THEN json_extract(g.source_payload, '{json_path('candidate', f'table2.__match_{source_column}')}') END",
            f"CASE WHEN json_valid(g.source_payload) THEN json_extract(g.source_payload, '{json_path('candidate', f'table1.__match_{source_column}')}') END",
        ]
        select_columns.append(
            f"COALESCE({', '.join(candidate_fallbacks)}, {fallback}) AS {quote_sql_identifier(output_column)}"
        )
    select_columns.extend(
        [
            "e.status AS status",
            "g.selected_source_table AS selected_source_table",
            "g.confidence AS confidence",
            "g.created_at AS created_at",
        ]
    )
    sql = (
        "CREATE VIEW master_data_current AS "
        f"SELECT {', '.join(select_columns)} "
        "FROM golden_records g JOIN entities e ON e.id = g.entity_id"
    )
    with get_engine().begin() as connection:
        connection.execute(text("DROP VIEW IF EXISTS master_data_current"))
        connection.execute(text(sql))
        connection.execute(text("DROP TABLE IF EXISTS master_data"))
        connection.execute(text("CREATE TABLE master_data AS SELECT * FROM master_data_current"))


def master_data_view_metadata(schema_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    specs = schema_master_field_specs(schema_profile)
    if not specs:
        latest_payload = load_latest_match_run_payload()
        specs = schema_master_field_specs(latest_payload.get("schema_profile") if latest_payload else None)
    if not specs:
        specs = [("title", "title"), ("manufacturer", "manufacturer"), ("price", "price")]
    return {
        "table": "master_data",
        "view": "master_data_current",
        "columns": ["id", *[output_column for output_column, _source_column in specs], "status", "selected_source_table", "confidence", "created_at"],
    }


def has_master_data() -> bool:
    engine = get_engine()
    try:
        metadata.create_all(engine)
        ensure_schema_migrations(engine)
        with engine.connect() as connection:
            golden_count = connection.execute(select(func.count()).select_from(golden_records)).scalar_one()
            entity_count = connection.execute(select(func.count()).select_from(entities)).scalar_one()
    except OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        global _engine
        engine.dispose()
        _engine = None
        engine = get_engine()
        with engine.connect() as connection:
            golden_count = connection.execute(select(func.count()).select_from(golden_records)).scalar_one()
            entity_count = connection.execute(select(func.count()).select_from(entities)).scalar_one()
    return int(golden_count) > 0 or int(entity_count) > 0


def resolve_csv_reference(reference: Any) -> str:
    if reference is None:
        return ""
    if isinstance(reference, dict):
        for key in ("url", "source_url", "preview_url", "remote_url", "original_url", "path"):
            if reference.get(key):
                return str(reference[key])
        if reference.get("id"):
            return build_dify_file_url(str(reference["id"]))
        raise HTTPException(status_code=400, detail=f"Unsupported Dify file object: {reference}")

    value = str(reference).strip()
    if value.startswith("{") and value.endswith("}"):
        try:
            return resolve_csv_reference(json.loads(value))
        except json.JSONDecodeError:
            pass
    if not value:
        return ""
    if "/" not in value and "\\" not in value and "." not in value and len(value) > 8:
        return build_dify_file_url(value)
    return value


def build_dify_file_url(file_id: str) -> str:
    dify_api_base = os.getenv("DIFY_API_BASE", "").rstrip("/")
    if not dify_api_base:
        raise HTTPException(status_code=400, detail="DIFY_API_BASE is required when passing a Dify file id.")
    return f"{dify_api_base}/files/{file_id}/preview"


def load_csv(reference: Any) -> pd.DataFrame:
    resolved = resolve_csv_reference(reference)
    if resolved.startswith(("http://", "https://")):
        response = requests.get(resolved, headers=csv_request_headers(resolved), timeout=60)
        response.raise_for_status()
        return limit_test_rows(map_input_columns(pd.read_csv(StringIO(response.text))))

    local_path = Path(resolved)
    if not local_path.exists():
        raise HTTPException(status_code=400, detail=f"CSV file not found: {resolved}")
    return limit_test_rows(map_input_columns(pd.read_csv(local_path)))


def csv_request_headers(resolved: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    dify_api_key = os.getenv("DIFY_API_KEY")
    if "/files/" in resolved and dify_api_key:
        headers["Authorization"] = f"Bearer {dify_api_key}"
    return headers


def load_csv_columns(reference: Any) -> list[str]:
    resolved = resolve_csv_reference(reference)
    if resolved.startswith(("http://", "https://")):
        response = requests.get(resolved, headers=csv_request_headers(resolved), stream=True, timeout=60)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8-sig"
        for line in response.iter_lines(decode_unicode=True):
            if line is None or line == "":
                continue
            return [str(column) for column in next(csv.reader([line]), [])]
        return []

    local_path = Path(resolved)
    if not local_path.exists():
        raise HTTPException(status_code=400, detail=f"CSV file not found: {resolved}")
    try:
        with local_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [str(column) for column in next(csv.reader(handle), [])]
    except UnicodeDecodeError:
        with local_path.open("r", newline="") as handle:
            return [str(column) for column in next(csv.reader(handle), [])]


def limit_test_rows(df: pd.DataFrame) -> pd.DataFrame:
    if TEST_MAX_ROWS > 0 and len(df) > TEST_MAX_ROWS:
        return df.head(TEST_MAX_ROWS).copy()
    return df


def dominant_category_values(df: pd.DataFrame) -> set[str]:
    category_columns = [
        column
        for column in df.columns
        if normalize_text(column) in {"class", "type", "category", "dataset_type", "entity_type", "domain"}
    ]
    values: set[str] = set()
    for column in category_columns:
        normalized = df[column].dropna().astype(str).map(normalize_text)
        normalized = normalized[normalized != ""]
        if normalized.empty:
            continue
        counts = normalized.value_counts()
        threshold = max(1, int(len(normalized) * 0.1))
        values.update(str(value) for value, count in counts.items() if int(count) >= threshold)
    return values


def dataset_compatibility_issue(table_a: pd.DataFrame, table_b: pd.DataFrame, schema_profile: dict[str, Any]) -> str | None:
    domain_a = str(schema_profile.get("domain_a") or "unknown")
    domain_b = str(schema_profile.get("domain_b") or "unknown")
    if domain_a != "unknown" and domain_b != "unknown" and domain_a != domain_b:
        return f"Table A 被识别为 {domain_a}，Table B 被识别为 {domain_b}"

    categories_a = dominant_category_values(table_a)
    categories_b = dominant_category_values(table_b)
    if categories_a and categories_b and categories_a.isdisjoint(categories_b):
        return f"Table A 分类 {sorted(categories_a)} 与 Table B 分类 {sorted(categories_b)} 不重合"

    return None


def schema_mismatch_response(database_has_data: bool, reason: str, schema_profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "valid": False,
        "status": "schema_mismatch",
        "database_has_data": database_has_data,
        "message": DATASET_MISMATCH_MESSAGE,
        "errors": [DATASET_MISMATCH_MESSAGE, reason],
        "schema_profile": schema_profile,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "database_url": mask_database_url(database_url()),
        "has_master_data": has_master_data(),
        "model_loaded": MODEL_PATH.exists(),
        "auto_merge_confidence": AUTO_MERGE_CONFIDENCE,
        "llm_cluster_min_confidence": LLM_CLUSTER_MIN_CONFIDENCE,
        "mutual_top_auto_merge_confidence": MUTUAL_TOP_AUTO_MERGE_CONFIDENCE,
    }


@app.post("/validate-upload")
def validate_upload(request: CsvIngestRequest) -> dict[str, Any]:
    database_has_data = has_master_data()
    required_refs = [request.incoming_csv_path] if database_has_data else [request.table_a_path, request.table_b_path]
    if any(reference in (None, "") for reference in required_refs):
        return {
            "valid": False,
            "database_has_data": database_has_data,
            "errors": ["database has data: upload one incoming CSV" if database_has_data else "empty database: upload tableA and tableB"],
        }

    if not database_has_data:
        try:
            table_a_columns = load_csv_columns(request.table_a_path)
            table_b_columns = load_csv_columns(request.table_b_path)
        except Exception as exc:
            return {"valid": False, "database_has_data": database_has_data, "errors": [str(exc)]}
        if has_exact_column_headers(table_a_columns, table_b_columns):
            return {
                "valid": True,
                "database_has_data": database_has_data,
                "errors": [],
                "schema_profile": exact_header_schema_profile(table_a_columns),
            }

        errors: list[str] = []
        loaded_tables: list[pd.DataFrame] = []
        for reference in required_refs:
            try:
                loaded_tables.append(load_csv(reference))
            except Exception as exc:
                errors.append(f"{resolve_csv_reference(reference)}: {exc}")
        if errors:
            return {"valid": False, "database_has_data": database_has_data, "errors": errors}

        schema_profile = analyze_table_pair(loaded_tables[0], loaded_tables[1], request.schema_profile)
        if not schema_profile.get("related"):
            return schema_mismatch_response(
                database_has_data,
                str(schema_profile.get("reason") or "Table A and Table B are not related entity tables."),
                schema_profile,
            )
        compatibility_issue = dataset_compatibility_issue(loaded_tables[0], loaded_tables[1], schema_profile)
        if compatibility_issue:
            schema_profile = dict(schema_profile)
            schema_profile["related"] = False
            schema_profile["relation_type"] = "dataset_type_mismatch"
            schema_profile["reason"] = compatibility_issue
            return schema_mismatch_response(database_has_data, compatibility_issue, schema_profile)
    else:
        errors: list[str] = []
        schema_profile: dict[str, Any] | None = None
        loaded_tables: list[pd.DataFrame] = []
        for reference in required_refs:
            try:
                loaded_tables.append(load_csv(reference))
            except Exception as exc:
                errors.append(f"{resolve_csv_reference(reference)}: {exc}")
        if errors:
            return {"valid": False, "database_has_data": database_has_data, "errors": errors}
        for reference, df in zip(required_refs, loaded_tables):
            validation = validate_columns(df)
            if not validation.valid:
                errors.extend([f"{resolve_csv_reference(reference)}: {error}" for error in validation.errors])

    return {"valid": not errors, "database_has_data": database_has_data, "errors": errors, "schema_profile": schema_profile}


@app.post("/analyze-schema")
def analyze_schema(request: CsvIngestRequest) -> dict[str, Any]:
    if request.table_a_path in (None, "") or request.table_b_path in (None, ""):
        raise HTTPException(status_code=400, detail="table_a_path and table_b_path are required")
    table_a = load_csv(request.table_a_path)
    table_b = load_csv(request.table_b_path)
    schema_profile = analyze_table_pair(table_a, table_b, request.schema_profile)
    return {"status": "schema_related" if schema_profile.get("related") else "schema_mismatch", "schema_profile": schema_profile}


@app.post("/strict-schema-match")
def strict_schema_match(request: CsvIngestRequest) -> dict[str, Any]:
    if request.table_a_path in (None, "") or request.table_b_path in (None, ""):
        if request.incoming_csv_path not in (None, ""):
            return {
                "status": "strict_schema_bypass",
                "exact_column_schema": False,
                "reason": "incoming_csv_path was provided without Table A/B; bypass strict initial schema matching.",
            }
        return {
            "status": "strict_schema_skipped",
            "exact_column_schema": False,
            "reason": "table_a_path and table_b_path are required for strict schema matching",
        }
    table_a_columns = load_csv_columns(request.table_a_path)
    table_b_columns = load_csv_columns(request.table_b_path)
    exact = has_exact_column_headers(table_a_columns, table_b_columns)
    if not exact:
        return {
            "status": "strict_schema_miss",
            "exact_column_schema": False,
            "table_a_columns": table_a_columns,
            "table_b_columns": table_b_columns,
            "reason": "Table A and Table B column headers are not exactly identical in order, characters, and case.",
        }
    schema_profile = exact_header_schema_profile(table_a_columns)
    return {
        "status": "strict_schema_matched",
        "exact_column_schema": True,
        "schema_profile": schema_profile,
        "table_a_columns": table_a_columns,
        "table_b_columns": table_b_columns,
        "reason": "Table A and Table B column headers are exactly identical; bypass schema LLM and continue matching.",
    }


@app.post("/run-dify-workflow")
def run_dify_workflow_proxy(request: DifyWorkflowProxyRequest) -> dict[str, Any]:
    endpoint = request.dify_endpoint.strip()
    if not endpoint:
        raise HTTPException(status_code=400, detail="dify_endpoint is required")
    if not request.dify_api_key.strip():
        raise HTTPException(status_code=400, detail="dify_api_key is required")
    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {request.dify_api_key.strip()}",
                "Content-Type": "application/json",
            },
            json={
                "inputs": request.inputs,
                "response_mode": request.response_mode,
                "user": request.user,
            },
            timeout=600,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Dify workflow request failed: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=body)
    return body


def build_entity_cluster_plan(scored: pd.DataFrame, threshold: float) -> dict[str, Any]:
    candidate_rows = scored.to_dict(orient="records")
    parent: dict[str, str] = {}
    auto_merged_clusters: list[dict[str, Any]] = []
    llm_conflict_clusters: list[dict[str, Any]] = []
    ignored_low_confidence_clusters: list[dict[str, Any]] = []
    matched_master_ids: set[str] = set()
    matched_incoming_ids: set[str] = set()

    mutual_top_rows = find_mutual_top_auto_merge_rows(candidate_rows)
    for index, row in enumerate(mutual_top_rows, start=1):
        cluster = build_cluster_payload(index, [row])
        cluster["routing_decision"] = "mutual_top_auto_merge"
        cluster["routing_reason"] = "互为最佳候选、无明显品牌价格冲突且达到互选自动合并线"
        auto_merged_clusters.append(cluster)
        matched_master_ids.update(str(left_id) for left_id in cluster["table1_ids"])
        matched_incoming_ids.update(str(right_id) for right_id in cluster["table2_ids"])

    def is_already_auto_merged(row: dict[str, Any]) -> bool:
        return str(row.get("table1.id")) in matched_master_ids or str(row.get("table2.id")) in matched_incoming_ids

    def find(node: str) -> str:
        parent.setdefault(node, node)
        if parent[node] != node:
            parent[node] = find(parent[node])
        return parent[node]

    def union(left: str, right: str) -> None:
        parent[find(right)] = find(left)

    retained_rows = [
        row
        for row in candidate_rows
        if safe_float(row.get("confidence")) >= LLM_CLUSTER_MIN_CONFIDENCE and not is_already_auto_merged(row)
    ]
    for row in retained_rows:
        union(f"table1:{row.get('table1.id')}", f"table2:{row.get('table2.id')}")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in retained_rows:
        root = find(f"table1:{row.get('table1.id')}")
        grouped.setdefault(root, []).append(row)

    for index, rows in enumerate(grouped.values(), start=len(auto_merged_clusters) + 1):
        rows = sorted(rows, key=lambda item: safe_float(item.get("confidence")), reverse=True)
        cluster = build_cluster_payload(index, rows)

        if is_auto_merge_cluster(cluster, threshold):
            cluster["routing_decision"] = "auto_merge"
            cluster["routing_reason"] = "一对一、无明显品牌价格冲突且置信度达到自动合并线"
            auto_merged_clusters.append(cluster)
            for left_id in cluster["table1_ids"]:
                matched_master_ids.add(str(left_id))
            for right_id in cluster["table2_ids"]:
                matched_incoming_ids.add(str(right_id))
        elif should_send_cluster_to_llm(cluster, threshold):
            cluster["routing_decision"] = "llm_cluster"
            if cluster.get("has_competition") or cluster.get("has_field_conflict"):
                cluster["routing_reason"] = "存在一对多、多对一、候选分数接近竞争或字段冲突，需要按实体簇判定"
            else:
                cluster["routing_reason"] = "高于大模型保留线但未达到自动合并安全线，交给大模型判定，避免潜在同实体被拆成多个 active 实体"
            llm_conflict_clusters.append(cluster)
            for left_id in cluster["table1_ids"]:
                matched_master_ids.add(str(left_id))
            for right_id in cluster["table2_ids"]:
                matched_incoming_ids.add(str(right_id))
        else:
            cluster["routing_decision"] = "different_entity"
            cluster["routing_reason"] = "低于阈值或没有必须判定的竞争关系，默认不合并；对应 incoming 记录作为独立实体写入"
            ignored_low_confidence_clusters.append(cluster)

    low_rows = [
        row
        for row in candidate_rows
        if safe_float(row.get("confidence")) < LLM_CLUSTER_MIN_CONFIDENCE and not is_already_auto_merged(row)
    ]
    if low_rows:
        ignored_low_confidence_clusters.append(
            {
                "cluster_id": "low_confidence_pairs",
                "routing_decision": "different_entity",
                "routing_reason": "低于保留阈值，默认不合并",
                "pair_count": len(low_rows),
                "max_confidence": max(safe_float(row.get("confidence")) for row in low_rows),
                "candidates": low_rows[:MAX_DIFY_PAIR_SAMPLE],
            }
        )

    return {
        "auto_merged_clusters": auto_merged_clusters,
        "llm_conflict_clusters": llm_conflict_clusters,
        "ignored_low_confidence_clusters": ignored_low_confidence_clusters,
        "matched_master_ids": matched_master_ids,
        "matched_incoming_ids": matched_incoming_ids,
    }


def find_mutual_top_auto_merge_rows(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_left = rank_best_rows(candidate_rows, "table1.id")
    best_by_right = rank_best_rows(candidate_rows, "table2.id")
    selected: list[dict[str, Any]] = []
    used_left_ids: set[str] = set()
    used_right_ids: set[str] = set()

    for row in sorted(candidate_rows, key=lambda item: safe_float(item.get("confidence")), reverse=True):
        left_id = str(row.get("table1.id"))
        right_id = str(row.get("table2.id"))
        confidence = safe_float(row.get("confidence"))
        if confidence < MUTUAL_TOP_AUTO_MERGE_CONFIDENCE or confidence >= AUTO_MERGE_CONFIDENCE:
            continue
        if left_id in used_left_ids or right_id in used_right_ids or has_field_conflict(row):
            continue

        left_best, left_margin = best_by_left.get(left_id, (None, 0.0))
        right_best, right_margin = best_by_right.get(right_id, (None, 0.0))
        if left_best is row and right_best is row and left_margin >= AMBIGUOUS_MARGIN and right_margin >= AMBIGUOUS_MARGIN:
            selected.append(row)
            used_left_ids.add(left_id)
            used_right_ids.add(right_id)

    return selected


def rank_best_rows(candidate_rows: list[dict[str, Any]], id_column: str) -> dict[str, tuple[dict[str, Any], float]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in candidate_rows:
        grouped.setdefault(str(row.get(id_column)), []).append(row)

    ranked: dict[str, tuple[dict[str, Any], float]] = {}
    for source_id, rows in grouped.items():
        rows = sorted(rows, key=lambda item: safe_float(item.get("confidence")), reverse=True)
        top_score = safe_float(rows[0].get("confidence"))
        second_score = safe_float(rows[1].get("confidence")) if len(rows) > 1 else -1.0
        ranked[source_id] = (rows[0], top_score - second_score)
    return ranked


def build_cluster_payload(index: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    table1_ids: set[str] = set()
    table2_ids: set[str] = set()
    for row in rows:
        for side in ("table1", "table2"):
            source_id = str(row.get(f"{side}.id"))
            node_id = f"{side}:{source_id}"
            records[node_id] = {
                "node_id": node_id,
                "source_table": side,
                "source_id": source_id,
                "title": row.get(f"{side}.title"),
                "manufacturer": row.get(f"{side}.manufacturer"),
                "price": row.get(f"{side}.price"),
                "attributes": {
                    key.removeprefix(f"{side}.__match_"): value
                    for key, value in row.items()
                    if key.startswith(f"{side}.__match_")
                },
            }
        table1_ids.add(str(row.get("table1.id")))
        table2_ids.add(str(row.get("table2.id")))

    scores = [safe_float(row.get("confidence")) for row in rows]
    margin = scores[0] - scores[1] if len(scores) > 1 else 1.0
    return {
        "cluster_id": f"cluster_{index:04d}",
        "records": list(records.values()),
        "candidates": rows,
        "table1_ids": sorted(table1_ids),
        "table2_ids": sorted(table2_ids),
        "pair_count": len(rows),
        "record_count": len(records),
        "max_confidence": max(scores) if scores else 0.0,
        "score_margin": margin,
        "has_competition": len(table1_ids) > 1 or len(table2_ids) > 1 or margin < AMBIGUOUS_MARGIN,
        "has_field_conflict": any(has_field_conflict(row) for row in rows),
    }


def has_field_conflict(candidate: dict[str, Any]) -> bool:
    if candidate.get("schema_entity_type") and candidate.get("schema_entity_type") != "product":
        return False
    left_maker = normalize_manufacturer(candidate.get("table1.manufacturer"))
    right_maker = normalize_manufacturer(candidate.get("table2.manufacturer"))
    maker_conflict = bool(left_maker and right_maker and left_maker != right_maker)
    left_price = normalize_price(candidate.get("table1.price"))
    right_price = normalize_price(candidate.get("table2.price"))
    price_conflict = False
    if left_price is not None and right_price is not None:
        price_conflict = abs(left_price - right_price) / max(left_price, right_price, 1.0) > 0.35
    return maker_conflict or price_conflict


def is_auto_merge_cluster(cluster: dict[str, Any], threshold: float) -> bool:
    effective_threshold = max(float(threshold), AUTO_MERGE_CONFIDENCE)
    return (
        len(cluster.get("table1_ids", [])) == 1
        and len(cluster.get("table2_ids", [])) == 1
        and not cluster.get("has_competition")
        and not cluster.get("has_field_conflict")
        and safe_float(cluster.get("max_confidence")) >= effective_threshold
    )


def should_send_cluster_to_llm(cluster: dict[str, Any], threshold: float) -> bool:
    return safe_float(cluster.get("max_confidence")) >= LLM_CLUSTER_MIN_CONFIDENCE


def flatten_cluster_candidates(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for cluster in clusters:
        for candidate in cluster.get("candidates", []):
            if isinstance(candidate, dict):
                item = dict(candidate)
                item["cluster_id"] = cluster.get("cluster_id")
                flattened.append(item)
    return flattened


def commit_auto_clusters(clusters: list[dict[str, Any]]) -> list[str]:
    titles: list[str] = []
    for cluster in clusters:
        candidates = [candidate for candidate in cluster.get("candidates", []) if isinstance(candidate, dict)]
        if not candidates:
            continue
        candidate = candidates[0]
        chosen_title = choose_canonical_title_from_candidate(candidate)
        titles.append(
            commit_golden_record_payload(
                chosen_title=chosen_title,
                chosen_record=candidate,
                source="auto",
                confidence=safe_float(candidate.get("confidence"), default=safe_float(cluster.get("max_confidence"), default=1.0)),
                reason=f"自动实体聚类合并：{cluster.get('routing_reason', '')}",
            )
        )
    return titles


@app.post("/ingest-and-match")
def ingest_and_match(request: CsvIngestRequest) -> dict[str, Any]:
    validation = validate_upload(request)
    if not validation["valid"]:
        return {"status": "reupload_required", **validation}

    model_bundle = load_model()
    is_initial_load = not validation["database_has_data"]
    schema_profile = validation.get("schema_profile") or request.schema_profile
    if validation["database_has_data"]:
        incoming = preprocess_table(load_csv(request.incoming_csv_path))
        master = load_master_as_table()
    else:
        table_a = load_csv(request.table_a_path)
        table_b = load_csv(request.table_b_path)
        if not schema_profile:
            schema_profile = analyze_table_pair(table_a, table_b, request.schema_profile)
        incoming = apply_schema_profile(table_b, schema_profile, "tableB")
        master = apply_schema_profile(table_a, schema_profile, "tableA")

    candidate_pairs = build_candidate_pairs(master, incoming, schema_profile=schema_profile if is_initial_load else None)
    if candidate_pairs.empty:
        inserted_incoming = write_unmatched_to_master(incoming, source_table="table2" if is_initial_load else "incoming")
        inserted_master = write_unmatched_to_master(master, source_table="table1") if is_initial_load else 0
        refresh_master_data_view(schema_profile if isinstance(schema_profile, dict) else None)
        return {
            "status": "completed",
            "master_data_view": master_data_view_metadata(schema_profile if isinstance(schema_profile, dict) else None),
            "inserted_unmatched": inserted_incoming + inserted_master,
            "inserted_unmatched_incoming": inserted_incoming,
            "inserted_unmatched_master": inserted_master,
            "candidates": [],
            "action_log": ["no candidates above rough filter"],
        }

    scored = score_candidates(candidate_pairs, model_bundle, schema_profile=schema_profile if is_initial_load else None)
    scored = scored.sort_values("confidence", ascending=False)
    cluster_plan = build_entity_cluster_plan(scored, threshold=request.threshold)
    auto_inserted = write_unmatched_to_master(
        incoming,
        matched_ids=cluster_plan["matched_incoming_ids"],
        source_table="table2" if is_initial_load else "incoming",
    )
    auto_inserted_master = (
        write_unmatched_to_master(master, matched_ids=cluster_plan["matched_master_ids"], source_table="table1") if is_initial_load else 0
    )
    auto_merged_titles = commit_auto_clusters(cluster_plan["auto_merged_clusters"])
    full_high_confidence = flatten_cluster_candidates(cluster_plan["llm_conflict_clusters"])
    full_review_candidates: list[dict[str, Any]] = []
    all_candidate_pairs_sample = scored.head(MAX_DIFY_PAIR_SAMPLE).to_dict(orient="records")
    llm_candidates: list[dict[str, Any]] = []
    llm_cluster_preview = cluster_plan["llm_conflict_clusters"][:MAX_DIFY_REVIEW_PREVIEW]
    review_preview: list[dict[str, Any]] = []
    match_run_id = save_match_run(
        {
            "threshold": request.threshold,
            "is_initial_load": is_initial_load,
            "expected_table1_source_link_count": len(master) if is_initial_load else None,
            "expected_table2_source_link_count": len(incoming) if is_initial_load else None,
            "table_a_path": request.table_a_path,
            "table_b_path": request.table_b_path,
            "incoming_csv_path": request.incoming_csv_path,
            "schema_profile": schema_profile,
            "auto_merge_confidence": AUTO_MERGE_CONFIDENCE,
            "llm_cluster_min_confidence": LLM_CLUSTER_MIN_CONFIDENCE,
            "mutual_top_auto_merge_confidence": MUTUAL_TOP_AUTO_MERGE_CONFIDENCE,
            "all_candidate_pairs_count": len(scored),
            "high_confidence_candidates": full_high_confidence,
            "llm_conflict_clusters": cluster_plan["llm_conflict_clusters"],
            "auto_merged_clusters": cluster_plan["auto_merged_clusters"],
            "ignored_low_confidence_clusters": cluster_plan["ignored_low_confidence_clusters"],
            "human_review_candidates": full_review_candidates,
            "llm_commits": [],
        }
    )
    refresh_master_data_view(schema_profile if isinstance(schema_profile, dict) else None)
    review_task = {
        "status": "no_review_required",
        "task_id": None,
        "review_url": None,
        "message": "当前方案已用自动聚类和冲突簇大模型判定替代人工审核主流程",
    }
    if cluster_plan["llm_conflict_clusters"]:
        status = "llm_required"
    else:
        status = "completed"
    return {
        "status": status,
        "match_run_id": match_run_id,
        "master_data_view": master_data_view_metadata(schema_profile if isinstance(schema_profile, dict) else None),
        "threshold": request.threshold,
        "schema_profile": schema_profile,
        "auto_merge_confidence": AUTO_MERGE_CONFIDENCE,
        "llm_cluster_min_confidence": LLM_CLUSTER_MIN_CONFIDENCE,
        "mutual_top_auto_merge_confidence": MUTUAL_TOP_AUTO_MERGE_CONFIDENCE,
        "auto_inserted_unmatched": auto_inserted,
        "auto_inserted_unmatched_master": auto_inserted_master,
        "auto_merged_entities": len(auto_merged_titles),
        "routing_summary": {
            "needs_llm": bool(cluster_plan["llm_conflict_clusters"]),
            "needs_human_review": False,
            "all_candidate_pairs_count": len(scored),
            "high_confidence_count": len(full_high_confidence),
            "llm_conflict_cluster_count": len(cluster_plan["llm_conflict_clusters"]),
            "auto_merged_cluster_count": len(cluster_plan["auto_merged_clusters"]),
            "ignored_low_confidence_cluster_count": len(cluster_plan["ignored_low_confidence_clusters"]),
            "covered_master_count": len(cluster_plan["matched_master_ids"]),
            "covered_incoming_count": len(cluster_plan["matched_incoming_ids"]),
            "human_review_count": 0,
            "returned_llm_count": len(llm_candidates),
            "returned_llm_cluster_preview_count": len(llm_cluster_preview),
            "returned_human_review_preview_count": len(review_preview),
            "review_task_status": review_task["status"],
            "review_url": review_task["review_url"],
            "dify_output_compacted": True,
            "rule": "无冲突且达到自动合并安全线的一对一候选自动合并；互为最佳、分差足够且达到互选自动合并线的一对一候选自动合并；高于大模型保留线但未自动合并的候选簇全部交给大模型；低于大模型保留线的候选默认不合并，来源记录作为独立实体写入",
        },
        "review_task": review_task,
        "llm_payload": {
            "threshold": request.threshold,
            "llm_conflict_clusters": llm_cluster_preview,
        },
        "human_review_payload": {
            "threshold": request.threshold,
            "human_review_candidates": review_preview,
        },
        "all_candidate_pairs_sample": all_candidate_pairs_sample,
        "high_confidence_candidates": llm_candidates,
        "llm_conflict_clusters": llm_cluster_preview,
        "human_review_candidates": review_preview,
        "action_log": [
            f"candidate_pairs={len(candidate_pairs)}",
            f"auto_merged_clusters={len(cluster_plan['auto_merged_clusters'])}",
            f"llm_conflict_clusters={len(cluster_plan['llm_conflict_clusters'])}",
            f"ignored_low_confidence_clusters={len(cluster_plan['ignored_low_confidence_clusters'])}",
            f"auto_inserted_unmatched_master={auto_inserted_master}",
            f"match_run_id={match_run_id}",
        ],
    }


@app.post("/candidate-batch")
def candidate_batch(request: CandidateBatchRequest) -> dict[str, Any]:
    match_run_id = resolve_match_run_id(request.match_run_id, request.ingest_payload)
    payload = load_match_run(match_run_id)
    if request.route in {"llm", "llm_cluster"}:
        candidates_key = "llm_conflict_clusters" if isinstance(payload.get("llm_conflict_clusters"), list) else "high_confidence_candidates"
    else:
        candidates_key = "human_review_candidates"
    candidates = payload.get(candidates_key, [])
    if not isinstance(candidates, list):
        candidates = []
    offset = max(0, int(request.offset))
    limit = max(1, min(int(request.limit), 100))
    batch = candidates[offset : offset + limit]
    next_offset = offset + len(batch)
    return {
        "status": "batch_ready" if batch else "batch_empty",
        "match_run_id": match_run_id,
        "route": request.route,
        "payload_type": "entity_conflict_clusters" if candidates_key == "llm_conflict_clusters" else "candidate_pairs",
        "offset": offset,
        "limit": limit,
        "returned_count": len(batch),
        "total_count": len(candidates),
        "next_offset": next_offset,
        "has_more": next_offset < len(candidates),
        "clusters": batch if candidates_key == "llm_conflict_clusters" else [],
        "candidates": [] if candidates_key == "llm_conflict_clusters" else batch,
    }


@app.post("/commit-llm-batch")
async def commit_llm_batch_endpoint(request: Request) -> dict[str, Any]:
    raw = await request.body()
    parsed = parse_request_payload(raw)
    return commit_llm_batch(CommitLlmBatchRequest(**normalize_commit_llm_batch_payload(parsed)))


def normalize_commit_llm_batch_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    payload = dict(parsed)
    if "llm_result" not in payload:
        for key in ("result", "text", "answer", "records", "choices", "items"):
            if key in payload:
                payload["llm_result"] = payload[key]
                break
    payload.setdefault("offset", 0)
    payload.setdefault("limit", 50)
    payload.setdefault("source", "llm")
    payload.setdefault("reason", "高置信候选由大模型批量选择")
    return payload


def commit_llm_batch(request: CommitLlmBatchRequest) -> dict[str, Any]:
    match_run_id = resolve_match_run_id(request.match_run_id, request.ingest_payload)
    parsed = parse_record_payload(request.llm_result)
    payload = load_match_run(match_run_id)
    if payload.get("stopped_reason"):
        return {
            "status": "stopped",
            "match_run_id": match_run_id,
            "committed_count": 0,
            "titles": [],
            "next_offset": request.offset,
            "has_more": False,
            "message": str(payload.get("stopped_reason")),
        }
    commits = payload.get("llm_commits")
    if not isinstance(commits, list):
        commits = []
    existing_commit = find_existing_batch_commit(commits, request.offset, request.limit)
    cluster_candidates = payload.get("llm_conflict_clusters")
    high_confidence = (
        cluster_candidates
        if "llm_conflict_clusters" in payload and isinstance(cluster_candidates, list)
        else payload.get("high_confidence_candidates", [])
    )
    next_offset = request.offset + request.limit
    batch = high_confidence[request.offset : request.offset + request.limit] if isinstance(high_confidence, list) else []
    if existing_commit:
        return {
            "status": "already_committed",
            "match_run_id": match_run_id,
            "committed_count": int(existing_commit.get("committed_count") or 0),
            "titles": existing_commit.get("titles") or [],
            "next_offset": int(existing_commit.get("next_offset") or next_offset),
            "has_more": next_offset < len(high_confidence) if isinstance(high_confidence, list) else False,
            "message": "当前批次已经写入过，本次请求已跳过，避免重复写库。",
        }
    effective_batch = batch
    if isinstance(cluster_candidates, list):
        effective_batch = covered_batch_prefix(parsed, batch)
        validation_error = validate_cluster_llm_result(parsed, effective_batch)
        if validation_error:
            return retry_or_fail_llm_batch(
                payload=payload,
                match_run_id=match_run_id,
                offset=request.offset,
                limit=request.limit,
                total_count=len(high_confidence) if isinstance(high_confidence, list) else 0,
                reason=validation_error,
                llm_result=request.llm_result,
            )
        next_offset = request.offset + len(effective_batch)

    records = extract_cluster_commit_records(parsed, effective_batch)
    uncovered_members = find_uncovered_cluster_members(parsed, effective_batch) if records and isinstance(parsed.get("clusters"), list) else []
    if uncovered_members:
        return retry_or_fail_llm_batch(
            payload=payload,
            match_run_id=match_run_id,
            offset=request.offset,
            limit=request.limit,
            total_count=len(high_confidence) if isinstance(high_confidence, list) else 0,
            reason=f"大模型未完整覆盖当前批次成员：{uncovered_members}",
            llm_result=request.llm_result,
        )
    if not records:
        records = extract_commit_records({"llm_result": parsed})
    if not records:
        records = extract_commit_records(parsed)
    if not records:
        return retry_or_fail_llm_batch(
            payload=payload,
            match_run_id=match_run_id,
            offset=request.offset,
            limit=request.limit,
            total_count=len(high_confidence) if isinstance(high_confidence, list) else 0,
            reason="大模型输出没有可提交的实体记录",
            llm_result=request.llm_result,
        )

    titles: list[str] = []
    for record in records:
        chosen_title = extract_chosen_title(record.get("chosen_title") or record.get("title") or "")
        chosen_record = record.get("chosen_record") or record.get("record") or record.get("candidate") or record
        record_confidence = chosen_record.get("confidence") if isinstance(chosen_record, dict) else None
        confidence = safe_float(record.get("confidence"), default=safe_float(record_confidence, default=0.9))
        titles.append(
            commit_golden_record_payload(
                chosen_title=chosen_title,
                chosen_record=chosen_record,
                source=request.source,
                confidence=confidence,
                reason=record.get("reason") or request.reason,
            )
        )

    status = "committed"
    if isinstance(cluster_candidates, list) and len(effective_batch) < len(batch):
        status = "partial_committed"
        log_action(
            "commit_llm_batch_partial_current_batch",
            {
                "match_run_id": match_run_id,
                "offset": request.offset,
                "limit": request.limit,
                "covered_cluster_count": len(effective_batch),
                "requested_cluster_count": len(batch),
                "next_offset": next_offset,
            },
        )

    clear_llm_retry_count(payload, request.offset, request.limit)
    append_llm_commit(payload, match_run_id, request.offset, request.limit, len(titles), titles, next_offset, status)

    return {
        "status": status if titles else "no_valid_records",
        "match_run_id": match_run_id,
        "committed_count": len(titles),
        "titles": titles,
        "next_offset": next_offset,
        "has_more": next_offset < len(high_confidence) if isinstance(high_confidence, list) else False,
        "message": "如果 has_more 为 true，可继续调用 /candidate-batch 拉取下一批冲突实体簇。",
    }


def mark_uncovered_members_uncertain(match_run_id: str, uncovered_members: list[dict[str, Any]]) -> None:
    now = time.time()
    with get_engine().begin() as connection:
        for item in uncovered_members:
            cluster = item.get("cluster") if isinstance(item, dict) else {}
            if not isinstance(cluster, dict):
                continue
            missing_members = set(str(member) for member in item.get("missing_members", []))
            for record in cluster.get("records", []):
                if isinstance(record, dict) and str(record.get("node_id")) in missing_members:
                    upsert_uncertain_record_entity(connection, record, cluster, match_run_id)
            connection.execute(
                insert(match_decisions).values(
                    match_run_id=match_run_id,
                    cluster_id=str(cluster.get("cluster_id") or ""),
                    left_id=",".join(str(value) for value in cluster.get("table1_ids", [])),
                    right_id=",".join(str(value) for value in cluster.get("table2_ids", [])),
                    decision="uncertain",
                    confidence=safe_float(cluster.get("max_confidence"), default=0.0),
                    reason=f"大模型没有覆盖簇内成员：{item.get('missing_members')}",
                    model_scores=json.dumps({"missing_members": item.get("missing_members")}, ensure_ascii=False),
                    created_at=now,
                )
            )


def validate_cluster_llm_result(parsed: dict[str, Any], batch: list[dict[str, Any]]) -> str | None:
    if not batch:
        return "大模型输出没有覆盖当前批次的第一个冲突簇"
    parsed_clusters = parsed.get("clusters")
    if not isinstance(parsed_clusters, list) or not parsed_clusters:
        return "结构化输出缺少 clusters 或 clusters 为空"

    expected_cluster_ids = [str(cluster.get("cluster_id") or "") for cluster in batch if isinstance(cluster, dict)]
    expected_cluster_id_set = set(expected_cluster_ids)
    parsed_cluster_ids = [str(cluster.get("cluster_id") or "") for cluster in parsed_clusters if isinstance(cluster, dict)]
    parsed_cluster_id_set = set(parsed_cluster_ids)
    extra_cluster_ids = sorted(parsed_cluster_id_set - expected_cluster_id_set)
    if extra_cluster_ids:
        log_action(
            "commit_llm_batch_extra_clusters_ignored",
            {
                "expected_cluster_ids": expected_cluster_ids,
                "extra_cluster_ids": extra_cluster_ids,
            },
        )

    cluster_by_id = {str(cluster.get("cluster_id")): cluster for cluster in batch if isinstance(cluster, dict)}
    for cluster_result in parsed_clusters:
        if not isinstance(cluster_result, dict):
            return "clusters 中存在非对象元素"
        cluster_id = str(cluster_result.get("cluster_id") or "")
        cluster = cluster_by_id.get(cluster_id)
        if not cluster:
            continue
        entities_result = cluster_result.get("entities")
        if not isinstance(entities_result, list) or not entities_result:
            return f"{cluster_id} 缺少 entities 或 entities 为空"
        expected_members = {str(record.get("node_id")) for record in cluster.get("records", []) if isinstance(record, dict)}
        actual_members: list[str] = []
        for entity_result in entities_result:
            if not isinstance(entity_result, dict):
                return f"{cluster_id} entities 中存在非对象元素"
            members = entity_result.get("members")
            if not isinstance(members, list) or not members:
                return f"{cluster_id} 存在空 members"
            actual_members.extend(str(member) for member in members)
            title = str(entity_result.get("canonical_title") or "")
            if not title.strip():
                return f"{cluster_id} 存在空 canonical_title"
        actual_member_set = set(actual_members)
        duplicate_members = sorted(member for member in actual_member_set if actual_members.count(member) > 1)
        if duplicate_members:
            return f"{cluster_id} members 重复出现：{duplicate_members}"
        if actual_member_set != expected_members:
            missing = sorted(expected_members - actual_member_set)
            unexpected = sorted(actual_member_set - expected_members)
            return f"{cluster_id} members 未完整覆盖当前输入，missing={missing}, unexpected={unexpected}"
    return None


def covered_batch_prefix(parsed: dict[str, Any], batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the consecutive current-batch clusters covered by the LLM output.

    Dify/LLM may return accumulated clusters from previous loop iterations. We only
    commit a safe prefix of the current batch, then continue from the first missing
    current cluster instead of failing the whole workflow.
    """
    parsed_clusters = parsed.get("clusters") if isinstance(parsed.get("clusters"), list) else []
    parsed_cluster_ids = {
        str(cluster.get("cluster_id") or "")
        for cluster in parsed_clusters
        if isinstance(cluster, dict)
    }
    covered: list[dict[str, Any]] = []
    for cluster in batch:
        if not isinstance(cluster, dict):
            break
        cluster_id = str(cluster.get("cluster_id") or "")
        if cluster_id not in parsed_cluster_ids:
            break
        covered.append(cluster)
    return covered


def retry_or_fail_llm_batch(
    payload: dict[str, Any],
    match_run_id: str,
    offset: int,
    limit: int,
    total_count: int,
    reason: str,
    llm_result: Any,
) -> dict[str, Any]:
    retry_counts = payload.get("llm_retry_counts")
    if not isinstance(retry_counts, dict):
        retry_counts = {}
    retry_key = f"{offset}:{limit}"
    retry_count = int(retry_counts.get(retry_key) or 0) + 1
    retry_counts[retry_key] = retry_count
    payload["llm_retry_counts"] = retry_counts
    update_match_run(match_run_id, payload)
    log_action(
        "commit_llm_batch_retry_required",
        {
            "match_run_id": match_run_id,
            "offset": offset,
            "limit": limit,
            "retry_count": retry_count,
            "reason": reason,
            "llm_result_preview": str(llm_result)[:1000],
        },
    )
    if retry_count >= 3:
        payload["stopped_reason"] = f"当前批次大模型结构化输出连续 {retry_count} 次无效，已停止写库。原因：{reason}"
        update_match_run(match_run_id, payload)
        return {
            "status": "invalid_llm_output_stopped",
            "match_run_id": match_run_id,
            "committed_count": 0,
            "titles": [],
            "next_offset": offset,
            "has_more": False,
            "message": payload["stopped_reason"],
        }
    return {
        "status": "retry_required",
        "match_run_id": match_run_id,
        "committed_count": 0,
        "titles": [],
        "next_offset": offset,
        "has_more": offset < total_count,
        "message": f"当前批次大模型结构化输出无效，保持 offset={offset} 重新调用 LLM。原因：{reason}",
    }


def clear_llm_retry_count(payload: dict[str, Any], offset: int, limit: int) -> None:
    retry_counts = payload.get("llm_retry_counts")
    if not isinstance(retry_counts, dict):
        return
    retry_key = f"{offset}:{limit}"
    retry_counts.pop(retry_key, None)
    payload["llm_retry_counts"] = retry_counts


def find_existing_batch_commit(commits: list[dict[str, Any]], offset: int, limit: int) -> dict[str, Any] | None:
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        commit_offset = commit.get("offset")
        commit_limit = commit.get("limit")
        if commit_offset is None or commit_limit is None:
            continue
        if int(commit_offset) == int(offset) and int(commit_limit) == int(limit):
            return commit
    return None


def append_llm_commit(
    payload: dict[str, Any],
    match_run_id: str,
    offset: int,
    limit: int,
    committed_count: int,
    titles: list[str],
    next_offset: int,
    status: str,
) -> None:
    commits = payload.get("llm_commits")
    if not isinstance(commits, list):
        commits = []
    commits.append(
        {
            "offset": offset,
            "limit": limit,
            "status": status,
            "committed_count": committed_count,
            "titles": titles,
            "next_offset": next_offset,
            "created_at": time.time(),
        }
    )
    payload["llm_commits"] = commits
    update_match_run(match_run_id, payload)


def mark_uncertain_batch(match_run_id: str, offset: int, limit: int, batch: list[dict[str, Any]], llm_result: Any) -> None:
    now = time.time()
    with get_engine().begin() as connection:
        for cluster in batch:
            if not isinstance(cluster, dict):
                continue
            for record in cluster.get("records", []):
                if isinstance(record, dict):
                    upsert_uncertain_record_entity(connection, record, cluster, match_run_id)
            connection.execute(
                insert(match_decisions).values(
                    match_run_id=match_run_id,
                    cluster_id=str(cluster.get("cluster_id") or ""),
                    left_id=",".join(str(value) for value in cluster.get("table1_ids", [])),
                    right_id=",".join(str(value) for value in cluster.get("table2_ids", [])),
                    decision="uncertain",
                    confidence=safe_float(cluster.get("max_confidence"), default=0.0),
                    reason="大模型输出不可解析，未合并写入唯一实体表",
                    model_scores=json.dumps(
                        {
                            "offset": offset,
                            "limit": limit,
                            "llm_result_preview": str(llm_result)[:1000],
                        },
                        ensure_ascii=False,
                    ),
                    created_at=now,
                )
            )


def upsert_uncertain_record_entity(connection, record: dict[str, Any], cluster: dict[str, Any], match_run_id: str) -> int:
    title = str(record.get("title") or "未命名未确认实体")
    source_table = str(record.get("source_table") or "unknown")
    source_id = str(record.get("source_id") or "")
    candidate = {
        "id": source_id,
        "source_table": source_table,
        "title": title,
        "manufacturer": record.get("manufacturer"),
        "price": record.get("price"),
        "cluster_id": cluster.get("cluster_id"),
        "match_run_id": match_run_id,
    }
    canonical_payload = {
        "title": title,
        "manufacturer": record.get("manufacturer"),
        "price": normalize_price(record.get("price")),
        "source": "uncertain",
        "confidence": safe_float(cluster.get("max_confidence"), default=0.0),
        "reason": "冲突簇未被大模型可靠解析，来源记录先作为独立 uncertain 实体保留，避免丢失覆盖",
        "candidate": candidate,
    }
    entity_id = upsert_entity_with_connection(
        connection=connection,
        title=title,
        candidate=candidate,
        canonical_payload=canonical_payload,
        source="uncertain",
        confidence=safe_float(cluster.get("max_confidence"), default=0.0),
        reason="冲突簇未被大模型可靠解析，来源记录先作为独立 uncertain 实体保留，避免丢失覆盖",
    )
    upsert_golden_record_with_connection(
        connection=connection,
        entity_id=entity_id,
        title=title,
        canonical_payload=canonical_payload,
        source="uncertain",
        confidence=safe_float(cluster.get("max_confidence"), default=0.0),
    )
    return entity_id


@app.post("/process-all-high-confidence")
def process_all_high_confidence(request: ProcessAllHighConfidenceRequest) -> dict[str, Any]:
    match_run_id = resolve_match_run_id(request.match_run_id, request.ingest_payload)
    payload = load_match_run(match_run_id)
    candidates = payload.get("llm_conflict_clusters") or payload.get("high_confidence_candidates", [])
    if not isinstance(candidates, list):
        candidates = []

    batch_size = max(1, min(int(request.batch_size), 100))
    if not candidates:
        log_action("process_all_high_confidence_skipped", {"match_run_id": match_run_id, "reason": "no high confidence candidates"})
        return {
            "status": "no_high_confidence_required",
            "match_run_id": match_run_id,
            "total_high_confidence": 0,
            "processed_batches": 0,
            "committed_count": 0,
            "failed_batches": [],
        }

    processed_batches = 0
    committed_count = 0
    failed_batches: list[dict[str, Any]] = []
    batch_results: list[dict[str, Any]] = []

    for offset in range(0, len(candidates), batch_size):
        batch = candidates[offset : offset + batch_size]
        batch_payload = {
            "status": "batch_ready",
            "match_run_id": match_run_id,
            "route": "llm_cluster" if payload.get("llm_conflict_clusters") else "llm",
            "payload_type": "entity_conflict_clusters" if payload.get("llm_conflict_clusters") else "candidate_pairs",
            "offset": offset,
            "limit": batch_size,
            "returned_count": len(batch),
            "total_count": len(candidates),
            "next_offset": offset + len(batch),
            "has_more": offset + len(batch) < len(candidates),
            "clusters": batch if payload.get("llm_conflict_clusters") else [],
            "candidates": batch,
        }
        try:
            llm_result = call_dify_batch_review(batch_payload)
            commit_result = commit_llm_batch(
                CommitLlmBatchRequest(
                    match_run_id=match_run_id,
                    offset=offset,
                    limit=batch_size,
                    llm_result=llm_result,
                    source="llm",
                    reason="高置信候选由知识库子工作流和大模型批量选择",
                )
            )
            processed_batches += 1
            committed_count += int(commit_result.get("committed_count", 0))
            batch_results.append(
                {
                    "offset": offset,
                    "returned_count": len(batch),
                    "committed_count": commit_result.get("committed_count", 0),
                    "status": commit_result.get("status"),
                }
            )
        except Exception as exc:
            failed = {"offset": offset, "returned_count": len(batch), "error": str(exc)}
            failed_batches.append(failed)
            log_action("process_high_confidence_batch_failed", {"match_run_id": match_run_id, **failed})

    payload = load_match_run(match_run_id)
    payload["high_confidence_processing"] = {
        "status": "completed_with_errors" if failed_batches else "completed",
        "batch_size": batch_size,
        "processed_batches": processed_batches,
        "committed_count": committed_count,
        "failed_batches": failed_batches,
        "updated_at": time.time(),
    }
    update_match_run(match_run_id, payload)
    result_status = "completed_with_errors" if failed_batches else "completed"
    log_action(
        "process_all_high_confidence",
        {
            "match_run_id": match_run_id,
            "status": result_status,
            "total_high_confidence": len(candidates),
            "processed_batches": processed_batches,
            "committed_count": committed_count,
            "failed_batches": failed_batches,
        },
    )
    return {
        "status": result_status,
        "match_run_id": match_run_id,
        "total_high_confidence": len(candidates),
        "processed_batches": processed_batches,
        "committed_count": committed_count,
        "failed_batches": failed_batches,
        "batch_results": batch_results,
    }


@app.post("/finalize-match-run")
def finalize_match_run(request: FinalizeMatchRunRequest) -> dict[str, Any]:
    match_run_id = resolve_existing_match_run_id(request.match_run_id, request.ingest_payload, allow_latest=True)
    payload = load_match_run(match_run_id)
    fallback_result = {"fallback_entity_count": 0, "fallback_cluster_count": 0}
    if request.fallback_remaining:
        fallback_result = commit_uncovered_conflict_clusters(match_run_id, payload)

    repaired_table1 = 0
    repaired_table2 = 0
    table_a_path = request.table_a_path or payload.get("table_a_path")
    table_b_path = request.table_b_path or payload.get("table_b_path")
    repair_schema_profile = payload.get("schema_profile") if isinstance(payload, dict) else None
    if table_a_path:
        repaired_table1 = repair_missing_source_links_from_csv(table_a_path, "table1", repair_schema_profile)
    if table_b_path:
        repaired_table2 = repair_missing_source_links_from_csv(table_b_path, "table2", repair_schema_profile)

    merge_result = {"status": "skipped", "merge_groups": 0, "merged_entity_count": 0}
    if request.merge_duplicates:
        canonical_merge_result = merge_duplicate_canonical_entities()
        alias_merge_result = merge_duplicate_alias_entities()
        merge_result = {
            "status": "merged",
            "canonical": canonical_merge_result,
            "alias": alias_merge_result,
            "merge_groups": int(canonical_merge_result.get("merge_groups", 0)) + int(alias_merge_result.get("merge_groups", 0)),
            "merged_entity_count": int(canonical_merge_result.get("merged_entity_count", 0)) + int(alias_merge_result.get("merged_entity_count", 0)),
        }
    rebuild_result = rebuild_golden_records_from_entities()

    expected_table1, expected_table2 = expected_source_counts_from_request_or_payload(request, payload)
    payload = load_match_run(match_run_id)
    payload.pop("stopped_reason", None)
    payload["expected_table1_source_link_count"] = expected_table1
    payload["expected_table2_source_link_count"] = expected_table2
    payload["finalization"] = {
        "status": "completed",
        "fallback_result": fallback_result,
        "repaired_table1": repaired_table1,
        "repaired_table2": repaired_table2,
        "merge_result": merge_result,
        "updated_at": time.time(),
    }
    update_match_run(match_run_id, payload)

    quality = entity_quality(expected_table1=expected_table1, expected_table2=expected_table2)
    refresh_master_data_view(payload.get("schema_profile") if isinstance(payload, dict) else None)
    result_status = "completed" if quality["valid"] else "completed_with_quality_warnings"
    log_action(
        "finalize_match_run",
        {
            "match_run_id": match_run_id,
            "status": result_status,
            "fallback_result": fallback_result,
            "repaired_table1": repaired_table1,
            "repaired_table2": repaired_table2,
            "merge_result": merge_result,
            "quality": quality,
        },
    )
    return {
        "status": result_status,
        "match_run_id": match_run_id,
        "master_data_view": master_data_view_metadata(payload.get("schema_profile") if isinstance(payload, dict) else None),
        "fallback_result": fallback_result,
        "repaired_table1": repaired_table1,
        "repaired_table2": repaired_table2,
        "merge_result": merge_result,
        "rebuild_result": rebuild_result,
        "quality": quality,
    }


def commit_uncovered_conflict_clusters(match_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    clusters = payload.get("llm_conflict_clusters")
    if not isinstance(clusters, list):
        return {"fallback_entity_count": 0, "fallback_cluster_count": 0}

    fallback_entity_count = 0
    fallback_cluster_count = 0
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        uncovered_records = uncovered_cluster_records(cluster)
        if not uncovered_records:
            continue
        fallback_cluster_count += 1
        for entity_record in fallback_cluster_commit_records(cluster, uncovered_records):
            commit_golden_record_payload(
                chosen_title=str(entity_record["chosen_title"]),
                chosen_record=entity_record["chosen_record"],
                source="auto",
                confidence=safe_float(entity_record.get("confidence"), default=safe_float(cluster.get("max_confidence"), default=0.8)),
                reason=str(entity_record.get("reason") or "最终收尾：LLM 未覆盖该冲突簇，后端按保守规则补齐来源记录"),
            )
            fallback_entity_count += 1
    return {"fallback_entity_count": fallback_entity_count, "fallback_cluster_count": fallback_cluster_count}


def uncovered_cluster_records(cluster: dict[str, Any]) -> list[dict[str, Any]]:
    records = [record for record in cluster.get("records", []) if isinstance(record, dict)]
    if not records:
        return []
    with get_engine().connect() as connection:
        uncovered: list[dict[str, Any]] = []
        for record in records:
            source_table = str(record.get("source_table") or "")
            source_id = str(record.get("source_id") or "")
            if not source_table or not source_id:
                continue
            existing = connection.execute(
                select(source_record_links.c.id).where(
                    source_record_links.c.source_table == source_table,
                    source_record_links.c.source_id == source_id,
                )
            ).first()
            if not existing:
                uncovered.append(record)
    return uncovered


def fallback_cluster_commit_records(cluster: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    singles: list[dict[str, Any]] = []
    for record in records:
        title_key = normalize_text(record.get("title"))
        if is_safe_duplicate_key(title_key):
            grouped.setdefault(title_key, []).append(record)
        else:
            singles.append(record)

    member_groups = [group for group in grouped.values() if len(group) > 1]
    member_groups.extend([[record] for group in grouped.values() if len(group) == 1 for record in group])
    member_groups.extend([[record] for record in singles])

    results: list[dict[str, Any]] = []
    for members in member_groups:
        entity_result = {
            "canonical_title": choose_canonical_title_from_members(members),
            "members": [str(member.get("node_id")) for member in members],
            "aliases": [str(member.get("title")) for member in members if member.get("title")],
            "confidence": safe_float(cluster.get("max_confidence"), default=0.8) if len(members) > 1 else 1.0,
            "decision": "same_entity",
            "reason": "最终收尾：规范化标题完全一致，后端保守合并" if len(members) > 1 else "最终收尾：LLM 未覆盖，作为独立实体补齐",
        }
        candidate = build_candidate_from_members(members, cluster, entity_result)
        results.append(
            {
                "chosen_title": entity_result["canonical_title"],
                "chosen_record": candidate,
                "confidence": entity_result["confidence"],
                "reason": entity_result["reason"],
            }
        )
    return results


def expected_source_counts_from_request_or_payload(request: FinalizeMatchRunRequest, payload: dict[str, Any]) -> tuple[int | None, int | None]:
    expected_table1 = payload.get("expected_table1_source_link_count")
    expected_table2 = payload.get("expected_table2_source_link_count")
    table_a_path = request.table_a_path or payload.get("table_a_path")
    table_b_path = request.table_b_path or payload.get("table_b_path")
    if expected_table1 is None and table_a_path:
        expected_table1 = len(load_csv(table_a_path))
    if expected_table2 is None and table_b_path:
        expected_table2 = len(load_csv(table_b_path))
    return (
        int(expected_table1) if expected_table1 is not None else None,
        int(expected_table2) if expected_table2 is not None else None,
    )


@app.post("/create-review-task")
def create_review_task(request: CreateReviewTaskRequest) -> dict[str, Any]:
    payload = parse_record_payload(request.review_payload)
    if payload.get("match_run_id"):
        payload = load_match_run(str(payload["match_run_id"]))
    return create_review_task_from_payload(payload)


def create_review_task_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = extract_review_candidates(payload)
    if not candidates:
        log_action("create_review_task_skipped", {"reason": "no human review candidates"})
        return {
            "status": "no_review_required",
            "task_id": None,
            "review_url": None,
            "message": "没有需要人工审核的低置信候选",
        }
    now = time.time()
    with get_engine().begin() as connection:
        result = connection.execute(insert(review_tasks).values(status="pending", payload=json.dumps(payload, ensure_ascii=False), created_at=now, updated_at=now))
        task_id = int(result.inserted_primary_key[0])
        connection.execute(insert(action_log).values(operation="create_review_task", payload=json.dumps({"task_id": task_id}, ensure_ascii=False), created_at=now))
    return {"status": "review_created", "task_id": task_id, "review_url": f"{public_review_base_url()}/review/{task_id}"}


@app.get("/review/{task_id}", response_class=HTMLResponse)
def review_task(task_id: int, page: int = 1, page_size: int = 50) -> HTMLResponse:
    task = get_review_task(task_id)
    candidates = extract_review_candidates(json.loads(task["payload"]))
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    start = (page - 1) * page_size
    end = start + page_size
    page_candidates = candidates[start:end]
    rows = "\n".join(render_candidate_row(start + index, candidate) for index, candidate in enumerate(page_candidates))
    if not rows:
        rows = "<p>No review candidates were found in this task.</p>"
        submit_button = ""
    else:
        submit_button = '<p><button type="submit">提交本页已选择的黄金记录</button></p>'
    total_pages = max(1, (len(candidates) + page_size - 1) // page_size)
    previous_link = f"<a href='/review/{task_id}?page={page - 1}&page_size={page_size}'>上一页</a>" if page > 1 else ""
    next_link = f"<a href='/review/{task_id}?page={page + 1}&page_size={page_size}'>下一页</a>" if end < len(candidates) else ""
    return HTMLResponse(
        f"""
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          <title>MDM Review Task {task_id}</title>
          <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #d0d7de; padding: 8px; vertical-align: top; }}
            th {{ background: #f6f8fa; text-align: left; }}
            button {{ margin: 4px 6px 4px 0; padding: 6px 10px; }}
            .score {{ white-space: nowrap; }}
            label {{ display: block; margin: 4px 0; }}
          </style>
        </head>
        <body>
          <h1>MDM Review Task {task_id}</h1>
          <p>Status: {html.escape(task["status"])}</p>
          <p>候选总数：{len(candidates)}；当前第 {page} / {total_pages} 页；每页 {page_size} 条。</p>
          <p>{previous_link} {next_link}</p>
          <form method="post" action="/review/{task_id}">
            <input type="hidden" name="page" value="{page}">
            <input type="hidden" name="page_size" value="{page_size}">
            <table>
              <thead><tr><th>Confidence</th><th>Table A</th><th>Table B</th><th>人工选择</th></tr></thead>
              <tbody>{rows}</tbody>
            </table>
            {submit_button}
          </form>
        </body>
        </html>
        """
    )


@app.post("/review/{task_id}", response_class=HTMLResponse)
async def submit_review(task_id: int, request: Request) -> HTMLResponse:
    task = get_review_task(task_id)
    candidates = extract_review_candidates(json.loads(task["payload"]))
    form = parse_qs((await request.body()).decode("utf-8"))
    page = int(form.get("page", ["1"])[0])
    page_size = int(form.get("page_size", ["50"])[0])
    selections: list[tuple[int, str]] = []
    if "candidate_index" in form:
        index = int(form.get("candidate_index", ["-1"])[0])
        if index < 0 or index >= len(candidates):
            raise HTTPException(status_code=400, detail="candidate_index is out of range")
        selections.append((index, str(form.get("title_choice", ["table2"])[0])))
    else:
        for index in range(len(candidates)):
            title_choice = str(form.get(f"choice_{index}", ["skip"])[0])
            if title_choice in {"table1", "table2"}:
                selections.append((index, title_choice))

    committed_titles: list[str] = []
    for index, title_choice in selections:
        candidate = candidates[index]
        title_key = "table1.title" if title_choice == "table1" else "table2.title"
        chosen_title = str(candidate.get(title_key) or candidate.get("table2.title") or candidate.get("table1.title"))
        commit_golden_record_payload(
            chosen_title=chosen_title,
            chosen_record=candidate,
            source="human",
            confidence=float(candidate.get("confidence", 0.0)),
            reason=f"manual review task {task_id}",
        )
        committed_titles.append(chosen_title)
    with get_engine().begin() as connection:
        connection.execute(update(review_tasks).where(review_tasks.c.id == task_id).values(status="pending", updated_at=time.time()))
    title_list = "".join(f"<li>{html.escape(title)}</li>" for title in committed_titles)
    if not title_list:
        title_list = "<li>本页未选择任何候选。</li>"
    next_page = page + 1
    return HTMLResponse(
        f"<p>本页已写入 {len(committed_titles)} 条人工审核黄金记录。</p>"
        f"<ul>{title_list}</ul>"
        f"<p><a href='/review/{task_id}?page={page}&page_size={page_size}'>返回本页</a> "
        f"<a href='/review/{task_id}?page={next_page}&page_size={page_size}'>继续下一页</a></p>"
    )


@app.post("/commit-golden-record")
async def commit_golden_record(request: Request) -> dict[str, Any]:
    raw = await request.body()
    parsed = parse_request_payload(raw)
    records = extract_commit_records(parsed)
    if not records:
        log_action("commit_golden_record_skipped", {"reason": "no records in llm result", "payload": parsed})
        return {"status": "no_commit_required", "committed_count": 0, "titles": []}

    titles: list[str] = []
    for record in records:
        chosen_title = extract_chosen_title(record.get("chosen_title") or record.get("title") or "")
        source = str(record.get("source") or parsed.get("source") or "llm")
        reason = str(record.get("reason") or parsed.get("reason") or "")
        chosen_record = record.get("chosen_record") or record.get("record") or record.get("candidate") or record
        record_confidence = chosen_record.get("confidence") if isinstance(chosen_record, dict) else None
        confidence = safe_float(record.get("confidence"), default=safe_float(record_confidence, default=safe_float(parsed.get("confidence"), default=0.9)))
        titles.append(
            commit_golden_record_payload(
                chosen_title=chosen_title,
                chosen_record=chosen_record,
                source=source,
                confidence=confidence,
                reason=reason,
            )
        )
    return {"status": "committed", "committed_count": len(titles), "titles": titles}


@app.get("/admin/entity-quality")
def entity_quality(expected_table1: int | None = None, expected_table2: int | None = None) -> dict[str, Any]:
    if expected_table1 is None or expected_table2 is None:
        latest_payload = load_latest_match_run_payload()
        if latest_payload:
            if expected_table1 is None and latest_payload.get("expected_table1_source_link_count") is not None:
                expected_table1 = int(latest_payload["expected_table1_source_link_count"])
            if expected_table2 is None and latest_payload.get("expected_table2_source_link_count") is not None:
                expected_table2 = int(latest_payload["expected_table2_source_link_count"])
    with get_engine().connect() as connection:
        entity_count = int(connection.execute(select(func.count()).select_from(entities)).scalar_one())
        golden_count = int(connection.execute(select(func.count()).select_from(golden_records)).scalar_one())
        duplicate_golden = int(
            connection.execute(
                text("SELECT COUNT(*) FROM (SELECT entity_id, COUNT(*) c FROM golden_records GROUP BY entity_id HAVING c > 1)")
            ).scalar_one()
        )
        null_golden_entity_id = int(connection.execute(text("SELECT COUNT(*) FROM golden_records WHERE entity_id IS NULL")).scalar_one())
        duplicate_source_links = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM ("
                    "SELECT source_table, source_id, COUNT(*) c "
                    "FROM source_record_links GROUP BY source_table, source_id HAVING c > 1)"
                )
            ).scalar_one()
        )
        uncertain_decisions = int(
            connection.execute(select(func.count()).select_from(match_decisions).where(match_decisions.c.decision == "uncertain")).scalar_one()
        )
        duplicate_normalized_titles = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM ("
                    "SELECT normalized_title, COUNT(DISTINCT entity_id) c "
                    "FROM entity_aliases WHERE normalized_title != '' "
                    "GROUP BY normalized_title HAVING c > 1)"
                )
            ).scalar_one()
        )
        duplicate_canonical_titles = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM ("
                    "SELECT lower(trim(canonical_title)) normalized, COUNT(*) c "
                    "FROM entities WHERE trim(canonical_title) != '' "
                    "GROUP BY lower(trim(canonical_title)) HAVING c > 1)"
                )
            ).scalar_one()
        )
        table1_links = int(
            connection.execute(select(func.count()).select_from(source_record_links).where(source_record_links.c.source_table == "table1")).scalar_one()
        )
        table2_links = int(
            connection.execute(select(func.count()).select_from(source_record_links).where(source_record_links.c.source_table == "table2")).scalar_one()
        )
        incoming_links = int(
            connection.execute(select(func.count()).select_from(source_record_links).where(source_record_links.c.source_table == "incoming")).scalar_one()
        )
    table1_coverage_ok = expected_table1 is None or table1_links == int(expected_table1)
    table2_coverage_ok = expected_table2 is None or table2_links == int(expected_table2)
    valid = (
        golden_count == entity_count
        and duplicate_golden == 0
        and null_golden_entity_id == 0
        and duplicate_source_links == 0
        and table1_coverage_ok
        and table2_coverage_ok
    )
    return {
        "valid": valid,
        "entity_count": entity_count,
        "golden_records_count": golden_count,
        "duplicate_golden_entity_id_count": duplicate_golden,
        "null_golden_entity_id_count": null_golden_entity_id,
        "duplicate_source_link_count": duplicate_source_links,
        "duplicate_normalized_alias_count": duplicate_normalized_titles,
        "duplicate_canonical_title_count": duplicate_canonical_titles,
        "uncertain_decision_count": uncertain_decisions,
        "table1_source_link_count": table1_links,
        "table2_source_link_count": table2_links,
        "incoming_source_link_count": incoming_links,
        "expected_table1_source_link_count": expected_table1,
        "expected_table2_source_link_count": expected_table2,
        "expected_incoming_source_link_count": incoming_links,
        "table1_coverage_ok": table1_coverage_ok,
        "table2_coverage_ok": table2_coverage_ok,
        "note": "valid=true 表示主数据表满足一实体一行、来源唯一链接和期望源记录覆盖；duplicate_* 用于提示仍需审计的同名实体风险。",
    }


def parse_json_text(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def row_to_public_golden_record(row: dict[str, Any], aliases: list[str] | None = None, source_links: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload = parse_json_text(row.get("source_payload"), {})
    return {
        "id": row.get("id"),
        "entity_id": row.get("entity_id"),
        "title": row.get("title"),
        "manufacturer": row.get("manufacturer"),
        "price": row.get("price"),
        "selected_source_table": row.get("selected_source_table"),
        "source": row.get("source"),
        "confidence": row.get("confidence"),
        "aliases": aliases or [],
        "source_links": source_links or [],
        "source_payload": payload,
        "created_at": row.get("created_at"),
    }


def load_public_golden_records(limit: int | None = 100, offset: int = 0, order: str = "asc") -> dict[str, Any]:
    effective_limit = None if limit is None else max(1, min(int(limit), 10000))
    offset = max(0, int(offset))
    with get_engine().connect() as connection:
        total = int(connection.execute(select(func.count()).select_from(golden_records)).scalar_one())
        order_by = golden_records.c.id.desc() if order == "desc" else golden_records.c.id.asc()
        query = select(golden_records).order_by(order_by).offset(offset)
        if effective_limit is not None:
            query = query.limit(effective_limit)
        rows = connection.execute(query).mappings().all()
        entity_ids = [int(row["entity_id"]) for row in rows if row.get("entity_id") is not None]
        aliases_by_entity: dict[int, list[str]] = defaultdict(list)
        links_by_entity: dict[int, list[dict[str, Any]]] = defaultdict(list)
        if entity_ids:
            alias_rows = connection.execute(select(entity_aliases).where(entity_aliases.c.entity_id.in_(entity_ids))).mappings().all()
            for alias_row in alias_rows:
                aliases_by_entity[int(alias_row["entity_id"])].append(str(alias_row["alias_title"]))
            link_rows = connection.execute(select(source_record_links).where(source_record_links.c.entity_id.in_(entity_ids))).mappings().all()
            for link_row in link_rows:
                links_by_entity[int(link_row["entity_id"])].append(
                    {
                        "source_table": link_row["source_table"],
                        "source_id": link_row["source_id"],
                        "raw_payload": parse_json_text(link_row["raw_payload"], {}),
                    }
                )
    items = [
        row_to_public_golden_record(
            dict(row),
            aliases_by_entity.get(int(row["entity_id"])) if row.get("entity_id") is not None else [],
            links_by_entity.get(int(row["entity_id"])) if row.get("entity_id") is not None else [],
        )
        for row in rows
    ]
    return {"total": total, "limit": effective_limit, "offset": offset, "order": order, "items": items}


def load_master_data_records(limit: int | None = 100, offset: int = 0, order: str = "asc") -> dict[str, Any]:
    effective_limit = None if limit is None else max(1, min(int(limit), 10000))
    offset = max(0, int(offset))
    refresh_master_data_view()
    order_direction = "DESC" if str(order).lower() == "desc" else "ASC"
    with get_engine().connect() as connection:
        columns = [str(row[1]) for row in connection.execute(text("PRAGMA table_info(master_data)")).fetchall()]
        total = int(connection.execute(text("SELECT COUNT(*) FROM master_data")).scalar_one())
        query = f"SELECT * FROM master_data ORDER BY id {order_direction} OFFSET :offset"
        params: dict[str, Any] = {"offset": offset}
        if effective_limit is not None:
            query += " LIMIT :limit"
            params["limit"] = effective_limit
        rows = [dict(row) for row in connection.execute(text(query), params).mappings().all()]
    return {
        "table": "master_data",
        "view": "master_data_current",
        "columns": columns,
        "total": total,
        "limit": effective_limit,
        "offset": offset,
        "order": order,
        "items": rows,
    }


@app.get("/golden-records")
def golden_records_endpoint(limit: int = 100, offset: int = 0, order: str = "asc") -> dict[str, Any]:
    return load_public_golden_records(limit=limit, offset=offset, order=order)


@app.get("/master-data")
def master_data_endpoint(limit: int = 100, offset: int = 0, order: str = "asc") -> dict[str, Any]:
    return load_master_data_records(limit=limit, offset=offset, order=order)


@app.get("/golden-records.csv")
def golden_records_csv_endpoint() -> Response:
    data = load_public_golden_records(limit=None, offset=0, order="asc")
    frame = pd.DataFrame(
        [
            {
                "id": item["id"],
                "entity_id": item["entity_id"],
                "title": item["title"],
                "manufacturer": item["manufacturer"],
                "price": item["price"],
                "selected_source_table": item["selected_source_table"],
                "source": item["source"],
                "confidence": item["confidence"],
                "aliases": " | ".join(item["aliases"]),
            }
            for item in data["items"]
        ]
    )
    csv_text = frame.to_csv(index=False)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="golden_records.csv"'},
    )


@app.get("/master-data.csv")
def master_data_csv_endpoint() -> Response:
    data = load_master_data_records(limit=None, offset=0, order="asc")
    frame = pd.DataFrame(data["items"], columns=data["columns"])
    csv_text = frame.to_csv(index=False)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="master_data.csv"'},
    )


def searchable_record_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("title"),
        item.get("manufacturer"),
        item.get("selected_source_table"),
        item.get("source"),
        " ".join(item.get("aliases") or []),
    ]
    for link in item.get("source_links") or []:
        raw = link.get("raw_payload") if isinstance(link, dict) else {}
        if isinstance(raw, dict):
            parts.extend(str(raw.get(key) or "") for key in ("id", "title", "manufacturer", "name", "product_name", "brand", "sku"))
    return " ".join(str(part or "") for part in parts)


def searchable_record_fields(item: dict[str, Any]) -> list[str]:
    fields = [
        item.get("title"),
        item.get("manufacturer"),
        item.get("selected_source_table"),
        item.get("source"),
        *(item.get("aliases") or []),
    ]
    for link in item.get("source_links") or []:
        raw = link.get("raw_payload") if isinstance(link, dict) else {}
        if isinstance(raw, dict):
            fields.extend(raw.get(key) for key in ("id", "title", "manufacturer", "name", "product_name", "brand", "sku"))
    return [str(field or "").strip() for field in fields if str(field or "").strip()]


def compact_search_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).lower()


def search_initials(value: str) -> str:
    tokens = [token for token in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", value.lower()) if token]
    return "".join(token[0] for token in tokens)


def search_rule_score(query: str, fields: list[str]) -> tuple[float, str]:
    compact_query = compact_search_text(query)
    if not compact_query:
        return 0.0, ""
    best_score = 0.0
    best_rule = ""
    for field in fields:
        compact_field = compact_search_text(field)
        initials = search_initials(field)
        if not compact_field:
            continue
        score = 0.0
        rule = ""
        if compact_query == compact_field:
            score, rule = 1.0, "exact"
        elif compact_field.startswith(compact_query):
            score, rule = 0.94, "prefix"
        elif compact_query in compact_field:
            score, rule = 0.86, "contains"
        elif initials and initials.startswith(compact_query):
            score, rule = 0.78, "initial_prefix"
        elif initials and compact_query in initials:
            score, rule = 0.70, "initial_contains"
        elif all(char in compact_field for char in compact_query):
            score, rule = 0.48, "character_contains"
        if score > best_score:
            best_score, best_rule = score, rule
    return best_score, best_rule


@app.get("/search")
def search_golden_records(q: str, limit: int = 20) -> dict[str, Any]:
    query = str(q or "").strip()
    if not query:
        return {"query": q, "items": []}
    data = load_public_golden_records(limit=1000, offset=0)
    scored: list[dict[str, Any]] = []
    for item in data["items"]:
        score, rule = search_rule_score(query, searchable_record_fields(item))
        if score > 0:
            result = dict(item)
            result["match_score"] = round(float(score), 4)
            result["match_rule"] = rule
            scored.append(result)
    scored.sort(key=lambda item: (item["match_score"], safe_float(item.get("confidence"), default=0.0)), reverse=True)
    return {"query": q, "items": scored[: max(1, min(int(limit), 100))]}


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_latest_comparison_report() -> tuple[dict[str, Any], str | None]:
    comparison_dir = Path("models/main/comparison")
    if not comparison_dir.exists():
        return {}, None
    paths = sorted(comparison_dir.glob("comparison_report*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        payload = load_json_file(path)
        if payload:
            return payload, str(path)
    return {}, None


def load_model_metrics_file() -> dict[str, Any]:
    metrics_path = Path("models/main/metrics.json")
    return load_json_file(metrics_path) if metrics_path.exists() else {}


def build_evaluation_metrics(latest_run_updated_at: float | None = None) -> dict[str, Any]:
    comparison_report, comparison_path = load_latest_comparison_report()
    model_metrics = load_model_metrics_file()
    pair_linkage = comparison_report.get("cross_source_pair_linkage") if isinstance(comparison_report.get("cross_source_pair_linkage"), dict) else {}
    tp = int(pair_linkage.get("tp") or 0)
    fp = int(pair_linkage.get("fp") or 0)
    fn = int(pair_linkage.get("fn") or 0)
    precision = safe_float(pair_linkage.get("precision"), default=(tp / (tp + fp) if tp + fp else 0.0))
    recall = safe_float(pair_linkage.get("recall"), default=(tp / (tp + fn) if tp + fn else 0.0))
    f1 = safe_float(pair_linkage.get("f1"), default=(2 * precision * recall / (precision + recall) if precision + recall else 0.0))
    roc_auc = safe_float(model_metrics.get("roc_auc") or model_metrics.get("auc"), default=0.0)
    cluster_exact = comparison_report.get("cluster_exact_match") if isinstance(comparison_report.get("cluster_exact_match"), dict) else {}
    comparison_updated_at = Path(comparison_path).stat().st_mtime if comparison_path and Path(comparison_path).exists() else None
    model_metrics_path = Path("models/main/metrics.json")
    model_metrics_updated_at = model_metrics_path.stat().st_mtime if model_metrics_path.exists() else None
    stale = bool(latest_run_updated_at and comparison_updated_at and comparison_updated_at < latest_run_updated_at)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "accuracy": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "roc_auc": round(roc_auc, 6),
        "average_precision": safe_float(model_metrics.get("average_precision"), default=0.0),
        "cluster_exact_precision": safe_float(cluster_exact.get("precision"), default=0.0),
        "cluster_exact_recall": safe_float(cluster_exact.get("recall"), default=0.0),
        "source": comparison_path or "models/main/metrics.json",
        "kind": "offline_reference_evaluation",
        "comparison_updated_at": comparison_updated_at,
        "model_metrics_updated_at": model_metrics_updated_at,
        "latest_run_updated_at": latest_run_updated_at,
        "stale": stale,
        "needs_reference_report": not bool(comparison_report),
        "note": "tp/fp/fn 来自 comparison_report 的 cross_source_pair_linkage；准确率按 Precision 计算，即 tp/(tp+fp)。ROC-AUC 来自 artifacts/metrics.json。",
    }


@app.get("/dashboard-metrics")
def dashboard_metrics() -> dict[str, Any]:
    quality = entity_quality()
    latest_payload = load_latest_match_run_payload() or {}
    with get_engine().connect() as connection:
        selected_source_rows = connection.execute(
            select(golden_records.c.selected_source_table, func.count()).group_by(golden_records.c.selected_source_table)
        ).all()
        decision_rows = connection.execute(select(match_decisions.c.decision, func.count()).group_by(match_decisions.c.decision)).all()
    routing_summary = latest_payload.get("routing_summary") if isinstance(latest_payload.get("routing_summary"), dict) else {}
    table_a_input = int(quality.get("expected_table1_source_link_count") or quality.get("table1_source_link_count") or 0)
    table_b_input = int(quality.get("expected_table2_source_link_count") or quality.get("table2_source_link_count") or 0)
    incoming_input = int(quality.get("expected_incoming_source_link_count") or quality.get("incoming_source_link_count") or 0)
    latest_run_updated_at = safe_float(latest_payload.get("_updated_at"), default=0.0) or None
    evaluation = build_evaluation_metrics(latest_run_updated_at=latest_run_updated_at)
    schema_profile = latest_payload.get("schema_profile") if isinstance(latest_payload.get("schema_profile"), dict) else {}
    return {
        "quality": quality,
        "input_flow": {
            "table_a_input": table_a_input,
            "table_b_input": table_b_input,
            "incoming_input": incoming_input,
            "total_input": table_a_input + table_b_input + incoming_input,
            "table_a_covered": int(quality.get("table1_source_link_count") or 0),
            "table_b_covered": int(quality.get("table2_source_link_count") or 0),
            "incoming_covered": int(quality.get("incoming_source_link_count") or 0),
            "master_output": int(quality.get("golden_records_count") or 0),
            "entity_output": int(quality.get("entity_count") or 0),
        },
        "evaluation": evaluation,
        "latest_run": {
            "match_run_id": latest_payload.get("match_run_id"),
            "updated_at": latest_run_updated_at,
            "is_initial_load": latest_payload.get("is_initial_load"),
            "threshold": latest_payload.get("threshold"),
            "schema_related": schema_profile.get("related"),
            "schema_entity_type": schema_profile.get("entity_type"),
            "schema_reason": schema_profile.get("reason"),
            "all_candidate_pairs_count": latest_payload.get("all_candidate_pairs_count"),
            "auto_merged_cluster_count": len(latest_payload.get("auto_merged_clusters") or []),
            "llm_conflict_cluster_count": len(latest_payload.get("llm_conflict_clusters") or []),
            "ignored_low_confidence_cluster_count": len(latest_payload.get("ignored_low_confidence_clusters") or []),
            "llm_committed_batches": len(latest_payload.get("llm_commits") or []),
            "routing_summary": routing_summary,
        },
        "charts": {
            "selected_source_tables": [{"label": str(label or "unknown"), "value": int(value)} for label, value in selected_source_rows],
            "match_decisions": [{"label": str(label or "unknown"), "value": int(value)} for label, value in decision_rows],
            "input_sources": [
                {"label": "Table A", "value": table_a_input},
                {"label": "Table B", "value": table_b_input},
                {"label": "Table Incoming", "value": incoming_input},
            ],
            "pair_evaluation": [
                {"label": "TP 合并正确", "value": evaluation["tp"]},
                {"label": "FP 误合并", "value": evaluation["fp"]},
                {"label": "FN 漏合并", "value": evaluation["fn"]},
            ],
        },
    }


def load_latest_match_run_payload() -> dict[str, Any] | None:
    if not MATCH_RUN_DIR.exists():
        return None
    paths = sorted(MATCH_RUN_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            payload.setdefault("match_run_id", path.stem)
            payload["_path"] = str(path)
            payload["_updated_at"] = path.stat().st_mtime
            return payload
    return None


@app.post("/admin/rebuild-golden-records")
def rebuild_golden_records_endpoint() -> dict[str, Any]:
    return rebuild_golden_records_from_entities()


@app.post("/admin/repair-missing-source-links")
def repair_missing_source_links_endpoint(request: RepairMissingSourceLinksRequest) -> dict[str, Any]:
    repaired_table1 = repair_missing_source_links_from_csv(request.table_a_path, "table1") if request.table_a_path else 0
    repaired_table2 = repair_missing_source_links_from_csv(request.table_b_path, "table2") if request.table_b_path else 0
    rebuild_golden_records_from_entities()
    log_action(
        "repair_missing_source_links",
        {
            "table1_repaired": repaired_table1,
            "table2_repaired": repaired_table2,
                    "reason": "补齐缺失来源链接，作为 backend_active 独立实体保留",
        },
    )
    return {
        "status": "repaired",
        "table1_repaired": repaired_table1,
        "table2_repaired": repaired_table2,
        "message": "已将缺失来源记录补为 backend_active 独立实体，并重建 golden_records 展示表。",
    }


@app.post("/admin/merge-duplicate-alias-entities")
def merge_duplicate_alias_entities_endpoint() -> dict[str, Any]:
    canonical_result = merge_duplicate_canonical_entities()
    alias_result = merge_duplicate_alias_entities()
    rebuild_golden_records_from_entities()
    return {
        "status": "merged",
        "canonical": canonical_result,
        "alias": alias_result,
        "merge_groups": int(canonical_result.get("merge_groups", 0)) + int(alias_result.get("merge_groups", 0)),
        "merged_entity_count": int(canonical_result.get("merged_entity_count", 0)) + int(alias_result.get("merged_entity_count", 0)),
    }


def merge_duplicate_canonical_entities() -> dict[str, Any]:
    merged_entities: set[int] = set()
    merge_groups = 0
    with get_engine().begin() as connection:
        duplicate_titles = connection.execute(
            text(
                "SELECT lower(trim(canonical_title)) normalized_title "
                "FROM entities "
                "WHERE trim(canonical_title) != '' "
                "GROUP BY lower(trim(canonical_title)) "
                "HAVING COUNT(*) > 1"
            )
        ).mappings().all()
        for title_row in duplicate_titles:
            normalized_title = normalize_text(title_row["normalized_title"])
            if not is_safe_duplicate_key(normalized_title):
                continue
            entity_ids = [
                int(row["id"])
                for row in connection.execute(
                    select(entities.c.id).where(func.lower(func.trim(entities.c.canonical_title)) == str(title_row["normalized_title"]))
                ).mappings().all()
            ]
            if len(entity_ids) <= 1:
                continue
            target_entity_id = min(entity_ids)
            source_entity_ids = [entity_id for entity_id in entity_ids if entity_id != target_entity_id]
            merge_groups += 1
            merge_entities_into_target(connection, target_entity_id, source_entity_ids, f"exact canonical title: {normalized_title}")
            merged_entities.update(source_entity_ids)
    return {"status": "merged", "merge_groups": merge_groups, "merged_entity_count": len(merged_entities)}


def merge_duplicate_alias_entities() -> dict[str, Any]:
    merged_entities: set[int] = set()
    merge_groups = 0
    with get_engine().begin() as connection:
        duplicate_aliases = connection.execute(
            text(
                "SELECT normalized_title "
                "FROM entity_aliases "
                "WHERE normalized_title != '' "
                "GROUP BY normalized_title "
                "HAVING COUNT(DISTINCT entity_id) > 1"
            )
        ).mappings().all()
        for alias_row in duplicate_aliases:
            normalized_title = str(alias_row["normalized_title"])
            if not is_safe_duplicate_key(normalized_title):
                continue
            entity_ids = [
                int(row["entity_id"])
                for row in connection.execute(
                    select(entity_aliases.c.entity_id)
                    .where(entity_aliases.c.normalized_title == normalized_title)
                    .distinct()
                ).mappings().all()
            ]
            if len(entity_ids) <= 1:
                continue
            entity_rows = connection.execute(select(entities).where(entities.c.id.in_(entity_ids))).mappings().all()
            if not is_safe_duplicate_entity_group([dict(row) for row in entity_rows]):
                continue
            target_entity_id = min(entity_ids)
            source_entity_ids = [entity_id for entity_id in entity_ids if entity_id != target_entity_id]
            if not source_entity_ids:
                continue
            merge_groups += 1
            merge_entities_into_target(connection, target_entity_id, source_entity_ids, f"exact normalized alias: {normalized_title}")
            merged_entities.update(source_entity_ids)
    return {"status": "merged", "merge_groups": merge_groups, "merged_entity_count": len(merged_entities)}


def is_safe_duplicate_key(normalized_title: str) -> bool:
    normalized_title = normalize_text(normalized_title)
    tokens = normalized_title.split()
    generic_titles = {
        "software",
        "quickbooks r",
        "deposit slips",
    }
    if normalized_title in generic_titles:
        return False
    return len(normalized_title) >= 6 and len(tokens) >= 1


def is_safe_duplicate_entity_group(rows: list[dict[str, Any]]) -> bool:
    manufacturers = {normalize_manufacturer(row.get("manufacturer")) for row in rows if normalize_manufacturer(row.get("manufacturer"))}
    if len(manufacturers) > 1:
        return False
    prices = [normalize_price(row.get("price")) for row in rows]
    prices = [price for price in prices if price is not None]
    if len(prices) >= 2:
        low = min(prices)
        high = max(prices)
        if high > 0 and (high - low) / high > 0.5:
            return False
    return True


def merge_entities_into_target(connection, target_entity_id: int, source_entity_ids: list[int], reason: str) -> None:
    now = time.time()
    rows = connection.execute(select(entities).where(entities.c.id.in_([target_entity_id, *source_entity_ids]))).mappings().all()
    canonical_title = choose_canonical_title_from_members(
        [{"title": row["canonical_title"]} for row in rows if row.get("canonical_title")]
    )
    manufacturer = next((row["manufacturer"] for row in rows if row.get("manufacturer")), None)
    price = next((row["price"] for row in rows if row.get("price") is not None), None)
    confidence = max((safe_float(row["confidence"]) for row in rows), default=1.0)

    for source_entity_id in source_entity_ids:
        move_source_links(connection, target_entity_id, source_entity_id)
        move_aliases(connection, target_entity_id, source_entity_id)
        connection.execute(entities.delete().where(entities.c.id == source_entity_id))
        connection.execute(golden_records.delete().where(golden_records.c.entity_id == source_entity_id))
        connection.execute(
            insert(match_decisions).values(
                match_run_id="admin",
                cluster_id="duplicate_alias_merge",
                left_id=str(target_entity_id),
                right_id=str(source_entity_id),
                decision="same_entity",
                confidence=1.0,
                reason=reason,
                model_scores=json.dumps({"merge_reason": reason}, ensure_ascii=False),
                created_at=now,
            )
        )

    connection.execute(
        update(entities)
        .where(entities.c.id == target_entity_id)
        .values(
            canonical_title=canonical_title,
            manufacturer=manufacturer,
            price=price,
            confidence=confidence,
            updated_at=now,
        )
    )
    connection.execute(
        insert(action_log).values(
            operation="merge_duplicate_alias_entities",
            payload=json.dumps({"target_entity_id": target_entity_id, "source_entity_ids": source_entity_ids, "reason": reason}, ensure_ascii=False),
            created_at=now,
        )
    )


def move_source_links(connection, target_entity_id: int, source_entity_id: int) -> None:
    rows = connection.execute(select(source_record_links).where(source_record_links.c.entity_id == source_entity_id)).mappings().all()
    for row in rows:
        existing = connection.execute(
            select(source_record_links.c.id).where(
                source_record_links.c.entity_id == target_entity_id,
                source_record_links.c.source_table == row["source_table"],
                source_record_links.c.source_id == row["source_id"],
            )
        ).mappings().first()
        if existing:
            connection.execute(source_record_links.delete().where(source_record_links.c.id == row["id"]))
        else:
            connection.execute(update(source_record_links).where(source_record_links.c.id == row["id"]).values(entity_id=target_entity_id))


def move_aliases(connection, target_entity_id: int, source_entity_id: int) -> None:
    rows = connection.execute(select(entity_aliases).where(entity_aliases.c.entity_id == source_entity_id)).mappings().all()
    for row in rows:
        existing = connection.execute(
            select(entity_aliases.c.id).where(
                entity_aliases.c.entity_id == target_entity_id,
                entity_aliases.c.normalized_title == row["normalized_title"],
                entity_aliases.c.source_table == row["source_table"],
                entity_aliases.c.source_id == row["source_id"],
            )
        ).mappings().first()
        if existing:
            connection.execute(entity_aliases.delete().where(entity_aliases.c.id == row["id"]))
        else:
            connection.execute(update(entity_aliases).where(entity_aliases.c.id == row["id"]).values(entity_id=target_entity_id))


def rebuild_golden_records_from_entities() -> dict[str, Any]:
    now = time.time()
    with get_engine().begin() as connection:
        entity_rows = connection.execute(select(entities)).mappings().all()
        connection.execute(golden_records.delete())
        for row in entity_rows:
            selected_source_table = row.get("selected_source_table") or infer_entity_selected_source_table(
                connection, int(row["id"]), str(row["canonical_title"] or "")
            )
            payload = {
                "title": row["canonical_title"],
                "manufacturer": row["manufacturer"],
                "price": row["price"],
                "source": "entity_snapshot",
                "status": row["status"],
                "confidence": row["confidence"],
                "selected_source_table": selected_source_table,
                "reason": "由 entities 重建 golden_records，一实体一行",
                "entity_id": row["id"],
            }
            connection.execute(
                insert(golden_records).values(
                    entity_id=row["id"],
                    title=row["canonical_title"],
                    manufacturer=row["manufacturer"],
                    price=row["price"],
                    selected_source_table=selected_source_table,
                    source_payload=json.dumps(payload, ensure_ascii=False),
                    source="entity_snapshot",
                    confidence=row["confidence"],
                    created_at=now,
                )
            )
        connection.execute(
            insert(action_log).values(
                operation="rebuild_golden_records",
                payload=json.dumps({"rebuilt_count": len(entity_rows)}, ensure_ascii=False),
                created_at=now,
            )
        )
    return {"status": "rebuilt", "golden_records_count": len(entity_rows)}


def commit_golden_record_payload(
    chosen_title: str,
    chosen_record: dict[str, Any] | str,
    source: str,
    confidence: float,
    reason: str = "",
) -> str:
    envelope = parse_record_payload(chosen_record)
    payload = envelope.get("chosen_record") if isinstance(envelope.get("chosen_record"), dict) else envelope
    title = chosen_title or envelope.get("chosen_title") or payload.get("title") or payload.get("table1.title") or payload.get("table2.title")
    if title is None or str(title).strip() == "":
        title = payload.get("value") or "未命名黄金记录"
    title = str(title)
    canonical_payload = build_golden_source_payload(
        title=title,
        candidate=payload,
        source=source,
        confidence=confidence,
        reason=reason,
    )
    with get_engine().begin() as connection:
        entity_id = upsert_entity_with_connection(
            connection=connection,
            title=title,
            candidate=payload,
            canonical_payload=canonical_payload,
            source=source,
            confidence=confidence,
            reason=reason,
        )
        upsert_golden_record_with_connection(
            connection=connection,
            entity_id=entity_id,
            title=title,
            canonical_payload=canonical_payload,
            source=source,
            confidence=confidence,
        )
        connection.execute(
            insert(action_log).values(
                operation="commit_golden_record",
                payload=json.dumps(
                    {
                        "chosen_title": chosen_title,
                        "chosen_record": payload,
                        "source": source,
                        "confidence": confidence,
                        "reason": reason,
                        "entity_id": entity_id,
                    },
                    ensure_ascii=False,
                ),
                created_at=time.time(),
            )
        )
    return title


def upsert_golden_record_with_connection(
    connection,
    entity_id: int,
    title: str,
    canonical_payload: dict[str, Any],
    source: str,
    confidence: float,
) -> None:
    now = time.time()
    canonical_payload = ensure_lineage_payload(title, {}, canonical_payload, source)
    values = {
        "entity_id": entity_id,
        "title": title,
        "manufacturer": canonical_payload.get("manufacturer"),
        "price": canonical_payload.get("price"),
        "selected_source_table": canonical_payload.get("selected_source_table"),
        "source_payload": json.dumps(canonical_payload, ensure_ascii=False),
        "source": source,
        "confidence": confidence,
        "created_at": now,
    }
    existing = connection.execute(select(golden_records.c.id).where(golden_records.c.entity_id == entity_id)).mappings().first()
    if existing:
        connection.execute(update(golden_records).where(golden_records.c.id == int(existing["id"])).values(**values))
    else:
        connection.execute(insert(golden_records).values(**values))


def upsert_entity_with_connection(
    connection,
    title: str,
    candidate: dict[str, Any],
    canonical_payload: dict[str, Any],
    source: str,
    confidence: float,
    reason: str,
) -> int:
    now = time.time()
    canonical_payload = ensure_lineage_payload(title, candidate, canonical_payload, source)
    source_refs = extract_source_refs(candidate)
    existing_entity_ids: list[int] = extract_existing_entity_ids(candidate)
    for source_table, source_id, _raw in source_refs:
        existing = connection.execute(
            select(source_record_links.c.entity_id).where(
                source_record_links.c.source_table == source_table,
                source_record_links.c.source_id == source_id,
            )
        ).mappings().first()
        if existing:
            existing_entity_ids.append(int(existing["entity_id"]))

    if existing_entity_ids:
        entity_id = existing_entity_ids[0]
        connection.execute(
            update(entities)
            .where(entities.c.id == entity_id)
            .values(
                canonical_title=title,
                manufacturer=canonical_payload.get("manufacturer"),
                price=canonical_payload.get("price"),
                selected_source_table=canonical_payload.get("selected_source_table"),
                status=status_for_source(source),
                confidence=max(confidence, 0.0),
                updated_at=now,
            )
        )
        if len(set(existing_entity_ids)) > 1:
            connection.execute(
                insert(action_log).values(
                    operation="entity_link_conflict",
                    payload=json.dumps({"entity_ids": existing_entity_ids, "title": title, "reason": reason}, ensure_ascii=False),
                    created_at=now,
                )
            )
    else:
        result = connection.execute(
            insert(entities).values(
                canonical_title=title,
                manufacturer=canonical_payload.get("manufacturer"),
                price=canonical_payload.get("price"),
                status=status_for_source(source),
                selected_source_table=canonical_payload.get("selected_source_table"),
                confidence=confidence,
                created_at=now,
                updated_at=now,
            )
        )
        entity_id = int(result.inserted_primary_key[0])

    for source_table, source_id, raw_payload in source_refs:
        add_source_link_if_missing(connection, entity_id, source_table, source_id, raw_payload, now)
    for source_table, source_id, alias_title in extract_alias_refs(candidate, title):
        add_alias_if_missing(connection, entity_id, source_table, source_id, alias_title, now)

    connection.execute(
        insert(match_decisions).values(
            match_run_id=str(candidate.get("match_run_id") or ""),
            cluster_id=str(candidate.get("cluster_id") or ""),
            left_id=str(candidate.get("table1.id") or ""),
            right_id=str(candidate.get("table2.id") or ""),
            decision="same_entity" if source in {"llm", "human", "auto"} else "uncertain",
            confidence=confidence,
            reason=reason,
            model_scores=json.dumps(canonical_payload.get("match_scores", {}), ensure_ascii=False),
            created_at=now,
        )
    )
    return entity_id


def extract_source_refs(candidate: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    refs: list[tuple[str, str, dict[str, Any]]] = []
    for member in candidate.get("member_records", []):
        if not isinstance(member, dict):
            continue
        source_table = str(member.get("source_table") or "")
        source_id = str(member.get("source_id") or "")
        if source_table and source_id:
            refs.append((source_table, source_id, member))
    for side in ("table1", "table2"):
        source_id = candidate.get(f"{side}.id")
        if side == "table1" and str(source_id).startswith("entity:"):
            continue
        if source_id not in (None, ""):
            ref = (side, str(source_id), {key: value for key, value in candidate.items() if key.startswith(f"{side}.")})
            if not any(existing[0] == ref[0] and existing[1] == ref[1] for existing in refs):
                refs.append(ref)
    if not refs and candidate.get("id") not in (None, ""):
        refs.append((str(candidate.get("source_table") or "incoming"), str(candidate["id"]), candidate))
    return refs


def extract_existing_entity_ids(candidate: dict[str, Any]) -> list[int]:
    entity_ids: list[int] = []
    table1_id = str(candidate.get("table1.id") or "")
    if table1_id.startswith("entity:") and table1_id.split(":", 1)[1].isdigit():
        entity_ids.append(int(table1_id.split(":", 1)[1]))
    entity_id = candidate.get("entity_id")
    if entity_id is not None and str(entity_id).isdigit():
        entity_ids.append(int(entity_id))
    return entity_ids


def extract_alias_refs(candidate: dict[str, Any], canonical_title: str) -> list[tuple[str, str, str]]:
    aliases: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for member in candidate.get("member_records", []):
        if not isinstance(member, dict):
            continue
        title_value = member.get("title")
        source_table = str(member.get("source_table") or "")
        source_id = str(member.get("source_id") or "")
        if title_value in (None, ""):
            continue
        key = (source_table, source_id, normalize_text(title_value))
        if key in seen:
            continue
        seen.add(key)
        aliases.append((source_table, source_id, str(title_value)))
    for side in ("table1", "table2"):
        title_value = candidate.get(f"{side}.title")
        source_id = str(candidate.get(f"{side}.id") or "")
        alias_source_table = side
        if side == "table1" and source_id.startswith("entity:"):
            alias_source_table = "entity"
            source_id = source_id.split(":", 1)[1]
        if title_value in (None, ""):
            continue
        key = (alias_source_table, source_id, normalize_text(title_value))
        if key in seen:
            continue
        seen.add(key)
        aliases.append((alias_source_table, source_id, str(title_value)))
    if not aliases and canonical_title:
        aliases.append(("canonical", "", canonical_title))
    return aliases


def add_source_link_if_missing(connection, entity_id: int, source_table: str, source_id: str, raw_payload: dict[str, Any], now: float) -> None:
    existing = connection.execute(
        select(source_record_links.c.id).where(
            source_record_links.c.source_table == source_table,
            source_record_links.c.source_id == source_id,
        )
    ).first()
    if existing:
        return
    connection.execute(
        insert(source_record_links).values(
            entity_id=entity_id,
            source_table=source_table,
            source_id=source_id,
            raw_payload=json.dumps(raw_payload, ensure_ascii=False),
            created_at=now,
        )
    )


def add_alias_if_missing(connection, entity_id: int, source_table: str, source_id: str, alias_title: str, now: float) -> None:
    normalized_title = normalize_text(alias_title)
    existing = connection.execute(
        select(entity_aliases.c.id).where(
            entity_aliases.c.entity_id == entity_id,
            entity_aliases.c.normalized_title == normalized_title,
            entity_aliases.c.source_table == source_table,
            entity_aliases.c.source_id == source_id,
        )
    ).first()
    if existing:
        return
    connection.execute(
        insert(entity_aliases).values(
            entity_id=entity_id,
            alias_title=alias_title,
            normalized_title=normalized_title,
            source_table=source_table,
            source_id=source_id,
            created_at=now,
        )
    )


def build_golden_source_payload(
    title: str,
    candidate: dict[str, Any],
    source: str,
    confidence: float,
    reason: str = "",
    schema_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_side = infer_selected_side(title, candidate)
    selected_source_table = infer_selected_source_table(title, candidate, selected_side)
    manufacturer = selected_value(candidate, selected_side, "manufacturer")
    price = normalize_price(selected_value(candidate, selected_side, "price"))
    master_columns, master_record = build_master_record(title, candidate, selected_side, schema_profile)
    return {
        "title": title,
        "manufacturer": manufacturer,
        "price": price,
        "master_columns": master_columns,
        "master_record": master_record,
        "source": source,
        "status": status_for_source(source),
        "selected_side": selected_side,
        "selected_source_table": selected_source_table,
        "confidence": confidence,
        "reason": reason,
        "source_ids": {
            "table1": candidate.get("table1.id"),
            "table2": candidate.get("table2.id"),
        },
        "match_scores": {
            "xgboost_score": candidate.get("xgboost_score"),
            "mlp_score": candidate.get("mlp_score"),
            "lexical_score": candidate.get("lexical_score"),
            "rank_fusion_score": candidate.get("rank_fusion_score"),
            "confidence": candidate.get("confidence"),
        },
        "candidate": candidate,
    }


def ensure_lineage_payload(title: str, candidate: dict[str, Any], canonical_payload: dict[str, Any], source: str) -> dict[str, Any]:
    payload = dict(canonical_payload)
    source_candidate = candidate or payload.get("candidate") or {}
    if not payload.get("selected_source_table"):
        payload["selected_source_table"] = infer_selected_source_table(title, source_candidate, payload.get("selected_side")) or "unknown"
    payload["status"] = status_for_source(source)
    return payload


def infer_selected_side(title: str, candidate: dict[str, Any]) -> str | None:
    normalized_title = normalize_text(title)
    for side in ("table1", "table2"):
        side_title = normalize_text(candidate.get(f"{side}.title"))
        if normalized_title and normalized_title == side_title:
            return side
    return None


def infer_selected_source_table(title: str, candidate: dict[str, Any], selected_side: str | None = None) -> str | None:
    if selected_side:
        return source_table_label(selected_side)
    normalized_title = normalize_text(title)
    for member in candidate.get("member_records", []):
        if not isinstance(member, dict):
            continue
        if normalized_title and normalized_title == normalize_text(member.get("title")):
            return source_table_label(str(member.get("source_table") or ""))
    source_table = candidate.get("source_table")
    if source_table:
        return source_table_label(str(source_table))
    return infer_selected_source_table_from_refs(candidate)


def infer_selected_source_table_from_refs(candidate: dict[str, Any]) -> str | None:
    refs = extract_source_refs(candidate)
    if refs:
        return source_table_label(refs[0][0])
    return None


def source_table_label(source_table: str) -> str | None:
    mapping = {
        "table1": "tableA",
        "table_a": "tableA",
        "tableA": "tableA",
        "table2": "tableB",
        "table_b": "tableB",
        "tableB": "tableB",
        "incoming": "incoming",
    }
    return mapping.get(source_table)


def status_for_source(source: str) -> str:
    if source == "uncertain":
        return "uncertain"
    if source == "llm":
        return "llm_active"
    return "backend_active"


def selected_value(candidate: dict[str, Any], selected_side: str | None, field: str) -> Any:
    if selected_side:
        value = candidate.get(f"{selected_side}.{field}")
        if value not in (None, ""):
            return value
    return candidate.get(field) or candidate.get(f"table1.{field}") or candidate.get(f"table2.{field}")


def choose_canonical_title_from_candidate(candidate: dict[str, Any]) -> str:
    title1 = str(candidate.get("table1.title") or "")
    title2 = str(candidate.get("table2.title") or "")
    if not title1:
        return title2
    if not title2:
        return title1
    return title1 if len(normalize_text(title1)) >= len(normalize_text(title2)) else title2


def extract_chosen_title(value: str) -> str:
    if not value:
        return ""
    if not isinstance(value, str):
        if isinstance(value, list) and value:
            value = value[0]
        else:
            return str(value)
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
    if isinstance(parsed, dict):
        return str(parsed.get("chosen_title") or parsed.get("title") or "")
    return str(parsed)


def parse_record_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"items": payload}
    if payload is None:
        return {}
    return parse_json_like_string(str(payload))


def parse_json_like_string(value: str, max_depth: int = 5) -> dict[str, Any]:
    text_value = strip_json_fence(strip_think_blocks(value)).strip()
    for _ in range(max_depth):
        try:
            parsed = json.loads(text_value)
        except json.JSONDecodeError:
            extracted = extract_json_document(text_value)
            if extracted is None:
                return {}
            text_value = extracted
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
        if isinstance(parsed, str):
            stripped = strip_json_fence(strip_think_blocks(parsed)).strip()
            if stripped == text_value:
                return {}
            text_value = stripped
            continue
        return {}
    return {}


def strip_think_blocks(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL | re.IGNORECASE).strip()


def extract_json_document(value: str) -> str | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(value):
        if char not in "{[":
            continue
        try:
            _parsed, end = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        return value[index : index + end]
    return None


def extract_cluster_commit_records(parsed: dict[str, Any], batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cluster_by_id = {str(cluster.get("cluster_id")): cluster for cluster in batch if isinstance(cluster, dict)}
    parsed_clusters = parsed.get("clusters") if isinstance(parsed.get("clusters"), list) else []
    records: list[dict[str, Any]] = []

    for cluster_result in parsed_clusters:
        if not isinstance(cluster_result, dict):
            continue
        cluster = cluster_by_id.get(str(cluster_result.get("cluster_id")))
        if not cluster:
            continue
        records.extend(entity_results_to_commit_records(cluster_result.get("entities"), cluster))

    if not records and isinstance(parsed.get("entities"), list):
        cluster = batch[0] if len(batch) == 1 and isinstance(batch[0], dict) else {}
        records.extend(entity_results_to_commit_records(parsed.get("entities"), cluster))
    return records


def find_uncovered_cluster_members(parsed: dict[str, Any], batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed_clusters = parsed.get("clusters") if isinstance(parsed.get("clusters"), list) else []
    covered_by_cluster: dict[str, set[str]] = {}
    for cluster_result in parsed_clusters:
        if not isinstance(cluster_result, dict):
            continue
        cluster_id = str(cluster_result.get("cluster_id") or "")
        covered = covered_by_cluster.setdefault(cluster_id, set())
        entities_result = cluster_result.get("entities") if isinstance(cluster_result.get("entities"), list) else []
        for entity_result in entities_result:
            if not isinstance(entity_result, dict):
                continue
            decision = str(entity_result.get("decision") or "same_entity")
            if decision not in {"same_entity", "same", "merge"}:
                continue
            covered.update(str(member) for member in entity_result.get("members", []))

    uncovered: list[dict[str, Any]] = []
    for cluster in batch:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("cluster_id") or "")
        all_members = {str(record.get("node_id")) for record in cluster.get("records", []) if isinstance(record, dict)}
        missing = sorted(member for member in all_members if member not in covered_by_cluster.get(cluster_id, set()))
        if missing:
            uncovered.append({"cluster": cluster, "missing_members": missing})
    return uncovered


def entity_results_to_commit_records(entity_results: Any, cluster: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(entity_results, list) or not isinstance(cluster, dict):
        return []
    cluster_records = {str(record.get("node_id")): record for record in cluster.get("records", []) if isinstance(record, dict)}
    result: list[dict[str, Any]] = []
    for entity_result in entity_results:
        if not isinstance(entity_result, dict):
            continue
        decision = str(entity_result.get("decision") or "same_entity")
        if decision not in {"same_entity", "same", "merge"}:
            continue
        member_ids = [str(member) for member in entity_result.get("members", []) if str(member) in cluster_records]
        if not member_ids:
            continue
        member_records = [cluster_records[member_id] for member_id in member_ids]
        chosen_title = str(entity_result.get("canonical_title") or choose_canonical_title_from_members(member_records))
        candidate = build_candidate_from_members(member_records, cluster, entity_result)
        result.append(
            {
                "chosen_title": chosen_title,
                "chosen_record": candidate,
                "confidence": safe_float(entity_result.get("confidence"), default=safe_float(cluster.get("max_confidence"), default=0.9)),
                "reason": str(entity_result.get("reason") or "大模型按冲突簇完成实体分组"),
            }
        )
    return result


def choose_canonical_title_from_members(member_records: list[dict[str, Any]]) -> str:
    titles = [str(record.get("title") or "") for record in member_records if record.get("title")]
    if not titles:
        return "未命名黄金记录"
    return max(titles, key=lambda value: len(normalize_text(value)))


def build_candidate_from_members(member_records: list[dict[str, Any]], cluster: dict[str, Any], entity_result: dict[str, Any]) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "cluster_id": cluster.get("cluster_id"),
        "confidence": safe_float(entity_result.get("confidence"), default=safe_float(cluster.get("max_confidence"), default=0.9)),
        "member_records": member_records,
        "aliases": entity_result.get("aliases") or [record.get("title") for record in member_records if record.get("title")],
        "llm_entity_result": entity_result,
    }
    for record in member_records:
        side = str(record.get("source_table") or "")
        if side in {"table1", "table2"} and f"{side}.id" not in candidate:
            candidate[f"{side}.id"] = record.get("source_id")
            candidate[f"{side}.title"] = record.get("title")
            candidate[f"{side}.manufacturer"] = record.get("manufacturer")
            candidate[f"{side}.price"] = record.get("price")
    if cluster.get("candidates"):
        first_pair = cluster["candidates"][0]
        if isinstance(first_pair, dict):
            for key in ("xgboost_score", "mlp_score", "lexical_score", "rank_fusion_score"):
                candidate[key] = first_pair.get(key)
    return candidate


def extract_commit_records(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    llm_result = parsed.get("llm_result") or parsed.get("result") or parsed.get("text")
    if llm_result is not None:
        parsed = parse_record_payload(strip_json_fence(strip_think_blocks(llm_result)))

    for key in ("records", "choices", "items"):
        records = parsed.get(key)
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]

    title_value = parsed.get("title")
    if isinstance(title_value, str) and title_value.strip().startswith(("{", "[", "```")):
        nested = parse_record_payload(title_value)
        if nested != parsed:
            nested_records = extract_commit_records(nested)
            if nested_records:
                return nested_records
        return []

    if any(key in parsed for key in ("chosen_title", "chosen_record", "record", "candidate")):
        return [parsed]
    return []


def call_dify_batch_review(batch_payload: dict[str, Any]) -> dict[str, Any]:
    if os.getenv("MDM_ALLOW_LOCAL_LLM_FALLBACK", "").lower() in {"1", "true", "yes"}:
        return local_batch_review_fallback(batch_payload)

    api_url = os.getenv("DIFY_BATCH_REVIEW_API_URL", "").strip()
    api_key = os.getenv("DIFY_BATCH_REVIEW_API_KEY", "").strip()
    if not api_url or not api_key:
        raise HTTPException(
            status_code=500,
            detail="DIFY_BATCH_REVIEW_API_URL and DIFY_BATCH_REVIEW_API_KEY are required for high-confidence batch processing.",
        )

    candidate_batch_json = json.dumps(batch_payload, ensure_ascii=False)
    response = requests.post(
        api_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "inputs": {
                "candidate_batch_json": candidate_batch_json,
                "knowledge_query": build_knowledge_query(batch_payload),
            },
            "response_mode": "blocking",
            "user": os.getenv("DIFY_BATCH_REVIEW_USER", "mdm-service"),
        },
        timeout=int(os.getenv("DIFY_BATCH_REVIEW_TIMEOUT", "300")),
    )
    response.raise_for_status()
    return extract_dify_workflow_output(response.json())


def extract_dify_workflow_output(response_payload: dict[str, Any]) -> dict[str, Any]:
    data = response_payload.get("data") if isinstance(response_payload.get("data"), dict) else response_payload
    outputs = data.get("outputs") if isinstance(data, dict) and isinstance(data.get("outputs"), dict) else {}
    for key in ("result", "records", "text", "answer"):
        if key in outputs:
            parsed = parse_record_payload(strip_json_fence(outputs[key]))
            if parsed:
                return parsed
    if isinstance(outputs, dict) and outputs:
        return outputs
    if isinstance(data, dict):
        for key in ("result", "text", "answer"):
            if key in data:
                parsed = parse_record_payload(strip_json_fence(data[key]))
                if parsed:
                    return parsed
    return response_payload


def build_knowledge_query(batch_payload: dict[str, Any]) -> str:
    title_parts: list[str] = []
    for candidate in batch_payload.get("clusters") or batch_payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        if isinstance(candidate.get("records"), list):
            title_parts.extend(str(record.get("title") or "") for record in candidate["records"] if isinstance(record, dict))
        else:
            title_parts.append(str(candidate.get("table1.title") or ""))
            title_parts.append(str(candidate.get("table2.title") or ""))
    compact_titles = " | ".join(part for part in title_parts if part)[:1000]
    return f"主数据标题选择规则、品牌别名、制造商归一化、历史匹配样例。当前候选标题：{compact_titles}"


def local_batch_review_fallback(batch_payload: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for candidate in batch_payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        if isinstance(candidate.get("records"), list) and candidate.get("cluster_id"):
            # 冲突簇不能本地降级合并；返回空 records，让提交接口标记 uncertain。
            continue
        title1 = str(candidate.get("table1.title") or "")
        title2 = str(candidate.get("table2.title") or "")
        chosen_title = title1 if len(title1) >= len(title2) else title2
        records.append(
            {
                "chosen_title": chosen_title,
                "chosen_record": candidate,
                "confidence": safe_float(candidate.get("confidence"), default=0.9),
                "reason": "大模型输出不可解析时的安全降级：选择更完整的标题，避免整批候选丢失。",
            }
        )
    return {"records": records}


def strip_json_fence(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text_value = value.strip()
    if text_value.startswith("```"):
        lines = [line for line in text_value.splitlines() if not line.strip().startswith("```")]
        return "\n".join(lines).strip()
    return text_value


def parse_request_payload(raw: bytes) -> dict[str, Any]:
    text_value = raw.decode("utf-8").strip()
    if not text_value:
        return {}
    try:
        parsed = json.loads(text_value)
    except json.JSONDecodeError:
        form_data = {key: values[0] if len(values) == 1 else values for key, values in parse_qs(text_value).items()}
        if form_data:
            return form_data
        return {
            "chosen_title": text_value,
            "chosen_record": text_value,
            "source": "llm",
            "confidence": 0.9,
            "reason": "raw text payload",
        }
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_model() -> dict[str, Any]:
    if MODEL_PATH.exists():
        return joblib.load(MODEL_PATH)
    feature_builder = PairFeatureBuilder()
    return {"feature_builder": feature_builder, "xgb_model": None, "mlp_model": None, "metrics": {}}


def load_master_as_table() -> pd.DataFrame:
    from_entities = False
    with get_engine().connect() as connection:
        entity_count = connection.execute(select(func.count()).select_from(entities)).scalar_one()
        if int(entity_count) > 0:
            from_entities = True
            rows = connection.execute(
                select(
                    entities.c.id,
                    entities.c.canonical_title.label("title"),
                    entities.c.manufacturer,
                    entities.c.price,
                )
            ).mappings().all()
        else:
            rows = connection.execute(select(golden_records.c.id, golden_records.c.title, golden_records.c.manufacturer, golden_records.c.price)).mappings().all()
    records = [dict(row) for row in rows]
    if from_entities:
        records = [{**record, "id": f"entity:{record['id']}"} for record in records]
    return preprocess_table(pd.DataFrame(records, columns=["id", "title", "manufacturer", "price"]))


def build_candidate_pairs(
    master: pd.DataFrame,
    incoming: pd.DataFrame,
    rough_threshold: float = 0.18,
    schema_profile: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if schema_profile:
        return build_schema_candidate_pairs(master, incoming, schema_profile, rough_threshold=rough_threshold)

    rows: list[dict[str, Any]] = []
    incoming_records = incoming.reset_index(drop=True).to_dict(orient="records")
    token_index: dict[str, set[int]] = defaultdict(set)
    maker_index: dict[str, set[int]] = defaultdict(set)
    empty_title_indices: set[int] = set()

    for index, record in enumerate(incoming_records):
        tokens = set(str(record.get("title_norm") or "").split())
        if tokens:
            for token in tokens:
                token_index[token].add(index)
        else:
            empty_title_indices.add(index)
        maker = str(record.get("manufacturer_norm") or "")
        if maker:
            maker_index[maker].add(index)

    for _, left in master.iterrows():
        left_title_norm = str(left["title_norm"] or "")
        left_tokens = set(left_title_norm.split())
        candidate_indices: set[int] = set()
        for token in left_tokens:
            candidate_indices.update(token_index.get(token, set()))

        if not left_tokens:
            candidate_indices.update(empty_title_indices)

        left_maker = str(left["manufacturer_norm"] or "")
        if left_maker:
            candidate_indices.update(maker_index.get(left_maker, set()))

        for index in sorted(candidate_indices):
            right = incoming_records[index]
            same_maker = bool(left_maker and left_maker == str(right.get("manufacturer_norm") or ""))
            rough_score = 1.0 if same_maker else token_jaccard(left_title_norm, str(right.get("title_norm") or ""))
            if rough_score >= rough_threshold or same_maker:
                rows.append(
                    {
                        "table1.id": left["id"],
                        "table2.id": right["id"],
                        "table1.title": left["title"],
                        "table2.title": right["title"],
                        "table1.manufacturer": left["manufacturer"],
                        "table2.manufacturer": right["manufacturer"],
                        "table1.price": left["price"],
                        "table2.price": right["price"],
                    }
                )
    return pd.DataFrame(rows)


def build_schema_candidate_pairs(
    master: pd.DataFrame,
    incoming: pd.DataFrame,
    schema_profile: dict[str, Any],
    rough_threshold: float = 0.12,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    incoming_records = incoming.reset_index(drop=True).to_dict(orient="records")
    blocking_columns = schema_profile.get("blocking_columns") or list((schema_profile.get("weights") or {}).keys())
    token_index: dict[str, set[int]] = defaultdict(set)
    empty_indices: set[int] = set()

    for index, record in enumerate(incoming_records):
        indexed = False
        for column in blocking_columns:
            tokens = set(str(record.get(f"__match_{column}_norm") or "").split())
            for token in tokens:
                token_index[token].add(index)
                indexed = True
        if not indexed:
            empty_indices.add(index)

    max_postings = max(25, int(len(incoming_records) * MAX_SCHEMA_TOKEN_POSTINGS_RATIO))
    weights = schema_profile.get("weights") or {}
    for _, left in master.iterrows():
        candidate_indices: set[int] = set()
        for column in blocking_columns:
            for token in set(str(left.get(f"__match_{column}_norm") or "").split()):
                postings = token_index.get(token, set())
                if len(postings) <= max_postings:
                    candidate_indices.update(postings)
        if not candidate_indices:
            candidate_indices.update(empty_indices)

        left_rows: list[tuple[float, dict[str, Any]]] = []
        for index in sorted(candidate_indices):
            right = incoming_records[index]
            rough_score = rough_schema_score(left, right, weights)
            if rough_score < rough_threshold:
                continue
            row = {
                "table1.id": left["id"],
                "table2.id": right["id"],
                "table1.title": left["title"],
                "table2.title": right["title"],
                "table1.manufacturer": left["manufacturer"],
                "table2.manufacturer": right["manufacturer"],
                "table1.price": left["price"],
                "table2.price": right["price"],
                "schema_rough_score": rough_score,
                "schema_entity_type": schema_profile.get("entity_type"),
            }
            for column in weights:
                row[f"table1.__match_{column}"] = left.get(f"__match_{column}")
                row[f"table2.__match_{column}"] = right.get(f"__match_{column}")
            left_rows.append((rough_score, row))
        left_rows.sort(key=lambda item: item[0], reverse=True)
        rows.extend(row for _, row in left_rows[:MAX_SCHEMA_CANDIDATES_PER_LEFT])
    return pd.DataFrame(rows)


def rough_schema_score(left: pd.Series, right: dict[str, Any], weights: dict[str, float]) -> float:
    total = 0.0
    used_weight = 0.0
    for column, weight in weights.items():
        left_text = str(left.get(f"__match_{column}_norm") or "")
        right_text = str(right.get(f"__match_{column}_norm") or "")
        if not left_text and not right_text:
            continue
        total += float(weight) * token_jaccard(left_text, right_text)
        used_weight += float(weight)
    return total / used_weight if used_weight else 0.0


def score_candidates(
    candidate_pairs: pd.DataFrame,
    model_bundle: dict[str, Any],
    schema_profile: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if schema_profile:
        features = generic_pair_features(candidate_pairs, schema_profile)
        lexical = features["schema_weighted_similarity"].fillna(0.0).to_numpy()
        result = candidate_pairs.copy()
        result["xgboost_score"] = lexical
        result["mlp_score"] = lexical
        result["lexical_score"] = lexical
        result["rank_fusion_score"] = rank_normalize(lexical)
        result["confidence"] = lexical
        for column in features.columns:
            result[column] = features[column]
        return result

    feature_builder = model_bundle["feature_builder"]
    try:
        features = feature_builder.transform_pairs(candidate_pairs)
    except Exception:
        feature_builder.fit(
            candidate_pairs["table1.title"].fillna("").astype(str).tolist()
            + candidate_pairs["table2.title"].fillna("").astype(str).tolist()
        )
        features = feature_builder.transform_pairs(candidate_pairs)

    lexical_columns = [
        column
        for column in [
            "char_tfidf_cosine",
            "word_tfidf_cosine",
            "title_token_jaccard",
            "title_bm25_similarity",
            "important_token_jaccard",
            "important_token_containment",
        ]
        if column in features.columns
    ]
    lexical = features[lexical_columns].mean(axis=1).to_numpy()
    model_features = select_model_features(features, model_bundle)
    xgb_model = model_bundle.get("xgb_model")
    mlp_model = model_bundle.get("mlp_model")
    xgb_scores = probability_column(xgb_model.predict_proba(model_features)) if xgb_model is not None else lexical
    if mlp_model is not None:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            mlp_scores = probability_column(mlp_model.predict_proba(model_features))
    else:
        mlp_scores = lexical
    rank_fusion = (rank_normalize(xgb_scores) + rank_normalize(mlp_scores) + rank_normalize(lexical)) / 3
    confidence = 0.45 * rank_fusion + 0.35 * xgb_scores + 0.10 * mlp_scores + 0.10 * lexical

    result = candidate_pairs.copy()
    result["xgboost_score"] = xgb_scores
    result["mlp_score"] = mlp_scores
    result["lexical_score"] = lexical
    result["rank_fusion_score"] = rank_fusion
    result["confidence"] = confidence
    return result


def select_model_features(features: pd.DataFrame, model_bundle: dict[str, Any]) -> pd.DataFrame:
    feature_columns = model_bundle.get("feature_columns")
    if not feature_columns:
        return features
    selected = features.copy()
    for column in feature_columns:
        if column not in selected.columns:
            selected[column] = 0.0
    return selected[list(feature_columns)]


def probability_column(probabilities: Any):
    values = pd.DataFrame(probabilities)
    if values.shape[1] == 1:
        return values.iloc[:, 0].to_numpy()
    return values.iloc[:, 1].to_numpy()


def rank_normalize(values):
    series = pd.Series(values)
    if len(series) <= 1:
        return series.fillna(0.0).to_numpy()
    return series.rank(method="average", pct=True).to_numpy()


def write_unmatched_to_master(incoming: pd.DataFrame, matched_ids: set[str] | None = None, source_table: str = "incoming") -> int:
    matched_ids = matched_ids or set()
    inserted = 0
    now = time.time()
    with get_engine().begin() as connection:
        for _, row in incoming.iterrows():
            if str(row["id"]) in matched_ids:
                continue
            candidate = {
                "id": row["id"],
                "source_table": source_table,
                "title": row["title"],
                "manufacturer": row["manufacturer"],
                "price": row["price"],
                "attributes": {
                    key.removeprefix("__match_"): value
                    for key, value in row.items()
                    if isinstance(key, str) and key.startswith("__match_") and not key.endswith("_norm")
                },
            }
            canonical_payload = {
                "title": row["title"],
                "manufacturer": row["manufacturer"],
                "price": row["price_norm"],
                "master_columns": list(candidate["attributes"].keys()) or ["title", "manufacturer", "price"],
                "master_record": candidate["attributes"] or {
                    "title": row["title"],
                    "manufacturer": row["manufacturer"],
                    "price": row["price_norm"],
                },
                "source": "auto",
                "confidence": 1.0,
                "reason": "粗筛后没有保留候选，作为独立实体写入",
                "candidate": candidate,
            }
            insert_unmatched_record_with_connection(connection, str(row["title"]), candidate, canonical_payload, source_table, now)
            inserted += 1
        connection.execute(insert(action_log).values(operation="write_unmatched_to_master", payload=json.dumps({"inserted": inserted}), created_at=now))
    return inserted


def insert_unmatched_record_with_connection(
    connection,
    title: str,
    candidate: dict[str, Any],
    canonical_payload: dict[str, Any],
    source_table: str,
    now: float,
) -> int:
    canonical_payload = ensure_lineage_payload(title, candidate, canonical_payload, "auto")
    result = connection.execute(
        insert(entities).values(
            canonical_title=title,
            manufacturer=canonical_payload.get("manufacturer"),
            price=canonical_payload.get("price"),
            status=status_for_source("auto"),
            selected_source_table=canonical_payload.get("selected_source_table"),
            confidence=1.0,
            created_at=now,
            updated_at=now,
        )
    )
    entity_id = int(result.inserted_primary_key[0])
    connection.execute(
        insert(golden_records).values(
            entity_id=entity_id,
            title=title,
            manufacturer=canonical_payload.get("manufacturer"),
            price=canonical_payload.get("price"),
            selected_source_table=canonical_payload.get("selected_source_table"),
            source_payload=json.dumps(canonical_payload, ensure_ascii=False),
            source="auto",
            confidence=1.0,
            created_at=now,
        )
    )
    raw_source_id = candidate.get("id")
    source_id = "" if raw_source_id is None else str(raw_source_id)
    connection.execute(
        insert(source_record_links).values(
            entity_id=entity_id,
            source_table=source_table,
            source_id=source_id,
            raw_payload=json.dumps(candidate, ensure_ascii=False),
            created_at=now,
        )
    )
    connection.execute(
        insert(entity_aliases).values(
            entity_id=entity_id,
            alias_title=title,
            normalized_title=normalize_text(title),
            source_table=source_table,
            source_id=source_id,
            created_at=now,
        )
    )
    connection.execute(
        insert(match_decisions).values(
            match_run_id="",
            cluster_id="",
            left_id="",
            right_id="",
            decision="same_entity",
            confidence=1.0,
            reason="粗筛后没有保留候选，作为独立实体写入",
            model_scores=json.dumps({}, ensure_ascii=False),
            created_at=now,
        )
    )
    return entity_id


def repair_missing_source_links_from_csv(csv_path: Any, source_table: str, schema_profile: dict[str, Any] | None = None) -> int:
    raw_rows = load_csv(csv_path)
    if schema_profile:
        rows = apply_schema_profile(raw_rows, schema_profile, "tableA" if source_table == "table1" else "tableB")
    else:
        rows = preprocess_table(raw_rows)
    repaired = 0
    now = time.time()
    with get_engine().begin() as connection:
        for _, row in rows.iterrows():
            source_id = str(row["id"])
            existing = connection.execute(
                select(source_record_links.c.id).where(
                    source_record_links.c.source_table == source_table,
                    source_record_links.c.source_id == source_id,
                )
            ).first()
            if existing:
                continue
            attributes = {
                key.removeprefix("__match_"): value
                for key, value in row.items()
                if isinstance(key, str) and key.startswith("__match_") and not key.endswith("_norm")
            }
            title = str(row.get("title") or row.get("name") or next((value for value in attributes.values() if value not in (None, "")), source_id))
            candidate = {
                "id": source_id,
                "source_table": source_table,
                "title": title,
                "manufacturer": row.get("manufacturer", ""),
                "price": row.get("price", ""),
                "attributes": attributes,
            }
            master_record = attributes or {
                key: value
                for key, value in row.items()
                if isinstance(key, str)
                and key not in {"id"}
                and not key.startswith("__match_")
                and value not in (None, "")
            }
            canonical_payload = {
                "title": title,
                "manufacturer": row.get("manufacturer", ""),
                "price": row.get("price_norm", row.get("price", "")),
                "master_columns": list(master_record.keys()) or ["title"],
                "master_record": master_record or {"title": title},
                "source": "auto",
                "confidence": 1.0,
                "reason": "最终收尾：来源记录缺少实体链接，后端作为独立实体补齐，避免覆盖缺失",
                "candidate": candidate,
            }
            entity_id = upsert_entity_with_connection(
                connection=connection,
                title=title,
                candidate=candidate,
                canonical_payload=canonical_payload,
                source="auto",
                confidence=1.0,
                reason="最终收尾：来源记录缺少实体链接，后端作为独立实体补齐，避免覆盖缺失",
            )
            upsert_golden_record_with_connection(
                connection=connection,
                entity_id=entity_id,
                title=title,
                canonical_payload=canonical_payload,
                source="auto",
                confidence=1.0,
            )
            repaired += 1
        connection.execute(
            insert(action_log).values(
                operation="repair_missing_source_links_from_csv",
                payload=json.dumps({"source_table": source_table, "repaired": repaired}, ensure_ascii=False),
                created_at=now,
            )
        )
    return repaired


def get_review_task(task_id: int) -> dict[str, Any]:
    with get_engine().connect() as connection:
        row = connection.execute(select(review_tasks).where(review_tasks.c.id == task_id)).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"review task not found: {task_id}")
    return dict(row)


def extract_review_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("human_review_candidates"), list):
        return payload["human_review_candidates"]
    if isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    return []


def render_candidate_row(index: int, candidate: dict[str, Any]) -> str:
    table1_title = str(candidate.get("table1.title", ""))
    table2_title = str(candidate.get("table2.title", ""))
    confidence = float(candidate.get("confidence", 0.0))
    manufacturer = f"{candidate.get('table1.manufacturer', '')} / {candidate.get('table2.manufacturer', '')}"
    price = f"{candidate.get('table1.price', '')} / {candidate.get('table2.price', '')}"
    return f"""
    <tr>
      <td class="score">{confidence:.4f}</td>
      <td><strong>{html.escape(table1_title)}</strong><br>{html.escape(manufacturer)}<br>{html.escape(price)}</td>
      <td><strong>{html.escape(table2_title)}</strong></td>
      <td>
        <label><input type="radio" name="choice_{index}" value="table1"> 使用 Table A 标题</label>
        <label><input type="radio" name="choice_{index}" value="table2" checked> 使用 Table B 标题</label>
        <label><input type="radio" name="choice_{index}" value="skip"> 暂不写入</label>
      </td>
    </tr>
    """


def public_review_base_url() -> str:
    return os.getenv("PUBLIC_REVIEW_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def mask_database_url(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"
