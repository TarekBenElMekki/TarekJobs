
from __future__ import annotations

import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

async def main():
    from playwright.async_api import async_playwright

    errors = []

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            candidates = [
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            ]
            edge = next((x for x in candidates if x.exists()), None)
            if edge is None:
                raise
            browser = await p.chromium.launch(executable_path=str(edge), headless=True)

        context = await browser.new_context(
            viewport={"width":1296,"height":987},
            color_scheme="light"
        )
        page = await context.new_page()

        page.on("pageerror", lambda exc: errors.append(f"PAGE: {exc}"))
        page.on(
            "console",
            lambda msg: errors.append(f"CONSOLE: {msg.text}") if msg.type == "error" else None
        )

        await page.goto(
            "http://127.0.0.1:8765/?runtime_smoke=0451",
            wait_until="domcontentloaded"
        )

        await page.wait_for_function(
            """() => document.getElementById('connectionText')?.textContent?.includes('ONLINE')""",
            timeout=12000
        )

        await page.locator('.nav-item[data-view="jobs"]').click()

        await page.wait_for_function(
            """() => {
                const count = document.getElementById('jobsCount')?.textContent || '';
                const table = document.getElementById('jobsTable');
                return !count.includes('LOADING') &&
                       table &&
                       (table.querySelector('.job-data-row') ||
                        table.textContent.includes('NO JOBS'));
            }""",
            timeout=12000
        )

        logo = page.locator(".brand-logo").first
        await logo.wait_for(state="visible", timeout=5000)

        metrics = await logo.evaluate(
            """img => ({
                naturalWidth: img.naturalWidth,
                naturalHeight: img.naturalHeight,
                displayedWidth: img.getBoundingClientRect().width,
                imageRendering: getComputedStyle(img).imageRendering,
                src: img.currentSrc || img.src
            })"""
        )

        (ROOT / "logs").mkdir(parents=True, exist_ok=True)
        await page.screenshot(
            path=str(ROOT / "logs" / "v0451_frontend_smoke.png"),
            full_page=True,
            animations="disabled"
        )
        await browser.close()

    if metrics["naturalWidth"] < 512:
        raise RuntimeError(f"HD logo is too small: {metrics}")
    if "pixel" in (metrics["imageRendering"] or "").lower():
        raise RuntimeError(f"Logo still uses pixelated scaling: {metrics}")
    if errors:
        raise RuntimeError("Front-end errors detected:\n" + "\n".join(errors))

    print("V0.4.5.1 FRONT-END SMOKE OK")
    print(f"Logo natural size : {metrics['naturalWidth']} x {metrics['naturalHeight']}")
    print(f"Logo display width: {metrics['displayedWidth']:.1f}px")
    print(f"Image rendering   : {metrics['imageRendering']}")
    print(f"Asset             : {metrics['src']}")

if __name__ == "__main__":
    asyncio.run(main())
