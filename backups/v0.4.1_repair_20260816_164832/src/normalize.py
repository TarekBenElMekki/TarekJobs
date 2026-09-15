from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Iterable

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

FRENCH_MONTHS = {
    "janvier": "January", "fÃ©vrier": "February", "fevrier": "February",
    "mars": "March", "avril": "April", "mai": "May", "juin": "June",
    "juillet": "July", "aoÃ»t": "August", "aout": "August",
    "septembre": "September", "octobre": "October",
    "novembre": "November", "dÃ©cembre": "December", "decembre": "December",
}

SALARY_CONTEXT = [
    "salary", "salaire", "pay", "compensation", "remuneration", "rÃ©munÃ©ration",
    "mensuel", "monthly", "annuel", "annual", "yearly", "hourly", "par mois",
    "par an", "per month", "per year", "per hour",
]


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def norm_text(value: str | None) -> str:
    value = clean_text(value).lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9+#.]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_date(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError):
            return None

    text = clean_text(str(value))
    lowered = text.lower()
    for fr, en in FRENCH_MONTHS.items():
        lowered = re.sub(rf"\b{re.escape(fr)}\b", en, lowered, flags=re.I)
    try:
        parsed = date_parser.parse(lowered, dayfirst=True, fuzzy=True)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def extract_years(text: str) -> tuple[int | None, int | None]:
    text = norm_text(text)
    range_match = re.search(
        r"\b(?:entre\s+)?(\d+)\s*(?:-|to|a|et|and)\s*(\d+)\s*(?:years?|ans?)\b",
        text,
    )
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        if 0 <= low <= 20 and 0 <= high <= 20:
            return min(low, high), max(low, high)

    matches = [int(x) for x in re.findall(r"\b(\d+)\+?\s*(?:years?|ans?)\b", text)]
    sensible = [x for x in matches if 0 <= x <= 20]
    if sensible:
        value = min(sensible)
        return value, value
    return None, None


def detect_seniority(text: str) -> str:
    t = f" {norm_text(text)} "
    ordered = [
        ("director", [r"\bdirector\b", r"\bdirecteur\b", r"\bdirectrice\b", r"\bhead of\b", r"\bchief\b", r"\bvice president\b", r"\bvp\b"]),
        ("principal", [r"\bprincipal\b"]),
        ("staff", [r"\bstaff\b"]),
        ("senior", [r"\bsenior\b", r"\bsr\b", r"\bconfirme\b", r"\bconfirmee\b"]),
        ("lead", [r"\btech lead\b", r"\bteam lead\b", r"\blead engineer\b", r"\blead developer\b", r"\bchef d equipe\b", r"\bchef equipe\b", r"\bsuperviseur\b", r"\bsupervisor\b"]),
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


def detect_job_type(text: str) -> str:
    t = norm_text(text)
    pairs = [
        ("internship", ["internship", "stage", "stagiaire", "pfe"]),
        ("apprenticeship", ["apprenticeship", "alternance", "apprenti"]),
        ("civp", ["civp", "sivp"]),
        ("part-time", ["part time", "part-time", "temps partiel"]),
        ("freelance", ["freelance", "independant", "independent contractor"]),
        ("fixed-term", ["cdd", "fixed term", "fixed-term"]),
        ("contract", ["contract", "contractor"]),
        ("full-time", ["full time", "full-time", "temps plein", "cdi", "plein temps"]),
    ]
    for label, terms in pairs:
        if any(term in t for term in terms):
            return label
    return ""


def parse_salary_text(text: str) -> tuple[float | None, float | None, str | None, str | None]:
    """Parse salary only when the text actually looks like compensation.

    Dates such as 2026 must never become salaries. Numeric extraction is accepted
    when a currency is present, or when an explicit salary/pay context is present.
    """
    if not text:
        return None, None, None, None

    normalized = clean_text(text).replace(",", " ").replace("\u202f", " ")
    low = norm_text(normalized)

    currency = None
    if re.search(r"\b(?:TND|DT)\b", normalized, re.I):
        currency = "TND"
    elif "$" in normalized or re.search(r"\bUSD\b", normalized, re.I):
        currency = "USD"
    elif "â‚¬" in normalized or re.search(r"\bEUR\b", normalized, re.I):
        currency = "EUR"
    elif "Â£" in normalized or re.search(r"\bGBP\b", normalized, re.I):
        currency = "GBP"

    has_pay_context = currency is not None or any(term in low for term in SALARY_CONTEXT)
    if not has_pay_context:
        return None, None, None, None

    numbers: list[float] = []
    for raw in re.findall(r"(?<!\d)(\d{2,7})(?!\d)", normalized):
        try:
            n = float(raw)
        except ValueError:
            continue
        # Years/dates are the main false-positive that triggered V0.4.
        if 1900 <= n <= 2100:
            continue
        if 10 <= n <= 10_000_000:
            numbers.append(n)

    period = None
    if any(x in low for x in ["/year", "per year", "yearly", "annual", "annuel", "par an"]):
        period = "yearly"
    elif any(x in low for x in ["/month", "per month", "monthly", "mensuel", "par mois", "/ mois", "mois"]):
        period = "monthly"
    elif any(x in low for x in ["/hour", "per hour", "hourly", "heure"]):
        period = "hourly"

    if not numbers:
        return None, None, currency, period
    if len(numbers) == 1:
        return numbers[0], numbers[0], currency, period

    # Salary ranges are usually the first two plausible compensation numbers.
    a, b = numbers[0], numbers[1]
    return min(a, b), max(a, b), currency, period


def first_match(text: str, candidates: Iterable[str]) -> str:
    t = norm_text(text)
    for candidate in candidates:
        if norm_text(candidate) in t:
            return candidate
    return ""