import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from financas.domain.errors import DomainError
from financas.infrastructure.backup import last_backup_date, make_backup, sqlite_path
from financas.infrastructure.db.migrate import upgrade_to_head
from financas.infrastructure.images import FileImageStore
from financas.infrastructure.settings import Settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x02" * 16
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x03" * 16


def test_image_store_round_trip_names_files_by_id_only(tmp_path: Path) -> None:
    store = FileImageStore(tmp_path / "images")
    ids = {t: store.save(d) for t, d in {"png": PNG, "jpeg": JPEG, "webp": WEBP}.items()}
    assert store.open(ids["png"]) == (PNG, "png")
    assert store.open(ids["jpeg"]) == (JPEG, "jpeg")
    assert store.open(ids["webp"]) == (WEBP, "webp")
    names = sorted(p.name for p in (tmp_path / "images").iterdir())
    assert names == sorted(f"{ids['png']}.png {ids['jpeg']}.jpg {ids['webp']}.webp".split())


def test_image_store_validates_and_deletes(tmp_path: Path) -> None:
    store = FileImageStore(tmp_path / "images")
    with pytest.raises(DomainError) as exc:
        store.save(b"<svg/>")
    assert exc.value.code == "IMAGE_TYPE_NOT_ALLOWED"
    image_id = store.save(PNG)
    store.delete(image_id)
    assert store.open(image_id) is None
    store.delete(image_id)  # deleting twice is harmless


@pytest.mark.parametrize(
    "bad_id", ["../../etc/passwd", "*", "", "A" * 32, "a" * 31, "../" + "a" * 32]
)
def test_image_store_never_lets_a_bad_id_reach_the_filesystem(tmp_path: Path, bad_id: str) -> None:
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG)
    store = FileImageStore(tmp_path / "images")
    store.save(PNG)
    assert store.open(bad_id) is None
    store.delete(bad_id)
    assert secret.exists()


def test_backup_copies_database_and_images(tmp_path: Path) -> None:
    db = tmp_path / "data" / "financas.db"
    url = f"sqlite:///{db}"
    upgrade_to_head(url)
    images = tmp_path / "data" / "images"
    image_id = FileImageStore(images).save(PNG)
    now = dt.datetime(2026, 7, 25, 18, 30, 5)
    target = make_backup(url, images, tmp_path / "data" / "backups", now)
    assert target.name == "financas-20260725-183005"
    with sqlite3.connect(target / "financas.db") as copy:
        tables = {r[0] for r in copy.execute("select name from sqlite_master where type='table'")}
    assert {"transactions", "accounts", "alembic_version"} <= tables
    assert (target / "images" / f"{image_id}.png").read_bytes() == PNG


def test_backup_without_images_folder_and_needs_a_sqlite_file(tmp_path: Path) -> None:
    db = tmp_path / "f.db"
    url = f"sqlite:///{db}"
    upgrade_to_head(url)
    target = make_backup(url, tmp_path / "none", tmp_path / "b", dt.datetime(2026, 1, 2, 3, 4, 5))
    assert not (target / "images").exists()
    with pytest.raises(DomainError) as exc:
        sqlite_path("sqlite:///:memory:")
    assert exc.value.code == "BACKUP_NEEDS_SQLITE_FILE"
    with pytest.raises(DomainError):
        sqlite_path("postgresql://x/y")


def test_last_backup_date(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    assert last_backup_date(backups) is None
    for name in ("financas-20260101-000000", "financas-20260720-235959", "financas-bad", "other"):
        (backups / name).mkdir(parents=True)
    assert last_backup_date(backups) == dt.date(2026, 7, 20)


def test_settings_defaults_and_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    for key in ("DB_URL", "DATA_DIR", "HOST", "BACKUP_WARN_DAYS"):
        monkeypatch.delenv(f"FINANCAS_{key}", raising=False)
    settings = Settings()
    assert settings.db_url == "sqlite:///data/financas.db"
    assert settings.host == "127.0.0.1"
    assert settings.backup_warn_days == 7
    assert settings.fgc_limit_cents == 25_000_000
    monkeypatch.setenv("FINANCAS_DATA_DIR", "/x/data")
    monkeypatch.setenv("FINANCAS_BACKUP_WARN_DAYS", "3")
    custom = Settings()
    assert custom.images_dir == Path("/x/data/images")
    assert custom.backups_dir == Path("/x/data/backups")
    assert custom.backup_warn_days == 3
