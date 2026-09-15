from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

PROJECT = Path(__file__).resolve().parents[1]
DASHBOARD = "http://127.0.0.1:8765"

desktop = Path.home() / "OneDrive" / "Desktop"
if not desktop.exists():
    desktop = Path.home() / "Desktop"

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUT = desktop / f"TarekJobs_UI_Audit_{stamp}"
OUTPUT.mkdir(parents=True, exist_ok=True)

VIEWPORT = {"width": 1600, "height": 1000}


async def settle(page, delay=650):
    # networkidle can remain noisy because the Runs screen may poll.
    try:
        await page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    await page.wait_for_timeout(delay)


async def screenshot(page, filename: str):
    path = OUTPUT / filename
    await settle(page)
    await page.screenshot(
        path=str(path),
        full_page=True,
        animations="disabled",
    )
    print(f"CAPTURED  {path.name}")


async def click_view(page, view: str, expected_id: str):
    await page.locator(f'.nav-item[data-view="{view}"]').click()
    await page.locator(expected_id).wait_for(state="visible", timeout=8000)
    await settle(page, 750)


async def launch_browser(p):
    # Prefer the user's installed Microsoft Edge so no browser binary download
    # is required. Fall back to common executable paths if channel discovery
    # is unavailable.
    try:
        return await p.chromium.launch(channel="msedge", headless=True)
    except Exception as first:
        candidates = [
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return await p.chromium.launch(
                    executable_path=str(candidate),
                    headless=True,
                )
        raise RuntimeError(
            "Could not launch Microsoft Edge or Chrome for screenshot capture."
        ) from first


async def main():
    async with async_playwright() as p:
        browser = await launch_browser(p)
        context = await browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
            color_scheme="light",
        )
        page = await context.new_page()

        console_errors = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text)
            if msg.type == "error"
            else None,
        )

        await page.goto(f"{DASHBOARD}/?audit={stamp}", wait_until="domcontentloaded")
        await page.locator("#connectionText").wait_for(timeout=10000)
        await settle(page, 1000)

        # Disable transitions after the first render to make captures stable.
        await page.add_style_tag(
            content="""
                *, *::before, *::after {
                    animation-duration: 0s !important;
                    transition-duration: 0s !important;
                    caret-color: transparent !important;
                }
            """
        )

        # 01 - Overview
        await click_view(page, "overview", "#overviewSnapshot")
        await screenshot(page, "01_overview.png")

        # 02 - Jobs register
        await click_view(page, "jobs", "#jobsTable")
        await screenshot(page, "02_jobs.png")

        # 03 - First real job detail state
        first_job = page.locator('#jobsTable [data-job-id]').first
        if await first_job.count():
            await first_job.click()
            await page.locator("#jobModal:not(.hidden)").wait_for(
                state="visible",
                timeout=8000,
            )
            await settle(page, 650)
            await screenshot(page, "03_job_detail.png")
            await page.locator("#closeJobModal").click()
            await settle(page, 250)
        else:
            print("SKIPPED   03_job_detail.png (no job row found)")

        # 04 - Application workflow
        await click_view(page, "applications", "#applicationBoard")
        await screenshot(page, "04_applications.png")

        # 05 - Source register
        await click_view(page, "sources", "#sourcesTable")
        await screenshot(page, "05_sources.png")

        # 06 - Current run / collector control
        await click_view(page, "pipeline", "#runLogs")
        await screenshot(page, "06_runs.png")

        # 07 - Profile / scoring rules
        await click_view(page, "profile", "#profileSummary")
        await screenshot(page, "07_profile.png")

        # Return to Overview and capture only the fixed frame/top-level shell.
        await click_view(page, "overview", "#overviewSnapshot")

        manifest = {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "dashboard": DASHBOARD,
            "viewport": VIEWPORT,
            "captures": [
                "01_overview.png",
                "02_jobs.png",
                "03_job_detail.png",
                "04_applications.png",
                "05_sources.png",
                "06_runs.png",
                "07_profile.png",
            ],
            "review_order": [
                "1. Functional purpose / question answered",
                "2. Missing actions or information",
                "3. Redundant or misplaced information",
                "4. Interaction / workflow problems",
                "5. Visual hierarchy",
                "6. Vintage / analog visual refinement",
            ],
            "browser_console_errors": console_errors,
        }
        (OUTPUT / "audit_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        await browser.close()

    print()
    print(f"AUDIT_FOLDER={OUTPUT}")
    print(f"CAPTURE_COUNT={len(list(OUTPUT.glob('*.png')))}")


if __name__ == "__main__":
    asyncio.run(main())