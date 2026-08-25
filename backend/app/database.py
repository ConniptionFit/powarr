import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

logger = logging.getLogger(__name__)

connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app.models import media, integration, app_setting, failed_import, deletion_log  # noqa: F401
    from app.models import smart_playlist  # noqa: F401
    from app.models import artist_discovery  # noqa: F401
    from app.models import llm_match_log  # noqa: F401
    from app.models import artist_add_log  # noqa: F401
    from app.models import malformed_import_flag  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate():
    """Add new columns to existing tables without dropping data. New tables come from create_all."""
    inspector = inspect(engine)
    pending_by_table = {
        "media_items": {
            "parent_title": "VARCHAR",
            "protected": "BOOLEAN DEFAULT FALSE",
            "watch_protected": "BOOLEAN DEFAULT FALSE",
            "seeding_protected": "BOOLEAN DEFAULT FALSE",
            "progress_protected": "BOOLEAN DEFAULT FALSE",
            "pending_delete_at": "TIMESTAMP",
            "pending_delete_mode": "VARCHAR",
            "llm_rationale": "TEXT",
            "llm_rationale_at": "TIMESTAMP",
            "llm_rationale_key": "VARCHAR",
            "llm_second_opinion": "VARCHAR",
            "llm_second_opinion_at": "TIMESTAMP",
            "llm_second_opinion_key": "VARCHAR",
        },
        "failed_imports": {
            "verified": "BOOLEAN",
            "heuristic_confidence": "FLOAT",
            "pack_file_matches": "TEXT",
            "mapping_overrides": "TEXT",
            "quality_downgrade": "BOOLEAN",
            "partial_import": "BOOLEAN",
            "suspicious_files": "TEXT",
            "llm_agrees": "BOOLEAN",
            "still_in_queue": "BOOLEAN",
        },
        "integrations": {
            "username": "VARCHAR",
            "password": "VARCHAR",
        },
        "deletion_log": {},
        "smart_playlists": {
            "mood": "VARCHAR",
            "era": "VARCHAR",
            "track_count": "INTEGER DEFAULT 0",
            "last_generated_at": "TIMESTAMP",
            "last_run_message": "VARCHAR",
            "auto_add_override": "BOOLEAN",
            "max_tracks_override": "INTEGER",
            # v0.50.0 — set once when plex_playlist_id is first assigned, distinct
            # from created_at (draft-definition creation) and updated_at (any edit);
            # feeds the weekly digest's "playlists created" section.
            "plex_created_at": "TIMESTAMP",
            # SP-12 — True when genre_tag names a configured template (union of
            # several genres) rather than a single real genre.
            "is_template": "BOOLEAN DEFAULT FALSE",
        },
        "discovered_artists": {
            "image_url": "VARCHAR",
            "bio": "TEXT",
            "years_active": "VARCHAR",
            "seed_artist_names": "TEXT",
        },
    }
    with engine.connect() as conn:
        for table, pending in pending_by_table.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col, col_type in pending.items():
                if col not in existing:
                    if settings.is_sqlite:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                    else:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                    conn.commit()

    _ensure_indexes()


# PERF (v0.89.0) — media_items is by far the largest table (~160k rows in the
# live install) and shipped with indexes on id and plex_rating_key only, so
# every filtered read of it was a full scan. These cover the filter shapes the
# code actually issues (see the per-index notes). Deliberately NOT a blanket
# index-every-column: each index is write amplification on the Plex sync that
# rewrites this table wholesale, so each one below has to earn its place.
#
# Purely additive, like the column migrations above — an index is never a data
# change, and IF NOT EXISTS is supported by both PostgreSQL and SQLite.
# Whole-table aggregates (library health) still seq-scan by design; that is the
# correct plan for them and none of these is meant to serve it.
_INDEXES: dict[str, list[tuple[str, str]]] = {
    "media_items": [
        # media_type gates nearly every read. Highly selective for the
        # top-level types (movie/show/artist/album together are ~2% of rows),
        # which is exactly what the duplicate finder and per-type views scan.
        ("ix_media_items_media_type", "(media_type)"),
        # Track -> parent artist lookups (artist detail, playlist generation).
        ("ix_media_items_parent_title", "(parent_title)"),
        # Deletion suggestions and the score-sorted list views: filter by
        # media_type, order by score. This is the one index here with a real
        # write cost — scorer's age/decay factors are day-granular, so nearly
        # every row's score changes once a day, and an indexed score column
        # makes those sync updates non-HOT (a HOT update is only possible when
        # no indexed column changed). Kept anyway because the read side is
        # user-facing and the win is large: sorting 116k tracks by score goes
        # from a ~66ms seq-scan-and-sort to ~0.4ms, on every browse, against a
        # few extra seconds on a nightly background sync.
        ("ix_media_items_type_score", "(media_type, score)"),
        # The scheduler polls the soft-delete purge on a timer and it matches
        # almost nothing, so this stays cheap and answers from the index.
        ("ix_media_items_pending_delete", "(pending_delete_at)"),
    ],
}


def _ensure_indexes() -> None:
    """Create the perf indexes if absent.

    Failures are logged, never fatal: a missing index means a slow app, but a
    startup that aborts on one means no app at all — and this runs on every
    boot against a database the user may have altered by hand.
    """
    inspector = inspect(engine)
    with engine.connect() as conn:
        for table, specs in _INDEXES.items():
            if not inspector.has_table(table):
                continue
            existing = {ix["name"] for ix in inspector.get_indexes(table)}
            for name, cols in specs:
                if name in existing:
                    continue
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}"))
                    conn.commit()
                    logger.info("created index %s on %s", name, table)
                except Exception as exc:  # pragma: no cover - defensive
                    conn.rollback()
                    logger.warning("could not create index %s on %s: %s", name, table, exc)
