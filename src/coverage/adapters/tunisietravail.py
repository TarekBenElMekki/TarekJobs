"""Recruitment archive with explicit scope decisions and article-to-position expansion."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from ...normalize import norm_text
from ..artifacts import stable_id
from ..contracts import (AdapterMetadata, CanonicalHints, Capabilities, DetailEmission,
                         DetailResult, InventoryRecord, Limits)
from .base import Listing, NativeAdapter, pagination
from .parsing import (absolute, application, foreign_scope, freshness, iso_date, job_postings,
                      labelled_fields, soup_for, structured_fields, supplement, text)

NON_JOB = re.compile(r"\b(immigration|visa express|conseils? cv|conseils? emploi|actualit[eé]s?|concours)\b", re.I)


class TunisieTravailAdapter(NativeAdapter):
    listing_url = "https://www.tunisietravail.net/category/offres-d-emploi-et-recrutement/"
    metadata = AdapterMetadata(
        "tunisietravail_tn", "Tunisie Travail", "native_board", "public_html", "tunisietravail", "tn1.1",
        "https://www.tunisietravail.net", Capabilities(detail_pages=True, country_filter=True,
        location_filter=True, category_filter=True), Limits(requests_per_minute=12),
        CanonicalHints(external_id=True, application_url=True))
    boundary_strategy = "Recruitment archive to its linked final page; classify details; follow only explicit position links."

    @staticmethod
    def external_id(url):
        match = re.search(r"-(\d+)/?$", urlsplit(url).path)
        if not match:
            raise ValueError("invalid_tunisietravail_article_url")
        return match.group(1)

    def parse_listing(self, html, url):
        soup = soup_for(html)
        records = []
        for card in soup.select("article"):
            link = card.select_one("h2 a[href]")
            detail_url = absolute(url, link.get("href")) if link else None
            if not detail_url or urlsplit(detail_url).hostname != urlsplit(self.listing_url).hostname:
                continue
            try:
                external = self.external_id(detail_url)
            except ValueError:
                continue
            categories = [{"url": absolute(url, a["href"]), "label": text(a)}
                          for a in card.select('a[href*="/category/"]')]
            date = card.select_one("time")
            values = {"title": text(link), "canonical_url": detail_url,
                      "_archive_url": self.listing_url, "_archive_categories": categories,
                      "_archive_date": text(date) or text(card.select_one(".PostDate, .PostInfo")),
                      "_list_text": text(card), "_freshness": {"state": "discovered"}}
            posted = iso_date(date.get("datetime") or text(date)) if date else None
            if posted:
                values["posted_at"] = posted
            records.append(InventoryRecord(external, detail_url, external, values))
        next_url, last_page = pagination(soup, url, self.listing_url)
        main = soup.select_one("main") or soup
        empty = not records and bool(re.search(r"(aucun article trouv[eé]|nothing found)", text(main), re.I))
        return Listing(tuple(records), next_url, None, last_page, empty, {
            "archive": self.listing_url, "article_dates": [r.values["_archive_date"] for r in records],
            "relevance_stage": "detail", "categories_not_multiplied": True})

    def _classify(self, soup, body, values, postings):
        categories = [a.get("href", "") for a in soup.select(".PostHead .PostInfo a[href], .post-categories a[href]")]
        values["_detail_categories"] = categories
        title = values.get("title", "")
        if NON_JOB.search(title) or any("/concours-" in c or "/immigration" in c for c in categories):
            return "excluded", "non_job_or_competition_content"
        foreign = foreign_scope(values) if len(postings) <= 1 else None
        if foreign:
            return "excluded", foreign
        countries = [c for c in categories if "/category/pays/" in c]
        if countries and not any("/pays/tunisie/" in c for c in countries):
            return "excluded", "foreign_only_category"
        if not postings and not re.search(r"\b(recrute|recrutement|recrutent|poste|offre d[’']emploi)\b", title, re.I):
            return "excluded", "no_recruitment_evidence"
        positive = any("/pays/tunisie/" in c for c in countries) or norm_text(values.get("country")) in {"tn", "tunisia", "tunisie"}
        values["_scope_evidence"] = {"classification": "tunisia" if positive else "unknown_geography",
                                     "categories": categories, "country": values.get("country", "")}
        if positive and not values.get("country"):
            values["country"] = "Tunisia"
        return "record", "recruitment_article"

    def parse_detail(self, html, url, record, fetched_at):
        soup = soup_for(html)
        body = soup.select_one(".PostContent, .entry-content, .post-content")
        if body is None or not text(body):
            raise ValueError("tunisietravail_article_layout_unrecognized")
        postings = job_postings(soup)
        values = dict(record.values)
        if postings:
            values.update({k: v for k, v in structured_fields(postings[0]).items() if v not in ("", None) and not (k == "company" and v == "Unknown" and values.get("company"))})
        article_title = text(soup.select_one("h1")) or record.values.get("title", "")
        values.setdefault("title", article_title)
        if not values.get("company") or values.get("company") == "Unknown":
            employer = re.match(r"^(.+?)\s+recrut(?:e|ent)\b", article_title, re.I)
            if employer:
                values["company"] = employer.group(1).strip()
        values.update(description=text(body), canonical_url=url, _article_title=article_title)
        supplement(values, labelled_fields(body))
        values.update(application(soup, body, url))
        freshness(soup, values, fetched_at)
        disposition, reason = self._classify(soup, body, values, postings)
        if disposition == "excluded":
            return DetailResult(values, disposition="excluded", evidence={"reason": reason})

        # Real observed campaign structure: a Postes blockquote contains explicit
        # links to independently identifiable articles. Queue their details; never
        # substitute the campaign summary for the linked vacancy's full text.
        linked = {}
        for block in body.select("blockquote"):
            if not re.match(r"^(postes|positions|offres|nos postes)\b", norm_text(text(block))):
                continue
            for link in block.select("a[href]"):
                child_url = absolute(url, link.get("href"))
                if not child_url or urlsplit(child_url).hostname != urlsplit(url).hostname:
                    continue
                try:
                    child_id = self.external_id(child_url)
                except ValueError:
                    continue
                if child_id == self.external_id(url) or not text(link):
                    continue
                linked[child_id] = DetailEmission(InventoryRecord(
                    child_id, child_url, child_id,
                    {"title": text(link), "canonical_url": child_url, "_parent_article_url": url,
                     "_freshness": {"state": "discovered"}}), requires_detail=True)
        if linked:
            return DetailResult(values, disposition="expanded", emissions=tuple(linked.values()),
                                evidence={"reason": "explicit_position_links", "parent_article": url})

        positions = []
        if len(postings) > 1:
            signatures = set()
            for posting in postings:
                mapped = structured_fields(posting)
                signature = (norm_text(mapped.get("title")), norm_text(mapped.get("location")))
                if signature in signatures or not signature[0]:
                    continue
                signatures.add(signature)
                # One structured position must not inherit another position's
                # location, salary, dates, or organization.
                common = {k: values[k] for k in ("application_url", "_application",
                    "_parent_article_url", "canonical_url", "last_verified_at", "_freshness") if k in values}
                positions.append({**common, **mapped})
        else:
            # Only explicitly labelled position headings split an inline article.
            # Generic 'Profile', 'Missions', bold text, or slash titles do not.
            headings = []
            for node in body.select("h2, h3, h4"):
                match = re.match(r"^(?:poste|position)\s*(?:\d+)?\s*[:\-]\s*(.+)$", text(node), re.I)
                if match:
                    headings.append((node, match.group(1).strip()))
            if len(headings) >= 2:
                for node, title in headings:
                    parts = [title]
                    for sibling in node.next_siblings:
                        if getattr(sibling, "name", None) in {"h2", "h3", "h4"}:
                            break
                        if getattr(sibling, "get_text", None):
                            parts.append(text(sibling))
                    if len(parts) > 1 and any(parts[1:]):
                        positions.append({**values, "title": title, "description": "\n".join(parts)})
                if len(positions) != len(headings):
                    positions = []  # Incomplete segmentation preserves the article.

        if len(positions) >= 2:
            children = []
            excluded_positions = []
            keys = set()
            for position in positions:
                outside = foreign_scope(position)
                if outside:
                    excluded_positions.append({"title": position.get("title"), "reason": outside})
                    continue
                signature = stable_id([norm_text(position["title"]), norm_text(position.get("location"))])[:24]
                key = self.external_id(url) + ":position:" + signature
                if key in keys:
                    raise ValueError("ambiguous_inline_position_identity")
                keys.add(key)
                position["_source_url_is_identity"] = False
                position["_parent_article_url"] = url
                children.append(DetailEmission(InventoryRecord(key, url, key, position)))
            if not children:
                return DetailResult(values, disposition="excluded",
                    evidence={"reason": "all_separable_positions_outside_scope", "positions": excluded_positions})
            return DetailResult(values, disposition="expanded", emissions=tuple(children),
                                evidence={"reason": "separable_inline_positions", "excluded_positions": excluded_positions, "parent_article": url,
                                          "shared_url_not_identity": True})
        outside = foreign_scope(values)
        if outside:
            return DetailResult(values, disposition="excluded", evidence={"reason": outside})
        # Preserve the source title when segmentation is unsafe; no invented role.
        return DetailResult(values, evidence={"reason": "article_level_posting",
                                              "segmentation": "not_needed_or_not_safe"})
