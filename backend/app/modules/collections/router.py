import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, Member, Viewer
from app.core.errors import Conflict, NotFound
from app.db.models import Collection, Document

router = APIRouter(prefix="/collections", tags=["collections"])


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    created_at: datetime
    document_count: int = 0


@router.get("", response_model=list[CollectionOut])
def list_collections(db: DB, user: Viewer) -> list[CollectionOut]:
    rows = db.execute(
        select(Collection, func.count(Document.id))
        .outerjoin(Document, Document.collection_id == Collection.id)
        .where(Collection.owner_id == user.id)
        .group_by(Collection.id)
        .order_by(Collection.name)
    ).all()
    return [CollectionOut(id=c.id, name=c.name, created_at=c.created_at, document_count=n) for c, n in rows]


@router.post("", response_model=CollectionOut, status_code=201)
def create_collection(body: CollectionIn, db: DB, user: Member) -> CollectionOut:
    c = Collection(owner_id=user.id, name=body.name.strip())
    db.add(c)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("You already have a collection with this name") from exc
    return CollectionOut(id=c.id, name=c.name, created_at=c.created_at)


@router.delete("/{collection_id}", status_code=204)
def delete_collection(collection_id: uuid.UUID, db: DB, user: Member) -> None:
    """Documents in the collection are kept (their collection is cleared)."""
    c = db.get(Collection, collection_id)
    if c is None or c.owner_id != user.id:
        raise NotFound("Collection not found")
    db.delete(c)
    db.commit()
