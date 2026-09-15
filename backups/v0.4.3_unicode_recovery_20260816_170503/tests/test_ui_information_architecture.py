from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'ui' / 'index.html').read_text(encoding='utf-8')
JS = (ROOT / 'ui' / 'app.js').read_text(encoding='utf-8')


def section(section_id: str) -> str:
    start = HTML.index(f'id="view-{section_id}"')
    next_pos = HTML.find('<section class="view"', start + 1)
    if next_pos == -1:
        next_pos = HTML.find('</main>', start)
    return HTML[start:next_pos]


def test_overview_owns_only_database_change_question():
    overview = section('overview').upper()
    assert 'DATABASE SNAPSHOT' in overview
    assert 'COLLECTION LEDGER' in overview
    assert 'TOP OPPORTUNITIES' not in overview
    assert 'APPLICATION FUNNEL' not in overview
    assert 'SOURCE COVERAGE' not in overview


def test_jobs_owns_review_queue_and_opportunity_register():
    jobs = section('jobs').upper()
    assert 'REVIEW QUEUE' in jobs
    assert 'OPPORTUNITY REGISTER' in jobs
    assert 'VERDICT' in jobs
    assert 'DECISION' in jobs


def test_applications_has_empty_state_destination_and_board():
    assert 'applicationBoard' in section('applications')
    assert 'NO ACTIVE APPLICATIONS' in JS
    assert 'OPEN REVIEW QUEUE' in JS


def test_sources_owns_coverage_registry():
    sources = section('sources').upper()
    assert 'COVERAGE REGISTER' in sources
    assert 'HOW COMPLETE AND HEALTHY IS MY MARKET COVERAGE?' in sources
    assert 'SOURCESTABLE' in sources


def test_runs_owns_live_execution_and_stop_control_only():
    runs = section('pipeline')
    assert 'runSourceProgress' in runs
    assert 'runLogs' in runs
    assert 'stopRunButton' in runs
    assert 'runHistory' not in runs


def test_profile_is_editable():
    profile = section('profile')
    assert 'editProfileButton' in profile
    assert 'profileSummary' in profile


def test_no_global_collection_button():
    assert 'id="runButton"' not in HTML
    assert 'id="runButtonPipeline"' in HTML



def test_v042_reviewable_option_exists():
    jobs = section("jobs")
    assert 'value="reviewable"' in jobs


def test_v042_ascii_placeholders_only():
    assert "Ã¢â‚¬â€" not in JS
    assert "â€”" not in JS


def test_v042_conditional_salary_policy_is_visible():
    assert "ACCEPT IF PAY" in JS
    assert "SALARY-SENSITIVE MIN MONTHLY TND" in JS