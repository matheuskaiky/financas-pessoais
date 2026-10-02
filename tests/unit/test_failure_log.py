"""The failure log: useful for finding broken features, never a leak of real data (rule 1)."""

import json
from pathlib import Path

import pytest

from financas.infrastructure import failure_log
from financas.infrastructure.failure_log import FailureLog, describe_exception


def test_record_and_recent_newest_first(tmp_path: Path) -> None:
    log = FailureLog(tmp_path / "logs")
    first = log.record("server", "server_error", method="GET", path="/cards", status=500)
    second = log.record("server", "http_error", method="GET", path="/nope", status=404)
    entries = log.recent()
    assert [e["id"] for e in entries] == [second, first]
    assert entries[1]["path"] == "/cards" and entries[1]["status"] == 500
    assert entries[0]["kind"] == "http_error" and "ts" in entries[0]
    assert log.path.read_text(encoding="utf-8").count("\n") == 2  # one JSON object per line


def test_exception_summary_never_carries_the_message() -> None:
    try:
        try:
            raise KeyError("saldo 1.234,56 de Café São João")
        except KeyError as inner:
            raise RuntimeError("falhou ao gravar 99,90") from inner
    except RuntimeError as error:
        info = describe_exception(error)
    text = json.dumps(info, ensure_ascii=False)
    assert info["error"] == "RuntimeError" and info["cause"] == "KeyError"
    assert "1.234,56" not in text and "99,90" not in text and "Café" not in text
    assert info["where"] and "test_failure_log.py" in str(info["where"][-1])  # type: ignore[index]


def test_domain_error_code_is_kept() -> None:
    from financas.domain.errors import DomainError

    try:
        raise DomainError("AMOUNT_NOT_POSITIVE", amount=12345)
    except DomainError as error:
        info = describe_exception(error)
    assert info["code"] == "AMOUNT_NOT_POSITIVE"
    assert "12345" not in json.dumps(info)


def test_record_exception_writes_class_and_locations(tmp_path: Path) -> None:
    log = FailureLog(tmp_path)
    try:
        raise ValueError("segredo 123,45")
    except ValueError as error:
        failure_id = log.record_exception("cli", "cli_error", error, path="card")
    (entry,) = log.recent()
    assert entry["id"] == failure_id and entry["error"] == "ValueError"
    assert "segredo" not in log.path.read_text(encoding="utf-8")


def test_client_reports_are_whitelisted_clipped_and_rate_limited(tmp_path: Path) -> None:
    log = FailureLog(tmp_path)
    assert log.accept_client_report({"kind": "evil"}, "ua") is False
    assert log.accept_client_report({"nothing": 1}, "ua") is False
    ok = {
        "kind": "js_error",
        "message": "x" * 1000,
        "path": "/entries",
        "file": "/static/app.js",
        "line": 12,
        "valor": "R$ 500,00",  # an unknown field: dropped
    }
    assert log.accept_client_report(ok, "Mozilla " * 50) is True
    (entry,) = log.recent()
    assert len(str(entry["message"])) == 300 and "valor" not in entry
    assert entry["source"] == "client" and entry["line"] == 12
    assert len(str(entry["ua"])) <= 120
    accepted = sum(log.accept_client_report(ok, "ua") for _ in range(100))
    assert accepted == 29  # 30 a minute in total, one already used


def test_logging_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    log = FailureLog(blocker / "logs")  # the folder cannot be created
    assert log.record("server", "server_error")  # still returns a code
    assert log.recent() == []


def test_rotation_keeps_the_log_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(failure_log, "MAX_BYTES", 400)
    log = FailureLog(tmp_path)
    ids = [log.record("server", "server_error", path=f"/p{i}", status=500) for i in range(30)]
    assert (tmp_path / "failures.jsonl.1").exists()
    assert log.recent(5)[0]["id"] == ids[-1]
