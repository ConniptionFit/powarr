from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional


class Settings(BaseSettings):
    data_dir: str = "/config"
    log_level: str = "INFO"
    db_url: Optional[str] = None  # Set to postgresql://user:pass@host:5432/dbname to use Postgres

    @property
    def database_url(self) -> str:
        if self.db_url:
            return self.db_url
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir}/powarr.db"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    model_config = {"env_prefix": "POWARR_"}


settings = Settings()
