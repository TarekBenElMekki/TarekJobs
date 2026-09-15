from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .models import JobRecord, ScoreResult
from .normalize import detect_job_type, detect_seniority, extract_years, norm_text

TARGET_TITLE_SIGNALS = [
    "software", "developer", "backend", "frontend", "full stack", "python", "c++", "java", "rust",
    "embedded", "firmware", "iot", "robot", "ros", "automation", "automatisme", "plc", "mechatronic",
    "electronic", "electrical", "mechanical", "mecanique", "cad", "solidworks", "biomedical",
    "medical device", "medtech", "healthtech", "medical software", "computer vision", "machine learning",
    "artificial intelligence", "ai", "data analyst", "data engineer", "data scientist", "qa",
    "quality assurance", "test engineer", "test validation", "validation", "systems engineer", "system engineer",
    "application engineer", "integration", "solutions engineer", "solution engineer", "technical support",
    "it support", "support it", "support informatique", "service desk", "helpdesk", "technicien support", "sda",
    "business application", "erp", "business analyst", "technical consultant", "implementation consultant",
    "implementation engineer", "devops", "cloud", "cybersecurity", "network engineer", "networking",
    "research engineer", "r&d", "product engineer", "technical product", "clinical application",
    "application specialist", "infrastructure", "maintenance", "gpao",
]

NON_TARGET_TITLE_SIGNALS = [
    "graphic designer", "designer graphique", "account executive", "sales executive", "sales representative",
    "commercial", "marketing", "community manager", "recruiter", "recruitment", "human resources",
    "ressources humaines", "accountant", "comptable", "controller", "controleur de gestion", "finance manager",
    "legal", "lawyer", "juriste", "procurement", "buyer", "acheteur",
]

REGION_RESTRICTED_SIGNALS = {
    "usa": ["usa", "united states", "u.s.", "us only"],
    "canada": ["canada"],
    "uk": ["united kingdom", "uk only", "remote uk"],
    "apac": ["apac", "asia pacific"],
    "latam": ["latam", "latin america"],
    "australia": ["australia", "new zealand"],
}

LANGUAGE_ALIASES = {
    "Arabic": ["arabic", "arabe"],
    "English": ["english", "anglais", "anglaise"],
    "French": ["french", "francais", "française", "français"],
    "German": ["german", "allemand", "allemande", "deutsch"],
    "Italian": ["italian", "italien", "italienne"],
    "Spanish": ["spanish", "espagnol", "espagnole"],
    "Portuguese": ["portuguese", "portugais", "portugaise"],
    "Dutch": ["dutch", "neerlandais", "neerlandaise"],
    "Turkish": ["turkish", "turc", "turque"],
}

MANDATORY_LANGUAGE_SIGNALS = [
    "required", "mandatory", "must speak", "must have", "fluent", "bilingual", "bilingue", "courant",
    "couramment", "obligatoire", "requis", "requise", "maitrise", "maitriser", "maitrisant", "niveau",
    "parler", "langue", "language", "hispanophone", "francophone", "anglophone", "germanophone",
]
OPTIONAL_LANGUAGE_SIGNALS = [
    "preferred", "nice to have", "a plus", "plus", "souhaitable", "apprecie", "apprécie", "bonus",
]


def _contains_phrase(text: str, phrase: str) -> bool:
    """Token/phrase matching; 'ai' must not match inside 'italien'."""
    t = norm_text(text)
    p = norm_text(phrase)
    if not p:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", t) is not None


def _title_seniority(title: str) -> str:
    # Title is authoritative. French supervisory titles are included explicitly.
    t = f" {norm_text(title)} "
    ordered = [
        ("director", [r"\bdirector\b", r"\bdirecteur\b", r"\bdirectrice\b", r"\bhead of\b", r"\bchief\b", r"\bvice president\b", r"\bvp\b"]),
        ("principal", [r"\bprincipal\b"]),
        ("staff", [r"\bstaff\b"]),
        ("senior", [r"\bsenior\b", r"\bsr\b", r"\bconfirme\b", r"\bconfirmee\b"]),
        ("lead", [r"\bteam lead\b", r"\btech lead\b", r"\blead\b", r"\bchef d equipe\b", r"\bchef equipe\b", r"\bsuperviseur\b", r"\bsupervisor\b"]),
        ("manager", [r"\bmanager\b", r"\bresponsable\b"]),
        ("mid-level", [r"\bmid level\b", r"\bmid-level\b", r"\bintermediate\b"]),
        ("intern", [r"\bintern\b", r"\binternship\b", r"\bstage\b", r"\bstagiaire\b"]),
        ("graduate", [r"\bgraduate\b", r"\bfresh graduate\b", r"\bnew grad\b", r"\bjeune diplome\b"]),
        ("junior", [r"\bjunior\b", r"\bjr\b"]),
        ("entry-level", [r"\bentry level\b", r"\bentry-level\b", r"\bdebutant\b", r"\bdebutante\b"]),
        ("associate", [r"\bassociate\b"]),
    ]
    for label, patterns in ordered:
        if any(re.search(pattern, t, re.I) for pattern in patterns):
            return label
    return ""


def _effective_seniority(job: JobRecord) -> str:
    title_level = _title_seniority(job.title)
    if title_level:
        return title_level
    if job.seniority:
        return norm_text(job.seniority)
    return detect_seniority(job.description)


def _title_role_relevance(title: str) -> tuple[str, list[str]]:
    non_target = [signal for signal in NON_TARGET_TITLE_SIGNALS if _contains_phrase(title, signal)]
    if non_target:
        return "non-target", non_target[:3]
    target = [signal for signal in TARGET_TITLE_SIGNALS if _contains_phrase(title, signal)]
    if target:
        return "target", target[:5]
    t = f" {norm_text(title)} "
    if any(term in t for term in [" engineer ", " ingenieur ", " ingénieur ", " analyst ", " support ", " specialist ", " consultant ", " research ", " technicien "]):
        return "adjacent", []
    return "unclear", []


def extract_required_languages(job: JobRecord, profile: dict) -> list[dict[str, Any]]:
    title = norm_text(job.title)
    description = norm_text(job.description)
    sentences = [norm_text(x) for x in re.split(r"[\n\r.!?;:]+", job.description or "") if norm_text(x)]
    result: list[dict[str, Any]] = []

    for canonical, aliases in LANGUAGE_ALIASES.items():
        alias_hit = next((a for a in aliases if _contains_phrase(title, a)), None)
        evidence = ""
        mandatory = False
        optional = False

        if alias_hit:
            mandatory = True
            evidence = f"Title mentions {canonical}"
        else:
            for sentence in sentences:
                found = next((a for a in aliases if _contains_phrase(sentence, a)), None)
                if not found:
                    continue
                optional_here = any(signal in sentence for signal in OPTIONAL_LANGUAGE_SIGNALS)
                mandatory_here = any(signal in sentence for signal in MANDATORY_LANGUAGE_SIGNALS)
                if optional_here and not mandatory_here:
                    optional = True
                    evidence = sentence[:220]
                    continue
                if mandatory_here:
                    mandatory = True
                    evidence = sentence[:220]
                    break

        if mandatory or optional:
            result.append({
                "language": canonical,
                "mandatory": bool(mandatory),
                "optional": bool(optional and not mandatory),
                "evidence": evidence,
            })

    return result


def extract_requirements(job: JobRecord, profile: dict) -> tuple[dict[str, Any], dict[str, str]]:
    text = f"{job.title} {job.description}"
    min_years = job.min_years
    max_years = job.max_years
    if min_years is None and max_years is None:
        min_years, max_years = extract_years(text)

    languages = extract_required_languages(job, profile)
    detected_terms: list[str] = []
    for keyword in profile.get("target_keywords", []) + profile.get("skills", []):
        if _contains_phrase(text, keyword):
            detected_terms.append(str(keyword))
    detected_terms = list(dict.fromkeys(detected_terms))[:18]

    education_terms = []
    t = norm_text(text)
    for label, patterns in {
        "Bac+3 / Licence / Bachelor": ["bac+3", "licence", "bachelor"],
        "Bac+5 / Master": ["bac+5", "master", "mastère", "mastere"],
        "Engineering degree": ["engineering degree", "diplome d ingenieur", "diplôme d ingénieur", "ingenieur"],
    }.items():
        if any(norm_text(p) in t for p in patterns):
            education_terms.append(label)

    requirements = {
        "languages": languages,
        "experience": {"min_years": min_years, "max_years": max_years},
        "education_signals": education_terms,
        "technical_terms": detected_terms,
        "employment_type": job.employment_type or detect_job_type(text),
        "seniority": _effective_seniority(job),
    }

    confidence = {
        "languages": "high" if languages else "medium",
        "experience": "high" if min_years is not None else "medium",
        "salary": "high" if job.salary_min is not None and job.salary_currency else ("medium" if job.salary_min is not None else "low"),
        "posted_at": "high" if job.posted_at is not None else "low",
        "company": "low" if norm_text(job.company) in {"", "unknown", "entreprise anonyme"} else "medium",
        "location": "high" if job.location else "low",
    }
    return requirements, confidence


def _technical_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    title_hits: list[str] = []
    body_hits: list[str] = []
    for keyword in profile.get("target_keywords", []) + profile.get("skills", []):
        if not norm_text(keyword):
            continue
        if _contains_phrase(job.title, keyword):
            title_hits.append(str(keyword))
        elif _contains_phrase(job.description, keyword):
            body_hits.append(str(keyword))

    unique_title = list(dict.fromkeys(title_hits))
    unique_body = list(dict.fromkeys(body_hits))
    score = min(100, len(unique_title) * 20 + len(unique_body) * 5)
    role_class, title_signals = _title_role_relevance(job.title)
    details: list[str] = []

    if role_class == "non-target":
        score = min(score, 15)
        details.append("BLOCKER: Title is outside configured role families")
    elif role_class == "unclear":
        score = min(score, 35)
        details.append("VERIFY: Title has no clear configured role-family signal")
    elif role_class == "adjacent":
        score = max(score, 20)
        score = min(score, 60)
        details.append("VERIFY: Title is adjacent to the target market")
    else:
        score = max(score, 35)
        details.append("MATCH: Target title signal: " + ", ".join(title_signals))

    hits = (unique_title + unique_body)[:7]
    if hits:
        details.append("MATCH: Technical terms: " + ", ".join(hits))
    elif role_class not in {"non-target", "target"}:
        details.append("VERIFY: No strong target-skill keyword match detected")
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


def _eligibility_score(job: JobRecord, profile: dict, requirements: dict) -> tuple[int, list[str]]:
    text = norm_text(f"{job.title} {job.description} {job.employment_type} {job.seniority}")
    job_type = job.employment_type or detect_job_type(text)
    seniority = _effective_seniority(job)
    score = 100
    reasons: list[str] = []

    configured_types = profile.get("job_types", {})
    type_key = {
        "full-time": "full_time", "part-time": "part_time", "internship": "internship",
        "apprenticeship": "apprenticeship", "civp": "civp", "freelance": "freelance",
        "fixed-term": "fixed_term", "contract": "remote_contract" if job.remote else "fixed_term",
    }.get(job_type)
    if seniority == "intern":
        type_key = "internship"
    if type_key and configured_types.get(type_key) is False:
        return 0, [f"BLOCKER: Job type '{job_type or seniority}' is excluded by profile"]

    spoken = {norm_text(name) for name in profile.get("languages", {}).keys()}
    unsupported = [
        item["language"] for item in requirements.get("languages", [])
        if item.get("mandatory") and norm_text(item.get("language")) not in spoken
    ]
    if unsupported:
        return 0, ["BLOCKER: Mandatory language not in profile: " + ", ".join(unsupported)]

    disallowed = {norm_text(x) for x in profile.get("seniority", {}).get("disallowed", [])}
    if seniority and norm_text(seniority) in disallowed:
        score = min(score, 10)
        reasons.append(f"BLOCKER: Seniority '{seniority}' is outside the target level")

    min_years = requirements.get("experience", {}).get("min_years")
    target_max = int(profile.get("seniority", {}).get("max_required_years", 2))
    if min_years is not None and min_years > target_max:
        score = min(score, 20 if min_years == target_max + 1 else 10)
        reasons.append(f"BLOCKER: Requires about {min_years}+ years; target maximum is {target_max}")

    salary_sensitive = job_type in {"part-time", "apprenticeship"} or _contains_phrase(text, "graduate")
    if salary_sensitive:
        salary = _monthly_tnd(job)
        threshold = profile.get("salary_preferences", {}).get("part_time_min_monthly_tnd", 1500)
        if salary is not None:
            if salary < threshold:
                score = min(score, 25)
                reasons.append(f"BLOCKER: Known monthly pay ({salary:.0f} TND) is below {threshold} TND preference")
            else:
                reasons.append(f"MATCH: Known monthly pay meets the {threshold} TND preference")
        else:
            score = min(score, 75)
            reasons.append("VERIFY: Salary-sensitive role has no reliable salary")

    if not reasons:
        reasons.append("MATCH: No hard eligibility conflict detected")
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
        if any(_contains_phrase(text, x) for x in worldwide):
            return 100, ["MATCH: Remote role appears globally available"]
        if any(_contains_phrase(text, x) for x in emea):
            return 90, ["MATCH: Remote role targets EMEA/Africa"]
        if any(_contains_phrase(text, x) for x in europe):
            return 40, ["VERIFY: Europe/EU-restricted remote role; Tunisia eligibility needs verification"]
        for region_name, signals in REGION_RESTRICTED_SIGNALS.items():
            if any(_contains_phrase(text, signal) for signal in signals):
                return 10, [f"BLOCKER: Remote role appears restricted to {region_name.upper()}, not Tunisia"]
        if not location and not region:
            return 70, ["VERIFY: Remote role has unspecified geographic eligibility"]
        return 45, [f"VERIFY: Remote role has a geographic restriction: {job.location or job.remote_region or 'unknown'}"]

    if any(x and _contains_phrase(location, x) for x in allowed) or location == "tunis":
        return 100, ["MATCH: On-site/hybrid role is in an accepted Greater Tunis location"]

    if location in {"tunisia", "tunisie"}:
        return 55, ["VERIFY: Tunisian location is too broad; city must be confirmed before applying"]

    if "tunisia" in location or "tunisie" in location:
        return 0, [f"BLOCKER: Tunisian on-site role is outside the accepted Greater Tunis area: {job.location}"]

    if not location:
        return 55, ["VERIFY: Location is unknown"]
    return 0, [f"BLOCKER: Non-remote role outside Tunisia/Greater Tunis: {job.location}"]


def _experience_score(job: JobRecord, requirements: dict, profile: dict) -> tuple[int, list[str]]:
    seniority = _effective_seniority(job)
    min_years = requirements.get("experience", {}).get("min_years")

    target_max = int(profile.get("seniority", {}).get("max_required_years", 2))
    if min_years is not None:
        if min_years <= target_max:
            return 100, [f"MATCH: Experience requirement ({min_years} year(s)) is within target"]
        if min_years == target_max + 1:
            return 20, [f"BLOCKER: Requires about {min_years} years; target maximum is {target_max}"]
        return 0, [f"BLOCKER: Requires about {min_years}+ years; target maximum is {target_max}"]

    if seniority in {"graduate", "entry-level", "junior", "associate"}:
        return 100, [f"MATCH: Seniority '{seniority}' matches target"]
    if seniority in {"mid-level", "senior", "lead", "manager", "director", "principal", "staff"}:
        return 5, [f"BLOCKER: Seniority '{seniority}' is above target"]
    if seniority == "intern":
        return 0, ["BLOCKER: Internship seniority is excluded"]
    return 70, ["VERIFY: No explicit experience requirement found"]


def _ranking_caps(job: JobRecord, technical: int, eligibility: int, location: int, experience: int, settings: dict) -> list[tuple[int, str]]:
    caps: list[tuple[int, str]] = []
    cfg = settings.get("matching", {}).get("ranking_caps", {})
    def cap(name: str, default: int) -> int:
        return int(cfg.get(name, default))
    seniority = _effective_seniority(job)
    role_class, _ = _title_role_relevance(job.title)
    if role_class == "non-target":
        caps.append((cap("non_target_title", 25), f"Non-target title caps ranking at {cap('non_target_title', 25)}"))
    if location == 0:
        local_text = norm_text(job.location)
        if ("tunisia" in local_text or "tunisie" in local_text) and not job.remote:
            caps.append((cap("local_outside_allowed", 15), f"Tunisian on-site role outside accepted Greater Tunis caps ranking at {cap('local_outside_allowed', 15)}"))
        else:
            caps.append((cap("international_onsite", 15), f"International on-site incompatibility caps ranking at {cap('international_onsite', 15)}"))
    elif location <= 10:
        caps.append((cap("remote_geo_incompatible", 30), f"Remote geography incompatibility caps ranking at {cap('remote_geo_incompatible', 30)}"))
    if eligibility == 0:
        caps.append((cap("hard_eligibility", 15), f"Hard eligibility conflict caps ranking at {cap('hard_eligibility', 15)}"))
    if seniority in {"senior", "lead", "manager", "director", "principal", "staff"}:
        caps.append((cap("senior_title", 30), f"Title seniority '{seniority}' caps ranking at {cap('senior_title', 30)}"))
    if experience == 20:
        caps.append((cap("three_years", 40), f"3-year experience requirement caps ranking at {cap('three_years', 40)}"))
    elif experience == 0:
        caps.append((cap("four_plus_years", 25), f"Above-target experience requirement caps ranking at {cap('four_plus_years', 25)}"))
    if technical <= 15 and role_class != "target":
        caps.append((cap("weak_technical", 30), f"Very weak technical relevance caps ranking at {cap('weak_technical', 30)}"))
    return caps


def score_job(job: JobRecord, profile: dict, settings: dict) -> ScoreResult:
    job.seniority = _effective_seniority(job)
    if not job.employment_type:
        job.employment_type = detect_job_type(f"{job.title} {job.description}")
    if job.min_years is None and job.max_years is None:
        job.min_years, job.max_years = extract_years(f"{job.title} {job.description}")

    requirements, confidence = extract_requirements(job, profile)
    technical, e1 = _technical_score(job, profile)
    eligibility, e2 = _eligibility_score(job, profile, requirements)
    location, e3 = _location_score(job, profile)
    experience, e4 = _experience_score(job, requirements, profile)

    weights = settings.get("matching", {}).get("overall_weights", {})
    overall = round(
        technical * float(weights.get("technical", 0.45))
        + eligibility * float(weights.get("eligibility", 0.20))
        + location * float(weights.get("location", 0.20))
        + experience * float(weights.get("experience", 0.15))
    )

    explanations = e1 + e2 + e3 + e4
    caps = _ranking_caps(job, technical, eligibility, location, experience, settings)
    if caps:
        cap_value, cap_reason = min(caps, key=lambda item: item[0])
        if overall > cap_value:
            overall = cap_value
        explanations.append("RULE: " + cap_reason)

    mandatory_unsupported = any(
        item.get("mandatory")
        and norm_text(item.get("language")) not in {norm_text(x) for x in profile.get("languages", {}).keys()}
        for item in requirements.get("languages", [])
    )

    blocker_reasons = [
        reason for reason in explanations
        if reason.startswith("BLOCKER:")
    ]
    verification_reasons = [
        reason for reason in explanations
        if reason.startswith("VERIFY:")
    ]

    if mandatory_unsupported or eligibility <= 20 or location <= 15 or experience <= 5 or blocker_reasons:
        verdict = "blocked"
    elif verification_reasons:
        verdict = "needs_verification"
    elif technical >= 30 and eligibility >= 70 and location >= 60 and experience >= 70 and overall >= 45:
        verdict = "actionable"
    else:
        verdict = "needs_verification"

    return ScoreResult(
        technical=technical,
        eligibility=eligibility,
        location=location,
        experience=experience,
        overall=max(0, min(100, overall)),
        verdict=verdict,
        explanations=explanations,
        requirements=requirements,
        confidence=confidence,
    )


def is_stale(job: JobRecord, stale_after_days: int) -> bool:
    if not job.posted_at:
        return False
    dt = job.posted_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - dt
    return age.days > stale_after_days
