"""Coverage normalization uses the existing JobRecord and text normalization."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import JobRecord
from ..normalize import clean_text, norm_text, parse_date
from .contracts import AdapterMetadata, InventoryRecord


def canonical_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Expected an absolute HTTP(S) job URL")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in job URLs are not accepted")
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    if parsed.port and not ((parsed.scheme.lower(), parsed.port) in {("https",443),("http",80)}):
        host += ":" + str(parsed.port)
    # Preserve all identity-bearing query parameters and their order.
    query = [(k,v) for k,v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid","gclid"}]
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", urlencode(query), ""))


@dataclass(frozen=True)
class NormalizedCandidate:
    job: JobRecord
    canonical_url: str
    ats_namespace: str | None = None
    ats_requisition_id: str | None = None
    source_url_is_identity: bool = True
    application_url: str | None = None
    source_metadata: dict = field(default_factory=dict)

    def payload(self):
        return {"job": self.job.model_dump(mode="json"), "canonical_url": self.canonical_url,
                "ats_namespace": self.ats_namespace, "ats_requisition_id": self.ats_requisition_id,
                "source_url_is_identity": self.source_url_is_identity,
                "application_url": self.application_url, "source_metadata": self.source_metadata}

    @classmethod
    def from_payload(cls, payload):
        return cls(JobRecord.model_validate(payload["job"]), payload["canonical_url"],
                   payload.get("ats_namespace"), payload.get("ats_requisition_id"),
                   payload.get("source_url_is_identity", True), payload.get("application_url"),
                   payload.get("source_metadata", {}))


def normalize(record: InventoryRecord, values: dict, metadata: AdapterMetadata) -> NormalizedCandidate:
    # Whitelist the existing descriptive schema. No suitability/review fields.
    data = {k:v for k,v in values.items() if k in JobRecord.model_fields
            and k not in {"raw_payload","source_name","source_url","external_id"}}
    for key in ("title","company","location","description"):
        if key in data:
            data[key] = clean_text(data[key])
    if not data.get("title"):
        raise ValueError("A nonempty job title is required")
    for key in ("posted_at","deadline","last_verified_at"):
        if data.get(key):
            parsed = parse_date(data[key])
            if parsed is None:
                raise ValueError("Invalid descriptive timestamp")
            data[key] = parsed.astimezone(timezone.utc)
    data.update(source_name=metadata.source_name, source_url=record.source_url,
                external_id=record.external_id)
    job = JobRecord.model_validate(data)
    url = canonical_url(values.get("canonical_url") or values.get("application_url") or record.source_url)
    namespace = clean_text(values.get("ats_namespace")) or None
    requisition = clean_text(values.get("ats_requisition_id")) or None
    # Requisition IDs without an explicit ATS tenant/employer namespace never
    # become a strong cross-source identity.
    application = values.get("application_url")
    if application:
        application = canonical_url(application)
    return NormalizedCandidate(job, url, namespace, requisition,
        values.get("_source_url_is_identity", True), application,
        {key: values[key] for key in ("_application", "_freshness", "_scope_evidence", "_parent_article_url")
         if key in values})
