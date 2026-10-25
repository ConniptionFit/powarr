"""Saved filter rules ("unwatched 18 months and larger than 20 GB").

Rules are deliberately *advisory*: they select media and report how many items
and how many bytes match, and can be exported, but they never delete, protect
or re-score anything. Scoring and deletion are confirmation-gated surfaces in
Powarr, so an automation that acts on its own is a separate decision for the
owner; this gives the same expressiveness without that risk.

Everything is evaluated in SQL against the synced media table. Conditions are
validated against a whitelist of fields and operators, so a rule can never
smuggle in arbitrary SQL.
"""
import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.media import MediaItem

SETTING_KEY = "media_rules"
MAX_RULES = 50
MAX_CONDITIONS = 12
GB = 1024 ** 3

NUM_OPS = ("gt", "gte", "lt", "lte", "eq", "ne")
STR_OPS = ("eq", "ne", "contains")
BOOL_OPS = ("eq",)

# field -> (kind, label). The label doubles as the UI's source of truth.
FIELDS: dict[str, tuple[str, str]] = {
    "media_type": ("str", "Type"),
    "library_section": ("str", "Library"),
    "title": ("str", "Title"),
    "parent_title": ("str", "Show / artist"),
    "size_gb": ("num", "Size (GB)"),
    "days_unwatched": ("num", "Days since last watched"),
    "days_in_library": ("num", "Days in library"),
    "watch_count": ("num", "Watch count"),
    "year": ("num", "Year"),
    "score": ("num", "Deletion score"),
    "protected": ("bool", "Protected"),
}
OPS_BY_KIND = {"num": NUM_OPS, "str": STR_OPS, "bool": BOOL_OPS}


class RuleError(ValueError):
    pass


def _clean_condition(c: Any) -> dict:
    if not isinstance(c, dict):
        raise RuleError("condition must be an object")
    field, op, value = c.get("field"), c.get("op"), c.get("value")
    if field not in FIELDS:
        raise RuleError(f"unknown field: {field!r}")
    kind = FIELDS[field][0]
    if op not in OPS_BY_KIND[kind]:
        raise RuleError(f"operator {op!r} not allowed for {field}")
    if kind == "num":
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise RuleError(f"{field} needs a number")
    elif kind == "bool":
        if not isinstance(value, bool):
            raise RuleError(f"{field} needs true or false")
    else:
        value = str(value if value is not None else "").strip()
        if not value:
            raise RuleError(f"{field} needs a value")
        if len(value) > 200:
            raise RuleError(f"{field} value too long")
    return {"field": field, "op": op, "value": value}


def clean_rule(raw: Any, existing_ids: Optional[set] = None) -> dict:
    if not isinstance(raw, dict):
        raise RuleError("rule must be an object")
    name = str(raw.get("name") or "").strip()[:80]
    if not name:
        raise RuleError("rule needs a name")
    match = raw.get("match", "all")
    if match not in ("all", "any"):
        raise RuleError("match must be 'all' or 'any'")
    conds = raw.get("conditions")
    if not isinstance(conds, list) or not conds:
        raise RuleError("rule needs at least one condition")
    if len(conds) > MAX_CONDITIONS:
        raise RuleError(f"at most {MAX_CONDITIONS} conditions")
    rid = str(raw.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", rid):
        rid = f"r{int(datetime.utcnow().timestamp() * 1000)}"
    return {"id": rid, "name": name, "match": match,
            "conditions": [_clean_condition(c) for c in conds]}


def load_rules(db: Session) -> list[dict]:
    row = db.query(AppSetting).filter_by(key=SETTING_KEY).first()
    if not row or not row.value:
        return []
    try:
        data = json.loads(row.value)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def save_rules(db: Session, rules: list) -> list[dict]:
    if not isinstance(rules, list):
        raise RuleError("rules must be a list")
    if len(rules) > MAX_RULES:
        raise RuleError(f"at most {MAX_RULES} rules")
    cleaned, seen = [], set()
    for r in rules:
        c = clean_rule(r)
        if c["id"] in seen:
            c["id"] = f"{c['id']}-{len(seen)}"
        seen.add(c["id"])
        cleaned.append(c)
    row = db.query(AppSetting).filter_by(key=SETTING_KEY).first()
    payload = json.dumps(cleaned)
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key=SETTING_KEY, value=payload))
    db.commit()
    return cleaned


def _days_ago(n: float) -> datetime:
    return datetime.utcnow() - timedelta(days=n)


def _expr(cond: dict):
    f, op, v = cond["field"], cond["op"], cond["value"]
    # Numeric fields are expressed as a column or a derived expression, then
    # compared by one shared operator table. "Days" fields are inverted into
    # date comparisons so the index on the date column stays usable.
    if f in ("days_unwatched", "days_in_library"):
        col = (func.coalesce(MediaItem.last_watched_at, MediaItem.added_at)
               if f == "days_unwatched" else MediaItem.added_at)
        cutoff = _days_ago(v)
        # N days or more ago <=> timestamp <= cutoff; rows with no date at all
        # never match (we cannot claim they are old).
        in_day = and_(col <= cutoff, col > _days_ago(v + 1))
        flipped = {"gt": col < cutoff, "gte": col <= cutoff,
                   "lt": col > cutoff, "lte": col >= cutoff,
                   "eq": in_day, "ne": ~in_day}
        return and_(col.isnot(None), flipped[op])
    if f == "size_gb":
        col, v = MediaItem.file_size, v * GB
    elif f == "watch_count":
        col = func.coalesce(MediaItem.watch_count, 0)
    elif f == "year":
        col = MediaItem.year
    elif f == "score":
        col = func.coalesce(MediaItem.score, 0.0)
    elif f == "protected":
        col = func.coalesce(MediaItem.protected, False)
        return col.is_(True) if v else col.is_(False)
    else:
        col = getattr(MediaItem, f)
        if op == "contains":
            return col.ilike(f"%{v.replace('%', '').replace('_', '')}%")
        if op == "eq":
            return func.lower(col) == v.lower()
        return func.lower(col) != v.lower()
    return {"gt": col > v, "gte": col >= v, "lt": col < v, "lte": col <= v,
            "eq": col == v, "ne": col != v}[op]


def rule_filter(rule: dict):
    exprs = [_expr(c) for c in rule["conditions"]]
    return and_(*exprs) if rule["match"] == "all" else or_(*exprs)


def _base(db: Session, rule: dict):
    # Active leaf-or-container rows only; soft-deleted items are already gone
    # as far as the user is concerned.
    return db.query(MediaItem).filter(MediaItem.pending_delete_at.is_(None),
                                      rule_filter(rule))


def preview_rule(db: Session, rule: dict, sample: int = 25) -> dict:
    q = _base(db, rule)
    count, total = q.with_entities(
        func.count(MediaItem.id), func.coalesce(func.sum(MediaItem.file_size), 0)).one()
    rows = (q.order_by(MediaItem.file_size.desc().nullslast(), MediaItem.id)
            .limit(sample).all())
    return {
        "count": int(count), "total_bytes": int(total),
        "sample": [{"id": r.id, "title": r.title, "parent_title": r.parent_title,
                    "media_type": r.media_type, "library_section": r.library_section,
                    "file_size": r.file_size or 0,
                    "last_watched_at": r.last_watched_at.isoformat() if r.last_watched_at else None,
                    "added_at": r.added_at.isoformat() if r.added_at else None,
                    "watch_count": r.watch_count or 0} for r in rows],
    }


def matching_items(db: Session, rule: dict, limit: int = 20000):
    return (_base(db, rule).order_by(MediaItem.file_size.desc().nullslast(), MediaItem.id)
            .limit(limit).all())
