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
    holder_aliases: str = ""  # the user's own names, comma separated (spreadsheet import, 13.1)
    demo: bool = False  # demo mode: own database, images, logs and backups (``for_demo``)

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
        if self.demo and self.db_path != self.demo_db_path:
            # a demo run can never be pointed at another database, whatever the environment says
            raise ValueError("DEMO_DB_PATH_NOT_ISOLATED")
        return self

    @property
    def db_path(self) -> Path | None:
        """The SQLite file of ``db_url`` (``None`` for an in-memory or non-SQLite database)."""
        url = make_url(self.db_url)
        if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
            return None
        return Path(str(url.database))

    @property
    def demo_db_path(self) -> Path:
        return self.data_dir / "demo.db"

    def for_demo(self) -> "Settings":
        """The same settings on the demo database (``data/demo.db``) and its own folders.

        Refuses when the configured database already is the demo file: the demo must never share
        a file with the real data, and a real database named ``demo.db`` would be wiped by a reset.
        """
        if self.demo:
            return self
        if self.db_path == self.demo_db_path:
            raise ValueError("DEMO_DB_IS_THE_REAL_DB")
        return self.model_copy(update={"demo": True, "db_url": f"sqlite:///{self.demo_db_path}"})

    @property
    def images_dir(self) -> Path:
        return self.data_dir / ("demo_images" if self.demo else "images")

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / ("demo_logs" if self.demo else "logs")

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / ("demo_backups" if self.demo else "backups")

    @property
    def import_dir(self) -> Path:
        return self.data_dir / ("demo_import" if self.demo else "import")
