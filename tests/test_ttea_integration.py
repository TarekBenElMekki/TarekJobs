"""Offline integration coverage. All DB initialization is redirected to tmp_path.

The artifact fixture follows the inspected Job Hunter hardening validation run:
run.json plus ranked_jobs.json containing jobs/job/source_evidence.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from src import database
from src.database import (
    Base, JobORM, JobSourceORM, TteaAssessmentORM, TteaRunORM,
    get_engine, init_db, upsert_job, upsert_job_metadata_strong,
)
from src.models import JobRecord, ScoreResult
from src.ttea_integration import ImportRunError, import_run as _production_import_run, main


def _ensure_test_gate_table(engine):
    """Create the minimal active-pool gate surface for test databases."""
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """CREATE TABLE IF NOT EXISTS ttea_job_decisions (
                canonical_job_id INTEGER PRIMARY KEY,
                gate_decision TEXT NOT NULL
                    CHECK (gate_decision IN ('KEEP', 'ELIMINATE')),
                score INTEGER
            )"""
        )


def _mark_all_test_jobs_keep(engine):
    """Admit fixture jobs without weakening the production gate."""
    _ensure_test_gate_table(engine)

    with engine.begin() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                """
            )
        }

        if "ttea_assessments" in tables:
            rows = connection.exec_driver_sql(
                """
                SELECT
                    j.id,
                    COALESCE(
                        (
                            SELECT a.score
                            FROM ttea_assessments a
                            WHERE a.job_id=j.id
                            ORDER BY
                                a.scored_at DESC,
                                a.imported_at DESC,
                                a.id DESC
                            LIMIT 1
                        ),
                        j.overall_score,
                        0
                    )
                FROM jobs j
                """
            ).fetchall()
        else:
            rows = connection.exec_driver_sql(
                """
                SELECT
                    id,
                    COALESCE(overall_score, 0)
                FROM jobs
                """
            ).fetchall()

        for job_id, score in rows:
            connection.exec_driver_sql(
                """
                INSERT INTO ttea_job_decisions (
                    canonical_job_id,
                    gate_decision,
                    score
                )
                VALUES (?, 'KEEP', ?)
                ON CONFLICT(canonical_job_id)
                DO UPDATE SET
                    gate_decision='KEEP',
                    score=excluded.score
                """,
                (
                    int(job_id),
                    int(score or 0),
                ),
            )


def import_run(run_dir, engine):
    """Test wrapper around the production importer."""
    result = _production_import_run(
        run_dir,
        engine,
    )

    _mark_all_test_jobs_keep(
        engine
    )

    return result


@pytest.fixture
def db(tmp_path):
    engine = get_engine(str(tmp_path / "test.db"))
    init_db(engine)
    _ensure_test_gate_table(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def artifacts(tmp_path):
    directory = tmp_path / "run"
    directory.mkdir()
    manifest = {
        "run_id": "run_fixture", "status": "completed", "source": "keejob",
        "provider": "groq", "model": "fixture-model",
        "started_at": "2026-09-24T10:00:00+00:00",
        "completed_at": "2026-09-24T10:01:00+00:00",
        "counts": {"cards_discovered": 8, "cloud_scored": 1, "cache_hits": 8,
                   "source_requests_total": 0, "cloud_requests": 0, "candidate_name": "PRIVATE_NAME"},
        "usage": {"total_tokens": 42, "authorization": "PRIVATE_SECRET"},
        "candidate_profile": {"name": "PRIVATE_NAME"}, "api_key": "PRIVATE_SECRET",
        "quota": {"authorization": "PRIVATE_SECRET"},
    }
    entry = {
        "job": {"title": "Junior Software Engineer", "company": "Example Company", "location": "Tunis",
                "job_id": "job_fixture", "min_experience_years": 1.0,
                "qualifications": ["bac + 5"], "experience_level": None, "working_arrangement": None,
                "sources": [{"source_reference": "247250", "source_url": "https://example.test/jobs/1"}]},
        "source_url": "https://example.test/jobs/1",
        "source_evidence": {"description": "<p>Build Python software.</p><script>PRIVATE_SCRIPT</script>",
                            "contract": "CDI", "education": "Bac + 5", "experience": "Entre 1 et 2 ans",
                            "languages": "Français", "publication_date": "2026-09-20",
                            "source_reference": "247250", "raw_html": "PRIVATE_HTML"},
        "score": 78, "strengths": ["Python experience"], "gaps": ["SQL Server"],
        "explanation": "Relevant development experience.", "provider": "groq", "model": "fixture-model",
        "candidate_profile": {"name": "PRIVATE_NAME"}, "headers": {"authorization": "PRIVATE_SECRET"},
    }
    write_artifacts(directory, manifest, [entry])
    return directory, manifest, entry


def write_artifacts(directory, manifest, entries):
    (directory / "run.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (directory / "ranked_jobs.json").write_text(json.dumps({
        "jobs": entries, "metadata": {"run_id": manifest["run_id"]},
    }, ensure_ascii=False), encoding="utf-8")


def counts(engine):
    with Session(engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(model))
                     for model in (JobORM, JobSourceORM, TteaRunORM, TteaAssessmentORM))


def legacy_job(engine, **overrides):
    fields = dict(title="Junior Software Engineer", company="Example Company", location="Tunis",
                  source_name="legacy", source_url="https://example.test/jobs/1",
                  description="An existing description with considerably more useful detail than incoming evidence. " * 5,
                  min_years=2, max_years=5)
    fields.update(overrides)
    with Session(engine) as session, session.begin():
        job, _ = upsert_job(session, JobRecord(**fields), ScoreResult(
            technical=60, eligibility=70, location=80, experience=90, overall=61), False)
        job_id = int(job.id)

    _mark_all_test_jobs_keep(
        engine
    )

    return job_id


@pytest.fixture
def api(db, monkeypatch):
    # Inject the temporary engine before loading the private module; entering
    # TestClient then initializes that DB through the application's lifespan.
    monkeypatch.setattr(database, "get_engine", lambda _path: db)
    name = "src._ttea_ui_test"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "src" / "ui_server.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        yield client


def test_ui_import_leaves_schema_unchanged_until_lifespan(tmp_path, monkeypatch):
    engine = get_engine(str(tmp_path / "lifecycle.db"))
    try:
        legacy_tables = {"jobs", "job_sources"}
        Base.metadata.create_all(engine, tables=[JobORM.__table__, JobSourceORM.__table__])
        assert set(inspect(engine).get_table_names()) == legacy_tables

        monkeypatch.setattr(database, "get_engine", lambda _path: engine)
        name = "src._ttea_ui_lifecycle_test"
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "src" / "ui_server.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)

        assert module.engine is engine
        assert set(inspect(engine).get_table_names()) == legacy_tables

        with TestClient(module.app) as client:
            assert set(inspect(engine).get_table_names()) == legacy_tables | {
                "ttea_runs", "ttea_assessments",
            }
            assert client.get("/api/health").status_code == 200
    finally:
        engine.dispose()


def test_fresh_tables_and_unique_constraints(db):
    inspector = inspect(db)
    assert {"jobs", "job_sources", "ttea_runs", "ttea_assessments"} <= set(inspector.get_table_names())
    assert ["external_run_id"] in [c["column_names"] for c in inspector.get_unique_constraints("ttea_runs")]
    assert ["job_id", "run_id"] in [c["column_names"] for c in inspector.get_unique_constraints("ttea_assessments")]


def test_existing_schema_and_jobs_survive_additive_init(tmp_path):
    engine = get_engine(str(tmp_path / "old.db"))
    try:
        Base.metadata.create_all(engine, tables=[JobORM.__table__, JobSourceORM.__table__])
        with engine.begin() as connection:
            connection.execute(JobORM.__table__.insert().values(
                title="Historical job", norm_title="historical job", norm_company="", norm_location="",
                notes="Keep me", review_status="shortlisted", application_status="applied"))
        init_db(engine)
        init_db(engine)
        with Session(engine) as session:
            job = session.scalar(select(JobORM))
            assert (job.title, job.notes, job.review_status, job.application_status) == (
                "Historical job", "Keep me", "shortlisted", "applied")
            assert job.ttea_assessments == []
    finally:
        engine.dispose()


def test_valid_import_mapping_and_no_legacy_scores(db, artifacts):
    directory, _, _ = artifacts
    result = import_run(directory, db)
    assert result["jobs_read"] == result["new_jobs"] == result["assessments_added"] == 1
    assert counts(db) == (1, 1, 1, 1)
    with Session(db) as session:
        job = session.scalar(select(JobORM))
        assert job.title == "Junior Software Engineer"
        assert job.employment_type == "CDI"
        assert job.education_requirement == "Bac + 5"
        assert job.min_years == 1
        assert job.posted_at.date().isoformat() == "2026-09-20"
        assert "Build Python software." in job.description
        assert "Entre 1 et 2 ans" in job.description
        assert json.loads(job.required_languages_json) == ["Français"]
        assert job.overall_score == job.technical_score == job.location_score == 0
        assert job.sources[0].external_id == "247250"
        assert job.ttea_assessments[0].score == 78


@pytest.mark.parametrize("filename", ["run.json", "ranked_jobs.json"])
def test_malformed_json_rejected(db, artifacts, filename):
    directory, _, _ = artifacts
    (directory / filename).write_text('{"broken":', encoding="utf-8")
    with pytest.raises(ImportRunError, match=f"Malformed {filename}"):
        import_run(directory, db)
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.parametrize("filename", ["run.json", "ranked_jobs.json"])
def test_missing_file_rejected(db, artifacts, filename):
    directory, _, _ = artifacts
    (directory / filename).unlink()
    with pytest.raises(ImportRunError, match="Cannot read"):
        import_run(directory, db)
    assert counts(db) == (0, 0, 0, 0)


def test_reimport_is_idempotent(db, artifacts):
    directory, _, _ = artifacts
    import_run(directory, db)
    result = import_run(directory, db)
    assert counts(db) == (1, 1, 1, 1)
    assert result["runs_reused"] == result["existing_jobs"] == result["sources_reused"] == result["assessments_reused"] == 1
    assert result["runs_added"] == result["new_jobs"] == result["assessments_added"] == 0


@pytest.mark.parametrize("title,url,expected_sources", [
    ("Entirely different existing title", "https://example.test/jobs/1", 1),
    ("Junior Software Engineer", "https://example.test/other", 2),
    ("Junior Software Engineer Python", "https://example.test/fuzzy", 2),
])
def test_url_exact_and_fuzzy_dedup_preserved(db, artifacts, title, url, expected_sources):
    existing_id = legacy_job(db, title=title, source_url=url)
    import_run(artifacts[0], db)
    assert counts(db) == (1, expected_sources, 1, 1)
    with Session(db) as session:
        assert session.scalar(select(TteaAssessmentORM)).job_id == existing_id


def test_human_state_and_better_metadata_preserved(db, artifacts):
    job_id = legacy_job(db)
    human = dict(review_status="shortlisted", application_status="applied", notes="Call recruiter",
                 cv_version="cv-v2", cover_letter_version="letter-v1", response="Interview invited",
                 interview_stage="technical", application_date=datetime(2026, 9, 1))
    with Session(db) as session, session.begin():
        job = session.get(JobORM, job_id)
        for key, value in human.items():
            setattr(job, key, value)
        description = job.description
    import_run(artifacts[0], db)
    import_run(artifacts[0], db)
    with Session(db) as session:
        job = session.get(JobORM, job_id)
        assert {key: getattr(job, key) for key in human} == human
        assert (job.min_years, job.max_years, job.description) == (2, 5, description)
        assert job.overall_score == 61


def test_optional_fields_and_zero_score(db, artifacts):
    directory, manifest, entry = artifacts
    minimal = {"job": {"title": entry["job"]["title"], "sources": entry["job"]["sources"]}, "score": 0}
    write_artifacts(directory, {"run_id": manifest["run_id"], "status": "completed"}, [minimal])
    import_run(directory, db)
    with Session(db) as session:
        assessment = session.scalar(select(TteaAssessmentORM))
        assert assessment.score == 0
        assert assessment.explanation == ""
        assert json.loads(assessment.strengths_json) == []


def test_sensitive_artifact_fields_and_html_not_persisted(db, artifacts):
    import_run(artifacts[0], db)
    with db.connect() as connection:
        dump = "\n".join(connection.connection.driver_connection.iterdump())
    for forbidden in ("PRIVATE_NAME", "PRIVATE_SECRET", "PRIVATE_HTML", "PRIVATE_SCRIPT", "<p>", "<script>", "candidate_profile"):
        assert forbidden not in dump
    with Session(db) as session:
        payload = json.loads(session.scalar(select(JobSourceORM)).raw_payload_json)
        assert set(payload) == {"integration", "ttea_run_id", "source_job_id"}
        run = session.scalar(select(TteaRunORM))
        assert json.loads(run.usage_json) == {"total_tokens": 42}


def test_history_latest_score_and_legacy_rescore_safety(db, artifacts, api):
    directory, manifest, entry = artifacts
    import_run(directory, db)
    # Import an older run later: chronological assessment order must still win.
    manifest.update(run_id="run_older", completed_at="2026-09-23T10:01:00+00:00")
    entry["score"] = 10
    write_artifacts(directory, manifest, [entry])
    import_run(directory, db)
    assert counts(db) == (1, 1, 2, 2)
    with Session(db) as session, session.begin():
        job = session.scalar(select(JobORM))
        job_id = job.id
        job.overall_score = 99  # Same column changed by legacy profile rescoring.
    payload = api.get(f"/api/jobs/{job_id}").json()
    assert payload["score"] == {"value": 78, "origin": "ttea"}
    assert payload["scores"]["overall"] == 99
    assert payload["ttea_assessment"]["run_id"] == "run_fixture"
    assert payload["ttea_assessment"]["strengths"] == ["Python experience"]
    assert api.get("/api/jobs?min_score=80").json()["total"] == 0
    assert api.get("/api/jobs?min_score=70&sort=verdict").json()["total"] == 1
    runs = api.get("/api/ttea/runs").json()["items"]
    assert [run["run_id"] for run in runs] == ["run_older", "run_fixture"]
    assert runs[0]["counts"]["cache_hits"] == 8
    assert "candidate" not in json.dumps(runs)


def test_legacy_payload_and_application_patch(db, api):
    job_id = legacy_job(db)
    payload = api.get(f"/api/jobs/{job_id}").json()
    assert payload["score"] == {"value": 61, "origin": "legacy"}
    assert payload["ttea_assessment"] is None
    response = api.patch(f"/api/jobs/{job_id}", json={
        "application_status": "applied", "review_status": "shortlisted", "notes": "Follow up",
        "cv_version": "v2", "cover_letter_version": "v3", "response": "Pending", "interview_stage": "screen",
    })
    assert response.status_code == 200
    assert response.json()["application_date"]
    assert response.json()["notes"] == "Follow up"
    assert api.get("/api/applications").json()["items"][0]["id"] == job_id


def test_imported_application_patch_preserves_assessment(db, artifacts, api):
    import_run(artifacts[0], db)
    job_id = api.get("/api/jobs").json()["items"][0]["id"]
    response = api.patch(f"/api/jobs/{job_id}", json={"application_status": "applied", "notes": "Keep"})
    assert response.status_code == 200
    assert response.json()["score"]["origin"] == "ttea"
    import_run(artifacts[0], db)
    payload = api.get("/api/applications").json()["items"][0]
    assert payload["application_status"] == "applied"
    assert payload["notes"] == "Keep"


def test_transaction_rolls_back_on_conflicting_source_identity(db, artifacts):
    directory, manifest, entry = artifacts
    legacy_job(db, title="Unrelated vacancy", company="Other company", source_url="https://example.test/conflict")
    entry["job"]["sources"].append({"source_url": "https://example.test/conflict"})
    write_artifacts(directory, manifest, [entry])
    with pytest.raises(ImportRunError, match="different canonical jobs"):
        import_run(directory, db)
    assert counts(db) == (1, 1, 0, 0)


@pytest.mark.parametrize("score", [-1, 101, float("nan"), True, "78"])
def test_invalid_scores_rejected_atomically(db, artifacts, score):
    directory, manifest, entry = artifacts
    invalid = copy.deepcopy(entry)
    invalid["score"] = score
    write_artifacts(directory, manifest, [entry, invalid])
    with pytest.raises(ImportRunError, match="finite score"):
        import_run(directory, db)
    assert counts(db) == (0, 0, 0, 0)


def test_cli_explicit_temporary_database(artifacts, tmp_path, capsys):
    assert main(["import-run", "--run-dir", str(artifacts[0]), "--db", str(tmp_path / "cli.db")]) == 0
    assert json.loads(capsys.readouterr().out)["assessments_added"] == 1


def test_newer_run_replaces_display_but_preserves_history(db, artifacts, api):
    directory, manifest, entry = artifacts
    import_run(directory, db)
    manifest.update(run_id="run_newer", completed_at="2026-09-25T10:01:00+00:00")
    entry["score"] = 90
    write_artifacts(directory, manifest, [entry])
    import_run(directory, db)
    payload = api.get("/api/jobs?min_score=85").json()["items"][0]
    assert payload["score"] == {"value": 90, "origin": "ttea"}
    assert payload["ttea_assessment"]["run_id"] == "run_newer"
    assert counts(db) == (1, 1, 2, 2)


def test_multiple_sources_and_same_canonical_entry_are_idempotent(db, artifacts):
    directory, manifest, entry = artifacts
    entry["job"]["sources"].append({"source_url": "https://example.test/mirror/1", "source_reference": "mirror-1"})
    write_artifacts(directory, manifest, [entry, copy.deepcopy(entry)])
    result = import_run(directory, db)
    assert counts(db) == (1, 2, 1, 1)
    assert result["assessments_added"] == result["assessments_reused"] == 1
    import_run(directory, db)
    assert counts(db) == (1, 2, 1, 1)


@pytest.mark.parametrize("status", ["running", "failed", "interrupted", None])
def test_incomplete_runs_rejected(db, artifacts, status):
    directory, manifest, entry = artifacts
    manifest["status"] = status
    write_artifacts(directory, manifest, [entry])
    with pytest.raises(ImportRunError, match="Only completed"):
        import_run(directory, db)
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.parametrize("status", ["completed", "partial"])
@pytest.mark.parametrize("retained", [True, False])
def test_publishable_run_status_and_idempotency(db, artifacts, status, retained):
    directory, manifest, entry = artifacts
    manifest["status"] = status
    write_artifacts(directory, manifest, [entry] if retained else [])
    first = import_run(directory, db)
    second = import_run(directory, db)
    assert first["jobs_read"] == int(retained)
    assert counts(db) == (int(retained), int(retained), 1, int(retained))
    assert second["runs_reused"] == 1
    assert second["assessments_reused"] == int(retained)
    with Session(db) as session:
        assert session.scalar(select(TteaRunORM)).status == status


def test_invalid_partial_artifact_is_atomic(db, artifacts):
    directory, manifest, entry = artifacts
    manifest["status"] = "partial"
    entry["score"] = None
    write_artifacts(directory, manifest, [entry])
    with pytest.raises(ImportRunError, match="finite score"):
        import_run(directory, db)
    assert counts(db) == (0, 0, 0, 0)



def test_strong_upsert_does_not_fuzzy_merge_distinct_source_identity(db):
    legacy_id = legacy_job(
        db,
        title="Junior Software Engineer",
        company="Example Company",
        location="Tunis",
    )

    record = JobRecord(
        title="Junior Software Engineer",
        company="Example Company",
        location="Tunis",
        source_name="LinkedIn",
        source_url=(
            "https://www.linkedin.com/"
            "jobs/view/1111111111/"
        ),
        external_id="1111111111",
    )

    with Session(db) as session, session.begin():
        job, action = upsert_job_metadata_strong(
            session,
            record,
        )

        hunting_id = job.id

    assert action == "inserted"
    assert hunting_id != legacy_id
    assert counts(db) == (2, 2, 0, 0)


def test_strong_upsert_reuses_provider_external_id(db):
    first = JobRecord(
        title="QA Engineer",
        company="Example One",
        location="Tunis",
        source_name="LinkedIn",
        source_url=(
            "https://www.linkedin.com/"
            "jobs/view/2222222222/"
        ),
        external_id="2222222222",
        raw_payload={
            "capture": 1,
        },
    )

    with Session(db) as session, session.begin():
        job, action = upsert_job_metadata_strong(
            session,
            first,
        )

        first_id = job.id

    assert action == "inserted"

    second = JobRecord(
        title="QA Engineer",
        company="Example One",
        location="Tunis",
        source_name="LinkedIn",
        source_url=(
            "https://www.linkedin.com/"
            "jobs/view/2222222222/"
            "?trackingId=new-sighting"
        ),
        external_id="2222222222",
        raw_payload={
            "capture": 2,
        },
    )

    with Session(db) as session, session.begin():
        job, action = upsert_job_metadata_strong(
            session,
            second,
        )

        second_id = job.id

    assert action == "seen_again"
    assert second_id == first_id
    assert counts(db) == (1, 1, 0, 0)

    with Session(db) as session:
        source = session.scalar(
            select(JobSourceORM)
        )

        assert source.external_id == "2222222222"

        assert (
            json.loads(
                source.raw_payload_json
            )
            == {
                "capture": 2,
            }
        )


def test_strong_external_id_is_scoped_to_source_provider(db):
    linkedin = JobRecord(
        title="Software Engineer",
        company="Example",
        location="Tunis",
        source_name="LinkedIn",
        source_url=(
            "https://www.linkedin.com/"
            "jobs/view/3333333333/"
        ),
        external_id="3333333333",
    )

    with Session(db) as session, session.begin():
        first, _ = upsert_job_metadata_strong(
            session,
            linkedin,
        )

        first_id = first.id

    other = JobRecord(
        title="Software Engineer",
        company="Example",
        location="Tunis",
        source_name="AnotherProvider",
        source_url=(
            "https://jobs.example.test/"
            "3333333333"
        ),
        external_id="3333333333",
    )

    with Session(db) as session, session.begin():
        second, action = upsert_job_metadata_strong(
            session,
            other,
        )

        second_id = second.id

    assert action == "inserted"
    assert second_id != first_id
    assert counts(db) == (2, 2, 0, 0)


def test_strong_upsert_can_attach_source_by_exact_canonical_url(db):
    existing_id = legacy_job(
        db,
        title="Integration Engineer",
        company="Example",
        location="Tunis",
        source_name="legacy",
        source_url=(
            "https://example.test/"
            "jobs/canonical-44"
        ),
    )

    with Session(db) as session, session.begin():
        source = session.scalar(
            select(JobSourceORM).where(
                JobSourceORM.job_id
                == existing_id
            )
        )

        session.delete(
            source
        )

    assert counts(db) == (1, 0, 0, 0)

    record = JobRecord(
        title="Integration Engineer",
        company="Example",
        location="Tunis",
        source_name="ExampleProvider",
        source_url=(
            "https://example.test/"
            "jobs/canonical-44"
        ),
        external_id="canonical-44",
    )

    with Session(db) as session, session.begin():
        job, action = upsert_job_metadata_strong(
            session,
            record,
        )

        matched_id = job.id

    assert action == "matched_canonical_url"
    assert matched_id == existing_id
    assert counts(db) == (1, 1, 0, 0)
