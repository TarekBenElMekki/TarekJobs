from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote_plus, urlsplit, urlunsplit


APPLICATION_STATES = (
    "not_started",
    "application_preparing",
    "ready_to_apply",
    "applied",
    "awaiting_response",
    "assessment",
    "interview",
    "technical_test",
    "hr_interview",
    "offer",
    "hired",
    "rejected",
    "withdrawn",
    "expired",
    "not_interested",
)


ALLOWED_TRANSITIONS = {
    "not_started": {
        "applied",
        "application_preparing",
        "ready_to_apply",
        "not_interested",
        "expired",
    },

    "application_preparing": {
        "not_started",
        "ready_to_apply",
        "withdrawn",
        "expired",
    },

    "ready_to_apply": {
        "application_preparing",
        "applied",
        "withdrawn",
        "expired",
    },

    "applied": {
        "awaiting_response",
        "assessment",
        "interview",
        "technical_test",
        "hr_interview",
        "offer",
        "rejected",
        "withdrawn",
    },

    "awaiting_response": {
        "assessment",
        "interview",
        "technical_test",
        "hr_interview",
        "offer",
        "rejected",
        "withdrawn",
    },

    "assessment": {
        "awaiting_response",
        "interview",
        "technical_test",
        "hr_interview",
        "offer",
        "rejected",
        "withdrawn",
    },

    "technical_test": {
        "awaiting_response",
        "assessment",
        "interview",
        "hr_interview",
        "offer",
        "rejected",
        "withdrawn",
    },

    "interview": {
        "awaiting_response",
        "assessment",
        "technical_test",
        "hr_interview",
        "offer",
        "rejected",
        "withdrawn",
    },

    "hr_interview": {
        "awaiting_response",
        "assessment",
        "technical_test",
        "interview",
        "offer",
        "rejected",
        "withdrawn",
    },

    "offer": {
        "hired",
        "rejected",
        "withdrawn",
    },

    "hired": set(),
    "rejected": set(),
    "withdrawn": set(),
    "expired": set(),
    "not_interested": set(),
}


@dataclass(frozen=True)
class DocumentAsset:
    document_key: str
    kind: str
    language: str
    niche: str
    format: str
    file_path: str
    sha256: str


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def file_sha256(
    path: str | Path,
) -> str:
    return hashlib.sha256(
        Path(path).read_bytes()
    ).hexdigest()


def default_migration_path() -> Path:
    return (
        Path(__file__)
        .resolve()
        .parents[1]
        / "migrations"
        / "002_application_assistant_foundation.sql"
    )


def apply_migration(
    connection: sqlite3.Connection,
    migration_path: str | Path | None = None,
) -> None:

    path = (
        Path(migration_path)
        if migration_path
        else default_migration_path()
    )

    sql = path.read_text(
        encoding="utf-8"
    )

    connection.executescript(
        sql
    )


def _job_exists(
    connection: sqlite3.Connection,
    job_id: int,
) -> bool:

    return (
        connection.execute(
            """
            SELECT 1
            FROM jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        is not None
    )


def _application_row(
    connection: sqlite3.Connection,
    job_id: int,
):
    return connection.execute(
        """
        SELECT
            id,
            job_id,
            current_state,
            application_url,
            platform,
            next_action,
            next_action_at,
            follow_up_at,
            started_at,
            submitted_at,
            last_activity_at,
            created_at,
            updated_at
        FROM applications
        WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()


def get_application(
    connection: sqlite3.Connection,
    job_id: int,
) -> dict | None:

    row = _application_row(
        connection,
        job_id,
    )

    if row is None:
        return None

    keys = (
        "id",
        "job_id",
        "current_state",
        "application_url",
        "platform",
        "next_action",
        "next_action_at",
        "follow_up_at",
        "started_at",
        "submitted_at",
        "last_activity_at",
        "created_at",
        "updated_at",
    )

    return dict(
        zip(
            keys,
            row,
            strict=True,
        )
    )


def ensure_application(
    connection: sqlite3.Connection,
    job_id: int,
    *,
    actor: str = "user",
    source: str = "ttea",
) -> dict:

    existing = get_application(
        connection,
        job_id,
    )

    if existing is not None:
        return existing

    if not _job_exists(
        connection,
        job_id,
    ):
        raise ValueError(
            f"Unknown job_id: {job_id}"
        )

    legacy = connection.execute(
        """
        SELECT application_status
        FROM jobs
        WHERE id = ?
        """,
        (job_id,),
    ).fetchone()

    initial_state = (
        legacy[0]
        if legacy
        and legacy[0]
        in APPLICATION_STATES
        else "not_started"
    )

    now = utc_now()

    cursor = connection.execute(
        """
        INSERT INTO applications (
            job_id,
            current_state,
            started_at,
            last_activity_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            initial_state,
            (
                now
                if initial_state
                != "not_started"
                else None
            ),
            now,
            now,
            now,
        ),
    )

    application_id = int(
        cursor.lastrowid
    )

    connection.execute(
        """
        INSERT INTO application_events (
            application_id,
            event_type,
            from_state,
            to_state,
            actor,
            source,
            metadata_json,
            occurred_at
        )
        VALUES (?, ?, NULL, ?, ?, ?, '{}', ?)
        """,
        (
            application_id,
            "application.created",
            initial_state,
            actor,
            source,
            now,
        ),
    )

    return get_application(
        connection,
        job_id,
    )


def normalize_application_url(url: str) -> str:
    """Normalize a manually selected URL without network access or ATS guesses."""
    if not isinstance(url, str):
        raise ValueError("Application URL must be an HTTP or HTTPS URL")
    url = url.strip()
    if not url or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in url) or chr(92) in url:
        raise ValueError("Application URL contains invalid characters")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError("Invalid application URL") from exc
    if parts.scheme not in {"http", "https"} or not hostname:
        raise ValueError("Application URL must be an absolute HTTP or HTTPS URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Application URL must not contain credentials")
    if any(char in hostname for char in "%/?#") or parts.netloc.endswith(":"):
        raise ValueError("Invalid application URL host or port")

    # Keep raw query components: re-encoding can change signed/ATS routing data.
    tracking_keys = {"fbclid", "gclid", "msclkid", "li_fat_id"}
    query = []
    for component in parts.query.split("&"):
        if not component:
            continue  # Empty separators have no parameter semantics.
        key = unquote_plus(component.partition("=")[0]).lower()
        if key.startswith("utm_") or key in tracking_keys:
            continue
        # Unknown blank values and bare flags may be meaningful to an ATS.
        query.append(component)

    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and port != {"http": 80, "https": 443}[parts.scheme]:
        authority += f":{port}"
    # Preserve every nonempty fragment, including hash-based SPA routes.
    path = parts.path
    segments = path.rsplit("/", 2)
    referral_sources = {"linkedin", "indeed", "glassdoor", "facebook", "google"}
    if len(segments) == 3 and segments[-2].lower() == "apply" and segments[-1].lower() in referral_sources:
        path = path.rsplit("/", 1)[0]
    return urlunsplit((parts.scheme, authority, path, "&".join(query), parts.fragment))


def set_application_url(
    connection: sqlite3.Connection,
    job_id: int,
    url: str,
    *,
    actor: str = "user",
    source: str = "ttea",
) -> dict:
    """Save a link and its immutable provenance in the caller's transaction."""
    normalized_url = normalize_application_url(url)
    application = ensure_application(connection, job_id, actor=actor, source=source)
    previous_url = application["application_url"]
    now = utc_now()
    connection.execute(
        """
        UPDATE applications
        SET application_url = ?, last_activity_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (normalized_url, now, now, application["id"]),
    )
    connection.execute(
        """
        INSERT INTO application_events (
            application_id, event_type, from_state, to_state,
            actor, source, metadata_json, occurred_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            application["id"],
            "application.link_replaced" if previous_url else "application.link_saved",
            application["current_state"],
            application["current_state"],
            actor,
            source,
            json.dumps(
                {"source": source, "previous_url": previous_url, "application_url": normalized_url},
                ensure_ascii=False,
                sort_keys=True,
            ),
            now,
        ),
    )
    return get_application(connection, job_id)


def transition_application(
    connection: sqlite3.Connection,
    job_id: int,
    new_state: str,
    *,
    actor: str = "user",
    source: str = "ttea",
    note: str | None = None,
    metadata: dict | None = None,
) -> dict:

    if new_state not in APPLICATION_STATES:
        raise ValueError(
            f"Unknown application state: {new_state}"
        )

    # Historical reports record when we learned of an application, not when
    # it was submitted. Never infer a submission date from the report time.
    metadata = dict(metadata or {})
    historical_application = metadata.get("historical_application") is True
    application_date_unknown = metadata.get("application_date_unknown") is True
    for key in ("historical_application", "application_date_unknown"):
        if key in metadata and not isinstance(metadata[key], bool):
            raise ValueError(f"{key} must be a boolean")
    if historical_application or application_date_unknown:
        if new_state != "applied" or not historical_application or not application_date_unknown:
            raise ValueError(
                "Historical applications require state applied and both "
                "historical_application and application_date_unknown set to true"
            )

    application = ensure_application(
        connection,
        job_id,
        actor=actor,
        source=source,
    )

    old_state = application[
        "current_state"
    ]

    if new_state == old_state:
        return application

    if new_state not in ALLOWED_TRANSITIONS[
        old_state
    ]:
        raise ValueError(
            "Invalid application transition: "
            f"{old_state} -> {new_state}"
        )

    now = utc_now()

    submitted_at = application[
        "submitted_at"
    ]

    if (
        new_state == "applied"
        and submitted_at is None
        and not application_date_unknown
    ):
        submitted_at = now

    started_at = application[
        "started_at"
    ]

    if (
        new_state != "not_started"
        and started_at is None
        and not historical_application
    ):
        started_at = now

    connection.execute(
        """
        UPDATE applications
        SET
            current_state = ?,
            started_at = ?,
            submitted_at = ?,
            last_activity_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            new_state,
            started_at,
            submitted_at,
            now,
            now,
            application["id"],
        ),
    )

    connection.execute(
        """
        INSERT INTO application_events (
            application_id,
            event_type,
            from_state,
            to_state,
            actor,
            source,
            note,
            metadata_json,
            occurred_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            application["id"],
            "application.state_changed",
            old_state,
            new_state,
            actor,
            source,
            note,
            json.dumps(
                metadata or {},
                ensure_ascii=False,
                sort_keys=True,
            ),
            now,
        ),
    )

    # Compatibility cache for the current TTEA UI.
    if new_state == "applied":
        connection.execute(
            """
            UPDATE jobs
            SET
                application_status = ?,
                application_date =
                    COALESCE(
                        application_date,
                        ?
                    ),
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_state,
                submitted_at,
                now,
                job_id,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE jobs
            SET
                application_status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_state,
                now,
                job_id,
            ),
        )

    return get_application(
        connection,
        job_id,
    )


def application_events(
    connection: sqlite3.Connection,
    job_id: int,
) -> list[dict]:

    application = get_application(
        connection,
        job_id,
    )

    if application is None:
        return []

    rows = connection.execute(
        """
        SELECT
            id,
            event_type,
            from_state,
            to_state,
            actor,
            source,
            note,
            metadata_json,
            occurred_at
        FROM application_events
        WHERE application_id = ?
        ORDER BY id
        """,
        (
            application["id"],
        ),
    ).fetchall()

    result = []

    for row in rows:

        result.append({
            "id":
                row[0],

            "event_type":
                row[1],

            "from_state":
                row[2],

            "to_state":
                row[3],

            "actor":
                row[4],

            "source":
                row[5],

            "note":
                row[6],

            "metadata":
                json.loads(
                    row[7]
                ),

            "occurred_at":
                row[8],
        })

    return result


_CV_RE = re.compile(
    r"^Tarek_Ben_El_Mekki_CV_"
    r"(?P<language>EN|FR)_"
    r"(?P<niche>[A-Z0-9_]+)"
    r"\.(?P<format>pdf|docx)$",
    re.IGNORECASE,
)


def _letter_asset(
    path: Path,
) -> DocumentAsset | None:

    name = path.name.lower()

    if (
        "lettre_de_motivation_fr"
        in name
    ):
        language = "FR"

    elif (
        "motivation_letter_en"
        in name
    ):
        language = "EN"

    else:
        return None

    extension = (
        path.suffix
        .lstrip(".")
        .lower()
    )

    if extension not in {
        "pdf",
        "docx",
    }:
        return None

    return DocumentAsset(
        document_key=(
            "motivation_letter:"
            + language
            + ":general"
        ),
        kind="motivation_letter",
        language=language,
        niche="general",
        format=extension,
        file_path=str(
            path.resolve()
        ),
        sha256=file_sha256(
            path
        ),
    )


def scan_document_pack(
    cv_pdf_dir: str | Path,
    cv_docx_dir: str | Path,
    letter_dir: str | Path,
) -> list[DocumentAsset]:

    assets: list[
        DocumentAsset
    ] = []

    for directory in (
        Path(cv_pdf_dir),
        Path(cv_docx_dir),
    ):

        for path in sorted(
            directory.iterdir()
        ):

            if not path.is_file():
                continue

            match = _CV_RE.match(
                path.name
            )

            if not match:
                continue

            language = (
                match.group(
                    "language"
                )
                .upper()
            )

            niche = (
                match.group(
                    "niche"
                )
                .lower()
            )

            extension = (
                match.group(
                    "format"
                )
                .lower()
            )

            assets.append(
                DocumentAsset(
                    document_key=(
                        "cv:"
                        + language
                        + ":"
                        + niche
                    ),
                    kind="cv",
                    language=language,
                    niche=niche,
                    format=extension,
                    file_path=str(
                        path.resolve()
                    ),
                    sha256=file_sha256(
                        path
                    ),
                )
            )

    for path in sorted(
        Path(
            letter_dir
        ).iterdir()
    ):

        if not path.is_file():
            continue

        asset = _letter_asset(
            path
        )

        if asset is not None:
            assets.append(
                asset
            )

    return assets


def register_document_asset(
    connection: sqlite3.Connection,
    asset: DocumentAsset,
) -> int:

    existing = connection.execute(
        """
        SELECT id
        FROM document_library
        WHERE
            file_path = ?
            AND sha256 = ?
        """,
        (
            asset.file_path,
            asset.sha256,
        ),
    ).fetchone()

    if existing is not None:
        return int(
            existing[0]
        )

    now = utc_now()

    cursor = connection.execute(
        """
        INSERT INTO document_library (
            document_key,
            kind,
            language,
            niche,
            format,
            file_path,
            sha256,
            is_generated,
            active,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
        """,
        (
            asset.document_key,
            asset.kind,
            asset.language,
            asset.niche,
            asset.format,
            asset.file_path,
            asset.sha256,
            now,
        ),
    )

    return int(
        cursor.lastrowid
    )


def register_document_pack(
    connection: sqlite3.Connection,
    cv_pdf_dir: str | Path,
    cv_docx_dir: str | Path,
    letter_dir: str | Path,
) -> list[int]:

    assets = scan_document_pack(
        cv_pdf_dir,
        cv_docx_dir,
        letter_dir,
    )

    return [
        register_document_asset(
            connection,
            asset,
        )
        for asset in assets
    ]


def document_library(
    connection: sqlite3.Connection,
) -> list[dict]:

    rows = connection.execute(
        """
        SELECT
            id,
            document_key,
            kind,
            language,
            niche,
            format,
            file_path,
            sha256,
            is_generated,
            generated_for_job_id,
            parent_document_id,
            active,
            created_at
        FROM document_library
        ORDER BY
            kind,
            language,
            niche,
            format
        """
    ).fetchall()

    keys = (
        "id",
        "document_key",
        "kind",
        "language",
        "niche",
        "format",
        "file_path",
        "sha256",
        "is_generated",
        "generated_for_job_id",
        "parent_document_id",
        "active",
        "created_at",
    )

    return [
        dict(
            zip(
                keys,
                row,
                strict=True,
            )
        )
        for row in rows
    ]