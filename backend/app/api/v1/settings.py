import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.app_setting import AppSetting
from app.schemas.settings import (ScoringWeights, ImportMatchingSettings, OllamaSettings,
                                  CleanupSettings, SyncSettings, NotificationSettings)

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


@router.get("/notifications", response_model=NotificationSettings)
def get_notifications(db: Session = Depends(get_db)):
    return _get_json_setting(db, "notifications", NotificationSettings)


@router.put("/notifications", response_model=NotificationSettings)
def update_notifications(body: NotificationSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "notifications", body)
    return body


@router.post("/notifications/test")
async def test_notification(db: Session = Depends(get_db)):
    from app.services import notifier
    ok = await notifier.notify(db, "Powarr test", "Notifications are working 🎉", tags="tada")
    return {"ok": ok, "message": "Sent" if ok else "Failed — check URL/topic and that notifications are enabled"}


@router.post("/ollama/refine-prompt")
async def refine_prompt(body: dict = Body(...), db: Session = Depends(get_db)):
    """Have the configured LLM clean up a rough prompt draft into a solid template."""
    from app.services import llm_assist
    cfg = _get_json_setting(db, "ollama", OllamaSettings)
    if not (cfg.enabled and cfg.host and cfg.model):
        raise HTTPException(status_code=400, detail="LLM assist is not enabled — configure it on the Integrations page")
    draft = (body.get("draft") or "").strip()
    task = body.get("task") or "match"
    if not draft:
        raise HTTPException(status_code=400, detail="Draft text required")
    refined = await llm_assist.refine_prompt(cfg.host, cfg.model, draft, task, cfg.api_style)
    if not refined:
        raise HTTPException(status_code=502, detail="No response from the LLM — try again or check the host")
    return {"refined": refined}
