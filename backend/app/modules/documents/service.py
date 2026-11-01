"""Document access rules and upload handling.

Access levels (strongest first): ``owner`` > ``edit`` > ``read``. All checks go
through ``access_level``, which reads the same ``readable_documents`` view used by
retrieval, so the API and search can never disagree about who may see what.
A document the caller cannot read is reported as **404, not 403**, so its existence
is not revealed.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound
from app.db.models import Document, DocumentVersion
from app.jobs import queue
from app.pipeline.validate import validate_upload
from app.storage.files import get_store

LEVELS = {"read": 1, "edit": 2, "owner": 3}


def access_level(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> str | None:
    rows = (
        db.execute(
            text("SELECT access FROM readable_documents WHERE user_id = :u AND document_id = :d"),
            {"u": user_id, "d": document_id},
        )
        .scalars()
        .all()
    )
    return max(rows, key=lambda a: LEVELS[a]) if rows else None


def get_document(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, need: str = "read") -> Document:
    level = access_level(db, user_id, document_id)
    if level is None:
        raise NotFound("Document not found")
    if LEVELS[level] < LEVELS[need]:
        raise Forbidden(f"Requires '{need}' access to this document")
    doc = db.get(Document, document_id)
    assert doc is not None
    return doc


def find_exact_duplicate(db: Session, user_id: uuid.UUID, sha256: str) -> Document | None:
    return db.scalar(
        select(Document)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(DocumentVersion.sha256 == sha256)
        .where(Document.id.in_(text("SELECT document_id FROM readable_documents WHERE user_id = :u")))
        .params(u=user_id)
        .limit(1)
    )


def add_version(db: Session, doc: Document, data: bytes, filename: str, user_id: uuid.UUID) -> DocumentVersion:
    """Validate, store and queue a new version. The caller commits."""
    sniffed = validate_upload(data)
    next_no = (
        db.scalar(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == doc.id)) or 0
    ) + 1
    vid = uuid.uuid4()
    key = f"{doc.id}/{vid}.{sniffed.extension}"
    get_store().put(key, data)
    v = DocumentVersion(
        id=vid,
        document_id=doc.id,
        version_no=next_no,
        filename=filename[:255] or f"upload.{sniffed.extension}",
        file_key=key,
        sha256=hashlib.sha256(data).hexdigest(),
        mime=sniffed.mime,
        size_bytes=len(data),
        uploaded_by=user_id,
    )
    db.add(v)
    db.flush()
    doc.current_version_id = v.id
    queue.enqueue(db, "process_version", {"version_id": str(v.id)})
    return v


def create_document(
    db: Session,
    owner_id: uuid.UUID,
    data: bytes,
    filename: str,
    title: str | None,
    collection_id: uuid.UUID | None,
) -> tuple[Document, DocumentVersion, Document | None]:
    """Returns (document, version, existing exact duplicate or None). The caller commits."""
    validate_upload(data)  # fail fast before creating rows
    duplicate = find_exact_duplicate(db, owner_id, hashlib.sha256(data).hexdigest())
    doc = Document(
        owner_id=owner_id, title=(title or filename.rsplit(".", 1)[0] or "Untitled")[:300], collection_id=collection_id
    )
    db.add(doc)
    db.flush()
    v = add_version(db, doc, data, filename, owner_id)
    return doc, v, duplicate
