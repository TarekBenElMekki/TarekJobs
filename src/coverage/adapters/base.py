"""Common TN-0 adapter mechanics; site modules supply pure listing/detail parsers."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from urllib.parse import parse_qs, urlsplit

from ..artifacts import stable_id
from ..contracts import (Checkpoint, DetailResult, InventoryPage, InventoryRecord,
                         Partition, Scope, TerminalEvidence)
from .http import FetchProblem, PublicHttp
from .parsing import absolute, soup_for, text


@dataclass(frozen=True)
class Listing:
    records: tuple[InventoryRecord, ...]
    next_url: str | None
    reported_total: int | None = None
    last_page: int | None = None
    empty_confirmed: bool = False
    evidence: dict = field(default_factory=dict)


def page_number(url):
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if "page" in query:
        value = query["page"][0]
        if not value.isdigit() or int(value) < 1:
            raise ValueError("invalid_page_number")
        return int(value)
    match = re.search(r"/page/(\d+)/?$", parsed.path)
    return int(match.group(1)) if match else 1


def pagination(soup, current_url, listing_url):
    current = page_number(current_url)
    next_links, pages = [], []
    base_path = urlsplit(listing_url).path.rstrip("/")
    for anchor in soup.select('a[href]'):
        href = absolute(current_url, anchor.get("href"))
        if not href or urlsplit(href).hostname != urlsplit(listing_url).hostname:
            continue
        parsed = urlsplit(href)
        path = re.sub(r"/page/\d+/?$", "", parsed.path).rstrip("/")
        if path != base_path:
            continue
        if "page=" not in parsed.query and not re.search(r"/page/\d+/?$", parsed.path):
            continue
        number = page_number(href)
        pages.append(number)
        label = text(anchor).strip().lower()
        rel = anchor.get("rel", [])
        classes = anchor.get("class", [])
        if "next" in rel or "next" in classes or label in {"suivant", "suivante", "next", "→", "»", ">"}:
            next_links.append((number, href))
    next_links = list(dict.fromkeys(next_links))
    if next_links:
        if len({url for _, url in next_links}) != 1 or next_links[0][0] != current + 1:
            raise ValueError("inconsistent_next_page")
        return next_links[0][1], max(pages, default=current)
    # Only follow a numbered page link that is actually present; no URL guessing.
    for anchor in soup.select("a[href]"):
        href = absolute(current_url, anchor.get("href"))
        if not href:
            continue
        path = re.sub(r"/page/\d+/?$", "", urlsplit(href).path).rstrip("/")
        if (urlsplit(href).hostname == urlsplit(listing_url).hostname and path == base_path
                and text(anchor).isdigit() and int(text(anchor)) == current + 1
                and page_number(href) == current + 1):
            return href, max(pages, default=current)
    return None, max(pages, default=current)


def unique_cards(records):
    """Collapse duplicate DOM links/cards only; vacancy identity stays in Dedupe V2."""
    unique = {}
    duplicates = 0
    for record in records:
        previous = unique.get(record.key)
        if previous:
            if previous.source_url != record.source_url:
                raise ValueError("same_page_identity_conflict")
            duplicates += 1
        else:
            unique[record.key] = record
    return tuple(unique.values()), duplicates


class NativeAdapter:
    listing_url: str
    boundary_strategy = "Follow only declared pagination; never infer exhaustion from fetch failure."

    def __init__(self, http=None):
        self.http = http or PublicHttp({urlsplit(self.listing_url).hostname})

    def close(self):
        self.http.close()

    def plan(self, scope: Scope):
        if scope.country not in {"*", "TN", "Tunisia", "Tunisie"} or scope.work_mode != "*" or scope.region:
            raise ValueError("Native adapter inventories the complete Tunisia mission; narrower scopes are unsupported")
        return (Partition("tunisia-all", {"listing_url": self.listing_url, "country_policy": "explicit_foreign_excluded"}),)

    def inventory(self, scope, partition, checkpoint):
        if checkpoint.adapter_version != self.metadata.adapter_version:
            raise ValueError("Adapter version mismatch")
        if partition.key != "tunisia-all" or partition.metadata.get("listing_url") != self.listing_url:
            raise ValueError("Partition does not belong to this adapter")
        state = checkpoint.cursor or {}
        url = state.get("url", self.listing_url)
        if page_number(url) != checkpoint.pages_processed + 1:
            raise ValueError("Checkpoint page sequence mismatch")
        if (urlsplit(url).hostname != urlsplit(self.listing_url).hostname or
            re.sub(r"/page/\d+/?$", "", urlsplit(url).path).rstrip("/") != urlsplit(self.listing_url).path.rstrip("/")):
            raise ValueError("Checkpoint URL is outside the declared listing")
        response = None
        try:
            response = self.http.fetch(url)
            if response.status != 200:
                raise FetchProblem("listing_missing", response.metadata())
            if (page_number(response.final_url) != page_number(url) or
                re.sub(r"/page/\d+/?$", "", urlsplit(response.final_url).path).rstrip("/") !=
                    urlsplit(self.listing_url).path.rstrip("/")):
                raise FetchProblem("unexpected_listing_redirect", response.metadata())
            listing = self.parse_listing(response.text, response.final_url)
            records, duplicates = unique_cards(listing.records)
            if not records and not listing.empty_confirmed:
                raise ValueError("unrecognized_or_empty_listing")
            if not records and listing.next_url:
                raise ValueError("empty_page_before_boundary")
            emitted = state.get("records_seen", 0) + len(records)
            first_total = state.get("initial_reported_total", listing.reported_total)
            total_changed = bool(state.get("reported_total_changed") or
                                 first_total is not None and listing.reported_total != first_total)
            evidence = {"source_listing": self.listing_url, "http": response.evidence(),
                        "initial_reported_total": first_total, "reported_total": listing.reported_total,
                        "reported_total_changed": total_changed, "page": page_number(url),
                        "records_on_page": len(records), "records_seen": emitted,
                        "same_page_duplicates": duplicates, "next_url": listing.next_url,
                        "advertised_last_page": listing.last_page, **listing.evidence}
            fingerprint = stable_id(sorted(record.key for record in records))
            if listing.next_url:
                if page_number(listing.next_url) != page_number(url) + 1:
                    raise ValueError("pagination_cycle_or_jump")
                cursor = {"url": listing.next_url, "records_seen": emitted,
                          "initial_reported_total": first_total, "reported_total_changed": total_changed}
                return InventoryPage(records, cursor, evidence=evidence, content_fingerprint=fingerprint)
            # A vanished next link before an advertised boundary is a parse/state
            # failure, never evidence of exhaustion.
            if listing.last_page and listing.last_page > page_number(url):
                raise ValueError("pagination_control_disappeared_early")
            if listing.reported_total is not None and emitted < listing.reported_total:
                raise ValueError("reported_total_not_reached")
            evidence["boundary"] = "explicit_empty_listing" if not records else "pagination_next_absent"
            if first_total is not None:
                evidence["expected_unique_total"] = listing.reported_total
            terminal = TerminalEvidence("exhausted", evidence["boundary"],
                {**evidence, "http": response.metadata()})
            return InventoryPage(records, None, terminal, evidence=evidence, content_fingerprint=fingerprint)
        except FetchProblem as exc:
            evidence = {"source_listing": self.listing_url, "page": page_number(url),
                        "http_problem": exc.code, "access": exc.evidence}
            if exc.terminal_state:
                return InventoryPage((), None, TerminalEvidence(exc.terminal_state, exc.code, evidence),
                                     evidence=evidence)
            return InventoryPage((), None, error_code=exc.code, evidence=evidence)
        except (ValueError, TypeError, KeyError) as exc:
            return InventoryPage((), None, error_code="listing_parse_or_boundary_error",
                                 evidence={"source_listing": self.listing_url,
                                           "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                                           "http": response.evidence() if response else {}})

    def detail(self, record):
        response = None
        try:
            response = self.http.fetch(record.source_url)
            if response.status in {404, 410}:
                return DetailResult(error_code="detail_missing_from_source",
                                    evidence={"freshness": "missing_from_source", "http": response.evidence()})
            if response.status != 200:
                raise FetchProblem("detail_http_error", response.metadata())
            if self.external_id(response.final_url) != self.external_id(record.source_url):
                raise FetchProblem("unexpected_detail_redirect", response.metadata())
            result = self.parse_detail(response.text, response.final_url, record, response.completed_at)
            return replace(result, evidence={**result.evidence, "http": response.evidence()})
        except FetchProblem as exc:
            return DetailResult(error_code=exc.code, evidence={"access": exc.evidence,
                                "terminal_classification": exc.terminal_state})
        except (ValueError, TypeError, KeyError):
            return DetailResult(error_code="detail_parse_error",
                                evidence={"http": response.evidence() if response else {}})
