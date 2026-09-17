"""FI-10: nightly re-check of already-imported Sonarr pack grabs for
incomplete coverage that went unnoticed once the download left triage —
e.g. a double-segment/anthology pack (FI-02) or an anime absolute-numbered
batch (FI-08) that only ever imported half its claimed episodes, silently,
because nothing was still watching it once Sonarr's queue moved on.
Notify-and-triage only: this never rewrites the library. Pairs with FI-09's
Recent Downloads browse/re-import — flags surface there for the user to
review and force a re-import if they choose to.

Reuses the exact same in-scope episode logic FI-02/FI-08 already use
(import_matcher.pack_scope_episodes) so this audit can never disagree with
the live scorer about what a pack claims to cover. The one difference:
live scoring checks the manual-import preview against the download's own
files (it may still be in the download client); this checks Sonarr's
current `hasFile` flag on each in-scope episode, since by the time this
runs the original download may no longer be resolvable there at all."""
from datetime import datetime, timedelta

from app.models.integration import Integration
from app.models.malformed_import_flag import MalformedImportFlag
from app.services.import_matcher import APP_FIELDS, _get_client, _parse_release_numbers, pack_scope_episodes


def _pack_label(parsed: dict) -> str:
    if parsed["absolute_range"]:
        lo, hi = parsed["absolute_range"]
        return f"{lo}-{hi} (absolute)"
    if parsed["complete"] and not parsed["pack_seasons"]:
        return "complete series"
    seasons = parsed["pack_seasons"] or set()
    if not seasons:
        return "complete series"
    lo, hi = min(seasons), max(seasons)
    return f"S{lo:02d}" if lo == hi else f"S{lo:02d}-S{hi:02d}"


async def run_malformed_import_audit(db, lookback_days: int, threshold: float) -> dict:
    """Returns {"checked": int, "flagged": int, "new_flags": list[MalformedImportFlag]}.
    Sonarr-only — pack coverage scoring only exists for Sonarr's season/
    absolute numbering today (see APP_FIELDS)."""
    row = db.query(Integration).filter_by(name="sonarr", enabled=True).first()
    if not row:
        return {"checked": 0, "flagged": 0, "new_flags": []}
    client = _get_client("sonarr", row)
    id_key, lib_method, title_key = APP_FIELDS["sonarr"]

    try:
        history = await client.get_history(event_type=1, max_records=500)
    except Exception:
        return {"checked": 0, "flagged": 0, "new_flags": []}

    try:
        queue = await client.get_queue()
        queued_ids = {q.get("downloadId") for q in queue if q.get("downloadId")}
    except Exception:
        queued_ids = set()

    try:
        library = await getattr(client, lib_method)()
        lib_by_id = {item["id"]: item for item in library}
    except Exception:
        lib_by_id = {}

    already_flagged = {
        f.download_id for f in db.query(MalformedImportFlag).filter_by(source_app="sonarr").all()
    }

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    checked = 0
    new_flags: list[MalformedImportFlag] = []
    seen_download_ids: set[str] = set()
    episodes_cache: dict[int, list[dict]] = {}

    for h in history:
        download_id = h.get("downloadId")
        series_id = h.get(id_key)
        source_title = h.get("sourceTitle") or ""
        if not download_id or not series_id or download_id in seen_download_ids:
            continue
        seen_download_ids.add(download_id)
        # Still active (in queue or already flagged/dismissed once) — this
        # audit only cares about grabs that quietly settled as "done".
        if download_id in queued_ids or download_id in already_flagged:
            continue

        try:
            event_date = datetime.fromisoformat(
                str(h.get("date") or "").replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            continue
        if event_date < cutoff:
            continue

        parsed = _parse_release_numbers(source_title)
        if parsed["episode"] is not None:
            continue  # single-episode grab — nothing to audit
        if not (parsed["pack_seasons"] or parsed["complete"] or parsed["absolute_range"]):
            continue  # not a pack at all

        checked += 1
        if series_id not in episodes_cache:
            try:
                episodes_cache[series_id] = await client.get_episodes(series_id)
            except Exception:
                continue
        in_scope = pack_scope_episodes(episodes_cache[series_id], parsed["pack_seasons"],
                                       parsed["absolute_range"])
        total = len(in_scope)
        if total == 0:
            continue
        mapped = sum(1 for e in in_scope if e.get("hasFile"))
        ratio = mapped / total
        if ratio >= threshold:
            continue

        matched_title = (lib_by_id.get(series_id) or {}).get(title_key)
        flag = MalformedImportFlag(
            source_app="sonarr", matched_id=series_id, matched_title=matched_title,
            download_id=download_id, source_title=source_title, pack_label=_pack_label(parsed),
            mapped_episodes=mapped, total_episodes=total, coverage_ratio=round(ratio, 3),
        )
        db.add(flag)
        new_flags.append(flag)

    if new_flags:
        db.commit()
    return {"checked": checked, "flagged": len(new_flags), "new_flags": new_flags}
