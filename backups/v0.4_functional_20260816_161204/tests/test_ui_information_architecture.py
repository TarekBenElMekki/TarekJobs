from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "ui" / "styles.css").read_text(encoding="utf-8")
JS = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")


def section(section_id: str) -> str:
    start = HTML.index(f'id="view-{section_id}"')
    next_pos = HTML.find('<section class="view"', start + 1)
    if next_pos == -1:
        next_pos = HTML.find('</main>', start)
    return HTML[start:next_pos]


def test_vintage_visual_language_is_square_and_monospaced():
    assert '"Courier New"' in CSS
    assert 'border-radius: 0 !important' in CSS
    assert '--paper:' in CSS
    assert '--accent:' in CSS


def test_overview_does_not_duplicate_domain_panels():
    overview = section("overview")
    assert "TOP OPPORTUNITIES" not in overview.upper()
    assert "APPLICATION FUNNEL" not in overview.upper()
    assert "SOURCE COVERAGE" not in overview.upper()
    assert "RECENTLY DISCOVERED" not in overview.upper()
    assert "DATABASE SNAPSHOT" in overview.upper()
    assert "COLLECTION LEDGER" in overview.upper()


def test_jobs_owns_opportunity_register():
    jobs = section("jobs")
    assert "WHICH VACANCIES SHOULD I CONSIDER?" in jobs
    assert "OPPORTUNITY REGISTER" in jobs


def test_applications_owns_application_workflow():
    applications = section("applications")
    assert "WHERE DO MY SELECTED OPPORTUNITIES STAND?" in applications
    assert "applicationBoard" in applications


def test_sources_has_single_source_register_not_duplicate_metrics():
    sources = section("sources")
    assert "WHERE DOES THE MARKET DATA COME FROM?" in sources
    assert "sourcesTable" in sources
    assert "sourceMetrics" not in sources


def test_runs_contains_live_execution_but_not_history():
    runs = section("pipeline")
    assert "IS THE COLLECTION ENGINE WORKING NOW?" in runs
    assert "runSourceProgress" in runs
    assert "runLogs" in runs
    assert "runHistory" not in runs


def test_global_collection_button_removed():
    assert 'id="runButton"' not in HTML
    assert 'id="runButtonPipeline"' in HTML
