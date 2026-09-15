from __future__ import annotations

import re
import urllib.robotparser
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import BaseCollector
from ..models import JobRecord
from ..normalize import clean_text, detect_job_type, first_match, parse_date, parse_salary_text

TUNISIA_LOCATIONS = [
    "Tunis", "Ariana", "Ben Arous", "Manouba", "Mghira", "Charguia",
    "Ghazala", "El Ghazala", "Les Berges du Lac", "Lac 1", "Lac 2",
    "MÃ©grine", "Megrine", "RadÃ¨s", "Rades", "Sousse", "Sfax", "Nabeul",
    "Monastir", "Bizerte", "Kairouan", "Mahdia", "GabÃ¨s", "Gabes",
    "Kalaa El Kebira"
]


class KeejobCollector(BaseCollector):
    name = "keejob"

    def _robots_allows(self, url: str) -> bool:
        if not self.config.get("obey_robots_txt", True):
            return True
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            robots_text = self.get_text(robots_url)
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(robots_url)
            rp.parse(robots_text.splitlines())
            ua = self.settings.get("collection", {}).get("user_agent", "TarekJobs/0.1")
            return rp.can_fetch(ua, url)
        except Exception:
            # Fail closed for HTML scraping if robots.txt cannot be evaluated.
            return False

    @staticmethod
    def _best_card(anchor):
        for parent in anchor.parents:
            if getattr(parent, "name", None) not in {"article", "li", "div", "section"}:
                continue
            text = clean_text(parent.get_text(" ", strip=True))
            if 80 <= len(text) <= 2500:
                return parent
        return anchor.parent

    def fetch(self, limit: int) -> list[JobRecord]:
        found: dict[str, dict] = {}
        pages = self.config.get("pages", [])

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
                if not text or text.lower() in {"voir l'offre", "voir offre", "postuler"}:
                    continue
                if absolute not in found or len(text) > len(found[absolute]["title"]):
                    found[absolute] = {"title": text, "anchor": anchor}
                if len(found) >= limit:
                    break

        out: list[JobRecord] = []
        for url, item in list(found.items())[:limit]:
            title = item["title"]
            card = self._best_card(item["anchor"])
            card_text = clean_text(card.get_text(" ", strip=True)) if card else title
            lines = [clean_text(x) for x in getattr(card, "stripped_strings", [])] if card else []
            lines = [x for x in lines if x]

            company = "Unknown"
            try:
                idx = next(i for i, x in enumerate(lines) if clean_text(x) == title)
                for candidate in lines[idx + 1: idx + 5]:
                    low = candidate.lower()
                    if candidate == title or "voir l" in low:
                        continue
                    if re.search(r"\b(?:cdi|cdd|stage|sivp|civp|tnd|dt)\b", low):
                        continue
                    if any(loc.lower() in low for loc in TUNISIA_LOCATIONS):
                        continue
                    if len(candidate) <= 120:
                        company = candidate
                        break
            except StopIteration:
                pass

            location = first_match(card_text, TUNISIA_LOCATIONS) or "Tunisia"
            smin, smax, curr, period = parse_salary_text(card_text)
            date_match = re.search(
                r"\b\d{1,2}\s+(?:janvier|fÃ©vrier|fevrier|mars|avril|mai|juin|juillet|aoÃ»t|aout|septembre|octobre|novembre|dÃ©cembre|decembre)\s+\d{4}\b",
                card_text,
                re.I,
            )
            posted_at = parse_date(date_match.group(0)) if date_match else None

            out.append(
                JobRecord(
                    title=title,
                    company=company,
                    location=location,
                    description=card_text,
                    employment_type=detect_job_type(card_text),
                    remote="remote" in card_text.lower() or "tÃ©lÃ©travail" in card_text.lower(),
                    salary_min=smin,
                    salary_max=smax,
                    salary_currency=curr,
                    salary_period=period,
                    posted_at=posted_at,
                    source_name=self.name,
                    source_url=url,
                    external_id=re.search(r"/offres-emploi/(\d+)/", url).group(1),
                    raw_payload={"listing_text": card_text, "listing_page": "Keejob category page"},
                )
            )
        return out