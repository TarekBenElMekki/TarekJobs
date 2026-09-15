from __future__ import annotations

from .base import BaseCollector
from ..models import JobRecord
from ..normalize import clean_text, parse_date, parse_salary_text


class RemotiveCollector(BaseCollector):
    name = "remotive"

    def fetch(self, limit: int) -> list[JobRecord]:
        payload = self.get_json(self.config["base_url"])
        out: list[JobRecord] = []

        for raw in payload.get("jobs", [])[:limit]:
            smin, smax, curr, period = parse_salary_text(raw.get("salary") or "")
            tags = raw.get("tags") or []
            description = clean_text(raw.get("description"))
            if tags:
                description = description + " Skills/tags: " + ", ".join(map(str, tags))
            out.append(
                JobRecord(
                    title=raw.get("title") or "Untitled job",
                    company=raw.get("company_name") or "Unknown",
                    location=raw.get("candidate_required_location") or "",
                    description=description,
                    employment_type=raw.get("job_type") or "",
                    remote=True,
                    remote_region=raw.get("candidate_required_location") or "",
                    salary_min=smin,
                    salary_max=smax,
                    salary_currency=curr,
                    salary_period=period,
                    posted_at=parse_date(raw.get("publication_date")),
                    source_name=self.name,
                    source_url=raw.get("url") or "",
                    external_id=str(raw.get("id") or ""),
                    raw_payload=raw,
                )
            )
        return [x for x in out if x.source_url]