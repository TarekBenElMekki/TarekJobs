import sqlite3

from pathlib import Path

import pytest

from src.application_assistant import (
    application_events,
    apply_migration,
    document_library,
    ensure_application,
    register_document_pack,
    scan_document_pack,
    transition_application,
)


def make_db(
    tmp_path: Path,
):
    path = (
        tmp_path
        / "jobs.db"
    )

    db = sqlite3.connect(
        path
    )

    db.execute(
        """
        PRAGMA foreign_keys = ON
        """
    )

    db.execute(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,

            application_status TEXT
                NOT NULL
                DEFAULT 'not_started',

            application_date TEXT,
            cv_version TEXT,
            cover_letter_version TEXT,
            response TEXT,
            interview_stage TEXT,
            notes TEXT,

            updated_at TEXT
        )
        """
    )

    return db


def add_job(
    db,
    job_id=1,
):

    db.execute(
        """
        INSERT INTO jobs (
            id,
            application_status,
            updated_at
        )
        VALUES (?, 'not_started', NULL)
        """,
        (
            job_id,
        ),
    )

    db.commit()


def test_migration_is_additive_and_does_not_backfill_jobs(
    tmp_path,
):

    db = make_db(
        tmp_path
    )

    try:

        add_job(
            db,
            1,
        )

        add_job(
            db,
            2,
        )

        apply_migration(
            db
        )

        tables = {
            row[0]
            for row in db.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                """
            )
        }

        assert {
            "applications",
            "application_events",
            "document_library",
            "application_documents",
        }.issubset(
            tables
        )

        assert db.execute(
            """
            SELECT COUNT(*)
            FROM applications
            """
        ).fetchone()[0] == 0

    finally:
        db.close()


def test_application_lifecycle_and_legacy_cache(
    tmp_path,
):

    db = make_db(
        tmp_path
    )

    try:

        add_job(
            db
        )

        apply_migration(
            db
        )

        created = ensure_application(
            db,
            1,
        )

        assert (
            created[
                "current_state"
            ]
            == "not_started"
        )

        for state in (
            "application_preparing",
            "ready_to_apply",
            "applied",
            "awaiting_response",
        ):

            transition_application(
                db,
                1,
                state,
            )

        current = ensure_application(
            db,
            1,
        )

        assert (
            current[
                "current_state"
            ]
            == "awaiting_response"
        )

        assert (
            current[
                "submitted_at"
            ]
            is not None
        )

        legacy = db.execute(
            """
            SELECT
                application_status,
                application_date
            FROM jobs
            WHERE id = 1
            """
        ).fetchone()

        assert legacy[0] == (
            "awaiting_response"
        )

        assert legacy[1] is not None

        events = application_events(
            db,
            1,
        )

        assert len(
            events
        ) == 5

        assert events[0][
            "event_type"
        ] == "application.created"

        assert events[-1][
            "to_state"
        ] == "awaiting_response"

    finally:
        db.close()


def test_invalid_transition_is_rejected(
    tmp_path,
):

    db = make_db(
        tmp_path
    )

    try:

        add_job(
            db
        )

        apply_migration(
            db
        )

        ensure_application(
            db,
            1,
        )

        with pytest.raises(
            ValueError,
            match="Invalid application transition",
        ):
            transition_application(
                db,
                1,
                "offer",
            )

        current = ensure_application(
            db,
            1,
        )

        assert (
            current[
                "current_state"
            ]
            == "not_started"
        )

    finally:
        db.close()


def test_application_events_are_immutable(
    tmp_path,
):

    db = make_db(
        tmp_path
    )

    try:

        add_job(
            db
        )

        apply_migration(
            db
        )

        ensure_application(
            db,
            1,
        )

        event_id = db.execute(
            """
            SELECT id
            FROM application_events
            LIMIT 1
            """
        ).fetchone()[0]

        with pytest.raises(
            sqlite3.IntegrityError,
            match="Application events are immutable",
        ):
            db.execute(
                """
                UPDATE application_events
                SET note='changed'
                WHERE id=?
                """,
                (
                    event_id,
                ),
            )

    finally:
        db.close()


def test_document_pack_registration_is_idempotent(
    tmp_path,
):

    pdf = (
        tmp_path
        / "PDF"
    )

    docx = (
        tmp_path
        / "DOCX"
    )

    letters = (
        tmp_path
        / "LETTERS"
    )

    pdf.mkdir()
    docx.mkdir()
    letters.mkdir()

    (
        pdf
        / "Tarek_Ben_El_Mekki_CV_EN_SOFTWARE.pdf"
    ).write_bytes(
        b"pdf-software"
    )

    (
        docx
        / "Tarek_Ben_El_Mekki_CV_EN_SOFTWARE.docx"
    ).write_bytes(
        b"docx-software"
    )

    (
        letters
        / "Tarek_Ben_El_Mekki_Motivation_Letter_EN.pdf"
    ).write_bytes(
        b"letter-pdf"
    )

    (
        letters
        / "Tarek_Ben_El_Mekki_Motivation_Letter_EN.docx"
    ).write_bytes(
        b"letter-docx"
    )

    assets = scan_document_pack(
        pdf,
        docx,
        letters,
    )

    assert len(
        assets
    ) == 4

    assert {
        asset.document_key
        for asset in assets
    } == {
        "cv:EN:software",
        "motivation_letter:EN:general",
    }

    db = make_db(
        tmp_path
        / "database"
    ) if False else None

    database_path = (
        tmp_path
        / "library.db"
    )

    db = sqlite3.connect(
        database_path
    )

    try:

        db.execute(
            """
            PRAGMA foreign_keys = ON
            """
        )

        db.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,

                application_status TEXT
                    NOT NULL
                    DEFAULT 'not_started',

                application_date TEXT,
                updated_at TEXT
            )
            """
        )

        apply_migration(
            db
        )

        register_document_pack(
            db,
            pdf,
            docx,
            letters,
        )

        register_document_pack(
            db,
            pdf,
            docx,
            letters,
        )

        rows = document_library(
            db
        )

        assert len(
            rows
        ) == 4

    finally:
        db.close()