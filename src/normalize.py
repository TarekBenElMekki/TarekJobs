from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Iterable

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

FRENCH_MONTHS = {
    "janvier": "January", "février": "February", "fevrier": "February",
    "mars": "March", "avril": "April", "mai": "May", "juin": "June",
    "juillet": "July", "août": "August", "aout": "August",
    "septembre": "September", "octobre": "October",
    "novembre": "November", "décembre": "December", "decembre": "December",
}

SALARY_CONTEXT = [
    "salary", "salaire", "pay", "compensation", "remuneration", "rémunération",
    "mensuel", "monthly", "annuel", "annual", "yearly", "hourly", "par mois",
    "par an", "per month", "per year", "per hour",
]


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = str(value)
    stripped = value.strip()
    # Most values in the matcher are plain text tokens, titles, locations,
    # URLs, or already-clean page text. Avoid invoking BeautifulSoup unless
    # the string actually looks like markup.
    if "<" not in stripped or ">" not in stripped:
        return re.sub(r"\s+", " ", html.unescape(stripped)).strip()
    text = BeautifulSoup(stripped, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def norm_text(value: str | None) -> str:
    value = clean_text(value).lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9+#.]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_date(value) -> datetime | None:
    """Parse source timestamps without changing ISO month/day order.

    ISO timestamps are always year-month-day. The older implementation sent
    every string through dateutil with dayfirst=True, so 2026-08-11 could
    become 2026-11-08. French and slash-formatted local dates still use
    day-first parsing after ISO formats have been handled explicitly.
    """
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        try:
            # Millisecond timestamps occasionally appear in APIs.
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    text = clean_text(str(value)).strip()
    if not text:
        return None

    # ISO / RFC3339: never apply dayfirst=True to these.
    iso_candidate = text.replace("Z", "+00:00")
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:[T\s]|$)", iso_candidate):
        try:
            parsed = datetime.fromisoformat(iso_candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                parsed = date_parser.isoparse(iso_candidate)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError, OverflowError):
                pass

    lowered = text.lower()
    for fr, en in FRENCH_MONTHS.items():
        lowered = re.sub(rf"\b{re.escape(fr)}\b", en, lowered, flags=re.I)

    try:
        parsed = date_parser.parse(lowered, dayfirst=True, fuzzy=True)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def extract_years(text: str) -> tuple[int | None, int | None]:
    # Preserve range separators before normalising punctuation away.
    raw = clean_text(text).lower()
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.replace("\u2013", "-").replace("\u2014", "-")

    range_match = re.search(
        r"\b(?:entre\s+)?(\d+)\s*(?:-|to|a|et|and)\s*(\d+)\s*(?:years?|ans?)\b",
        raw,
    )
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        if 0 <= low <= 20 and 0 <= high <= 20:
            return min(low, high), max(low, high)

    normalised = norm_text(raw)
    matches = [int(x) for x in re.findall(r"\b(\d+)\+?\s*(?:years?|ans?)\b", normalised)]
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


def canonical_job_type(value: str | None, remote: bool = False) -> str:
    """Return one canonical employment-type spelling used by the matcher."""
    if not value:
        return ""

    token = norm_text(str(value)).replace("_", " ").replace("-", " ")
    token = re.sub(r"\s+", " ", token).strip()

    aliases = {
        "full time": "full-time",
        "fulltime": "full-time",
        "temps plein": "full-time",
        "plein temps": "full-time",
        "cdi": "full-time",
        "part time": "part-time",
        "parttime": "part-time",
        "temps partiel": "part-time",
        "intern": "internship",
        "internship": "internship",
        "stage": "internship",
        "stagiaire": "internship",
        "apprenticeship": "apprenticeship",
        "alternance": "apprenticeship",
        "civp": "civp",
        "sivp": "civp",
        "freelance": "freelance",
        "fixed term": "fixed-term",
        "cdd": "fixed-term",
        "contract": "contract",
        "contractor": "contract",
        "remote contract": "contract",
    }

    if token in aliases:
        return aliases[token]

    detected = detect_job_type(token)
    if detected:
        return detected

    return str(value).strip().lower()


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
    """Parse compensation without confusing dates, decimal commas or grouping.

    Supported examples include:
      900 - 1200 TND / Mois
      $31.2k - $52k per year
      $31,2k - $52k
      $1,200 - $1,800 per month
      1 500 - 2 000 TND
    """
    if not text:
        return None, None, None, None

    normalized = (
        clean_text(text)
        .replace("\u202f", " ")
        .replace("\u00a0", " ")
    )
    low = norm_text(normalized)

    currency = None
    if re.search(r"\b(?:TND|DT)\b", normalized, re.I):
        currency = "TND"
    elif "$" in normalized or re.search(r"\bUSD\b", normalized, re.I):
        currency = "USD"
    elif "€" in normalized or re.search(r"\bEUR\b", normalized, re.I):
        currency = "EUR"
    elif "£" in normalized or re.search(r"\bGBP\b", normalized, re.I):
        currency = "GBP"

    has_pay_context = currency is not None or any(
        term in low for term in SALARY_CONTEXT
    )
    if not has_pay_context:
        return None, None, None, None

    def numeric_value(raw: str, suffix: str) -> float | None:
        token = (
            raw.strip()
            .replace("\u202f", "")
            .replace("\u00a0", "")
            .replace(" ", "")
        )
        if not token:
            return None

        suffix = suffix.lower()

        # With k/m suffixes, punctuation is naturally a decimal separator:
        # 31,2k == 31.2k and 31.2k == 31.2k.
        if suffix:
            if "," in token and "." in token:
                # Whichever separator appears last is the decimal separator.
                if token.rfind(",") > token.rfind("."):
                    token = token.replace(".", "").replace(",", ".")
                else:
                    token = token.replace(",", "")
            elif "," in token:
                token = token.replace(",", ".")

        else:
            if "," in token and "." in token:
                # Last separator is decimal; earlier one is grouping.
                if token.rfind(",") > token.rfind("."):
                    token = token.replace(".", "").replace(",", ".")
                else:
                    token = token.replace(",", "")
            elif "," in token:
                head, tail = token.rsplit(",", 1)
                if len(tail) == 3 and head.replace(",", "").isdigit():
                    token = token.replace(",", "")
                else:
                    token = token.replace(",", ".")
            elif "." in token:
                head, tail = token.rsplit(".", 1)
                if len(tail) == 3 and head.replace(".", "").isdigit():
                    token = token.replace(".", "")

        try:
            value = float(token)
        except ValueError:
            return None

        if suffix == "k":
            value *= 1_000.0
        elif suffix == "m":
            value *= 1_000_000.0

        return value

    numbers: list[float] = []

    # Prefer grouped/decimal forms first so "1 500" is one number, not 1 + 500.
    number_pattern = (
        r"(?<!\d)"
        r"("
        r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+"
        r"|"
        r"\d+(?:[.,]\d+)?"
        r")"
        r"\s*([kKmM]?)"
        r"(?![A-Za-z])"
    )

    for raw, suffix in re.findall(number_pattern, normalized):
        value = numeric_value(raw, suffix)
        if value is None:
            continue

        # Bare 4-digit years must never become compensation.
        if not suffix and 1900 <= value <= 2100:
            continue

        if 1 <= value <= 100_000_000:
            numbers.append(value)

    period = None
    if any(
        x in low
        for x in [
            "/year",
            "per year",
            "yearly",
            "annual",
            "annuel",
            "par an",
        ]
    ):
        period = "yearly"
    elif any(
        x in low
        for x in [
            "/month",
            "per month",
            "monthly",
            "mensuel",
            "par mois",
            "/ mois",
            "mois",
        ]
    ):
        period = "monthly"
    elif any(
        x in low
        for x in [
            "/hour",
            "per hour",
            "hourly",
            "heure",
        ]
    ):
        period = "hourly"

    if not numbers:
        return None, None, currency, period

    if len(numbers) == 1:
        return numbers[0], numbers[0], currency, period

    a, b = numbers[0], numbers[1]
    return min(a, b), max(a, b), currency, period


def first_match(text: str, candidates: Iterable[str]) -> str:
    t = norm_text(text)
    for candidate in candidates:
        if norm_text(candidate) in t:
            return candidate
    return ""
