from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import JobRecord, ScoreResult
from .normalize import detect_job_type, detect_seniority, extract_years, norm_text

GERMAN_REQUIRED_PATTERNS = [
    r"\bfluent german\b",
    r"\bgerman (?:language )?(?:is )?required\b",
    r"\bdeutsch(?:kenntnisse)?\s*(?:c1|c2|b2)\b",
    r"\bmust speak german\b",
    r"\ballemand.*(?:obligatoire|requis|courant)\b",
]


def _technical_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    title = norm_text(job.title)
    body = norm_text(f"{job.title} {job.description}")
    keywords = profile.get("target_keywords", []) + profile.get("skills", [])

    title_hits = []
    body_hits = []
    for keyword in keywords:
        nk = norm_text(keyword)
        if not nk or len(nk) < 2:
            continue
        if nk in title:
            title_hits.append(keyword)
        elif nk in body:
            body_hits.append(keyword)

    unique_title = list(dict.fromkeys(title_hits))
    unique_body = list(dict.fromkeys(body_hits))
    score = min(100, len(unique_title) * 18 + len(unique_body) * 6)

    generic_technical = [
        "engineer", "developer", "software", "technical", "informatique",
        "system", "automation", "robot", "data", "support", "application",
    ]
    if score == 0 and any(x in body for x in generic_technical):
        score = 15

    details = []
    hits = (unique_title + unique_body)[:7]
    if hits:
        details.append("Technical matches: " + ", ".join(map(str, hits)))
    else:
        details.append("No strong target-skill keyword match detected")
    return score, details


def _monthly_tnd(job: JobRecord) -> float | None:
    if job.salary_currency != "TND" or job.salary_min is None:
        return None
    period = (job.salary_period or "").lower()
    if period == "monthly" or not period:
        return job.salary_min
    if period == "yearly":
        return job.salary_min / 12
    return None


def _eligibility_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    text = norm_text(f"{job.title} {job.description} {job.employment_type} {job.seniority}")
    job_type = job.employment_type or detect_job_type(text)
    seniority = job.seniority or detect_seniority(text)
    score = 100
    reasons = []

    if job_type == "internship" or seniority == "intern":
        return 0, ["Internships are excluded by profile"]
    if job_type == "freelance":
        return 0, ["Freelance roles are excluded by profile"]

    if any(re.search(pattern, text, re.I) for pattern in GERMAN_REQUIRED_PATTERNS):
        return 0, ["German appears to be a required language"]

    disallowed = profile.get("seniority", {}).get("disallowed", [])
    if seniority and norm_text(seniority) in {norm_text(x) for x in disallowed}:
        score = min(score, 15)
        reasons.append(f"Seniority '{seniority}' is outside the target level")

    min_years = job.min_years
    max_years = job.max_years
    if min_years is None and max_years is None:
        min_years, max_years = extract_years(text)
    if min_years is not None and min_years >= 3:
        score = min(score, 25)
        reasons.append(f"Requires about {min_years}+ years; target maximum is 2")

    salary_sensitive = job_type in {"part-time", "apprenticeship"}
    if "graduate" in text:
        salary_sensitive = True

    if salary_sensitive:
        salary = _monthly_tnd(job)
        threshold = profile.get("salary_preferences", {}).get("part_time_min_monthly_tnd", 1500)
        if salary is not None:
            if salary < threshold:
                score = min(score, 25)
                reasons.append(f"Known monthly pay ({salary:.0f} TND) is below {threshold} TND preference")
            else:
                reasons.append(f"Known monthly pay meets the {threshold} TND preference")
        else:
            score = min(score, 75)
            reasons.append("Salary-sensitive role but salary is unknown; kept for manual review")

    if not reasons:
        reasons.append("No hard eligibility conflict detected")
    return score, reasons


def _location_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    geo = profile.get("geography", {})
    location = norm_text(job.location)
    region = norm_text(job.remote_region)
    text = norm_text(f"{job.location} {job.remote_region} {job.description[:1000]}")
    allowed = [norm_text(x) for x in geo.get("allowed_local_locations", [])]

    if job.remote:
        worldwide = ["worldwide", "anywhere", "global", "all countries"]
        emea = ["emea", "africa", "middle east and africa"]
        europe = ["europe", "european union", "eu only", "eu"]
        if any(x in text for x in worldwide):
            return 100, ["Remote role appears globally available"]
        if any(x in text for x in emea):
            return 90, ["Remote role targets EMEA/Africa"]
        if any(x in text for x in europe):
            return 60, ["Europe/EU-restricted remote role kept per preference; eligibility needs manual verification"]
        if not location and not region:
            return 75, ["Remote role with unspecified geographic eligibility"]
        return 45, [f"Remote role has a geographic restriction: {job.location or job.remote_region or 'unknown'}"]

    if "tunisia" in location or "tunisie" in location or any(x and x in location for x in allowed):
        if any(x and x in location for x in allowed) or location in {"tunis", "tunisia", "tunisie"}:
            return 100, ["On-site/hybrid role is in the accepted Greater Tunis area or Tunisia-unspecified"]
        return 35, [f"Tunisian role appears outside the preferred Greater Tunis area: {job.location}"]

    if not location:
        return 55, ["Location is unknown; kept for manual review"]

    return 0, [f"Non-remote role outside Tunisia/Greater Tunis: {job.location}"]


def _experience_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    text = norm_text(f"{job.title} {job.description} {job.seniority}")
    seniority = job.seniority or detect_seniority(text)
    min_years = job.min_years
    max_years = job.max_years
    if min_years is None and max_years is None:
        min_years, max_years = extract_years(text)

    if min_years is not None:
        if min_years <= 2:
            return 100, [f"Experience requirement ({min_years} year(s)) is within target"]
        if min_years == 3:
            return 20, ["Requires about 3 years; target maximum is 2"]
        return 0, [f"Requires about {min_years}+ years; above target"]

    if seniority in {"graduate", "entry-level", "junior", "associate"}:
        return 100, [f"Seniority '{seniority}' matches target"]
    if seniority in {"mid-level", "senior", "lead", "manager", "director", "principal", "staff"}:
        return 10, [f"Seniority '{seniority}' is above target"]
    if seniority == "intern":
        return 0, ["Internship seniority is excluded"]
    return 70, ["No explicit experience requirement found"]


def score_job(job: JobRecord, profile: dict, settings: dict) -> ScoreResult:
    if not job.seniority:
        job.seniority = detect_seniority(f"{job.title} {job.description}")
    if not job.employment_type:
        job.employment_type = detect_job_type(f"{job.title} {job.description}")
    if job.min_years is None and job.max_years is None:
        job.min_years, job.max_years = extract_years(f"{job.title} {job.description}")

    technical, e1 = _technical_score(job, profile)
    eligibility, e2 = _eligibility_score(job, profile)
    location, e3 = _location_score(job, profile)
    experience, e4 = _experience_score(job, profile)

    weights = settings.get("matching", {}).get("overall_weights", {})
    overall = round(
        technical * float(weights.get("technical", 0.45))
        + eligibility * float(weights.get("eligibility", 0.20))
        + location * float(weights.get("location", 0.20))
        + experience * float(weights.get("experience", 0.15))
    )

    return ScoreResult(
        technical=technical,
        eligibility=eligibility,
        location=location,
        experience=experience,
        overall=max(0, min(100, overall)),
        explanations=e1 + e2 + e3 + e4,
    )


def is_stale(job: JobRecord, stale_after_days: int) -> bool:
    if not job.posted_at:
        return False
    dt = job.posted_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - dt
    return age.days > stale_after_days