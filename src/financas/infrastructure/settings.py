"""Configuration from the environment (prefix ``FINANCAS_``) and an optional ``.env`` file.

Relative paths (``data/``, the SQLite file) are resolved against the project root, never the
current directory: running ``financas`` from another folder must not create an empty database
there and make the real data look lost.
"""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_FROM_SOURCE = (PROJECT_ROOT / "pyproject.toml").is_file()
_BASE = PROJECT_ROOT if _FROM_SOURCE else Path.cwd()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINANCAS_", env_file=_BASE / ".env", extra="ignore"
    )

    db_url: str = "sqlite:///data/financas.db"
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    backup_warn_days: int = 7
    valuation_stale_days: int = 35
    fgc_limit_cents: int = 25_000_000  # confirm the current value at fgc.org.br

    @model_validator(mode="after")
    def _resolve_relative_paths(self) -> "Settings":
        if not self.data_dir.is_absolute():
            self.data_dir = _BASE / self.data_dir
        url = make_url(self.db_url)
        if (
            url.get_backend_name() == "sqlite"
            and url.database not in (None, "", ":memory:")
            and not Path(str(url.database)).is_absolute()
        ):
            self.db_url = url.set(database=str(_BASE / str(url.database))).render_as_string(
                hide_password=False
            )
        return self

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"
