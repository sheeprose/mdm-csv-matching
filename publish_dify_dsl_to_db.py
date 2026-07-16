from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import yaml
from sqlalchemy import select

from app import app
from extensions.ext_database import db
from models.model import App
from models.workflow import Workflow


APP_ID = os.environ.get("DIFY_APP_ID", "6ad6c25e-228e-48e7-880d-6a9525af1598")
DSL_PATH = Path("/tmp/mdm_workflow.yml")


def dumps(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def variables_to_mapping(value) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {item.get("name") or item.get("variable") or item.get("id"): item for item in value if isinstance(item, dict)}
    return {}


def main() -> None:
    dsl = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    workflow_payload = dsl["workflow"]
    now = datetime.utcnow()

    with app.app_context():
        app_model = db.session.get(App, APP_ID)
        if app_model is None:
            raise RuntimeError(f"app not found: {APP_ID}")

        draft = db.session.scalar(
            select(Workflow).where(
                Workflow.tenant_id == app_model.tenant_id,
                Workflow.app_id == app_model.id,
                Workflow.version == Workflow.VERSION_DRAFT,
            )
        )
        if draft is None:
            raise RuntimeError(f"draft workflow not found for app: {APP_ID}")

        draft.graph = dumps(workflow_payload.get("graph", {}))
        draft._features = dumps(workflow_payload.get("features", {}))
        draft._environment_variables = dumps(variables_to_mapping(workflow_payload.get("environment_variables", [])))
        draft._conversation_variables = dumps(variables_to_mapping(workflow_payload.get("conversation_variables", [])))
        draft._rag_pipeline_variables = dumps(variables_to_mapping(workflow_payload.get("rag_pipeline_variables", [])))
        draft.updated_by = draft.updated_by or draft.created_by
        draft.updated_at = now

        published = Workflow()
        published.id = str(uuid4())
        published.tenant_id = app_model.tenant_id
        published.app_id = app_model.id
        published.type = draft.type
        published.kind = draft.kind
        published.version = Workflow.version_from_datetime(now)
        published.graph = draft.graph
        published._features = draft._features
        published.created_by = draft.updated_by or draft.created_by
        published.created_at = now
        published.updated_by = published.created_by
        published.updated_at = now
        published._environment_variables = draft._environment_variables
        published._conversation_variables = draft._conversation_variables
        published._rag_pipeline_variables = draft._rag_pipeline_variables
        published.marked_name = "codex-local-publish"
        published.marked_comment = "Published from D:\\mdm7.6v2\\mdm_data\\dify\\mdm_workflow.yml for dataset tests."
        db.session.add(published)
        db.session.flush()

        app_model.workflow_id = published.id
        app_model.updated_by = published.created_by
        app_model.updated_at = now
        db.session.commit()

        print(
            json.dumps(
                {
                    "app_id": str(app_model.id),
                    "draft_id": str(draft.id),
                    "published_id": str(published.id),
                    "published_version": published.version,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
