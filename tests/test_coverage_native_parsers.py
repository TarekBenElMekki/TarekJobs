"""Pure deterministic parser coverage. These TN-1 tests have NOT BEEN RUN."""
from pathlib import Path

import pytest

from src.coverage.adapters import keejob_parsing as port
from src.coverage.adapters.keejob import KeejobAdapter
from src.coverage.adapters.tanitjobs import TanitjobsAdapter
from src.coverage.adapters.tunisietravail import TunisieTravailAdapter
from src.coverage.adapters.parsing import iso_date, foreign_scope, work_mode
from src.coverage.contracts import InventoryRecord

FIXTURES = Path(__file__).parent / "fixtures" / "tn1"
AT = "2026-10-05T12:00:00+00:00"


def html(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def pure(cls):
    return object.__new__(cls)  # Pure parser tests cannot construct HTTP clients.


def test_keejob_ported_listing_identity_fields_and_final_short_page():
    adapter = pure(KeejobAdapter)
    listing = adapter.parse_listing(html("keejob_listing.html"), adapter.listing_url)
    assert [r.key for r in listing.records] == ["910001", "910002"]
    assert listing.next_url == adapter.listing_url + "?page=2"
    assert listing.reported_total == 3
    first = listing.records[0]
    assert first.external_id == "910001"
    assert first.values["company"] == "Atelier Exemple"
    assert first.values["work_mode"] == "hybrid"
    assert first.values["salary_min"] == 900
    assert first.values["posted_at"].startswith("2026-10-05")
    final = adapter.parse_listing(html("keejob_final.html"), listing.next_url)
    assert len(final.records) == 1 and final.next_url is None


def test_keejob_proven_next_validation_and_pages_beyond_historical_66():
    root = KeejobAdapter.listing_url
    assert port.next_listing_url('<a href="?page=67">Suivant</a>', root + "?page=66") == root + "?page=67"
    for href in ("?page=68", "?page=66", "?page=67&token=x", "?page=67&page=67",
                 "https://outside.example/?page=67", "/offres-emploi/metiers/it/?page=67"):
        assert port.next_listing_url(f'<a href="{href}">Suivant</a>', root + "?page=66") is None
    assert KeejobAdapter.external_id(root + "910001/ing%C3%A9nieur/?utm_source=x") == "910001"


def test_keejob_details_application_and_scope():
    adapter = pure(KeejobAdapter)
    record = adapter.parse_listing(html("keejob_listing.html"), adapter.listing_url).records[0]
    result = adapter.parse_detail(html("keejob_detail.html"), record.source_url, record, AT)
    assert result.values["company"] == "Atelier Exemple"
    assert "maÃ®trise du franÃ§ais" in result.values["description"]
    assert result.values["min_years"] == 2 and result.values["max_years"] == 5
    assert result.values["education_requirement"] == "IngÃ©nieur"
    assert result.values["country"] == "Tunisia"
    assert result.values["application_url"].endswith("/910001/postuler/")
    assert result.values["_application"]["requires_login"] is True
    foreign = html("keejob_detail.html").replace('"addressCountry":"TN"', '"addressCountry":"FR"')
    assert adapter.parse_detail(foreign, record.source_url, record, AT).disposition == "excluded"


def test_tanit_contract_and_explicit_external_apply():
    adapter = pure(TanitjobsAdapter)
    listing = adapter.parse_listing(html("tanit_listing.html"), adapter.listing_url)
    record = listing.records[0]
    assert record.key == "920001"
    result = adapter.parse_detail(html("tanit_detail.html"), record.source_url, record, AT)
    assert result.values["application_url"] == "https://careers.example.org/jobs/abc/apply"
    assert result.values["_application"]["kind"] == "explicit_external"


def travail_record():
    return InventoryRecord("930001", "https://www.tunisietravail.net/exemple-recrute-930001/",
                           "930001", {"title": "Exemple recrute des collaborateurs"})


def test_travail_archive_does_not_invent_day_and_observes_pagination():
    adapter = pure(TunisieTravailAdapter)
    listing = adapter.parse_listing(html("travail_archive.html"), adapter.listing_url)
    assert listing.next_url.endswith("/page/2/") and listing.last_page == 3
    assert listing.records[0].key == "930001"
    assert "posted_at" not in listing.records[0].values
    assert iso_date("Oct,2026") is None


def test_travail_separable_positions_have_stable_distinct_keys_shared_url_and_apply():
    adapter, record = pure(TunisieTravailAdapter), travail_record()
    result = adapter.parse_detail(html("travail_multi.html"), record.source_url, record, AT)
    assert result.disposition == "expanded" and len(result.emissions) == 2
    keys = [e.record.key for e in result.emissions]
    assert len(set(keys)) == 2 and all(k.startswith("930001:position:") for k in keys)
    for emission in result.emissions:
        assert emission.record.source_url == record.source_url
        assert emission.record.values["_source_url_is_identity"] is False
        assert emission.record.values["_parent_article_url"] == record.source_url
        assert emission.record.values["_application"]["emails"] == ["recrutement@example.org"]
        assert emission.record.values["application_url"].endswith("post_id=930001")
    again = adapter.parse_detail(html("travail_multi.html"), record.source_url, record, AT)
    assert [e.record.key for e in again.emissions] == keys


def test_travail_explicit_campaign_links_queue_individual_details():
    adapter, record = pure(TunisieTravailAdapter), travail_record()
    result = adapter.parse_detail(html("travail_links.html"), record.source_url, record, AT)
    assert result.disposition == "expanded"
    assert {e.record.key for e in result.emissions} == {"930002", "930003"}
    assert all(e.requires_detail for e in result.emissions)


def test_travail_generic_headings_preserve_article_and_unlinked_email():
    adapter, record = pure(TunisieTravailAdapter), travail_record()
    result = adapter.parse_detail(html("travail_single.html"), record.source_url, record, AT)
    assert result.disposition == "record"
    assert result.values["application_url"] == record.source_url
    assert result.values["_application"]["emails"] == ["recrutement@example.org"]


@pytest.mark.parametrize("title", ["Immigration au Canada", "Concours public", "ActualitÃ©s emploi"])
def test_travail_non_job_classification(title):
    adapter, record = pure(TunisieTravailAdapter), travail_record()
    record = InventoryRecord(record.key, record.source_url, record.external_id, {"title": title})
    body = f'<h1>{title}</h1><div class="PostContent">Informations gÃ©nÃ©rales.</div>'
    assert adapter.parse_detail(body, record.source_url, record, AT).disposition == "excluded"


@pytest.mark.parametrize("cls", [TanitjobsAdapter, KeejobAdapter, TunisieTravailAdapter])
def test_malformed_detail_is_not_a_job(cls):
    adapter = pure(cls)
    url = (adapter.listing_url + "910001/role/") if cls is KeejobAdapter else (
        "https://www.tanitjobs.com/job/920001/role/" if cls is TanitjobsAdapter else travail_record().source_url)
    with pytest.raises(ValueError):
        adapter.parse_detail(html("malformed.html"), url, InventoryRecord("x", url), AT)


def test_no_vague_remote_or_foreign_client_inference():
    assert work_mode("PossibilitÃ© de tÃ©lÃ©travail") == ""
    assert foreign_scope({"country": "Tunisia", "description": "Clients en France"}) is None
    assert foreign_scope({"title": "Responsable (France)"}) == "explicit_foreign_location"
    assert iso_date("5 octobre 2026").startswith("2026-10-05")
