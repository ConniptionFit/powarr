import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.app_setting import AppSetting
from app.schemas.settings import (ScoringWeights, ImportMatchingSettings, OllamaSettings,
                                  CleanupSettings, SyncSettings)

router = APIRouter(prefix="/settings", tags=["settings"])


def _get_json_setting(db: Session, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


def _put_json_setting(db: Session, key: str, model) -> None:
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row:
        row = AppSetting(key=key)
        db.add(row)
    row.value = json.dumps(model.model_dump())
    db.commit()


def _get_weights(db: Session) -> ScoringWeights:
    row = db.query(AppSetting).filter_by(key="scoring_weights").first()
    if not row:
        return ScoringWeights()
    return ScoringWeights(**json.loads(row.value))


@router.get("/scoring", response_model=ScoringWeights)
def get_scoring_weights(db: Session = Depends(get_db)):
    return _get_weights(db)


@router.put("/scoring", response_model=ScoringWeights)
def update_scoring_weights(weights: ScoringWeights, db: Session = Depends(get_db)):
    row = db.query(AppSetting).filter_by(key="scoring_weights").first()
    if not row:
        row = AppSetting(key="scoring_weights")
        db.add(row)
    row.value = json.dumps(weights.model_dump())
    db.commit()

    from app.services.scorer import rescore_all
    rescore_all(db, weights)

    return weights


@router.get("/import-matching", response_model=ImportMatchingSettings)
def get_import_matching(db: Session = Depends(get_db)):
    return _get_json_setting(db, "import_matching", ImportMatchingSettings)


@router.put("/import-matching", response_model=ImportMatchingSettings)
def update_import_matching(body: ImportMatchingSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "import_matching", body)
    return body


@router.get("/ollama", response_model=OllamaSettings)
def get_ollama(db: Session = Depends(get_db)):
    return _get_json_setting(db, "ollama", OllamaSettings)


@router.put("/ollama", response_model=OllamaSettings)
def update_ollama(body: OllamaSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "ollama", body)
    return body


@router.get("/cleanup", response_model=CleanupSettings)
def get_cleanup(db: Session = Depends(get_db)):
    return _get_json_setting(db, "cleanup", CleanupSettings)


@router.put("/cleanup", response_model=CleanupSettings)
def update_cleanup(body: CleanupSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "cleanup", body)
    return body


@router.get("/sync", response_model=SyncSettings)
def get_sync(db: Session = Depends(get_db)):
    return _get_json_setting(db, "sync", SyncSettings)


@router.put("/sync", response_model=SyncSettings)
def update_sync(body: SyncSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "sync", body)
    return body
