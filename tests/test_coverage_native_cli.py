"""Offline CLI wiring tests; no live acquisition is permitted."""
import json

import pytest

from src.coverage.__main__ import main


def test_info_and_dry_run_never_construct_http_or_database(tmp_path, monkeypatch, capsys):
    from src.coverage import native_cli
    def forbidden(*a, **kw):
        raise AssertionError("Metadata/dry-run must not acquire data")
    monkeypatch.setattr(native_cli, "create", forbidden)
    db = str(tmp_path / "absent.db")
    assert main(["--db",db,"adapter-list"]) == 0
    assert {i["metadata"]["source_id"] for i in json.loads(capsys.readouterr().out)} == {
        "tanitjobs_tn", "keejob_tn", "tunisietravail_tn"}
    assert main(["--db",db,"start-source","--adapter","keejob","--artifacts",str(tmp_path/"art"),"--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"]
    assert not (tmp_path/"absent.db").exists() and not (tmp_path/"art").exists()


def test_typo_and_missing_db_are_errors():
    with pytest.raises(SystemExit):
        main(["adapter-list"])
    with pytest.raises(SystemExit):
        main(["--db","explicit.db","adapter-info","--adapter","keejobs"])


def test_diagnostics_reject_production_before_fetch(tmp_path):
    from src.coverage.diagnostics import diagnose
    from src.coverage.storage import ROOT
    with pytest.raises(PermissionError):
        diagnose("keejob", ROOT/"data/jobs.db", tmp_path/"artifacts")
