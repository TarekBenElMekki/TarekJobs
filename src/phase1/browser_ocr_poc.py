from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageEnhance, ImageFilter
import pytesseract

from playwright.sync_api import sync_playwright

from src.models import JobRecord
from src.matcher import score_job

import yaml


CAPTURE_DIR = ROOT / "data" / "phase1" / "captures"
OCR_DIR = ROOT / "data" / "phase1" / "ocr"
EXTRACTED_DIR = ROOT / "data" / "phase1" / "extracted"
NORMALIZED_DIR = ROOT / "data" / "phase1" / "normalized"
RESULT_DIR = ROOT / "data" / "phase1" / "results"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean(value: str | None) -> str:
    if not value:
        return ""

    value = re.sub(r"\s+", " ", value)
    value = value.replace("\u00a0", " ")
    return value.strip()


def safe_filename(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value)
    return value[:100].strip("_") or "page"


def configure_tesseract():
    cmd = os.environ.get("TESSERACT_CMD")

    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
        return

    # Common Windows installation path.
    default = Path(
        r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )

    if default.exists():
        pytesseract.pytesseract.tesseract_cmd = str(default)


def preprocess_image(source: Path, target: Path):
    """
    OCR preprocessing:
    grayscale -> contrast -> sharpen -> upscale.
    """

    image = Image.open(source).convert("L")

    # Upscale for better OCR on small web text.
    width, height = image.size
    image = image.resize(
        (width * 2, height * 2),
        Image.Resampling.LANCZOS,
    )

    image = ImageEnhance.Contrast(image).enhance(1.7)

    image = image.filter(
        ImageFilter.SHARPEN
    )

    image.save(target)


def run_ocr(image_path: Path) -> str:
    configure_tesseract()

    image = Image.open(image_path)

    # PSM 6 = assume a uniform block of text.
    text = pytesseract.image_to_string(
        image,
        config="--psm 6",
    )

    return clean(text)


def extract_from_text(text: str, url: str) -> dict:

    lines = [
        clean(x)
        for x in text.splitlines()
        if clean(x)
    ]

    blob = "\n".join(lines)

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    title = ""

    title_patterns = [
        r"(?:job title|position|role|vacancy)\s*[:\-]\s*(.+)",
        r"(?:poste|intitulé du poste)\s*[:\-]\s*(.+)",
    ]

    for pattern in title_patterns:
        m = re.search(pattern, blob, re.I)
        if m:
            title = clean(m.group(1))
            break

    if not title and lines:
        # Heuristic:
        # choose first reasonably sized line that looks like a title.
        candidates = [
            x for x in lines[:20]
            if 4 <= len(x) <= 100
        ]

        if candidates:
            title = candidates[0]

    # --------------------------------------------------------
    # Company
    # --------------------------------------------------------

    company = ""

    company_patterns = [
        r"(?:company|employer|organisation|organization)\s*[:\-]\s*(.+)",
        r"(?:entreprise|société)\s*[:\-]\s*(.+)",
    ]

    for pattern in company_patterns:
        m = re.search(pattern, blob, re.I)
        if m:
            company = clean(m.group(1))
            break

    # --------------------------------------------------------
    # Location
    # --------------------------------------------------------

    location = ""

    location_patterns = [
        r"(?:location|based in|勤務地)\s*[:\-]\s*(.+)",
        r"(?:lieu|localisation)\s*[:\-]\s*(.+)",
    ]

    for pattern in location_patterns:
        m = re.search(pattern, blob, re.I)
        if m:
            location = clean(m.group(1))
            break

    # --------------------------------------------------------
    # Employment type
    # --------------------------------------------------------

    employment_type = ""

    employment_terms = [
        "full-time",
        "full time",
        "part-time",
        "part time",
        "contract",
        "internship",
        "intern",
        "permanent",
        "temporary",
        "stage",
        "cdi",
        "cdd",
    ]

    for term in employment_terms:
        if re.search(re.escape(term), blob, re.I):
            employment_type = term
            break

    # --------------------------------------------------------
    # Remote
    # --------------------------------------------------------

    remote = bool(
        re.search(
            r"\b(remote|remotely|work from home|télétravail|remote-first)\b",
            blob,
            re.I,
        )
    )

    # --------------------------------------------------------
    # Experience
    # --------------------------------------------------------

    min_years = None
    max_years = None

    m = re.search(
        r"(\d+)\s*(?:-|to)\s*(\d+)\s*(?:years?|ans?)",
        blob,
        re.I,
    )

    if m:
        min_years = int(m.group(1))
        max_years = int(m.group(2))
    else:
        m = re.search(
            r"(\d+)\+?\s*(?:years?|ans?)\s+(?:of\s+)?experience",
            blob,
            re.I,
        )

        if m:
            min_years = int(m.group(1))

    # --------------------------------------------------------
    # Salary
    # --------------------------------------------------------

    salary_min = None
    salary_max = None
    salary_currency = None
    salary_period = None

    currency_map = {
        "€": "EUR",
        "$": "USD",
        "£": "GBP",
        "CHF": "CHF",
        "TND": "TND",
    }

    salary_match = re.search(
        r"([€$£]|CHF|TND)\s*([\d\s,.]+)"
        r"(?:\s*[-–]\s*([€$£]|CHF|TND)?\s*([\d\s,.]+))?",
        blob,
        re.I,
    )

    if salary_match:
        symbol = salary_match.group(1).upper()
        salary_currency = currency_map.get(symbol, symbol)

        def number(value):
            if not value:
                return None

            value = value.replace(" ", "")

            # Basic European/English number handling.
            if value.count(",") == 1 and value.count(".") == 0:
                value = value.replace(",", ".")
            else:
                value = value.replace(",", "")

            try:
                return float(value)
            except ValueError:
                return None

        salary_min = number(salary_match.group(2))
        salary_max = number(salary_match.group(4))

    # --------------------------------------------------------
    # Opportunity type
    # --------------------------------------------------------

    opportunity_type = "phd" if re.search(
        r"\b(ph\.?d|doctoral|doctorate|doctoral researcher)\b",
        blob,
        re.I,
    ) else "job"

    # --------------------------------------------------------
    # Education
    # --------------------------------------------------------

    education = ""

    education_patterns = [
        r"(?:education|degree|qualification)\s*[:\-]\s*(.+)",
        r"(?:formation|diplôme)\s*[:\-]\s*(.+)",
    ]

    for pattern in education_patterns:
        m = re.search(pattern, blob, re.I)
        if m:
            education = clean(m.group(1))
            break

    # --------------------------------------------------------
    # Visa
    # --------------------------------------------------------

    visa_sponsorship = None

    if re.search(
        r"(visa sponsorship|sponsorship available|work permit sponsorship)",
        blob,
        re.I,
    ):
        visa_sponsorship = True

    if re.search(
        r"(no visa sponsorship|without sponsorship|sponsorship not available)",
        blob,
        re.I,
    ):
        visa_sponsorship = False

    # --------------------------------------------------------
    # Description
    # --------------------------------------------------------

    description = blob

    return {
        "opportunity_type": opportunity_type,
        "title": title or "OCR extracted opportunity",
        "company": company or "Unknown",
        "location": location,
        "country": "",
        "city": "",
        "description": description,
        "employment_type": employment_type,
        "work_mode": "remote" if remote else "onsite_or_hybrid",
        "remote": remote,
        "remote_region": location if remote else "",
        "seniority": "",
        "min_years": min_years,
        "max_years": max_years,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "salary_period": salary_period,
        "education_requirement": education,
        "visa_status": "unknown",
        "visa_sponsorship": visa_sponsorship,
        "international_candidates": None,
        "relocation_support": None,
        "funding_status": "",
        "tuition_covered": None,
        "housing_support": None,
        "insurance_support": None,
        "travel_support": None,
        "benefits": {},
        "source_name": "browser_ocr_poc",
        "source_url": url,
        "external_id": None,
        "raw_payload": {
            "extraction_method": "browser_screenshot_ocr",
            "ocr_lines": lines,
            "captured_at": now_iso(),
        },
    }


def normalize_record(data: dict) -> JobRecord:
    """
    Convert OCR extraction into the EXISTING TarekJobs JobRecord.
    """

    return JobRecord(
        opportunity_type=data.get("opportunity_type", "job"),
        title=clean(data.get("title")),
        company=clean(data.get("company")) or "Unknown",
        location=clean(data.get("location")),
        country=clean(data.get("country")),
        city=clean(data.get("city")),
        description=clean(data.get("description")),
        employment_type=clean(data.get("employment_type")),
        work_mode=clean(data.get("work_mode")),
        remote=bool(data.get("remote")),
        remote_region=clean(data.get("remote_region")),
        seniority=clean(data.get("seniority")),
        min_years=data.get("min_years"),
        max_years=data.get("max_years"),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        salary_currency=data.get("salary_currency"),
        salary_period=data.get("salary_period"),
        education_requirement=clean(
            data.get("education_requirement")
        ),
        visa_status=data.get("visa_status", "unknown"),
        visa_sponsorship=data.get("visa_sponsorship"),
        international_candidates=data.get(
            "international_candidates"
        ),
        relocation_support=data.get("relocation_support"),
        funding_status=clean(data.get("funding_status")),
        tuition_covered=data.get("tuition_covered"),
        housing_support=data.get("housing_support"),
        insurance_support=data.get("insurance_support"),
        travel_support=data.get("travel_support"),
        benefits=data.get("benefits") or {},
        source_name="browser_ocr_poc",
        source_url=data["source_url"],
        external_id=data.get("external_id"),
        raw_payload=data.get("raw_payload") or {},
    )


def load_config():
    profile = json.loads(
        (ROOT / "config" / "profile.json").read_text(
            encoding="utf-8"
        )
    )

    settings = yaml.safe_load(
        (ROOT / "config" / "settings.yaml").read_text(
            encoding="utf-8"
        )
    )

    return profile, settings


def capture_page(url: str, slug: str):
    """
    Browser acquisition.

    We deliberately capture the rendered page as an image.
    This proves the browser -> visual -> OCR route.
    """

    capture_path = CAPTURE_DIR / f"{slug}.png"
    html_path = CAPTURE_DIR / f"{slug}.html"

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        context = browser.new_context(
            viewport={
                "width": 1440,
                "height": 1000,
            },
            device_scale_factor=1,
            locale="en-US",
        )

        page = context.new_page()

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        # Give JS-driven pages a short stabilization window.
        page.wait_for_timeout(2500)

        # Save rendered HTML for debugging.
        html_path.write_text(
            page.content(),
            encoding="utf-8",
        )

        page.screenshot(
            path=str(capture_path),
            full_page=True,
        )

        final_url = page.url
        title = page.title()

        browser.close()

    return {
        "capture": capture_path,
        "html": html_path,
        "final_url": final_url,
        "page_title": title,
    }


def run(url: str):

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    slug = safe_filename(
        urlparse(url).netloc
        + "_"
        + timestamp
    )

    print("")
    print("=" * 70)
    print("TAREKJOBS PHASE 1 — BROWSER/OCR PROOF OF CONCEPT")
    print("=" * 70)
    print("")
    print(f"URL: {url}")
    print("")

    # --------------------------------------------------------
    # A — Browser
    # --------------------------------------------------------

    print("[A/7] Browser acquisition...")

    browser_result = capture_page(
        url,
        slug,
    )

    print(
        f"      Screenshot: {browser_result['capture']}"
    )

    print(
        f"      Final URL : {browser_result['final_url']}"
    )

    print(
        f"      Title     : {browser_result['page_title']}"
    )

    # --------------------------------------------------------
    # B — OCR preprocessing
    # --------------------------------------------------------

    print("[B/7] OCR preprocessing...")

    processed = OCR_DIR / f"{slug}_processed.png"

    preprocess_image(
        browser_result["capture"],
        processed,
    )

    # --------------------------------------------------------
    # C — OCR
    # --------------------------------------------------------

    print("[C/7] OCR extraction...")

    text = run_ocr(processed)

    ocr_file = OCR_DIR / f"{slug}.txt"

    ocr_file.write_text(
        text,
        encoding="utf-8",
    )

    print(
        f"      OCR characters: {len(text)}"
    )

    # --------------------------------------------------------
    # D — Structured extraction
    # --------------------------------------------------------

    print("[D/7] Structured extraction...")

    extracted = extract_from_text(
        text,
        browser_result["final_url"],
    )

    extracted_file = (
        EXTRACTED_DIR / f"{slug}.json"
    )

    extracted_file.write_text(
        json.dumps(
            extracted,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        f"      Title   : {extracted['title']}"
    )
    print(
        f"      Company : {extracted['company']}"
    )
    print(
        f"      Location: {extracted['location']}"
    )

    # --------------------------------------------------------
    # E — Existing JobRecord normalization
    # --------------------------------------------------------

    print("[E/7] TarekJobs normalization...")

    record = normalize_record(
        extracted
    )

    normalized_file = (
        NORMALIZED_DIR / f"{slug}.json"
    )

    normalized_file.write_text(
        record.model_dump_json(
            indent=2
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # F — Existing TarekFit matching engine
    # --------------------------------------------------------

    print("[F/7] Existing TarekFit scoring...")

    profile, settings = load_config()

    score = score_job(
        record,
        profile,
        settings,
    )

    result = {
        "phase": "phase1_browser_ocr_poc",
        "captured_at": now_iso(),
        "browser": browser_result,
        "record": record.model_dump(
            mode="json"
        ),
        "score": score.model_dump(
            mode="json"
        ),
    }

    result_file = (
        RESULT_DIR / f"{slug}.json"
    )

    result_file.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # G — Human-readable result
    # --------------------------------------------------------

    print("[G/7] POC result")
    print("")
    print("-" * 70)

    print(
        f"TITLE       : {record.title}"
    )

    print(
        f"COMPANY     : {record.company}"
    )

    print(
        f"LOCATION    : {record.location or 'UNKNOWN'}"
    )

    print(
        f"REMOTE      : {record.remote}"
    )

    print(
        f"TYPE        : {record.opportunity_type}"
    )

    print(
        f"EXPERIENCE  : {record.min_years or '?'}"
        + (
            f"-{record.max_years}"
            if record.max_years
            else "+"
        )
        + " years"
    )

    print("")

    print(
        f"TECHNICAL   : {score.technical}/100"
    )

    print(
        f"ELIGIBILITY : {score.eligibility}/100"
    )

    print(
        f"LOCATION    : {score.location}/100"
    )

    print(
        f"EXPERIENCE  : {score.experience}/100"
    )

    print(
        f"OVERALL     : {score.overall}/100"
    )

    print(
        f"PRIORITY    : {score.priority}"
    )

    print(
        f"VERDICT     : {score.verdict}"
    )

    print("")

    print("FILES")
    print(f"  Screenshot : {browser_result['capture']}")
    print(f"  HTML       : {browser_result['html']}")
    print(f"  OCR        : {ocr_file}")
    print(f"  Extracted  : {extracted_file}")
    print(f"  Normalized : {normalized_file}")
    print(f"  Result     : {result_file}")

    print("")
    print("=" * 70)
    print("PHASE 1 PROOF OF CONCEPT COMPLETE")
    print("=" * 70)
    print("")

    return result


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "url",
        help="Public job/opportunity page URL",
    )

    args = parser.parse_args()

    run(args.url)


if __name__ == "__main__":
    main()
