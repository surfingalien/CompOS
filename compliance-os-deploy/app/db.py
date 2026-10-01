"""Persistence: a typed object store (one table) plus an append-only telemetry table."""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import Column, DateTime, Index, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12].upper()
    return f"{prefix}-{raw}" if prefix else raw


def utcnow() -> datetime:
    # Stored naive-UTC so SQLite and Postgres compare consistently.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def sha256_json(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def ensure_sqlite_directory():
    if DATABASE_URL.startswith("sqlite:///"):
        directory = os.path.dirname(DATABASE_URL.split("sqlite:///", 1)[1]) or "."
        os.makedirs(directory, exist_ok=True)
        if not os.access(directory, os.W_OK):
            raise RuntimeError(
                f"SQLite directory '{os.path.abspath(directory)}' is not writable by uid {os.getuid()}. "
                "On Railway set RAILWAY_RUN_UID=0 for volumes, or use Postgres via DATABASE_URL.")


class Obj(Base):
    __tablename__ = "objects"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=True, index=True)
    type = Column(String, nullable=False, index=True)
    title = Column(String, nullable=True)
    status = Column(String, nullable=True)
    payload = Column(Text, default="{}")
    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Event(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=True)
    correlation_id = Column(String, nullable=True, index=True)
    flow = Column(String, nullable=False)
    step = Column(String, nullable=False)
    status = Column(String, nullable=False)
    name = Column(String, nullable=False)
    # "metadata" is reserved by SQLAlchemy's declarative base; keep the column
    # name but map it to a different attribute.
    meta = Column("metadata", Text, default="{}")
    created_at = Column(DateTime, default=utcnow, index=True)


Index("ix_objects_type_created", Obj.type, Obj.created_at)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_json(value, default=None):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def serialize(obj: Obj, exclude: tuple = ()):
    if obj is None:
        return None
    data = {
        "id": obj.id,
        "tenant_id": obj.tenant_id,
        "type": obj.type,
        "title": obj.title,
        "status": obj.status,
        "created_at": iso(obj.created_at),
        "updated_at": iso(obj.updated_at),
    }
    payload = parse_json(obj.payload, {})
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k not in data and k not in exclude:
                data[k] = v
    return data


def add_obj(db: Session, obj_type: str, title: str, status: str, payload: dict,
            tenant_id: Optional[str] = None, obj_id: Optional[str] = None) -> Obj:
    item = Obj(id=obj_id or new_id(), tenant_id=tenant_id, type=obj_type, title=title, status=status,
               payload=json.dumps(payload, default=str), created_at=utcnow(), updated_at=utcnow())
    db.add(item)
    return item


def get_obj(db: Session, obj_type: str, obj_id: str) -> Obj:
    item = db.get(Obj, obj_id) if obj_id else None
    if not item or item.type != obj_type:
        raise HTTPException(status_code=404, detail=f"{obj_type} {obj_id} not found")
    return item


def query_objs(db: Session, obj_type: str, limit: Optional[int] = None):
    q = db.query(Obj).filter(Obj.type == obj_type).order_by(Obj.created_at.desc())
    if limit:
        q = q.limit(limit)
    return q.all()


def list_objs(db: Session, obj_type: str, limit: Optional[int] = None, exclude: tuple = ()):
    return [serialize(x, exclude) for x in query_objs(db, obj_type, limit)]


def payload_of(item: Obj) -> dict:
    return parse_json(item.payload, {}) or {}


def update_obj(db: Session, item: Obj, payload: Optional[dict] = None, status: Optional[str] = None,
               title: Optional[str] = None, commit: bool = True):
    if status is not None:
        item.status = status
    if title is not None:
        item.title = title
    if payload is not None:
        item.payload = json.dumps(payload, default=str)
    item.updated_at = utcnow()
    if commit:
        db.commit()
        db.refresh(item)
    return serialize(item)


def patch_payload(db: Session, item: Obj, changes: dict, status: Optional[str] = None, commit: bool = True):
    data = payload_of(item)
    data.update(changes)
    return update_obj(db, item, payload=data, status=status, commit=commit)


def record_event(flow: str, step: str, status: str, name: Optional[str] = None,
                 tenant_id: Optional[str] = None, correlation_id: Optional[str] = None,
                 metadata: Optional[dict] = None):
    """Write telemetry in its own session so it never interferes with the caller's transaction."""
    db = SessionLocal()
    try:
        db.add(Event(id=new_id("EVT"), tenant_id=tenant_id, correlation_id=correlation_id or new_id("CORR"),
                     flow=flow, step=step, status=status, name=name or f"{flow}.{step}",
                     meta=json.dumps(metadata or {}, default=str), created_at=utcnow()))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def serialize_event(e: Event) -> dict:
    return {"id": e.id, "tenant_id": e.tenant_id, "correlation_id": e.correlation_id, "flow": e.flow,
            "step": e.step, "status": e.status, "name": e.name, "metadata": parse_json(e.meta, {}),
            "created_at": iso(e.created_at)}


def get_default_tenant(db: Session) -> str:
    tenant = db.query(Obj).filter(Obj.type == "tenant").order_by(Obj.created_at.asc()).first()
    if not tenant:
        tenant = add_obj(db, "tenant", "Default Tenant", "active", {"name": "Default Tenant"}, obj_id="TENANT-DEFAULT")
        db.commit()
        db.refresh(tenant)
    return tenant.id
