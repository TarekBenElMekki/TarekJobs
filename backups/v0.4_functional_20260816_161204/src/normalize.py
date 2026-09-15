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

    text = str(value).strip()
    low = text.lower()
    for fr, en in FRENCH_MONTHS.items():
        low = re.sub(rf"\b{re.escape(fr)}\b", en, low, flags=re.I)
    try:
        dt = date_parser.parse(low, dayfirst=True)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def extract_years(text: str) -> tuple[int | None, int | None]:
    text = clean_text(text).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9+.-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    range_match = re.search(r"\b(\d+)\s*(?:-|to|a)\s*(\d+)\s*(?:years?|ans?)\b", text)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))

    matches = [int(x) for x in re.findall(r"\b(\d+)\+?\s*(?:years?|ans?)\b", text)]
    sensible = [x for x in matches if 0 <= x <= 20]
    if sensible:
        value = min(sensible)
        return value, value
    return None, None


def detect_seniority(text: str) -> str:
    t = norm_text(text)
    ordered = [
        ("intern", ["intern", "internship", "stage", "stagiaire"]),
        ("graduate", ["graduate", "fresh graduate", "new grad"]),
        ("entry-level", ["entry level", "entry-level", "debutant"]),
        ("junior", ["junior", "jr. ", "jr "]),
        ("associate", ["associate"]),
        ("senior", ["senior", "sr. ", "sr "]),
        ("lead", ["tech lead", "team lead", "lead engineer", "lead developer"]),
        ("manager", ["manager", "responsable"]),
        ("director", ["director", "directeur"]),
        ("principal", ["principal"]),
        ("staff", ["staff engineer", "staff software"]),
        ("mid-level", ["mid level", "mid-level", "intermediate"]),
    ]
    for label, terms in ordered:
        if any(term in t for term in terms):
            return label
    return ""


def detect_job_type(text: str) -> str:
    t = norm_text(text)
    pairs = [
        ("internship", ["internship", "stage", "stagiaire"]),
        ("apprenticeship", ["apprenticeship", "alternance", "apprenti"]),
        ("civp", ["civp", "sivp"]),
        ("part-time", ["part time", "part-time", "temps partiel"]),
        ("freelance", ["freelance"]),
        ("contract", ["contract", "contractor"]),
        ("fixed-term", ["cdd", "fixed term", "fixed-term"]),
        ("full-time", ["full time", "full-time", "temps plein", "cdi"]),
    ]
    for label, terms in pairs:
        if any(term in t for term in terms):
            return label
    return ""


def parse_salary_text(text: str) -> tuple[float | None, float | None, str | None, str | None]:
    if not text:
        return None, None, None, None
    normalized = text.replace(",", " ").replace("\u202f", " ")
    currency = None
    if re.search(r"\b(?:TND|DT)\b", normalized, re.I):
        currency = "TND"
    elif "$" in normalized or re.search(r"\bUSD\b", normalized, re.I):
        currency = "USD"
    elif "â‚¬" in normalized or re.search(r"\bEUR\b", normalized, re.I):
        currency = "EUR"

    numbers = []
    for raw in re.findall(r"(?<!\d)(\d{3,6})(?!\d)", normalized):
        try:
            n = float(raw)
            if n >= 100:
                numbers.append(n)
        except ValueError:
            pass

    period = None
    low = normalized.lower()
    if any(x in low for x in ["/year", "per year", "yearly", "annuel", "par an"]):
        period = "yearly"
    elif any(x in low for x in ["/month", "per month", "monthly", "mensuel", "par mois"]):
        period = "monthly"
    elif any(x in low for x in ["/hour", "per hour", "hourly", "heure"]):
        period = "hourly"

    if not numbers:
        return None, None, currency, period
    if len(numbers) == 1:
        return numbers[0], numbers[0], currency, period
    return min(numbers[:4]), max(numbers[:4]), currency, period


def first_match(text: str, candidates: Iterable[str]) -> str:
    t = norm_text(text)
    for candidate in candidates:
        if norm_text(candidate) in t:
            return candidate
    return ""