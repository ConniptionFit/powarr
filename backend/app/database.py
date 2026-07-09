from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

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
            "pending_delete_at": "TIMESTAMP",
            "llm_rationale": "TEXT",
            "llm_rationale_at": "TIMESTAMP",
            "llm_rationale_key": "VARCHAR",
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
