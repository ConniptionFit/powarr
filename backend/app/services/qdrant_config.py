"""Shared Qdrant connection (v0.40.0) — single source of truth for every module
that talks to the `music_affinity_space` collection. Configured once on
Settings -> Integrations (QdrantCard); Smart Playlists and Artist Discovery both
load from here instead of keeping their own copy of the connection details."""
from __future__ import annotations

import json

from app.models.app_setting import AppSetting
from app.schemas.settings import QdrantSettings

_KEY = "qdrant"


def load_settings(db) -> QdrantSettings:
    row = db.query(AppSetting).filter_by(key=_KEY).first()
    if not row or not row.value:
        return QdrantSettings()
    return QdrantSettings(**json.loads(row.value))


def save_settings(db, cfg: QdrantSettings) -> None:
    row = db.query(AppSetting).filter_by(key=_KEY).first()
    if not row:
        row = AppSetting(key=_KEY)
        db.add(row)
    row.value = cfg.model_dump_json()
    db.commit()


def client(db):
    """Returns a configured QdrantIntegration, or None if no URL is set."""
    cfg = load_settings(db)
    if not cfg.url:
        return None
    from app.integrations.qdrant import QdrantIntegration
    from app.services.secret_box import decrypt
    return QdrantIntegration(cfg.url, decrypt(cfg.api_key) or "", cfg.collection)
