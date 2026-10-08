"""Tanitjobs public /jobs/ adapter; challenges are terminal access evidence."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..contracts import AdapterMetadata, CanonicalHints, Capabilities, DetailResult, InventoryRecord, Limits
from .base import Listing, NativeAdapter, pagination
from .parsing import (absolute, application, foreign_scope, freshness, iso_date,
                      job_postings, labelled_fields, soup_for, structured_fields, supplement, text)


class TanitjobsAdapter(NativeAdapter):
    listing_url = "https://www.tanitjobs.com/jobs/"
    metadata = AdapterMetadata(
        "tanitjobs_tn", "Tanitjobs", "native_board", "public_html", "tanitjobs", "tn1.1",
        "https://www.tanitjobs.com", Capabilities(detail_pages=True, country_filter=True,
        location_filter=True, category_filter=True), Limits(requests_per_minute=12),
        CanonicalHints(external_id=True, application_url=True))
    boundary_strategy = "Broad /jobs/ listing; follow public page links including searchId; block on challenges."

    @staticmethod
    def external_id(url):
        match = re.match(r"^/job/(\d+)(?:/|$)", urlsplit(url).path)
        if not match:
            raise ValueError("invalid_tanitjobs_detail_url")
        return match.group(1)

    def parse_listing(self, html, url):
        soup = soup_for(html)
        records = []
        cards = soup.select("article, .listing-item, .job-listing, .job-card")
        if not cards:
            # Semantic anchors also support the current public server-rendered list.
            cards = [a.find_parent(["article", "li"]) or a.parent.parent
                     for a in soup.select('a[href*="/job/"]') if text(a)]
        for card in cards:
            links = card.select('h2 a[href], h3 a[href], .listing-item__title a[href], a[href*="/job/"]')
            link = next((a for a in links if re.match(r"^/job/\d+/", urlsplit(absolute(url, a.get("href")) or "").path)
                         and text(a) and text(a).lower() not in {"voir plus", "voir l'offre"}), None)
            if not link:
                continue
            detail_url = absolute(url, link["href"])
            external = self.external_id(detail_url)
            company = card.select_one(".listing-item__info--item-company, .company-name, [itemprop=hiringOrganization]")
            location = card.select_one(".listing-item__info--item-location, .location, [itemprop=jobLocation]")
            values = {"title": text(link), "company": text(company) or "Unknown",
                      "location": text(location), "canonical_url": detail_url,
                      "description": text(card.select_one(".listing-item__desc, .description, .job-description")),
                      "_list_text": text(card), "_freshness": {"state": "discovered"}}
            time = card.select_one("time")
            posted = iso_date(time.get("datetime") or text(time)) if time else None
            if not posted:
                match = re.search(r"\b\d{2}/\d{2}/\d{4}\b", text(card))
                posted = iso_date(match.group()) if match else None
            if posted:
                values["posted_at"] = posted
            supplement(values, labelled_fields(card))
            records.append(InventoryRecord(external, detail_url, external, values))
        next_url, last_page = pagination(soup, url, self.listing_url)
        match = re.search(r"([\d\s\u00a0]+)\s+(?:offres? d[’']emploi|emplois?|jobs)\b", text(soup), re.I)
        total = int(re.sub(r"\D", "", match.group(1))) if match and re.sub(r"\D", "", match.group(1)) else None
        empty = bool(soup.select_one(".no-results, .alert-no-results")) or bool(
            re.search(r"(aucune offre|aucun emploi|no jobs found)", text(soup), re.I))
        return Listing(tuple(records), next_url, total, last_page, empty,
                       {"inventory_route": "/jobs/", "keyword_search": False})

    def parse_detail(self, html, url, record, fetched_at):
        soup = soup_for(html)
        postings = job_postings(soup)
        body = soup.select_one(".details-body__content, .job-description, [itemprop=description], .details-body")
        values = dict(record.values)
        if postings:
            values.update({k: v for k, v in structured_fields(postings[0]).items() if v not in ("", None) and not (k == "company" and v == "Unknown" and values.get("company"))})
        if not values.get("title"):
            values["title"] = text(soup.select_one("h1"))
        if body:
            values["description"] = text(body)
        if not values.get("description") or (not body and not postings):
            raise ValueError("tanitjobs_detail_layout_unrecognized")
        values["canonical_url"] = url
        canonical = soup.select_one('link[rel=canonical]')
        if canonical:
            declared = absolute(url, canonical.get("href"))
            if declared and urlsplit(declared).hostname == urlsplit(url).hostname and self.external_id(declared) == self.external_id(url):
                values["canonical_url"] = declared
        supplement(values, labelled_fields(soup))
        values.update(application(soup, body or soup.select_one("main") or soup, url))
        freshness(soup, values, fetched_at)
        reason = foreign_scope(values)
        if reason:
            return DetailResult(values, disposition="excluded", evidence={"reason": reason})
        return DetailResult(values)
