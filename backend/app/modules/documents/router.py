"""Documents: upload, list, detail, versions, pages, download, sharing, type override, delete."""

from __future__ import annotations

import difflib
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, literal_column, select, text
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, Member, Viewer
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, UnprocessableData
from app.db.models import (
    DOC_TYPES,
    Chunk,
    Collection,
    Document,
    DocumentShare,
    DocumentVersion,
    Entity,
    Extraction,
    Page,
    User,
)
from app.modules import audit
from app.modules.documents import service
from app.storage.files import get_store

router = APIRouter(prefix="/documents", tags=["documents"])
READ_CHUNK = 1024 * 1024


class VersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    filename: str
    mime: str
    size_bytes: int
    page_count: int | None
    language: str | None
    status: str
    stage: str | None
    stages: dict[str, Any]
    error: str | None
    created_at: datetime
    processed_at: datetime | None


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    owner_id: uuid.UUID
    owner_email: str | None = None
    access: str
    collection_id: uuid.UUID | None
    doc_type: str | None
    doc_type_source: str | None
    created_at: datetime
    updated_at: datetime
    current_version: VersionOut | None


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int


class DocumentDetail(DocumentOut):
    versions: list[VersionOut]
    summary: dict[str, Any] | None
    fields: dict[str, Any] | None
    entities: list[dict[str, Any]]
    shares: list[dict[str, Any]] | None = None


class UploadOut(BaseModel):
    document: DocumentOut
    duplicate_of: uuid.UUID | None


def _version_out(v: DocumentVersion | None) -> VersionOut | None:
    if v is None:
        return None
    return VersionOut.model_validate(v, from_attributes=True)


def _doc_out(db: Session, doc: Document, access: str) -> DocumentOut:
    owner = db.get(User, doc.owner_id)
    v = db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        owner_id=doc.owner_id,
        owner_email=owner.email if owner else None,
        access=access,
        collection_id=doc.collection_id,
        doc_type=doc.doc_type,
        doc_type_source=doc.doc_type_source,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        current_version=_version_out(v),
    )


async def _read_upload(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    chunks, size = [], 0
    while chunk := await file.read(READ_CHUNK):
        size += len(chunk)
        if size > limit:
            raise UnprocessableData(f"File exceeds the {get_settings().max_upload_mb} MB limit")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=UploadOut, status_code=202)
async def upload(
    db: DB,
    user: Member,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=300)] = None,
    collection_id: Annotated[uuid.UUID | None, Form()] = None,
) -> UploadOut:
    """Upload a document. The type is detected from the file's bytes; processing runs in the
    background (poll ``GET /documents/{id}``). Exact duplicates are accepted but reported."""
    data = await _read_upload(file)
    if collection_id is not None:
        coll = db.get(Collection, collection_id)
        if coll is None or coll.owner_id != user.id:
            raise NotFound("Collection not found")
    doc, _, dup = service.create_document(db, user.id, data, file.filename or "upload", title, collection_id)
    audit.record(db, user.id, "document.upload", "document", str(doc.id), size=len(data))
    db.commit()
    return UploadOut(document=_doc_out(db, doc, "owner"), duplicate_of=dup.id if dup else None)


@router.get("", response_model=DocumentPage)
def list_documents(
    db: DB,
    user: Viewer,
    q: str | None = Query(default=None, max_length=200),
    doc_type: str | None = Query(default=None, alias="type"),
    collection_id: uuid.UUID | None = None,
    status: Literal["queued", "processing", "ready", "failed"] | None = None,
    owned: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocumentPage:
    # Strongest access level per document (a user can be owner and also have a share row).
    access = (
        select(
            literal_column("document_id").label("document_id"),
            func.max(literal_column("CASE access WHEN 'owner' THEN 3 WHEN 'edit' THEN 2 ELSE 1 END")).label("level"),
        )
        .select_from(text("readable_documents"))
        .where(text("user_id = :u"))
        .group_by(literal_column("document_id"))
        .subquery()
    )
    stmt = select(Document, access.c.level).join(access, access.c.document_id == Document.id)
    if q:
        stmt = stmt.where(Document.title.ilike(f"%{q}%"))
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if collection_id:
        stmt = stmt.where(Document.collection_id == collection_id)
    if owned is not None:
        stmt = stmt.where((Document.owner_id == user.id) if owned else (Document.owner_id != user.id))
    if status:
        stmt = stmt.join(DocumentVersion, DocumentVersion.id == Document.current_version_id).where(
            DocumentVersion.status == status
        )
    params = {"u": user.id}
    total = db.scalar(select(func.count()).select_from(stmt.subquery()), params) or 0
    rows = db.execute(stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset), params).all()
    names = {3: "owner", 2: "edit", 1: "read"}
    return DocumentPage(items=[_doc_out(db, d, names[lvl]) for d, lvl in rows], total=total)


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(document_id: uuid.UUID, db: DB, user: Viewer) -> DocumentDetail:
    doc = service.get_document(db, user.id, document_id)
    access = service.access_level(db, user.id, document_id) or "read"
    base = _doc_out(db, doc, access)
    versions = list(
        db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_no.desc())
        )
    )
    vid = doc.current_version_id
    extractions = {e.kind: e for e in db.scalars(select(Extraction).where(Extraction.version_id == vid))} if vid else {}
    summary = extractions.get("summary")
    fields = next((e for k, e in extractions.items() if k != "summary"), None)
    entities: list[Entity] = (
        list(db.scalars(select(Entity).where(Entity.version_id == vid).order_by(Entity.page_no).limit(200)))
        if vid
        else []
    )
    shares = None
    if access == "owner":
        shares = [
            {"user_id": str(s.user_id), "email": u.email, "permission": s.permission}
            for s, u in db.execute(
                select(DocumentShare, User)
                .join(User, User.id == DocumentShare.user_id)
                .where(DocumentShare.document_id == doc.id)
            )
        ]
    return DocumentDetail(
        **base.model_dump(),
        versions=[_version_out(v) for v in versions],
        summary={"text": summary.data.get("summary"), "source": summary.source} if summary else None,
        fields={"kind": fields.kind, "data": fields.data, "source": fields.source, "validation": fields.validation}
        if fields
        else None,
        entities=[
            {"type": e.entity_type, "value": e.value, "normalized": e.normalized, "page_no": e.page_no}
            for e in entities
        ],
        shares=shares,
    )


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    doc_type: Literal[DOC_TYPES] | None = None  # type: ignore[valid-type]
    collection_id: uuid.UUID | None = None


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(document_id: uuid.UUID, body: DocumentUpdate, db: DB, user: Member) -> DocumentOut:
    doc = service.get_document(db, user.id, document_id, need="edit")
    if body.title is not None:
        doc.title = body.title
    if body.doc_type is not None:
        doc.doc_type, doc.doc_type_source = body.doc_type, "user"  # a human correction wins over models
    if "collection_id" in body.model_fields_set:
        if body.collection_id is not None:
            coll = db.get(Collection, body.collection_id)
            if coll is None or coll.owner_id != user.id:
                raise NotFound("Collection not found")
        doc.collection_id = body.collection_id
    db.commit()
    return _doc_out(db, doc, service.access_level(db, user.id, doc.id) or "read")


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, db: DB, user: Member) -> None:
    doc = service.get_document(db, user.id, document_id, need="owner")
    keys = [v.file_key for v in db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id))]
    audit.record(db, user.id, "document.delete", "document", str(doc.id), title=doc.title)
    db.delete(doc)
    db.commit()
    for k in keys:
        get_store().delete(k)


@router.post("/{document_id}/versions", response_model=DocumentOut, status_code=202)
async def upload_version(
    document_id: uuid.UUID, db: DB, user: Member, file: Annotated[UploadFile, File()]
) -> DocumentOut:
    doc = service.get_document(db, user.id, document_id, need="edit")
    data = await _read_upload(file)
    service.add_version(db, doc, data, file.filename or "upload", user.id)
    audit.record(db, user.id, "document.version", "document", str(doc.id))
    db.commit()
    return _doc_out(db, doc, service.access_level(db, user.id, doc.id) or "read")


class PageOut(BaseModel):
    page_no: int
    page_count: int
    text: str
    ocr_used: bool
    ocr_confidence: float | None
    tables: list[Any]


def _version_of(db: Session, doc: Document, version_no: int | None) -> DocumentVersion:
    if version_no is None:
        v = db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    else:
        v = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id, DocumentVersion.version_no == version_no
            )
        )
    if v is None:
        raise NotFound("Version not found")
    return v


@router.get("/{document_id}/pages/{page_no}", response_model=PageOut)
def get_page(document_id: uuid.UUID, page_no: int, db: DB, user: Viewer, version: int | None = None) -> PageOut:
    doc = service.get_document(db, user.id, document_id)
    v = _version_of(db, doc, version)
    page = db.get(Page, (v.id, page_no))
    if page is None:
        raise NotFound("Page not found (the document may still be processing)")
    return PageOut(
        page_no=page.page_no,
        page_count=v.page_count or 0,
        text=page.text,
        ocr_used=page.ocr_used,
        ocr_confidence=page.ocr_confidence,
        tables=page.tables,
    )


@router.get("/{document_id}/file")
def download(document_id: uuid.UUID, db: DB, user: Viewer, version: int | None = None) -> Response:
    doc = service.get_document(db, user.id, document_id)
    v = _version_of(db, doc, version)
    safe = "".join(ch for ch in v.filename if ch.isalnum() or ch in "._- ")[:100] or "document"
    return Response(
        get_store().get(v.file_key),
        media_type=v.mime,
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.get("/{document_id}/diff")
def version_diff(
    document_id: uuid.UUID, db: DB, user: Viewer, base: int = Query(ge=1), other: int = Query(ge=1)
) -> dict[str, Any]:
    """Line diff between two versions' extracted text (deterministic)."""
    doc = service.get_document(db, user.id, document_id)
    va, vb = _version_of(db, doc, base), _version_of(db, doc, other)
    ta = "\n".join(p.text for p in db.scalars(select(Page).where(Page.version_id == va.id).order_by(Page.page_no)))
    tb = "\n".join(p.text for p in db.scalars(select(Page).where(Page.version_id == vb.id).order_by(Page.page_no)))
    diff = list(difflib.unified_diff(ta.splitlines(), tb.splitlines(), f"v{base}", f"v{other}", lineterm="", n=1))
    return {
        "base": base,
        "other": other,
        "similarity": round(difflib.SequenceMatcher(None, ta, tb).ratio(), 4),
        "added": sum(1 for d in diff if d.startswith("+") and not d.startswith("+++")),
        "removed": sum(1 for d in diff if d.startswith("-") and not d.startswith("---")),
        "diff": diff[:500],
    }


class ShareIn(BaseModel):
    email: EmailStr
    permission: Literal["read", "edit"] = "read"


@router.put("/{document_id}/shares", response_model=list[dict[str, Any]])
def set_shares(document_id: uuid.UUID, body: list[ShareIn], db: DB, user: Member) -> list[dict[str, Any]]:
    """Replace the share list (owner only)."""
    doc = service.get_document(db, user.id, document_id, need="owner")
    if len(body) > 100:
        raise UnprocessableData("At most 100 shares per document")
    db.query(DocumentShare).filter(DocumentShare.document_id == doc.id).delete()
    out = []
    for s in body:
        target = db.scalar(select(User).where(User.email == s.email.lower(), User.is_active))
        if target is None:
            raise NotFound(f"No active user with e-mail {s.email}")
        if target.id == doc.owner_id:
            raise Conflict("The owner already has full access")
        db.add(DocumentShare(document_id=doc.id, user_id=target.id, permission=s.permission))
        out.append({"user_id": str(target.id), "email": target.email, "permission": s.permission})
    audit.record(db, user.id, "document.share", "document", str(doc.id), shares=[o["email"] for o in out])
    db.commit()
    return out


@router.get("/{document_id}/chunks")
def list_chunks(
    document_id: uuid.UUID, db: DB, user: CurrentUser, limit: int = Query(default=50, le=500)
) -> list[dict[str, Any]]:
    """Debug/inspection view of how a document was chunked."""
    doc = service.get_document(db, user.id, document_id)
    rows = db.scalars(
        select(Chunk).where(Chunk.version_id == doc.current_version_id).order_by(Chunk.ordinal).limit(limit)
    )
    return [
        {"id": c.id, "page_no": c.page_no, "char_start": c.char_start, "char_end": c.char_end, "text": c.text}
        for c in rows
    ]
