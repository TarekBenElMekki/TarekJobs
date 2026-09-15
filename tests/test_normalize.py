from src.normalize import detect_job_type, extract_years, parse_salary_text


def test_extract_years():
    assert extract_years("Requires 2 years of experience") == (2, 2)
    assert extract_years("3-5 years experience") == (3, 5)


def test_detect_job_type():
    assert detect_job_type("CDI temps plein") == "full-time"
    assert detect_job_type("Stage PFE") == "internship"


def test_tnd_salary():
    low, high, currency, _ = parse_salary_text("CDD 800 - 1700 TND")
    assert low == 800
    assert high == 1700
    assert currency == "TND"