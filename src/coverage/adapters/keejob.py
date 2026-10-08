"""TN-0 adapter around deterministic parsing ported from T/TEA's proven campaign."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from ...normalize import extract_years

from ..contracts import AdapterMetadata, CanonicalHints, Capabilities, DetailResult, InventoryPage, InventoryRecord, Limits
from .base import Listing, NativeAdapter
from .http import FetchProblem, HttpPolicy, PublicHttp
from . import keejob_parsing as proven
from .parsing import (application, foreign_scope, freshness, iso_date, job_postings,
                      labelled_fields, soup_for, structured_fields, supplement, text, work_mode)


class KeejobAdapter(NativeAdapter):
    listing_url = proven.MAIN_SOURCE_URL
    metadata = AdapterMetadata(
        "keejob_tn", "keejob.com", "native_board", "public_html", "keejob", "tn1.2-port",
        "https://www.keejob.com", Capabilities(detail_pages=True, country_filter=True,
        location_filter=True, category_filter=True), Limits(requests_per_minute=12),
        CanonicalHints(external_id=True, application_url=True))
    boundary_strategy = ("Ported observed immediate-next links and numeric reference identity; "
                         "TN-0 durable seen set, one-page overlap on resume, explicit final-page evidence.")

    def __init__(self, http=None):
        # Preserve the proven campaign's immediate safe stop on 429.
        super().__init__(http or PublicHttp({"www.keejob.com", "keejob.com"},
            policy=HttpPolicy(retry_rate_limits=False, max_redirects=0)))

    @staticmethod
    def external_id(url):
        url = proven.public_url(url, detail=True)
        return proven.OFFER_PATH.fullmatch(urlsplit(url).path).group(1)

    def parse_listing(self, html, url):
        url = proven.public_url(url)
        if proven._looks_like_challenge(html):
            raise FetchProblem("keejob_public_challenge", {"url": url}, "manual_only")
        soup = soup_for(html)
        records = []
        # Reuse the proven anchor ranking and one-offer container isolation instead
        # of assuming a particular CSS layout.
        for card in proven.discover_cards(html, url, limit=1000):
            fields = {"secteur": card.get("sector", ""), "type de contrat": card.get("contract", "")}
            values = {"title": card["title"], "company": card.get("company") or "Unknown",
                      "location": card.get("location", ""), "canonical_url": card["source_url"],
                      "_list_text": card["card_text"], "_ported_card": card,
                      "_freshness": {"state": "discovered"}}
            posted = iso_date(card.get("publication_date"))
            if posted:
                values["posted_at"] = posted
            # Keep the proven card discovery; supplement only explicit current labels.
            for label, pattern in (
                ("salaire", r"\b\d[\d\s]*(?:[-\u2013]\s*\d[\d\s]*)?\s*(?:TND|DT)(?:\s*/\s*Mois)?"),
                ("mode de travail", r"\b(?:Hybride|Sur site|T\u00e9l\u00e9travail)\b"),
            ):
                found = re.search(pattern, card["card_text"], re.I)
                if found:
                    fields[label] = found.group()
            supplement(values, fields)
            records.append(InventoryRecord(card["source_reference"], card["source_url"],
                                           card["source_reference"], values))
        all_text = text(soup)
        match = re.search(r"([\d\s\u00a0]+)\s+offres? d[’']emploi trouv", all_text, re.I)
        if not match:
            match = re.search(r"sur\s+([\d\s\u00a0]+)\s+r[ée]sultats", all_text, re.I)
        total = int(re.sub(r"\D", "", match.group(1))) if match else None
        if total is None:
            raise ValueError("keejob_result_count_missing")
        next_url = proven.next_listing_url(html, url)
        pages = []
        for a in soup.select("a[href]"):
            from urllib.parse import urljoin
            raw = urljoin(url, a["href"])
            query = parse_qsl(urlsplit(raw).query, keep_blank_values=True)
            try:
                if len(query) == 1 and query[0][0] == "page":
                    safe = proven.public_url(raw)
                    if urlsplit(safe).path == urlsplit(url).path:
                        pages.append(int(dict(parse_qsl(urlsplit(safe).query)).get("page", "1")))
            except ValueError:
                continue
        current = int(dict(parse_qsl(urlsplit(url).query)).get("page", "1"))
        if not next_url and any(text(a).lower() in {"suivant", "next", "→"} for a in soup.select("a[href]")):
            raise ValueError("keejob_next_link_rejected")
        return Listing(tuple(records), next_url, total, max(pages, default=current), total == 0, {
            "count_source": "public_result_label", "parser_lineage": "TTEA_keejob_port",
            "pagination_detected": next_url is not None, "previously_seen_policy": "TN0_inventory_unique_key"})

    def parse_detail(self, html, url, record, fetched_at):
        url = proven.public_url(url, detail=True)
        if proven._looks_like_challenge(html):
            raise FetchProblem("keejob_public_challenge", {"url": url}, "manual_only")
        extracted = proven.extract_offer(html, url)
        soup = soup_for(html)
        postings = job_postings(soup)
        body = soup.select_one(".prose, [itemprop=description], .job-description")
        values = dict(record.values)
        values.update({key: extracted[key] for key in ("title", "company", "location", "description")
                       if extracted.get(key)})
        if postings:
            values.update({k: v for k, v in structured_fields(postings[0]).items() if v not in ("", None) and not (k == "company" and v == "Unknown" and values.get("company"))})
        if body:
            values["description"] = text(body)
        if not values.get("description"):
            raise ValueError("keejob_empty_description")
        values["canonical_url"] = url
        values["_ported_detail"] = extracted
        fields = {"type de contrat": extracted.get("contract", ""),
                  "experience requise": extracted.get("experience", ""),
                  "niveau d etudes": extracted.get("education", ""),
                  "date de publication": extracted.get("publication_date", ""),
                  **labelled_fields(soup)}
        # Match the explicit education label even when its French text was
        # decoded as Latin-1. Preserve the source value, including its encoding,
        # just as the other descriptive fields and the fixture contract do.
        for label_node in soup.select("h3, dt"):
            label = text(label_node)
            try:
                label = label.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            if proven._fold(label).strip(" :") == "niveau d'etudes":
                value_node = label_node.find_next_sibling()
                if value_node and value_node.name in {"p", "dd", "div", "span"} and text(value_node):
                    fields["niveau d etudes"] = text(value_node)
                    break
        supplement(values, fields)
        experience = fields.get("experience requise") or fields.get("experience")
        # Some source/fixture HTML has UTF-8 French text decoded as Latin-1.
        # Repair only the experience lookup; preserve the ported source payload.
        try:
            repaired = soup_for(html.encode("latin-1").decode("utf-8"))
            repaired_fields = labelled_fields(repaired)
            experience = repaired_fields.get("experience requise") or repaired_fields.get("experience") or experience
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        if experience:
            values.pop("min_years", None)
            values.pop("max_years", None)
            # Accept explicit year values/ranges only, not arbitrary numbers.
            years = re.fullmatch(
                r"(?:entre\s+|de\s+|from\s+)?(\d+)\s*"
                r"(?:(?:[-\u2013\u2014]|a|et|to|and)\s*(\d+)\s*)?"
                r"(?:ans?|years?)(?:\s+d['\u2019]experience)?",
                proven._fold(experience).strip(),
            )
            if years and all(int(n) <= 20 for n in years.groups() if n is not None):
                low, high = extract_years(experience)
                if low is not None and high is not None:
                    values.update(min_years=low, max_years=high)
        values.update(application(soup, body or soup, url))
        if "/postuler/" in values["application_url"]:
            values["_application"]["requires_login"] = True
        freshness(soup, values, fetched_at)
        reason = foreign_scope(values)
        if reason:
            return DetailResult(values, disposition="excluded", evidence={"reason": reason})
        return DetailResult(values)

    def resume_overlap(self, checkpoint):
        """Port run_next_batch's max(1,last_page-1), without rewinding TN-0 state.

        Revisit the preceding page and final persisted page; existing records are
        excluded by the DB's source-key uniqueness. Only newly moved-in records
        are staged. These observations never replace a committed checkpoint.
        """
        if checkpoint.completed or checkpoint.pages_processed < 1:
            return
        last_page = checkpoint.pages_processed
        for page in range(max(1, last_page - 1), last_page + 1):
            url = self.listing_url + ("?page=" + str(page) if page > 1 else "")
            response = self.http.fetch(url)
            if response.status != 200 or response.final_url != url:
                raise FetchProblem("keejob_overlap_fetch_failed", response.metadata())
            listing = self.parse_listing(response.text, url)
            if not listing.records and not listing.empty_confirmed:
                raise ValueError("keejob_overlap_unrecognized")
            yield InventoryPage(listing.records, None, evidence={
                "kind": "resume_overlap", "page": page, "http": response.evidence(),
                "reported_total": listing.reported_total, "next_url": listing.next_url,
                "proven_overlap_start": max(1, last_page - 1)})
