from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import JobRecord, ScoreResult
from .normalize import detect_job_type, extract_years, norm_text

GERMAN_REQUIRED_PATTERNS = [
    r"\bfluent german\b",
    r"\bgerman (?:language )?(?:is )?required\b",
    r"\bdeutsch(?:kenntnisse)?\s*(?:c1|c2|b2)\b",
    r"\bmust speak german\b",
    r"\ballemand.*(?:obligatoire|requis|courant)\b",
]

# Title-level signals matter more than incidental words in descriptions.
TARGET_TITLE_SIGNALS = [
    "software", "developer", "backend", "frontend", "full stack", "full-stack",
    "python", "c++", "java", "rust", "embedded", "firmware", "iot",
    "robot", "ros", "automation", "automatisme", "plc", "mechatronic",
    "electronic", "electrical", "mechanical", "mecanique", "cad", "solidworks",
    "biomedical", "medical device", "medtech", "healthtech", "medical software",
    "computer vision", "machine learning", "artificial intelligence", " ai ",
    "data analyst", "data engineer", "data scientist", "qa", "quality assurance",
    "test engineer", "systems engineer", "system engineer", "application engineer",
    "integration", "solutions engineer", "solution engineer", "technical support",
    "it support", "business application", "erp", "business analyst",
    "technical consultant", "implementation consultant", "implementation engineer",
    "devops", "cloud", "cybersecurity", "network engineer", "networking",
    "research engineer", "r&d", "product engineer", "technical product",
    "clinical application", "application specialist",
]

# These are not deleted; they simply should not rise to the top of a technical-job feed.
NON_TARGET_TITLE_SIGNALS = [
    "graphic designer", "designer graphique", "account executive", "sales executive",
    "sales representative", "commercial", "marketing", "community manager",
    "recruiter", "recruitment", "human resources", "ressources humaines",
    "accountant", "comptable", "controller", "controleur de gestion", "finance manager",
    "legal", "lawyer", "juriste", "procurement", "buyer", "acheteur",
]

REGION_RESTRICTED_SIGNALS = {
    "usa": ["usa", "united states", "u.s.", "us only"],
    "canada": ["canada"],
    "uk": ["united kingdom", "uk only", "remote - uk", "remote uk"],
    "apac": ["apac", "asia pacific"],
    "latam": ["latam", "latin america"],
    "australia": ["australia", "new zealand"],
}


def _title_seniority(title: str) -> str:
    """Detect seniority from the title first, with disallowed levels taking precedence."""
    t = f" {norm_text(title)} "
    ordered = [
        ("director", [r"\bdirector\b", r"\bdirecteur\b", r"\bhead of\b", r"\bchief\b", r"\bvp\b", r"\bvice president\b"]),
        ("principal", [r"\bprincipal\b"]),
        ("staff", [r"\bstaff\b"]),
        ("senior", [r"\bsenior\b", r"\bsr\b"]),
        ("lead", [r"\blead\b", r"\bteam lead\b", r"\btech lead\b"]),
        ("manager", [r"\bmanager\b", r"\bresponsable\b"]),
        ("mid-level", [r"\bmid level\b", r"\bmid-level\b", r"\bintermediate\b"]),
        ("intern", [r"\bintern\b", r"\binternship\b", r"\bstage\b", r"\bstagiaire\b"]),
        ("graduate", [r"\bgraduate\b", r"\bfresh graduate\b", r"\bnew grad\b"]),
        ("junior", [r"\bjunior\b", r"\bjr\b"]),
        ("entry-level", [r"\bentry level\b", r"\bentry-level\b", r"\bdebutant\b"]),
        ("associate", [r"\bassociate\b"]),
    ]
    for label, patterns in ordered:
        if any(re.search(pattern, t, re.I) for pattern in patterns):
            return label
    return ""


def _effective_seniority(job: JobRecord) -> str:
    # Never let a description-level "associate" or "junior" override "Senior/Manager" in the title.
    title_level = _title_seniority(job.title)
    if title_level:
        return title_level
    if job.seniority:
        return norm_text(job.seniority)
    # Description fallback only when the title itself contains no seniority signal.
    from .normalize import detect_seniority
    return detect_seniority(job.description)


def _title_role_relevance(title: str) -> tuple[str, list[str]]:
    t = f" {norm_text(title)} "
    non_target = [signal for signal in NON_TARGET_TITLE_SIGNALS if norm_text(signal) in t]
    if non_target:
        return "non-target", non_target[:3]
    target = [signal for signal in TARGET_TITLE_SIGNALS if norm_text(signal) in t]
    if target:
        return "target", target[:5]
    # Broad engineering/research/support titles are plausible but need body evidence.
    if any(term in t for term in [" engineer ", " ingenieur ", " analyst ", " support ", " specialist ", " consultant ", " research "]):
        return "adjacent", []
    return "unclear", []


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
    score = min(100, len(unique_title) * 20 + len(unique_body) * 5)

    role_class, title_signals = _title_role_relevance(job.title)
    details: list[str] = []

    if role_class == "non-target":
        score = min(score, 15)
        details.append("Title is outside configured role families; listing is kept but ranking is capped")
    elif role_class == "unclear":
        score = min(score, 35)
        details.append("Title has no clear configured role-family signal")
    elif role_class == "adjacent":
        score = min(score, 60)
        details.append("Title is adjacent to the target market; description evidence is required")
    else:
        details.append("Target title signal: " + ", ".join(title_signals))

    hits = (unique_title + unique_body)[:7]
    if hits:
        details.append("Technical matches: " + ", ".join(map(str, hits)))
    elif not details:
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
    seniority = _effective_seniority(job)
    score = 100
    reasons: list[str] = []

    if job_type == "internship" or seniority == "intern":
        return 0, ["Internships are excluded by profile"]
    if job_type == "freelance":
        return 0, ["Freelance roles are excluded by profile"]

    if any(re.search(pattern, text, re.I) for pattern in GERMAN_REQUIRED_PATTERNS):
        return 0, ["German appears to be a required language"]

    disallowed = {norm_text(x) for x in profile.get("seniority", {}).get("disallowed", [])}
    if seniority and norm_text(seniority) in disallowed:
        score = min(score, 10)
        reasons.append(f"Seniority '{seniority}' is outside the target level")

    min_years = job.min_years
    max_years = job.max_years
    if min_years is None and max_years is None:
        min_years, max_years = extract_years(text)
    if min_years is not None and min_years >= 3:
        score = min(score, 20 if min_years == 3 else 10)
        reasons.append(f"Requires about {min_years}+ years; target maximum is 2")

    salary_sensitive = job_type in {"part-time", "apprenticeship"} or "graduate" in text
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
    text = norm_text(f"{job.location} {job.remote_region}")
    allowed = [norm_text(x) for x in geo.get("allowed_local_locations", [])]

    if job.remote:
        worldwide = ["worldwide", "anywhere", "global", "all countries"]
        emea = ["emea", "africa", "middle east and africa", "mena"]
        europe = ["europe", "european union", "eu only", "eu residents", "europe only"]

        if any(x in text for x in worldwide):
            return 100, ["Remote role appears globally available"]
        if any(x in text for x in emea):
            return 90, ["Remote role targets EMEA/Africa"]
        if any(x in text for x in europe):
            return 40, ["Europe/EU-restricted remote role kept for visibility; Tunisia eligibility needs verification"]

        for region_name, signals in REGION_RESTRICTED_SIGNALS.items():
            if any(signal in text for signal in signals):
                return 10, [f"Remote role appears restricted to {region_name.upper()}, not Tunisia"]

        if not location and not region:
            return 65, ["Remote role with unspecified geographic eligibility; kept for verification"]
        return 25, [f"Remote role has an unverified geographic restriction: {job.location or job.remote_region or 'unknown'}"]

    if "tunisia" in location or "tunisie" in location or any(x and x in location for x in allowed):
        if any(x and x in location for x in allowed) or location in {"tunis", "tunisia", "tunisie"}:
            return 100, ["On-site/hybrid role is in the accepted Greater Tunis area or Tunisia-unspecified"]
        return 35, [f"Tunisian role appears outside the preferred Greater Tunis area: {job.location}"]

    if not location:
        return 50, ["Location is unknown; kept for manual review"]

    return 0, [f"Non-remote role outside Tunisia/Greater Tunis: {job.location}"]


def _experience_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    text = norm_text(f"{job.title} {job.description} {job.seniority}")
    seniority = _effective_seniority(job)
    min_years = job.min_years
    max_years = job.max_years
    if min_years is None and max_years is None:
        min_years, max_years = extract_years(text)

    if min_years is not None:
        if min_years <= 2:
            return 100, [f"Experience requirement ({min_years} year(s)) is within target"]
        if min_years == 3:
            return 15, ["Requires about 3 years; target maximum is 2"]
        return 0, [f"Requires about {min_years}+ years; above target"]

    if seniority in {"graduate", "entry-level", "junior", "associate"}:
        return 100, [f"Seniority '{seniority}' matches target"]
    if seniority in {"mid-level", "senior", "lead", "manager", "director", "principal", "staff"}:
        return 5, [f"Seniority '{seniority}' is above target"]
    if seniority == "intern":
        return 0, ["Internship seniority is excluded"]
    return 70, ["No explicit experience requirement found"]


def _apply_ranking_gates(job: JobRecord, overall: int, technical: int, eligibility: int, location: int, experience: int) -> tuple[int, list[str]]:
    """Prevent an incompatible dimension from being averaged away while retaining every job."""
    caps: list[tuple[int, str]] = []
    seniority = _effective_seniority(job)
    role_class, _ = _title_role_relevance(job.title)

    if eligibility == 0:
        caps.append((20, "Hard eligibility conflict caps ranking at 20"))
    elif seniority in {"mid-level", "senior", "lead", "manager", "director", "principal", "staff"}:
        caps.append((30, f"Title seniority '{seniority}' caps ranking at 30"))

    if job.min_years is not None:
        if job.min_years >= 4:
            caps.append((25, f"{job.min_years}+ years required caps ranking at 25"))
        elif job.min_years == 3:
            caps.append((40, "3 years required caps ranking at 40"))

    if not job.remote and location == 0:
        caps.append((15, "International on-site role is not accepted; ranking capped at 15"))
    elif job.remote and location <= 10:
        caps.append((30, "Remote geography appears incompatible with Tunisia; ranking capped at 30"))
    elif job.remote and location <= 40:
        caps.append((45, "Remote geography requires residency/region verification; ranking capped at 45"))

    if role_class == "non-target":
        caps.append((25, "Non-target title is retained but capped at 25"))
    elif role_class == "unclear" and technical <= 35:
        caps.append((55, "Unclear role-family fit caps ranking at 55 until reviewed"))

    if experience == 0 and seniority in {"senior", "lead", "manager", "director", "principal", "staff"}:
        caps.append((30, "Above-target experience/seniority caps ranking at 30"))

    if not caps:
        return overall, []

    cap, reason = min(caps, key=lambda item: item[0])
    return min(overall, cap), [reason]


def score_job(job: JobRecord, profile: dict, settings: dict) -> ScoreResult:
    # Title-first seniority is authoritative for ranking.
    job.seniority = _effective_seniority(job)
    if not job.employment_type:
        job.employment_type = detect_job_type(f"{job.title} {job.description}")
    if job.min_years is None and job.max_years is None:
        job.min_years, job.max_years = extract_years(f"{job.title} {job.description}")

    technical, e1 = _technical_score(job, profile)
    eligibility, e2 = _eligibility_score(job, profile)
    location, e3 = _location_score(job, profile)
    experience, e4 = _experience_score(job, profile)

    weights = settings.get("matching", {}).get("overall_weights", {})
    weighted = round(
        technical * float(weights.get("technical", 0.45))
        + eligibility * float(weights.get("eligibility", 0.20))
        + location * float(weights.get("location", 0.20))
        + experience * float(weights.get("experience", 0.15))
    )
    overall, gate_reasons = _apply_ranking_gates(job, weighted, technical, eligibility, location, experience)

    return ScoreResult(
        technical=technical,
        eligibility=eligibility,
        location=location,
        experience=experience,
        overall=max(0, min(100, overall)),
        explanations=e1 + e2 + e3 + e4 + gate_reasons,
    )


def is_stale(job: JobRecord, stale_after_days: int) -> bool:
    if not job.posted_at:
        return False
    dt = job.posted_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - dt
    return age.days > stale_after_days
