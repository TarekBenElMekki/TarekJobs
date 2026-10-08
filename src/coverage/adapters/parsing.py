"""Pure deterministic HTML helpers. No fetches, classifiers or model clients."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ...normalize import clean_text, norm_text, parse_date, parse_salary_text


def text(node) -> str:
    return clean_text(node.get_text(" ", strip=True)) if node else ""


def soup_for(body: str):
    return BeautifulSoup(body, "html.parser")


def iso_date(value):
    if not value:
        return None
    # Require a day/month/year or ISO date: never invent a day for month archives
    # or today's year for a partial string.
    string = str(value).strip()
    if not (re.search(r"\b\d{4}-\d{2}-\d{2}", string) or
            re.search(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b", string) or
            re.search(r"\b\d{1,2}\s+\w+\s+\d{4}\b", string)):
        return None
    try:
        moment = parse_date(string)
        return moment.astimezone(timezone.utc).isoformat() if moment else None
    except (ValueError, TypeError, OverflowError):
        return None


def absolute(base, value):
    if not isinstance(value, str) or not value.strip():
        return None
    url = urljoin(base, value.strip())
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        return None
    return url.split("#", 1)[0]


def job_postings(soup):
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if types == "JobPosting" or isinstance(types, list) and "JobPosting" in types:
                yield value
            for key in ("@graph", "itemListElement", "item"):
                if key in value:
                    yield from walk(value[key])
    result = []
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            result.extend(walk(json.loads(node.get_text())))
        except (ValueError, TypeError):
            continue
    return result


def labelled_fields(soup):
    result = {}
    for node in soup.select("dt, h3, .job-detail__title, .details-header"):
        value = node.find_next_sibling()
        if value and value.name in {"dd", "p", "div", "span"}:
            key = norm_text(text(node)).strip(" :")
            if key:
                result.setdefault(key, text(value))
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) == 2:
            result.setdefault(norm_text(text(cells[0])), text(cells[1]))
    return result


def work_mode(value):
    value = norm_text(value)
    return {"teletravail": "remote", "a distance": "remote", "remote": "remote",
            "telecommute": "remote", "hybride": "hybrid", "hybrid": "hybrid",
            "sur site": "onsite", "on site": "onsite", "onsite": "onsite"}.get(value, "")


def contract(value):
    if isinstance(value, list):
        value = " / ".join(str(v) for v in value)
    return clean_text(value) if value else ""


def structured_fields(posting):
    organization = posting.get("hiringOrganization") or {}
    if isinstance(organization, list):
        organization = organization[0] if organization else {}
    if not isinstance(organization, dict):
        organization = {"name": str(organization)}
    places = posting.get("jobLocation") or []
    if isinstance(places, dict):
        places = [places]
    addresses = [p.get("address", {}) for p in places if isinstance(p, dict)]
    addresses = [a for a in addresses if isinstance(a, dict)]
    locations = []
    countries = []
    for address in addresses:
        country = address.get("addressCountry") or ""
        if isinstance(country, dict):
            country = country.get("name", "")
        if country:
            countries.append(str(country))
        locations.append(", ".join(str(address[k]) for k in ("addressLocality", "addressRegion", "addressCountry")
                                   if address.get(k) and isinstance(address[k], str)))
    country = countries[0] if len(set(countries)) == 1 else ""
    values = {"title": clean_text(posting.get("title")),
              "company": clean_text(organization.get("name")) or "Unknown",
              "description": clean_text(posting.get("description")),
              "location": " / ".join(dict.fromkeys(locations)),
              "country": "Tunisia" if norm_text(country) in {"tn", "tunisia", "tunisie"} else country,
              "city": clean_text(addresses[0].get("addressLocality")) if len(addresses) == 1 else "",
              "employment_type": contract(posting.get("employmentType")),
              "_structured": posting}
    for source, target in (("datePosted", "posted_at"), ("validThrough", "deadline")):
        date = iso_date(posting.get(source))
        if date:
            values[target] = date
    mode = work_mode(posting.get("jobLocationType", ""))
    if mode:
        values.update(work_mode=mode, remote=mode == "remote")
    salary = posting.get("baseSalary") or {}
    if isinstance(salary, dict):
        amount = salary.get("value")
        if isinstance(amount, dict):
            for key, target in (("minValue", "salary_min"), ("maxValue", "salary_max")):
                if isinstance(amount.get(key), (int, float)):
                    values[target] = amount[key]
            if isinstance(amount.get("value"), (int, float)):
                values["salary_min"] = values["salary_max"] = amount["value"]
            units = {"HOUR": "hourly", "DAY": "daily", "WEEK": "weekly", "MONTH": "monthly", "YEAR": "yearly"}
            values["salary_period"] = units.get(str(amount.get("unitText", "")).upper())
            values["salary_currency"] = salary.get("currency")
    return values


def application(soup, body, source_url):
    candidates = []
    emails = re.findall(r"\b[A-Za-z0-9.!#$%&*+/=?^_~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b", text(body))
    for node in body.select("a[href], button[onclick], form[action]"):
        raw = node.get("href") or node.get("action")
        label = norm_text(text(node))
        if raw and raw.lower().startswith("mailto:"):
            address = raw[7:].split("?", 1)[0]
            if address and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
                emails.append(address)
            continue
        if node.get("onclick"):
            # Extract only a literal navigation target; never execute JavaScript.
            match = re.fullmatch(r"\s*(?:window\.)?location(?:\.href)?\s*=\s*(['\"])(.*?)\1\s*;?\s*",
                                 node["onclick"], re.S)
            if match:
                raw = match.group(2)
        if not raw:
            continue
        relevant = any(word in label for word in ("postuler", "apply", "candidature", "envoyer votre cv", "send your resume"))
        relevant = relevant or bool(re.search(r"/(?:postuler|apply|users_candidate\.php)(?:[/?]|$)", raw, re.I))
        if not relevant:
            continue
        url = absolute(source_url, raw)
        if url:
            external = urlsplit(url).hostname not in {urlsplit(source_url).hostname,
                                                      urlsplit(source_url).hostname.removeprefix("www.")}
            candidates.append((0 if external else 1, url))
    # Buttons can sit outside the description (Keejob), so inspect main content
    # too, excluding footer/navigation/related links.
    if body is not soup and not candidates:
        main = soup.select_one("main")
        if main and main is not body:
            result = application(soup, main, source_url)
            result["_application"]["emails"] = sorted(set(emails + result["_application"]["emails"]))
            return result
    candidates.sort(key=lambda item: item[0])
    destination = candidates[0][1] if candidates else source_url
    login = bool(re.search(r"/(?:login|signin|jobseeker/account|users/)", destination, re.I))
    notices = norm_text(text(body))
    login = login or any(term in notices for term in ("connectez vous pour postuler", "connexion pour postuler"))
    return {"application_url": destination, "_application": {
        "url": destination, "kind": "explicit_external" if candidates and candidates[0][0] == 0
        else "source_native" if candidates else "detail_fallback",
        "requires_login": True if login else None, "emails": sorted(set(emails)),
        "instructions": application_instructions(body)}}


def application_instructions(body):
    paragraphs = []
    active = False
    for node in body.find_all(["p", "h2", "h3", "h4"]):
        value = text(node)
        if re.search(r"(comment postuler|pour postuler|candidature|how to apply)", value, re.I):
            active = True
        if active:
            paragraphs.append(value)
            if sum(map(len, paragraphs)) > 4000:
                break
    return "\n".join(paragraphs)[:5000]


def freshness(soup, values, fetched_at):
    # Page availability alone is not proof that the vacancy is still open.
    # Prefer explicit source closed markers, then source-provided deadline.
    for node in soup.select(".job-expired, .expired-job, .alert, [data-job-status]"):
        value = norm_text(text(node))
        if node.get("data-job-status") in {"expired", "closed"} or any(
            term in value for term in ("offre expiree", "annonce expiree", "poste pourvu", "job expired", "job closed")):
            values["is_open"] = False
            values["_freshness"] = {
                "state": "expired",
                "evidence": text(node),
                "source_deadline": values.get("deadline"),
            }
            break
    else:
        deadline = values.get("deadline")

        def parse_datetime(value, end_of_day=False):
            if value is None:
                return None

            raw = str(value).strip()

            if not raw:
                return None

            try:
                from datetime import datetime, timezone

                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                    raw += (
                        "T23:59:59+00:00"
                        if end_of_day
                        else "T00:00:00+00:00"
                    )

                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"

                result = datetime.fromisoformat(raw)

                if result.tzinfo is None:
                    result = result.replace(
                        tzinfo=timezone.utc
                    )

                return result.astimezone(
                    timezone.utc
                )

            except (TypeError, ValueError):
                return None

        deadline_dt = parse_datetime(
            deadline,
            end_of_day=True,
        )

        fetched_dt = parse_datetime(
            fetched_at
        )

        if deadline:

            if (
                deadline_dt is not None
                and fetched_dt is not None
            ):

                if deadline_dt < fetched_dt:
                    values["is_open"] = False
                    values["_freshness"] = {
                        "state": "expired",
                        "evidence":
                            "source_deadline_passed",
                        "source_deadline":
                            deadline,
                    }

                else:
                    values["_freshness"] = {
                        "state": "verified_live",
                        "evidence":
                            "source_deadline_current",
                        "source_deadline":
                            deadline,
                    }

            else:
                values["_freshness"] = {
                    "state": "unknown_current",
                    "evidence":
                        "source_deadline_unparseable",
                    "source_deadline":
                        deadline,
                }

        else:
            values["_freshness"] = {
                "state": "page_available",
                "evidence":
                    "successful_public_detail_no_deadline",
                "source_deadline": None,
            }

    values["last_verified_at"] = fetched_at


def supplement(values, fields):
    mapping = {"lieu de travail": "location", "localisation": "location", "lieu": "location",
               "niveau d etudes": "education_requirement", "niveau d etude": "education_requirement",
               "type de contrat": "employment_type", "contrat": "employment_type"}
    for source, target in mapping.items():
        if fields.get(source):
            values[target] = fields[source]
    mode = work_mode(fields.get("mode de travail") or fields.get("work mode") or "")
    if mode:
        values.update(work_mode=mode, remote=mode == "remote")
    for label in ("date de publication", "date publication", "publie le"):
        if fields.get(label):
            parsed = iso_date(fields[label])
            if parsed:
                values["posted_at"] = parsed
    for label in ("date d expiration", "date limite", "date expiration"):
        parsed = iso_date(fields.get(label))
        if parsed:
            values["deadline"] = parsed
    experience = fields.get("experience requise") or fields.get("experience")
    if experience:
        numbers = [int(n) for n in re.findall(r"\b\d+\b", experience)]
        if "ans" in experience.lower() and numbers:
            values["min_years"] = min(numbers)
            if len(numbers) == 2:
                values["max_years"] = max(numbers)
    salary = fields.get("salaire propose") or fields.get("salaire")
    if salary:
        low, high, currency, period = parse_salary_text(salary)
        values.update(salary_min=low, salary_max=high, salary_currency=currency, salary_period=period)
    values["_source_fields"] = fields


def foreign_scope(values):
    country = norm_text(values.get("country", ""))
    if country and country not in {"tn", "tunisia", "tunisie"}:
        return "explicit_foreign_country"
    # Only explicit title suffix/location evidence, never mentions of foreign
    # customers or language requirements inside a Tunisian job's description.
    location = norm_text(values.get("location", ""))
    title = values.get("title", "")
    for country_name in ("France", "Canada", "Allemagne", "Belgique", "Qatar", "Arabie Saoudite", "Suisse"):
        if re.search(r"\(" + re.escape(country_name) + r"\)\s*$", title, re.I) or location == norm_text(country_name):
            return "explicit_foreign_location"
    return None
