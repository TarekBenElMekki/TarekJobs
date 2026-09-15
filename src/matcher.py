from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .models import JobRecord, ScoreResult
from .normalize import canonical_job_type, detect_job_type, detect_seniority, extract_years, norm_text

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
    "phd", "doctoral", "doctoral researcher", "doctoral candidate", "research assistant",
]

NON_TARGET_TITLE_SIGNALS = [
    "graphic designer", "designer graphique", "account executive", "sales executive", "sales representative",
    "commercial", "marketing", "community manager", "recruiter", "recruitment", "human resources",
    "ressources humaines", "accountant", "comptable", "controller", "controleur de gestion", "finance manager",
    "legal", "lawyer", "juriste", "procurement", "buyer", "acheteur",
    "office assistant", "administrative assistant", "virtual assistant",
    "bookkeeper", "bookkeeping assistant",
    "inside sales", "business development representative", "business development rep",
    "sales development representative", "sales development rep", "sdr", "bdr",
    "business developer", "teleprospector", "teleprospection",
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
    "Bulgarian": ["bulgarian", "bulgare"],
    "Romanian": ["romanian", "roumain", "roumaine"],
    "Polish": ["polish", "polonais", "polonaise"],
    "Czech": ["czech", "tcheque", "tchèque"],
    "Slovak": ["slovak", "slovaque"],
    "Hungarian": ["hungarian", "hongrois", "hongroise"],
    "Greek": ["greek", "grec", "grecque"],
    "Swedish": ["swedish", "suedois", "suédois", "suedoise", "suédoise"],
    "Norwegian": ["norwegian", "norvegien", "norvégien", "norvegienne", "norvégienne"],
    "Danish": ["danish", "danois", "danoise"],
    "Finnish": ["finnish", "finnois", "finnoise"],
    "Russian": ["russian", "russe"],
    "Ukrainian": ["ukrainian", "ukrainien", "ukrainienne"],
    "Japanese": ["japanese", "japonais", "japonaise"],
    "Korean": ["korean", "coreen", "coréen", "coreenne", "coréenne"],
    "Chinese": ["chinese", "mandarin", "chinois", "chinoise"],
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
        ("lead", [r"\bteam lead\b", r"\bteam leader\b", r"\btech lead\b", r"\blead\b", r"\bchef d equipe\b", r"\bchef equipe\b", r"\bsuperviseur\b", r"\bsupervisor\b"]),
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
    """Prefer title evidence and distrust stale derived seniority metadata.

    Earlier collectors sometimes persisted a seniority inferred from broad
    description text. That value can outlive later parser fixes. A high-impact
    seniority value is therefore accepted only when current text supports it.
    """
    title_level = _title_seniority(job.title)
    if title_level:
        return title_level

    stored = norm_text(job.seniority or "")
    description_level = detect_seniority(job.description or "")

    if stored and description_level and stored == description_level:
        return stored

    # Experience requirements are scored separately, so an unspecified title
    # is safer than inventing Intern/Senior from unrelated boilerplate.
    return ""


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
        "Doctoral / PhD": ["phd", "doctoral", "doctorate", "doctorat"],
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
    score = min(100, len(unique_title) * 18 + len(unique_body) * 5)
    role_class, title_signals = _title_role_relevance(job.title)
    details: list[str] = []

    if (job.opportunity_type or "job").lower() == "phd":
        # Research titles often do not repeat implementation keywords in the title.
        score = max(score, 45 if unique_body else 35)
        details.append("MATCH: Doctoral/research opportunity uses the research-fit pathway")
    elif role_class == "non-target":
        score = min(score, 15)
        details.append("BLOCKER: Title is outside configured role families")
    elif role_class == "unclear":
        score = min(score, 40)
        details.append("VERIFY: Title has no clear configured role-family signal")
    elif role_class == "adjacent":
        score = max(score, 25)
        score = min(score, 65)
        details.append("VERIFY: Title is adjacent to the target market")
    else:
        score = max(score, 40)
        details.append("MATCH: Target title signal: " + ", ".join(title_signals))

    hits = (unique_title + unique_body)[:8]
    if hits:
        details.append("MATCH: Technical terms: " + ", ".join(hits))
    elif role_class not in {"non-target", "target"}:
        details.append("VERIFY: No strong target-skill keyword match detected")
    return score, details


COUNTRY_ALIASES = {
    "Germany": ["germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "stuttgart", "frankfurt", "dresden"],
    "Netherlands": ["netherlands", "nederland", "amsterdam", "eindhoven", "enschede", "rotterdam", "delft", "maassluis", "venlo"],
    "Switzerland": ["switzerland", "schweiz", "suisse", "zurich", "zürich", "lausanne", "geneva", "genève", "epfl", "eth"],
    "Denmark": ["denmark", "danmark", "copenhagen", "københavn", "aalborg", "aarhus"],
    "Sweden": ["sweden", "sverige", "stockholm", "gothenburg", "göteborg", "lund", "uppsala"],
    "Norway": ["norway", "norge", "oslo", "trondheim", "bergen"],
    "Belgium": ["belgium", "belgique", "brussels", "bruxelles", "leuven", "ghent", "gent"],
    "France": ["france", "paris", "lyon", "grenoble", "toulouse", "lille", "strasbourg"],
    "Austria": ["austria", "österreich", "vienna", "wien", "graz", "linz"],
    "Canada": ["canada", "toronto", "montreal", "montréal", "vancouver", "ottawa", "waterloo"],
    "USA": ["united states", "usa", "u.s.", "new york", "california", "boston", "seattle", "texas"],
    "UK": ["united kingdom", "uk", "england", "scotland", "london", "cambridge", "oxford", "manchester"],
    "Australia": ["australia", "sydney", "melbourne", "brisbane", "perth"],
    "Tunisia": ["tunisia", "tunisie", "tunis", "ariana", "ben arous", "manouba", "sousse", "sfax"],
}


def _country_from_job(job: JobRecord) -> str:
    if job.country:
        return job.country.strip()
    text = norm_text(f"{job.location} {job.remote_region}")
    for country, aliases in COUNTRY_ALIASES.items():
        if any(_contains_phrase(text, alias) for alias in aliases):
            return country
    return ""


def _country_tier(country: str, profile: dict) -> int | None:
    geo = profile.get("geography", {})
    n = norm_text(country)
    tiers = geo.get("international_priority_tiers", {})
    for label, countries in tiers.items():
        if n in {norm_text(x) for x in countries}:
            m = re.search(r"(\d+)", str(label))
            return int(m.group(1)) if m else None
    return None


def _hard_work_authorization_block(job: JobRecord) -> str | None:
    if job.visa_sponsorship is False or job.international_candidates is False:
        return "Employer metadata indicates no sponsorship/international eligibility"
    text = norm_text(f"{job.title} {job.description}")
    blockers = [
        "no visa sponsorship", "visa sponsorship is not available", "without sponsorship",
        "must be authorized to work", "must have the right to work", "existing right to work",
        "eu citizenship required", "eu citizen required", "european union citizen",
        "security clearance required", "us citizen required", "u.s. citizen required",
    ]
    for signal in blockers:
        if signal in text:
            return f"Work-authorization restriction detected: {signal}"
    return None


def _positive_mobility_signal(job: JobRecord) -> str | None:
    if job.visa_sponsorship is True:
        return "Employer explicitly indicates visa sponsorship"
    if job.relocation_support is True:
        return "Employer explicitly indicates relocation support"
    text = norm_text(f"{job.title} {job.description}")
    positives = [
        "visa sponsorship", "work permit support", "relocation package", "relocation support",
        "international applicants", "international candidates", "global mobility",
    ]
    for signal in positives:
        if signal in text:
            return f"Mobility signal detected: {signal}"
    return None


def _education_score(job: JobRecord, profile: dict, requirements: dict) -> tuple[int, list[str]]:
    completed = [x for x in profile.get("education", []) if x.get("completed")]
    has_master = any("master" in norm_text(x.get("degree_level")) or "mastere" in norm_text(x.get("degree_level")) for x in completed)
    has_bachelor = bool(completed)
    signals = requirements.get("education_signals", [])
    text = norm_text(f"{job.education_requirement} {job.description}")

    if (job.opportunity_type or "job").lower() == "phd":
        if has_master:
            return 100, ["MATCH: Completed Master's degree supports doctoral eligibility"]
        return 20, ["BLOCKER: Doctoral opportunity normally requires a completed Master's-equivalent degree"]

    if any(x in text for x in ["phd required", "doctorate required"]):
        return 0, ["BLOCKER: Role explicitly requires a PhD/doctorate"]
    if signals:
        if has_master or has_bachelor:
            return 100, ["MATCH: Education level is compatible with detected degree requirement"]
        return 35, ["VERIFY: Degree requirement detected but profile education could not be confirmed"]
    return 80, ["VERIFY: No explicit education requirement detected"]


def _domain_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    text = norm_text(f"{job.title} {job.description}")
    primary = [
        "robot", "ros", "ros2", "embedded", "firmware", "medical device", "biomedical",
        "mechatronic", "control", "automation", "autonomous", "sensor", "computer vision", "r&d",
    ]
    secondary = ["software", "python", "c++", "systems", "integration", "iot", "machine learning", "ai"]
    primary_hits = [x for x in primary if _contains_phrase(text, x)]
    secondary_hits = [x for x in secondary if _contains_phrase(text, x)]
    role_class, _ = _title_role_relevance(job.title)
    if role_class == "non-target" and (job.opportunity_type or "job").lower() == "job":
        return 5, ["BLOCKER: Opportunity domain is outside the configured career families"]
    if primary_hits:
        return min(100, 70 + 8 * min(4, len(primary_hits))), ["MATCH: Priority engineering domain: " + ", ".join(primary_hits[:5])]
    if (job.opportunity_type or "job").lower() == "phd" and secondary_hits:
        return 75, ["MATCH: Research domain overlaps software/intelligent-systems profile"]
    if secondary_hits:
        return 65, ["MATCH: Secondary software/AI domain overlap: " + ", ".join(secondary_hits[:4])]
    return 35, ["VERIFY: Domain fit is unclear"]


def _language_score(job: JobRecord, profile: dict, requirements: dict) -> tuple[int, list[str]]:
    spoken = {norm_text(name) for name in profile.get("languages", {}).keys()}
    mandatory = [x for x in requirements.get("languages", []) if x.get("mandatory")]
    unsupported = [x["language"] for x in mandatory if norm_text(x.get("language")) not in spoken]
    if unsupported:
        return 0, ["BLOCKER: Mandatory language not in profile: " + ", ".join(unsupported)]
    if mandatory:
        return 100, ["MATCH: Mandatory language requirement is represented in profile"]
    optional_unsupported = [
        x["language"] for x in requirements.get("languages", [])
        if x.get("optional") and norm_text(x.get("language")) not in spoken
    ]
    if optional_unsupported:
        return 80, ["VERIFY: Optional language not in profile: " + ", ".join(optional_unsupported)]
    return 90, ["MATCH: No unsupported mandatory language detected"]


def _visa_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    geo = profile.get("geography", {})
    country = _country_from_job(job)
    if job.remote:
        text = norm_text(f"{job.location} {job.remote_region}")
        if any(_contains_phrase(text, x) for x in ["worldwide", "anywhere", "global", "all countries"]):
            return 100, ["MATCH: Remote opportunity appears globally accessible"]
        if any(_contains_phrase(text, x) for x in ["emea", "africa", "mena", "middle east and africa"]):
            return 95, ["MATCH: Remote opportunity covers Africa/EMEA"]
        if any(_contains_phrase(text, x) for x in ["europe", "eu only", "europe only", "eu residents"]):
            return 35, ["VERIFY: Europe/EU remote eligibility requires confirmation for a Tunisia-based applicant"]
        for region_name, signals in REGION_RESTRICTED_SIGNALS.items():
            if any(_contains_phrase(text, signal) for signal in signals):
                return 5, [f"BLOCKER: Remote opportunity appears restricted to {region_name.upper()}"]
        return 70, ["VERIFY: Remote geographic eligibility is not explicit"]

    if norm_text(country) == "tunisia":
        return 100, ["MATCH: No international work permit is required for Tunisia"]

    hard = _hard_work_authorization_block(job)
    if hard:
        return 0, ["BLOCKER: " + hard]
    positive = _positive_mobility_signal(job)
    if positive:
        return 100, ["MATCH: " + positive]

    if geo.get("relocation") and geo.get("international_onsite"):
        tier = _country_tier(country, profile)
        if tier in {1, 2, 3}:
            return 60, [f"VERIFY: Target-country role (Tier {tier}); sponsorship/work-permit support is not stated"]
        if country:
            return 50, [f"VERIFY: International on-site role in {country}; work-permit feasibility is unknown"]
        return 45, ["VERIFY: International location/work-permit feasibility is unknown"]
    return 10, ["BLOCKER: International on-site relocation is disabled in profile"]


def _location_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    geo = profile.get("geography", {})
    location = norm_text(job.location)
    region = norm_text(job.remote_region)
    text = norm_text(f"{job.location} {job.remote_region}")
    allowed = [norm_text(x) for x in geo.get("allowed_local_locations", [])]

    if job.remote:
        if any(_contains_phrase(text, x) for x in ["worldwide", "anywhere", "global", "all countries"]):
            return 100, ["MATCH: Remote role appears globally available"]
        if any(_contains_phrase(text, x) for x in ["emea", "africa", "middle east and africa", "mena"]):
            return 95, ["MATCH: Remote role targets EMEA/Africa"]
        if any(_contains_phrase(text, x) for x in ["europe", "european union", "eu only", "eu residents", "europe only"]):
            return 55, ["VERIFY: Europe/EU-restricted remote role; Tunisia eligibility needs verification"]
        for region_name, signals in REGION_RESTRICTED_SIGNALS.items():
            if any(_contains_phrase(text, signal) for signal in signals):
                return 15, [f"BLOCKER: Remote role appears restricted to {region_name.upper()}, not Tunisia"]
        if not location and not region:
            return 75, ["VERIFY: Remote role has unspecified geographic eligibility"]
        return 60, [f"VERIFY: Remote role has a geographic restriction: {job.location or job.remote_region or 'unknown'}"]

    country = _country_from_job(job)
    if norm_text(country) == "tunisia":
        if any(x and _contains_phrase(location, x) for x in allowed) or location == "tunis":
            return 90, ["MATCH: Tunisia role is in a historically preferred Greater Tunis location"]
        if location in {"tunisia", "tunisie"}:
            return 70, ["VERIFY: Tunisian city is not specified"]
        return 55, [f"VERIFY: Tunisia role is outside the historically preferred Greater Tunis area: {job.location}"]

    tier = _country_tier(country, profile)
    if tier == 1:
        return 100, [f"MATCH: Tier 1 international target market: {country}"]
    if tier == 2:
        return 90, [f"MATCH: Tier 2 international target market: {country}"]
    if tier == 3:
        return 75, [f"MATCH: Tier 3 selective international market: {country}"]
    if country:
        return 60, [f"VERIFY: International market outside configured priority tiers: {country}"]
    if not location:
        return 55, ["VERIFY: Location is unknown"]
    return 55, [f"VERIFY: Country could not be normalized from location: {job.location}"]


def _experience_score(job: JobRecord, requirements: dict, profile: dict) -> tuple[int, list[str]]:
    seniority = _effective_seniority(job)
    min_years = requirements.get("experience", {}).get("min_years")
    seniority_cfg = profile.get("seniority", {})
    strong_max = int(seniority_cfg.get("max_required_years", 3))
    stretch_max = int(seniority_cfg.get("stretch_max_required_years", 5))

    if (job.opportunity_type or "job").lower() == "phd":
        return 100, ["MATCH: Professional years are not used as a doctoral seniority barrier"]

    if min_years is not None:
        if min_years <= strong_max:
            return 100, [f"MATCH: Experience requirement ({min_years} year(s)) is within early-career target"]
        if min_years <= stretch_max:
            score = 45 if min_years == strong_max + 1 else 25
            return score, [f"VERIFY: {min_years}+ years is a stretch application for the current profile"]
        return 0, [f"BLOCKER: Requires about {min_years}+ years; above configured stretch ceiling ({stretch_max})"]

    if seniority in {"graduate", "entry-level", "junior", "associate"}:
        return 100, [f"MATCH: Seniority '{seniority}' matches early-career target"]
    if seniority in {"mid-level"}:
        return 45, ["VERIFY: Mid-level title is a stretch"]
    if seniority in {"senior", "lead", "manager", "director", "principal", "staff"}:
        return 10, [f"BLOCKER: Seniority '{seniority}' is above target"]
    if seniority == "intern":
        return 20, ["BLOCKER: Internship seniority is excluded by current job-type policy"]
    return 75, ["VERIFY: No explicit experience requirement found"]


def _salary_score(job: JobRecord, profile: dict) -> tuple[int, list[str]]:
    if (job.opportunity_type or "job").lower() == "phd":
        funding = norm_text(job.funding_status)
        if "self funded" in funding or "self-funded" in funding:
            return 0, ["BLOCKER: Doctoral opportunity appears self-funded"]
        if any(x in funding for x in ["salaried", "fully funded", "funded", "employment contract"]):
            return 100, ["MATCH: Doctoral opportunity is explicitly funded/salaried"]
        if job.salary_min is not None:
            return 95, ["MATCH: Doctoral compensation is explicitly stated"]
        if job.tuition_covered is True:
            return 85, ["MATCH: Doctoral tuition is explicitly covered"]
        return 45, ["VERIFY: PhD funding/compensation is not yet confirmed"]

    if job.salary_min is None:
        return 50, []
    # V0.5.0 intentionally avoids exchange-rate assumptions. Precise economic
    # normalization is reserved for V0.5.1; explicit salary data still earns
    # a stronger evidence score than an unknown salary.
    return 80, ["MATCH: Compensation is explicitly stated; V0.5.1 will normalize purchasing/economic value"]


def _eligibility_score(
    job: JobRecord,
    profile: dict,
    requirements: dict,
    education: int,
    visa: int,
    language: int,
    experience: int,
    salary: int,
) -> tuple[int, str, list[str]]:
    reasons: list[str] = []
    job_type = canonical_job_type(
        job.employment_type or detect_job_type(f"{job.title} {job.description}"),
        job.remote,
    )
    seniority = _effective_seniority(job)

    if (job.opportunity_type or "job").lower() == "job":
        configured_types = profile.get("job_types", {})
        type_key = {
            "full-time": "full_time", "part-time": "part_time", "internship": "internship",
            "apprenticeship": "apprenticeship", "civp": "civp", "freelance": "freelance",
            "fixed-term": "fixed_term", "contract": "remote_contract" if job.remote else "fixed_term",
        }.get(job_type)
        if seniority == "intern" and configured_types.get("internship") is False:
            reasons.append("BLOCKER: Internship seniority is excluded by profile")
        if type_key and configured_types.get(type_key) is False:
            reasons.append(f"BLOCKER: Job type '{job_type}' is excluded by profile")

    if education == 0:
        reasons.append("BLOCKER: Education requirement is not met")
    if language == 0:
        reasons.append("BLOCKER: Mandatory language requirement is not met")
    if visa <= 5:
        reasons.append("BLOCKER: Work authorization / geographic eligibility conflict")
    if experience == 0:
        reasons.append("BLOCKER: Experience requirement is above configured stretch ceiling")
    if seniority in {"senior", "lead", "manager", "director", "principal", "staff"}:
        reasons.append(f"BLOCKER: Seniority '{seniority}' is outside the early-career target")
    if salary == 0 and (job.opportunity_type or "job").lower() == "phd":
        reasons.append("BLOCKER: PhD appears not to be funded")

    role_class, _ = _title_role_relevance(job.title)
    if role_class == "non-target" and (job.opportunity_type or "job").lower() == "job":
        reasons.append("BLOCKER: Job title belongs to a non-target role family")

    if reasons:
        return 0, "blocked", reasons

    verification = []
    if visa < 80:
        verification.append("VERIFY: Visa/work-permit feasibility requires confirmation")
    if education < 80:
        verification.append("VERIFY: Education compatibility requires confirmation")
    if experience < 60:
        verification.append("VERIFY: Experience requirement is stretch-level")
    if (job.opportunity_type or "job").lower() == "phd" and salary < 70:
        verification.append("VERIFY: PhD funding must be confirmed before application")
    if job.is_open is False:
        return 0, "blocked", ["BLOCKER: Opportunity is marked closed"]

    if verification:
        return 60, "needs_verification", verification
    return 100, "eligible", ["MATCH: No hard eligibility conflict detected"]


def _priority(overall: int) -> str:
    if overall >= 80:
        return "A"
    if overall >= 65:
        return "B"
    if overall >= 50:
        return "C"
    if overall >= 35:
        return "stretch"
    return "discard"


def score_job(job: JobRecord, profile: dict, settings: dict) -> ScoreResult:
    job.opportunity_type = (job.opportunity_type or "job").lower().strip()
    if job.opportunity_type not in {"job", "phd"}:
        job.opportunity_type = "job"

    job.seniority = _effective_seniority(job)
    if job.opportunity_type == "job":
        job.employment_type = canonical_job_type(
            job.employment_type or detect_job_type(f"{job.title} {job.description}"),
            job.remote,
        )
    elif not job.employment_type:
        job.employment_type = "doctoral"

    if job.min_years is None and job.max_years is None and job.opportunity_type == "job":
        job.min_years, job.max_years = extract_years(f"{job.title} {job.description}")

    requirements, confidence = extract_requirements(job, profile)
    requirements["opportunity_type"] = job.opportunity_type
    requirements["country"] = _country_from_job(job)

    technical, e1 = _technical_score(job, profile)
    experience, e2 = _experience_score(job, requirements, profile)
    education, e3 = _education_score(job, profile, requirements)
    domain, e4 = _domain_score(job, profile)
    visa, e5 = _visa_score(job, profile)
    salary, e6 = _salary_score(job, profile)
    location, e7 = _location_score(job, profile)
    language, e8 = _language_score(job, profile, requirements)
    eligibility, eligibility_status, e9 = _eligibility_score(
        job, profile, requirements, education, visa, language, experience, salary
    )

    weights = settings.get("matching", {}).get("tarek_fit_weights") or settings.get("matching", {}).get("overall_weights", {})
    defaults = {
        "technical": 0.30,
        "experience": 0.20,
        "education": 0.10,
        "domain": 0.10,
        "visa": 0.10,
        "salary": 0.10,
        "location": 0.05,
        "language": 0.05,
    }
    values = {
        "technical": technical,
        "experience": experience,
        "education": education,
        "domain": domain,
        "visa": visa,
        "salary": salary,
        "location": location,
        "language": language,
    }
    overall = round(sum(values[key] * float(weights.get(key, defaults[key])) for key in defaults))
    overall = max(0, min(100, overall))
    priority = _priority(overall)

    explanations = e1 + e2 + e3 + e4 + e5 + e6 + e7 + e8 + e9
    verification_reasons = [x for x in explanations if x.startswith("VERIFY:")]

    if eligibility_status == "blocked":
        verdict = "blocked"
    elif eligibility_status == "eligible" and not verification_reasons and overall >= 50:
        verdict = "actionable"
    elif eligibility_status == "eligible" and overall >= 65:
        verdict = "actionable"
    else:
        verdict = "needs_verification"

    return ScoreResult(
        technical=technical,
        eligibility=eligibility,
        location=location,
        experience=experience,
        education=education,
        domain=domain,
        visa=visa,
        salary=salary,
        language=language,
        overall=overall,
        priority=priority,
        eligibility_status=eligibility_status,
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
