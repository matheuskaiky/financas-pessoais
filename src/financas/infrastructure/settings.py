"""Configuration from the environment (prefix ``FINANCAS_``) and an optional ``.env`` file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FINANCAS_", env_file=".env", extra="ignore")

    db_url: str = "sqlite:///data/financas.db"
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    backup_warn_days: int = 7
    valuation_stale_days: int = 35
    fgc_limit_cents: int = 25_000_000  # confirm the current value at fgc.org.br

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"
