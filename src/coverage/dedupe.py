"""Conservative deterministic cross-source identity; never touches scores."""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..normalize import norm_text
from .normalization import NormalizedCandidate, canonical_url
from .storage import rows


@dataclass(frozen=True)
class DedupeDecision:
    action: str
    candidate_canonical_job_id: int | None
    confidence: float
    method: str
    evidence: dict = field(default_factory=dict)


def description_similarity(left: str, right: str) -> float:
    a, b = set(norm_text(left).split()), set(norm_text(right).split())
    return len(a & b) / len(a | b) if a and b else 0.0


def decide(connection, source_id: str, record_key: str, candidate: NormalizedCandidate) -> DedupeDecision:
    from .record_outcomes import shared_url
    job = candidate.job
    matches: dict[int, list[str]] = {}

    def include(sql, parameters, method):
        for row in rows(connection, sql, parameters):
            matches.setdefault(row["job_id"], []).append(method)

    include("SELECT canonical_job_id AS job_id FROM coverage_provenance WHERE source_id=? AND source_record_key=?",
            (source_id, record_key), "source_record_key")
    if candidate.source_url_is_identity and not shared_url(connection, job.source_url):
        include("SELECT job_id FROM job_sources WHERE source_url=?",
                (job.source_url,), "legacy_source_url")
        include("SELECT canonical_job_id AS job_id FROM coverage_provenance WHERE source_url=?",
                (job.source_url,), "source_url")
    if job.external_id:
        include("SELECT canonical_job_id AS job_id FROM coverage_provenance WHERE source_id=? AND external_id=?",
                (source_id,job.external_id), "source_external_id")
        include("SELECT job_id FROM job_sources WHERE source_name=? AND external_id=?",
                (job.source_name,job.external_id), "legacy_external_id")
    if candidate.canonical_url and candidate.source_url_is_identity and not shared_url(connection, candidate.canonical_url):
        include("SELECT canonical_job_id AS job_id FROM coverage_provenance WHERE canonical_url=?",
                (candidate.canonical_url,), "canonical_url")
        # Legacy rows have not been backfilled or rewritten. Normalize their URL
        # read-only, streaming scalar rows so tracking parameters still match.
        for table, id_column, url_column in (("jobs","id","canonical_url"),("job_sources","job_id","source_url")):
            for row in connection.exec_driver_sql(
                f"SELECT {id_column}, {url_column} FROM {table} WHERE {url_column}<>''"):
                try:
                    equal = canonical_url(row[1]) == candidate.canonical_url
                except ValueError:
                    equal = False
                if equal:
                    matches.setdefault(row[0], []).append("legacy_canonical_url")
    if candidate.ats_namespace and candidate.ats_requisition_id:
        include("SELECT canonical_job_id AS job_id FROM coverage_provenance WHERE ats_namespace=? AND ats_requisition_id=?",
                (candidate.ats_namespace,candidate.ats_requisition_id), "ats_tenant_requisition")
    if matches:
        evidence = {"candidates": [{"job_id":k,"identifiers":sorted(set(v))}
                                   for k,v in sorted(matches.items())]}
        if len(matches) > 1:
            return DedupeDecision("review", min(matches), 1.0, "conflicting_strong_identifiers", evidence)
        return DedupeDecision("reuse", next(iter(matches)), 1.0, "strong_identifier", evidence)

    company, title, location = norm_text(job.company), norm_text(job.title), norm_text(job.location)
    candidates = []
    if company and company != "unknown":
        for row in connection.exec_driver_sql(
            "SELECT id,norm_title,norm_location,description,posted_at FROM jobs WHERE norm_company=?",
            (company,)).mappings():
            title_similarity = SequenceMatcher(None, title, row["norm_title"], autojunk=False).ratio()
            if title_similarity < 0.8:
                continue
            description = description_similarity(job.description, row["description"])
            same_location = bool(location and location == row["norm_location"])
            same_date = bool(job.posted_at and row["posted_at"] and
                             job.posted_at.date().isoformat() == str(row["posted_at"])[:10])
            confidence = min(0.95, 0.45*title_similarity + 0.3*description +
                             0.15*same_location + 0.05*same_date)
            candidates.append({"job_id":row["id"],"confidence":round(confidence,6),
                               "title_similarity":title_similarity,
                               "description_similarity":description,
                               "same_company":True,"same_location":same_location,
                               "same_posted_date":same_date})
    if candidates:
        candidates.sort(key=lambda r:(-r["confidence"],r["job_id"]))
        return DedupeDecision("review", candidates[0]["job_id"], candidates[0]["confidence"],
                              "descriptive_candidate", {"candidates":candidates})
    return DedupeDecision("create", None, 0.0, "no_duplicate_evidence", {"candidates":[]})
