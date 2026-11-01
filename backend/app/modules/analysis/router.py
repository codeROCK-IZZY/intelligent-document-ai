"""Comparison, duplicate detection and structured exports. All deterministic."""

from __future__ import annotations

import csv
import difflib
import io
import uuid
from collections.abc import Iterator
from itertools import combinations
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.api.deps import DB, Viewer
from app.db.models import Document, DocumentVersion, Extraction, Page
from app.modules.documents import service as docs
from app.pipeline.understand import jaccard_estimate

router = APIRouter(tags=["analysis"])
NEAR_DUP_THRESHOLD = 0.8


class CompareIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=2, max_length=5)


def _full_text(db: DB, version_id: uuid.UUID | None) -> str:
    return "\n".join(
        p.text for p in db.scalars(select(Page).where(Page.version_id == version_id).order_by(Page.page_no))
    )


@router.post("/compare")
def compare(body: CompareIn, db: DB, user: Viewer) -> dict[str, Any]:
    """Pairwise text similarity, changed lines, and field-by-field differences for documents
    of the same extracted kind (e.g. two invoices or two contracts)."""
    documents = [docs.get_document(db, user.id, d) for d in body.document_ids]
    texts = {d.id: _full_text(db, d.current_version_id) for d in documents}
    fields = {
        d.id: db.scalar(
            select(Extraction).where(Extraction.version_id == d.current_version_id, Extraction.kind != "summary")
        )
        for d in documents
    }
    pairs = []
    for a, b in combinations(documents, 2):
        ta, tb = texts[a.id], texts[b.id]
        diff = [
            line
            for line in difflib.unified_diff(ta.splitlines(), tb.splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and line[:3] not in ("+++", "---")
        ]
        fa, fb = fields[a.id], fields[b.id]
        field_diff = None
        if fa and fb and fa.kind == fb.kind:
            keys = sorted(set(fa.data) | set(fb.data))
            field_diff = [
                {"field": k, "a": fa.data.get(k), "b": fb.data.get(k)}
                for k in keys
                if fa.data.get(k) != fb.data.get(k) and k != "line_items"
            ]
        pairs.append(
            {
                "a": {"id": str(a.id), "title": a.title},
                "b": {"id": str(b.id), "title": b.title},
                "text_similarity": round(difflib.SequenceMatcher(None, ta[:50_000], tb[:50_000]).ratio(), 4),
                "changed_lines": diff[:200],
                "field_differences": field_diff,
            }
        )
    return {
        "documents": [{"id": str(d.id), "title": d.title, "doc_type": d.doc_type} for d in documents],
        "pairs": pairs,
        "method": "deterministic text diff and extracted-field comparison",
    }


@router.get("/duplicates")
def duplicates(db: DB, user: Viewer) -> dict[str, Any]:
    """Exact duplicates (same SHA-256) and near-duplicates (MinHash Jaccard ≥ 0.8) among the
    current versions of documents the caller may read."""
    rows = db.execute(
        text("""SELECT d.id, d.title, v.sha256, m.signature
                FROM (SELECT DISTINCT document_id FROM readable_documents WHERE user_id = :u) r
                JOIN documents d ON d.id = r.document_id
                JOIN document_versions v ON v.id = d.current_version_id
                LEFT JOIN minhash_signatures m ON m.version_id = v.id"""),
        {"u": user.id},
    ).all()
    exact: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        exact.setdefault(r.sha256, []).append({"id": str(r.id), "title": r.title})
    near = []
    signed = [r for r in rows if r.signature is not None]
    # O(n²) pairwise comparison — fine for a personal library; LSH banding is the scale-up path.
    for a, b in combinations(signed[:2000], 2):
        if a.sha256 == b.sha256:
            continue
        j = jaccard_estimate(a.signature, b.signature)
        if j >= NEAR_DUP_THRESHOLD:
            near.append(
                {
                    "a": {"id": str(a.id), "title": a.title},
                    "b": {"id": str(b.id), "title": b.title},
                    "similarity": round(j, 3),
                }
            )
    return {
        "exact": [g for g in exact.values() if len(g) > 1],
        "near": sorted(near, key=lambda x: -x["similarity"]),
        "threshold": NEAR_DUP_THRESHOLD,
    }


def _csv(rows: Iterator[list[Any]], filename: str) -> StreamingResponse:
    def gen() -> Iterator[str]:
        buf = io.StringIO()
        w = csv.writer(buf)
        for row in rows:
            # Neutralise spreadsheet formula injection.
            w.writerow([f"'{v}" if isinstance(v, str) and v[:1] in "=+-@" else v for v in row])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    return StreamingResponse(
        gen(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/reports/extractions.csv")
def extraction_report(db: DB, user: Viewer, kind: Literal["invoice", "contract"] = Query(...)) -> StreamingResponse:
    stmt = (
        select(Document, Extraction)
        .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .join(Extraction, (Extraction.version_id == DocumentVersion.id) & (Extraction.kind == kind))
        .where(
            Document.id.in_(
                select(text("document_id")).select_from(text("readable_documents")).where(text("user_id = :u"))
            )
        )
        .order_by(Document.title)
    )
    rows = db.execute(stmt, {"u": user.id}).all()
    if kind == "invoice":
        cols = [
            "invoice_number",
            "vendor",
            "customer",
            "invoice_date",
            "due_date",
            "currency",
            "subtotal",
            "tax",
            "total",
        ]
    else:
        cols = ["parties", "effective_date", "term", "auto_renewal", "termination_notice_days", "governing_law"]

    def gen() -> Iterator[list[Any]]:
        yield ["document", *cols, "source", "valid", "missing_fields", "issues"]
        for doc, ex in rows:
            vals = [("; ".join(ex.data.get(c) or []) if c == "parties" else ex.data.get(c)) for c in cols]
            yield [
                doc.title,
                *vals,
                ex.source,
                ex.validation.get("valid"),
                "; ".join(ex.validation.get("missing_fields", [])),
                "; ".join(ex.validation.get("issues", [])),
            ]

    return _csv(gen(), f"{kind}-extractions.csv")
