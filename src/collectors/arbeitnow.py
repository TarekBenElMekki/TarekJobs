from __future__ import annotations

from .base import BaseCollector
from ..models import JobRecord
from ..normalize import clean_text, parse_date


class ArbeitnowCollector(BaseCollector):
    name = "arbeitnow"

    def fetch(self, limit: int) -> list[JobRecord]:
        payload = self.get_json(self.config["base_url"])
        out: list[JobRecord] = []
        for raw in payload.get("data", [])[:limit]:
            tags = raw.get("tags") or []
            job_types = raw.get("job_types") or []
            description = clean_text(raw.get("description"))
            if tags:
                description = description + " Tags: " + ", ".join(map(str, tags))
            out.append(
                JobRecord(
                    title=raw.get("title") or "Untitled job",
                    company=raw.get("company_name") or "Unknown",
                    location=raw.get("location") or "",
                    description=description,
                    employment_type=", ".join(map(str, job_types)),
                    remote=bool(raw.get("remote")),
                    remote_region=raw.get("location") if raw.get("remote") else "",
                    posted_at=parse_date(raw.get("created_at")),
                    source_name=self.name,
                    source_url=raw.get("url") or "",
                    external_id=str(raw.get("slug") or ""),
                    raw_payload=raw,
                )
            )
        return [x for x in out if x.source_url]