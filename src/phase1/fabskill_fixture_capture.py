from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "data" / "fixtures" / "fabskill"

SEARCH_URL = "https://fabskill.com/candidate/search"
JOB_URL_TEMPLATE = "https://fabskill.com/fr/job/{job_id}"

MAX_SCROLL_BATCHES = 2

VIEWPORT = {
    "width": 1440,
    "height": 1000,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value)
    return value.strip("_")[:100] or "fixture"


def write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def extract_job_links(page):
    """
    Discover links pointing to Fabskill individual job pages.

    We intentionally don't depend on one CSS selector.
    The first POC uses href patterns as the canonical discovery signal.
    """

    links = page.locator("a").evaluate_all(
        """
        elements => elements.map(a => ({
            href: a.href || "",
            text: (a.innerText || a.textContent || "").trim()
        }))
        """
    )

    jobs = {}

    for item in links:

        href = item.get("href", "")
        text = item.get("text", "")

        if not href:
            continue

        parsed = urlparse(href)

        if parsed.netloc.lower() not in {
            "fabskill.com",
            "www.fabskill.com",
        }:
            continue

        match = re.search(
            r"/(?:fr/)?job/(\\d+)",
            parsed.path,
            re.I,
        )

        if not match:
            continue

        job_id = match.group(1)

        canonical = f"https://fabskill.com/fr/job/{job_id}"

        jobs[job_id] = {
            "job_id": job_id,
            "url": canonical,
            "anchor_text": re.sub(r"\s+", " ", text).strip(),
        }

    return list(jobs.values())


def page_metrics(page):
    return page.evaluate(
        """
        () => ({
            scrollY: window.scrollY,
            scrollHeight: document.documentElement.scrollHeight,
            viewportHeight: window.innerHeight,
            documentHeight: document.documentElement.scrollHeight,
            bodyTextLength: document.body
                ? document.body.innerText.length
                : 0
        })
        """
    )


def capture_accessibility(page):
    """
    Playwright accessibility snapshot compatibility helper.

    Older/newer Playwright versions expose accessibility differently.
    If unavailable, preserve the fact that it was unavailable rather
    than failing the entire capture.
    """

    try:
        snapshot = page.accessibility.snapshot(
            interesting_only=False
        )

        return snapshot

    except Exception as exc:

        return {
            "_error": str(exc),
            "_note": "Accessibility snapshot unavailable in this Playwright version."
        }


def capture_network_metadata(network_events, path: Path):

    serializable = []

    for event in network_events:

        item = {
            "timestamp": event["timestamp"],
            "type": event["type"],
            "method": event.get("method"),
            "url": event.get("url"),
            "resource_type": event.get("resource_type"),
            "status": event.get("status"),
            "content_type": event.get("content_type"),
        }

        serializable.append(item)

    write_json(
        path,
        serializable,
    )


def capture_search():

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    fixture = (
        FIXTURE_ROOT
        / "search"
        / timestamp
    )

    fixture.mkdir(
        parents=True,
        exist_ok=True,
    )

    network_events = []

    manifest = {
        "source": "fabskill",
        "page_type": "search",
        "url": SEARCH_URL,
        "captured_at": now_iso(),
        "capture_version": "0.1.0",
        "max_scroll_batches": MAX_SCROLL_BATCHES,
        "viewport": VIEWPORT,
        "batches": [],
        "unique_jobs": [],
    }

    print("")
    print("=" * 70)
    print("FABSKILL SEARCH FIXTURE CAPTURE")
    print("=" * 70)
    print("")
    print(f"URL: {SEARCH_URL}")
    print(f"Maximum scroll batches: {MAX_SCROLL_BATCHES}")
    print("")

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
            locale="fr-FR",
        )

        page = context.new_page()

        def on_response(response):

            try:

                request = response.request

                network_events.append(
                    {
                        "timestamp": now_iso(),
                        "type": "response",
                        "method": request.method,
                        "url": response.url,
                        "resource_type": request.resource_type,
                        "status": response.status,
                        "content_type": response.headers.get(
                            "content-type",
                            "",
                        ),
                    }
                )

            except Exception:
                pass

        page.on(
            "response",
            on_response,
        )

        print("[0/2] Opening search page...")

        page.goto(
            SEARCH_URL,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        page.wait_for_timeout(3000)

        # ----------------------------------------------------
        # Batch 0 — initial rendered state
        # ----------------------------------------------------

        print("[1/2] Capturing initial state...")

        metrics = page_metrics(page)
        jobs = extract_job_links(page)

        batch_dir = fixture / "batch-000"
        batch_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        page.screenshot(
            path=str(
                batch_dir / "screenshot.png"
            ),
            full_page=True,
        )

        write_text(
            batch_dir / "raw.html",
            page.content(),
        )

        write_text(
            batch_dir / "visible_text.txt",
            page.locator("body").inner_text(),
        )

        write_json(
            batch_dir / "links.json",
            jobs,
        )

        write_json(
            batch_dir / "accessibility.json",
            capture_accessibility(page),
        )

        write_json(
            batch_dir / "metrics.json",
            metrics,
        )

        manifest["batches"].append(
            {
                "batch": 0,
                "type": "initial",
                "captured_at": now_iso(),
                "job_count": len(jobs),
                "metrics": metrics,
            }
        )

        unique_jobs = {
            item["job_id"]: item
            for item in jobs
        }

        print(
            f"      Jobs detected: {len(jobs)}"
        )

        # ----------------------------------------------------
        # Scroll batches
        # ----------------------------------------------------

        for batch_number in range(
            1,
            MAX_SCROLL_BATCHES + 1,
        ):

            print(
                f"[1.{batch_number}/2] "
                f"Scroll batch {batch_number}..."
            )

            before = page_metrics(page)

            # Scroll to the bottom of the currently rendered page.
            page.evaluate(
                """
                () => {
                    window.scrollTo({
                        top: document.documentElement.scrollHeight,
                        behavior: "instant"
                    });
                }
                """
            )

            # Allow lazy loading / JS requests to complete.
            page.wait_for_timeout(2500)

            after = page_metrics(page)

            jobs_after = extract_job_links(page)

            new_jobs = [
                item
                for item in jobs_after
                if item["job_id"] not in unique_jobs
            ]

            for item in new_jobs:
                unique_jobs[item["job_id"]] = item

            batch_dir = fixture / (
                f"batch-{batch_number:03d}"
            )

            batch_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            page.screenshot(
                path=str(
                    batch_dir / "screenshot.png"
                ),
                full_page=True,
            )

            write_text(
                batch_dir / "raw.html",
                page.content(),
            )

            write_text(
                batch_dir / "visible_text.txt",
                page.locator("body").inner_text(),
            )

            write_json(
                batch_dir / "links.json",
                jobs_after,
            )

            write_json(
                batch_dir / "new_jobs.json",
                new_jobs,
            )

            write_json(
                batch_dir / "accessibility.json",
                capture_accessibility(page),
            )

            write_json(
                batch_dir / "metrics_before.json",
                before,
            )

            write_json(
                batch_dir / "metrics_after.json",
                after,
            )

            manifest["batches"].append(
                {
                    "batch": batch_number,
                    "type": "scroll",
                    "captured_at": now_iso(),
                    "job_count_visible": len(jobs_after),
                    "new_job_count": len(new_jobs),
                    "metrics_before": before,
                    "metrics_after": after,
                }
            )

            print(
                f"      Visible jobs: {len(jobs_after)}"
            )

            print(
                f"      New jobs:     {len(new_jobs)}"
            )

            print(
                f"      Scroll height: "
                f"{before['scrollHeight']} -> "
                f"{after['scrollHeight']}"
            )

        # ----------------------------------------------------
        # Final manifest
        # ----------------------------------------------------

        manifest["unique_jobs"] = list(
            unique_jobs.values()
        )

        manifest["final_unique_job_count"] = len(
            unique_jobs
        )

        manifest["finished_at"] = now_iso()

        write_json(
            fixture / "manifest.json",
            manifest,
        )

        capture_network_metadata(
            network_events,
            fixture / "network.json",
        )

        browser.close()

    print("")
    print("-" * 70)
    print("SEARCH CAPTURE COMPLETE")
    print("-" * 70)
    print("")
    print(
        f"Unique jobs discovered: "
        f"{len(unique_jobs)}"
    )
    print(
        f"Fixture: {fixture}"
    )
    print(
        f"Network events: {len(network_events)}"
    )
    print("")
    print(
        "The capture stopped after exactly "
        f"{MAX_SCROLL_BATCHES} scroll batches."
    )
    print("")

    return fixture


def capture_job(job_id: str):

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    fixture = (
        FIXTURE_ROOT
        / "jobs"
        / str(job_id)
    )

    fixture.mkdir(
        parents=True,
        exist_ok=True,
    )

    url = JOB_URL_TEMPLATE.format(
        job_id=job_id
    )

    network_events = []

    print("")
    print("=" * 70)
    print("FABSKILL JOB FIXTURE CAPTURE")
    print("=" * 70)
    print("")
    print(f"Job ID: {job_id}")
    print(f"URL: {url}")
    print("")

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
            locale="fr-FR",
        )

        page = context.new_page()

        def on_response(response):

            try:

                request = response.request

                network_events.append(
                    {
                        "timestamp": now_iso(),
                        "type": "response",
                        "method": request.method,
                        "url": response.url,
                        "resource_type": request.resource_type,
                        "status": response.status,
                        "content_type": response.headers.get(
                            "content-type",
                            "",
                        ),
                    }
                )

            except Exception:
                pass

        page.on(
            "response",
            on_response,
        )

        print("[1/1] Opening job page...")

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        page.wait_for_timeout(2500)

        final_url = page.url
        title = page.title()

        metrics = page_metrics(page)

        screenshot = fixture / "screenshot.png"

        page.screenshot(
            path=str(screenshot),
            full_page=True,
        )

        write_text(
            fixture / "raw.html",
            page.content(),
        )

        write_text(
            fixture / "visible_text.txt",
            page.locator("body").inner_text(),
        )

        write_json(
            fixture / "accessibility.json",
            capture_accessibility(page),
        )

        links = page.locator("a").evaluate_all(
            """
            elements => elements.map(a => ({
                href: a.href || "",
                text: (a.innerText || a.textContent || "").trim()
            }))
            """
        )

        write_json(
            fixture / "links.json",
            links,
        )

        metadata = {
            "source": "fabskill",
            "page_type": "job",
            "job_id": str(job_id),
            "requested_url": url,
            "final_url": final_url,
            "page_title": title,
            "captured_at": now_iso(),
            "capture_version": "0.1.0",
            "viewport": VIEWPORT,
            "metrics": metrics,
        }

        write_json(
            fixture / "metadata.json",
            metadata,
        )

        capture_network_metadata(
            network_events,
            fixture / "network.json",
        )

        browser.close()

    print("")
    print("-" * 70)
    print("JOB CAPTURE COMPLETE")
    print("-" * 70)
    print("")
    print(f"Title: {title}")
    print(f"Final URL: {final_url}")
    print(f"Fixture: {fixture}")
    print(f"Network events: {len(network_events)}")
    print("")

    return fixture


def main():

    parser = argparse.ArgumentParser(
        description="Capture local Fabskill fixtures."
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    search = sub.add_parser(
        "search",
        help="Capture Fabskill search page."
    )

    job = sub.add_parser(
        "job",
        help="Capture Fabskill job page."
    )

    job.add_argument(
        "job_id",
        help="Fabskill job ID.",
    )

    args = parser.parse_args()

    if args.command == "search":
        capture_search()

    elif args.command == "job":
        capture_job(args.job_id)


if __name__ == "__main__":
    main()
