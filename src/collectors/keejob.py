from __future__ import annotations

import re
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import BaseCollector
from ..models import JobRecord
from ..normalize import clean_text, detect_job_type, extract_years, first_match, norm_text, parse_date, parse_salary_text

TUNISIA_LOCATIONS = [
    "Tunis", "Ariana", "Ben Arous", "Manouba", "Mghira", "Charguia", "Ghazala", "El Ghazala",
    "Les Berges du Lac", "Lac 1", "Lac 2", "Mégrine", "Megrine", "Radès", "Rades", "Sousse",
    "Sfax", "Nabeul", "Monastir", "Bizerte", "Kairouan", "Mahdia", "Gabès", "Gabes", "Kalaa El Kebira",
]

DETAIL_LABELS = [
    "Référence", "Date de publication", "Type de contrat", "Lieu de travail", "Expérience requise",
    "Niveau d'études", "Salaire proposé", "Disponibilité", "Mobilité",
]


SECTOR_LABEL_SIGNALS = {
    "informatique", "telecoms", "telecom", "banque", "finance", "assurances",
    "industrie", "production", "commerce", "distribution", "marketing",
    "communication", "sante", "medical", "pharmaceutique", "transport",
    "logistique", "tourisme", "hotellerie", "restauration",
}


class KeejobCollector(BaseCollector):
    name = "keejob"

    def __init__(self, config: dict, settings: dict):
        super().__init__(config, settings)
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _robots_allows(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots_cache.get(root)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(urljoin(root, "/robots.txt"))
            try:
                parser.read()
            except Exception:
                # Fail closed for the HTML collector if robots could not be read.
                return False
            self._robots_cache[root] = parser
        user_agent = self.settings.get("collection", {}).get("user_agent", "TarekJobs")
        return parser.can_fetch(user_agent, url)

    @staticmethod
    def _best_card(anchor):
        node = anchor
        for _ in range(7):
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True))
            if len(text) >= 80 and len(text) <= 2500:
                return node
            node = getattr(node, "parent", None)
        return getattr(anchor, "parent", None)

    @staticmethod
    def _value_after_label(lines: list[str], label: str) -> str:
        target = norm_text(label)
        labels = {norm_text(x) for x in DETAIL_LABELS}
        for i, line in enumerate(lines):
            if norm_text(line) != target:
                continue
            for candidate in lines[i + 1: i + 5]:
                n = norm_text(candidate)
                if not n or n in labels:
                    continue
                return candidate
        return ""

    @staticmethod
    def _description_from_lines(lines: list[str]) -> str:
        start = next((i for i, line in enumerate(lines) if norm_text(line) == norm_text("Description de l'annonce")), None)
        if start is None:
            return ""
        stop_tokens = {
            norm_text("Partager"), norm_text("Imprimer"), norm_text("À propos de Keejob"),
            norm_text("Postuler maintenant"), norm_text("Offres similaires"),
        }
        selected = []
        for line in lines[start + 1:]:
            if norm_text(line) in stop_tokens:
                break
            if line:
                selected.append(line)
        return clean_text(" ".join(selected))

    @staticmethod
    def _looks_like_sector_label(value: str) -> bool:
        normalised = norm_text(value)
        tokens = set(normalised.split())
        # Keejob sometimes exposes the sector/category near the title in the
        # same position where an employer link would otherwise appear.
        return (
            "/" in value
            and bool(tokens.intersection(SECTOR_LABEL_SIGNALS))
        )

    @staticmethod
    def _company_from_page(soup: BeautifulSoup, title: str) -> str:
        # The employer section on Keejob is structurally much safer than
        # nearby category/sector links.
        enterprise_heading = next(
            (
                node for node in soup.find_all(["h2", "h3", "h4"])
                if norm_text(node.get_text(" ", strip=True)) == "entreprise"
            ),
            None,
        )
        if enterprise_heading:
            for node in enterprise_heading.find_all_next(["h2", "h3", "h4", "a"], limit=20):
                value = clean_text(node.get_text(" ", strip=True))
                if not value or value == title:
                    continue
                if norm_text(value).startswith("secteur"):
                    break
                if KeejobCollector._looks_like_sector_label(value):
                    continue
                if norm_text(value) in {"entreprise", "postuler maintenant"}:
                    continue
                if len(value) <= 140:
                    return value

        # Fallback to canonical company links.
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if "/companies/" not in href:
                continue
            value = clean_text(link.get_text(" ", strip=True))
            if value and value != title and not KeejobCollector._looks_like_sector_label(value):
                return value

        return "Confidential employer"

    @staticmethod
    def _publication_from_page(soup: BeautifulSoup, lines: list[str]) -> tuple[str, object]:
        # Normalize accents first so this parser is fully ASCII-safe:
        # "Publiée le 11 août 2026" -> "publiee le 11 aout 2026".
        page_norm = norm_text(soup.get_text(" ", strip=True))
        match = re.search(
            r"\bpubliee?\s+le\s+([0-3]?\d)\s+([a-z]+)\s+(\d{4})\b",
            page_norm,
        )
        raw = ""
        parsed = None

        if match:
            day, month, year = match.groups()
            raw = f"{day} {month} {year}"
            parsed = parse_date(raw)

        if parsed is None:
            raw = KeejobCollector._value_after_label(lines, "Date de publication")
            parsed = parse_date(raw)

        if parsed is None:
            return raw, None

        if parsed > datetime.now(timezone.utc) + timedelta(days=1):
            return raw, None

        return raw, parsed

    def fetch_detail(self, url: str, listing_title: str = "", listing_text: str = "") -> JobRecord:
        if not self._robots_allows(url):
            raise RuntimeError(f"robots.txt does not allow detail URL: {url}")

        html = self.get_text(url)
        soup = BeautifulSoup(html, "html.parser")
        lines = [clean_text(x) for x in soup.stripped_strings]
        lines = [x for x in lines if x]

        h1 = soup.find("h1")
        title = clean_text(h1.get_text(" ", strip=True)) if h1 else listing_title or "Untitled job"
        company = self._company_from_page(soup, title)
        location = self._value_after_label(lines, "Lieu de travail") or first_match(listing_text, TUNISIA_LOCATIONS) or "Tunisia"
        contract = self._value_after_label(lines, "Type de contrat")
        experience = self._value_after_label(lines, "Expérience requise")
        education = self._value_after_label(lines, "Niveau d'études")
        salary_text = self._value_after_label(lines, "Salaire proposé")
        availability = self._value_after_label(lines, "Disponibilité")
        publication, publication_dt = self._publication_from_page(soup, lines)
        description = self._description_from_lines(lines) or listing_text

        salary_min, salary_max, currency, period = parse_salary_text(
            f"Salaire proposé {salary_text}" if salary_text else ""
        )
        min_years, max_years = extract_years(experience or description)
        full_context = " ".join([title, contract, availability, description])
        remote = any(term in norm_text(full_context) for term in ["remote", "teletravail", "télétravail", "a distance", "à distance"])

        external_match = re.search(r"/offres-emploi/(\d+)/", url)
        external_id = external_match.group(1) if external_match else None

        return JobRecord(
            title=title,
            company=company,
            location=location,
            description=description,
            employment_type=detect_job_type(f"{contract} {availability} {description}"),
            remote=remote,
            seniority="",
            min_years=min_years,
            max_years=max_years,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            salary_period=period,
            posted_at=publication_dt,
            source_name=self.name,
            source_url=url,
            external_id=external_id,
            raw_payload={
                "detail_page": True,
                "reference": self._value_after_label(lines, "Référence"),
                "company": company,
                "location": location,
                "contract": contract,
                "experience": experience,
                "education": education,
                "salary": salary_text,
                "availability": availability,
                "publication": publication,
                "listing_text": listing_text,
            },
        )

    def fetch(self, limit: int) -> list[JobRecord]:
        pages = self.config.get("pages") or []
        found: dict[str, dict] = {}

        for page_url in pages:
            if len(found) >= limit:
                break
            if not self._robots_allows(page_url):
                continue

            html = self.get_text(page_url)
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = anchor.get("href", "")
                if not re.search(r"/offres-emploi/\d+/", href):
                    continue
                absolute = urljoin(page_url, href)
                text = clean_text(anchor.get_text(" ", strip=True))
                if not text or norm_text(text) in {"voir l offre", "voir offre", "postuler"}:
                    continue
                card = self._best_card(anchor)
                card_text = clean_text(card.get_text(" ", strip=True)) if card else text
                if absolute not in found or len(text) > len(found[absolute]["title"]):
                    found[absolute] = {"title": text, "card_text": card_text}
                if len(found) >= limit:
                    break

        out: list[JobRecord] = []
        for url, item in list(found.items())[:limit]:
            try:
                out.append(self.fetch_detail(url, item["title"], item["card_text"]))
            except Exception:
                # Detail enrichment should not make the whole collector brittle.
                title = item["title"]
                card_text = item["card_text"]
                location = first_match(card_text, TUNISIA_LOCATIONS) or "Tunisia"
                salary_min, salary_max, currency, period = parse_salary_text(card_text)
                out.append(JobRecord(
                    title=title,
                    company="Unknown",
                    location=location,
                    description=card_text,
                    employment_type=detect_job_type(card_text),
                    remote=any(term in norm_text(card_text) for term in ["remote", "teletravail", "a distance"]),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=currency,
                    salary_period=period,
                    posted_at=None,
                    source_name=self.name,
                    source_url=url,
                    external_id=(re.search(r"/offres-emploi/(\d+)/", url).group(1) if re.search(r"/offres-emploi/(\d+)/", url) else None),
                    raw_payload={"detail_page": False, "listing_text": card_text},
                ))
        return out
