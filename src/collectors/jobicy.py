from __future__ import annotations

from .base import BaseCollector
from ..models import JobRecord
from ..normalize import clean_text, parse_date


class JobicyCollector(BaseCollector):
    name = "jobicy"

    def fetch(self, limit: int) -> list[JobRecord]:
        url = self.config["base_url"]
        payload = self.get_json(url, params={"count": min(limit, 100)})
        out: list[JobRecord] = []

        for raw in payload.get("jobs", [])[:limit]:
            salary_min = raw.get("salaryMin")
            salary_max = raw.get("salaryMax")
            out.append(
                JobRecord(
                    title=raw.get("jobTitle") or "Untitled job",
                    company=raw.get("companyName") or "Unknown",
                    location=raw.get("jobGeo") or "",
                    description=clean_text(raw.get("jobDescription") or raw.get("jobExcerpt")),
                    employment_type=", ".join(raw.get("jobType") or []),
                    remote=True,
                    remote_region=raw.get("jobGeo") or "",
                    seniority=raw.get("jobLevel") or "",
                    salary_min=float(salary_min) if salary_min not in (None, "") else None,
                    salary_max=float(salary_max) if salary_max not in (None, "") else None,
                    salary_currency=raw.get("salaryCurrency") or None,
                    salary_period=raw.get("salaryPeriod") or None,
                    posted_at=parse_date(raw.get("pubDate")),
                    source_name=self.name,
                    source_url=raw.get("url") or f"https://jobicy.com/jobs/{raw.get('id')}",
                    external_id=str(raw.get("id") or ""),
                    raw_payload=raw,
                )
            )
        return out