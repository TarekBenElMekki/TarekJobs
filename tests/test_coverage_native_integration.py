"""Disposable TN-0/native integration cases. CODE ONLY; NOT RUN in TN-1."""
import hashlib
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from src.coverage.adapters.http import FetchProblem, FetchResult
from src.coverage.adapters.keejob import KeejobAdapter
from src.coverage.adapters.tanitjobs import TanitjobsAdapter
from src.coverage.adapters.tunisietravail import TunisieTravailAdapter
from src.coverage.contracts import DetailResult, InventoryRecord, Scope
from src.coverage.record_outcomes import migrate_native_scratch
from src.coverage.service import CoverageService
from src.coverage.storage import Database, rows

FIXTURES = Path(__file__).parent / "fixtures" / "tn1"


def html(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class RecordedHttp:
    def __init__(self, responses):
        self.responses, self.calls = responses, []
    def fetch(self, url):
        self.calls.append(url)
        body = self.responses[url]
        if isinstance(body, Exception):
            raise body
        if callable(body):
            body = body()
        return FetchResult(url, url, 200, body, hashlib.sha256(body.encode()).hexdigest(),
                           len(body.encode()), "text/html", "utf-8",
                           "2026-10-05T12:00:00Z", "2026-10-05T12:00:01Z", 1)
    def close(self):
        pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Native integration must remain offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def core(tmp_path):
    path = tmp_path / "native.db"
    migrate_native_scratch(path)
    return CoverageService(Database(path), tmp_path / "artifacts")


def query(core, sql, params=()):
    with core.database.read() as connection:
        return rows(connection, sql, params)


def keejob():
    base = KeejobAdapter.listing_url
    detail = html("keejob_detail.html")
    responses = {base: html("keejob_listing.html"), base + "?page=2": html("keejob_final.html"),
        base + "910001/ingÃ©nieur/": detail,
        base + "910002/comptable/": detail.replace('"addressCountry":"TN"', '"addressCountry":"FR"'),
        base + "910003/technicien/": detail.replace("IngÃ©nieur mÃ©thodes", "Technicien Ã©lectrique").replace("Atelier Exemple", "Usine Test")}
    http = RecordedHttp(responses)
    return KeejobAdapter(http), http


def test_inventory_overlap_freeze_detail_resume_and_zero_new_pass(core):
    adapter, http = keejob()
    run = core.start(adapter, Scope(country="TN"))
    first = core.run(adapter, run, max_pages=1)
    assert first["status"] == "partial" and first["pages_processed"] == 1
    assert first["detail_pages_attempted"] == 0
    resumed = CoverageService(Database(core.database.path), core.store.root)
    frozen = resumed.run(adapter, run, max_details=0)
    assert frozen["pages_processed"] == 2 and frozen["detail_pages_attempted"] == 0
    assert http.calls[:3] == [adapter.listing_url, adapter.listing_url, adapter.listing_url + "?page=2"]
    overlap = query(core, "SELECT records_seen,records_new FROM coverage_overlap_observations")
    assert overlap == [{"records_seen": 2, "records_new": 0}]
    partly = resumed.run(adapter, run, max_details=1)
    assert partly["detail_pages_completed"] == 1
    completed = resumed.run(adapter, run)
    assert completed["status"] == "exhausted" and completed["canonical_new"] == 2
    assert http.calls.count(adapter.listing_url + "910001/ingÃ©nieur/") == 1
    receipt = resumed.receipt(run)
    assert receipt["accounted_for"] and len(receipt["record_outcomes"]) == 1
    assert receipt["terminal_evidence"][0]["evidence"]["expected_unique_total"] == 3
    assert len(query(core, "SELECT * FROM coverage_scoring_outbox")) == 2
    next_run = resumed.start(adapter, Scope(country="TN"))
    again = resumed.run(adapter, next_run)
    assert again["canonical_new"] == 0 and again["canonical_reused"] == 2
    assert len(query(core, "SELECT * FROM coverage_scoring_outbox")) == 2
    assert query(core, "PRAGMA foreign_key_check") == []


@pytest.mark.parametrize("problem", [
    FetchProblem("access_denied", {"status": 403}, "blocked"),
    FetchProblem("rate_limited", {"status": 429}, "blocked"),
    FetchProblem("human_verification_required", {"status": 200}, "manual_only"),
    FetchProblem("transient_retries_exhausted", {"attempts": 3}),
])
def test_fetch_error_never_exhausts(core, problem):
    adapter = TanitjobsAdapter(RecordedHttp({TanitjobsAdapter.listing_url: problem}))
    run = core.start(adapter)
    result = core.run(adapter, run)
    assert not result["exhausted"]
    assert result["status"] == (problem.terminal_state or "partial")
    assert result["canonical_new"] == 0


def test_second_page_access_stop_keeps_inventory_and_prevents_detail_fetch(core):
    adapter, http = keejob()
    http.responses[adapter.listing_url + "?page=2"] = FetchProblem("access_denied", {"status":403}, "blocked")
    result = core.run(adapter, core.start(adapter))
    assert not result["exhausted"]
    assert len(query(core, "SELECT * FROM coverage_inventory")) == 2
    assert result["detail_pages_attempted"] == 0
    assert core.receipt(result["run_id"])["terminal_evidence"][0]["state"] == "blocked"


@pytest.mark.parametrize("kind", ["repeated", "cycle", "malformed"])
def test_inventory_failures_do_not_publish_exhaustion(core, kind):
    adapter, http = keejob()
    if kind == "repeated":
        http.responses[adapter.listing_url + "?page=2"] = html("keejob_listing.html").replace('?page=2', '?page=3')
    elif kind == "cycle":
        http.responses[adapter.listing_url] = html("keejob_listing.html").replace('?page=2', '?page=1')
    else:
        http.responses[adapter.listing_url] = html("malformed.html")
    result = core.run(adapter, core.start(adapter))
    assert result["status"] == "partial" and not result["exhausted"]
    assert core.receipt(result["run_id"])["failure_evidence"]


def test_version_mismatch_does_not_fetch(core):
    adapter, http = keejob()
    run = core.start(adapter)
    core.run(adapter, run, max_pages=1)
    calls = len(http.calls)
    adapter.metadata = replace(adapter.metadata, adapter_version="incompatible")
    with pytest.raises(ValueError, match="mismatch"):
        core.run(adapter, run)
    assert len(http.calls) == calls


def test_travail_shared_article_url_never_merges_child_jobs(core):
    base = TunisieTravailAdapter.listing_url
    article = "https://www.tunisietravail.net/exemple-recrute-930001/"
    archive = '<article><h2><a href="' + article + '">Exemple recrute des collaborateurs</a></h2></article>'
    adapter = TunisieTravailAdapter(RecordedHttp({base:archive, article:html("travail_multi.html")}))
    result = core.run(adapter, core.start(adapter))
    assert result["status"] == "exhausted" and result["canonical_new"] == 2
    children = query(core, "SELECT canonical_job_id,source_record_key FROM coverage_provenance")
    assert len({r["canonical_job_id"] for r in children}) == 2
    assert len(query(core, "SELECT * FROM coverage_record_links")) == 2
    assert len(query(core, "SELECT * FROM coverage_nonunique_urls")) == 1
    assert query(core, "SELECT * FROM job_sources") == []  # Legacy unique URL cannot represent siblings.
    assert len(query(core, "SELECT * FROM coverage_scoring_outbox")) == 2
    assert query(core, "PRAGMA foreign_key_check") == []


def test_explicit_failed_detail_retry_preserves_success(core):
    adapter, http = keejob()
    bad = adapter.listing_url + "910003/technicien/"
    good = adapter.listing_url + "910001/ingÃ©nieur/"
    saved = http.responses[bad]
    http.responses[bad] = FetchProblem("transient_retries_exhausted", {"attempts":3})
    run = core.start(adapter)
    assert core.run(adapter, run)["status"] == "partial"
    before = len(http.calls)
    assert core.run(adapter, run)["status"] == "partial"
    assert len(http.calls) == before
    http.responses[bad] = saved
    assert core.run(adapter, run, retry_failed=True)["status"] == "exhausted"
    assert http.calls.count(good) == 1


def test_575_reference_shape_final_75_and_subsequent_zero_new(core):
    # Synthetic regression shape, not a fresh scan or historical state import.
    # Adapter parser identity/pagination are exercised separately above.
    from src.coverage.contracts import AdapterMetadata, Capabilities, InventoryPage, Partition, TerminalEvidence
    class ReferenceShape:
        metadata = AdapterMetadata("shape", "Synthetic shape", "fixture", "offline",
                                   "shape", "1", capabilities=Capabilities(detail_pages=True))
        def plan(self, scope):
            return (Partition(),)
        def inventory(self, scope, partition, checkpoint):
            offset = (checkpoint.cursor or {}).get("offset", 0)
            end = min(offset + 100, 575)
            records = tuple(InventoryRecord(str(i), f"https://example.org/job/{i}", str(i),
                {"title": f"Role {i}", "company": f"Employer {i}", "description":"Synthetic vacancy"}) for i in range(offset,end))
            terminal = TerminalEvidence("exhausted", "synthetic_final_short_batch", {"processed":575,"last_batch":75}) if end == 575 else None
            return InventoryPage(records, None if terminal else {"offset":end}, terminal)
        def detail(self, record):
            return DetailResult(record.values)
    adapter = ReferenceShape()
    run = core.start(adapter)
    first = core.run(adapter, run)
    assert first["status"] == "exhausted" and first["canonical_new"] == 575
    pages = query(core, "SELECT raw_records_seen FROM coverage_inventory_pages WHERE run_id=? ORDER BY page_number", (run,))
    assert [r["raw_records_seen"] for r in pages] == [100,100,100,100,100,75]
    again = core.run(adapter, core.start(adapter))
    assert again["canonical_new"] == 0 and again["canonical_reused"] == 575
