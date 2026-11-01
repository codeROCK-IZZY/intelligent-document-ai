"""Background job handlers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.runner import process_version


def handle_process_version(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    return process_version(db, uuid.UUID(payload["version_id"]))


HANDLERS = {"process_version": handle_process_version}
