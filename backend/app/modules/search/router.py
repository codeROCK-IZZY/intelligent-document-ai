import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DB, Viewer
from app.retrieval.search import search

router = APIRouter(prefix="/search", tags=["search"])


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    doc_type: str | None = None
    collection_id: uuid.UUID | None = None
    mode: Literal["hybrid", "keyword", "vector"] = "hybrid"
    limit: int = Field(default=10, ge=1, le=50)


class SearchHit(BaseModel):
    chunk_id: int
    document_id: uuid.UUID
    document_title: str
    doc_type: str | None
    page_no: int
    char_start: int
    char_end: int
    text: str
    score: float
    vector_similarity: float | None
    keyword_rank: float | None


@router.post("", response_model=list[SearchHit])
def run_search(body: SearchIn, db: DB, user: Viewer) -> list[SearchHit]:
    """Hybrid keyword + semantic search over documents the caller may read."""
    hits = search(
        db,
        user.id,
        body.query,
        limit=body.limit,
        document_ids=body.document_ids,
        doc_type=body.doc_type,
        collection_id=body.collection_id,
        mode=body.mode,
    )
    return [SearchHit(**{k: getattr(h, k) for k in SearchHit.model_fields}) for h in hits]
