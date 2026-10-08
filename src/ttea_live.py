"""Bounded, read-only access to the separately configured T/TEA live channel."""
import json
import os
from pathlib import Path
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

STAGES = ("profile", "source_discovery", "detail_extraction", "normalization", "deterministic_filter", "cloud_scoring", "publication", "completion")
COUNTERS = ("cards_discovered", "detail_pages_fetched", "jobs_extracted", "unique_normalized", "deterministic_relevant", "cloud_candidates", "cloud_scored", "published_jobs")
STATUSES = {"pending", "running", "complete", "skipped", "failed"}


def root_path():
    value = os.environ.get("TTEA_JOB_LIVE_ROOT")
    if value:
        return Path(value).resolve()
    repo = os.environ.get("TTEA_REPO")
    return (Path(repo) / "data/job_hunter_live").resolve() if repo else None


def text(value, limit=1000):
    return value[:limit] if isinstance(value, str) else ""


def url(value):
    try:
        parsed = urlsplit(text(value, 2000))
        if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        pass
    return ""


def number(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def _path(root, *parts):
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Outside live root")
    return path


def _json(path, limit=262144):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Oversized artifact")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid artifact")
    return value


def run_path(run_id=None):
    root = root_path()
    if root is None:
        return None
    if run_id is None:
        run_id = _json(_path(root, "latest.json"), 4096).get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"run_[A-Za-z0-9_-]{1,180}", run_id):
        raise ValueError("Invalid run ID")
    return _path(root, "runs", run_id)


def item(value):
    value = value if isinstance(value, dict) else {}
    result = {key: text(value.get(key), 500) for key in ("job_id", "title", "company", "location", "experience", "action", "result")}
    result["source_url"] = url(value.get("source_url"))
    result["index"] = number(value.get("index"))
    result["total"] = number(value.get("total"))
    reasons = value.get("reasons", [])
    result["reasons"] = [text(reason, 500) for reason in reasons[:10]] if isinstance(reasons, list) else []
    score = value.get("score")
    result["score"] = score if type(score) in (int, float) and 0 <= score <= 100 else None
    return result


def state():
    idle = {"status": "idle", "configured": False, "message": "T/TEA LIVE SOURCE NOT CONFIGURED"}
    try:
        if root_path() is None:
            return idle
        idle.update(configured=True, message="WAITING FOR T/TEA LIVE OBSERVATION")
        directory = run_path()
        value = _json(_path(root_path(), "runs", directory.name, "live_state.json"))
        if value.get("run_id") != directory.name:
            return idle
        result = {key: text(value.get(key)) for key in ("run_id", "started_at", "updated_at", "finished_at", "source_name", "current_action", "hunt_status")}
        result["status"] = value.get("status") if value.get("status") in ("running", "completed", "partial", "failed", "interrupted") else "idle"
        result.update(configured=True, source_url=url(value.get("source_url")), current_item=item(value.get("current_item")))
        result["current_stage"] = value.get("current_stage") if value.get("current_stage") in STAGES else ""
        stages = value.get("stages") or {}
        result["stages"] = {}
        for key in STAGES:
            row = stages.get(key, {})
            result["stages"][key] = dict(status=row.get("status") if row.get("status") in STATUSES else "pending",
                                         current=number(row.get("current")), total=number(row.get("total")), message=text(row.get("message")))
        counters = value.get("counters") or {}
        result["counters"] = {key: number(counters.get(key)) for key in COUNTERS}
        summary = value.get("publication") or {}
        result["publication"] = {key: number(summary.get(key)) for key in ("new_jobs", "existing_jobs", "jobs_read")}
        observer = value.get("observer") or {}
        result["observer"] = {key: text(observer.get(key)) for key in ("page_type", "revision", "page_title")}
        result["observer"].update(source_url=url(observer.get("source_url")), http_status=number(observer.get("http_status")), cached=observer.get("cached") is True)
        return result
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return idle


def events(run_id=None, limit=100):
    try:
        directory = run_path(run_id)
        if directory is None:
            return {"items": []}
        path = _path(root_path(), "runs", directory.name, "events.jsonl")
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 131072))
            raw = stream.read(131072)
        lines = raw.split(b"\n")
        if size > 131072:
            lines = lines[1:]
        items = []
        for line in lines[:-1][-min(limit, 100):]:
            try:
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict) or value.get("run_id") != directory.name:
                    continue
                items.append({key: text(value.get(key)) for key in ("timestamp", "run_id", "event_type", "stage", "message")})
            except (ValueError, TypeError, RecursionError):
                continue
        return {"run_id": directory.name, "items": items}
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return {"items": []}


class PassivePage(HTMLParser):
    allowed = set("h1 h2 h3 h4 p div section article main header footer ul ol li table thead tbody tr td th strong em b i blockquote pre code br hr span".split())
    blocked = set("script style form iframe object embed svg math template noscript video audio textarea select button".split())
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], []
    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.hidden.append(tag)
        elif not self.hidden and tag in self.allowed:
            self.parts.append("<" + tag + ">")
    def handle_startendtag(self, tag, attrs):
        if not self.hidden and tag in self.allowed:
            self.parts.append("<" + tag + ">")
    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in self.allowed and tag not in ("br", "hr"):
            self.parts.append("</" + tag + ">")
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(escape(data))


def observer(run_id=None, page="detail"):
    try:
        if page not in ("listing", "detail"):
            raise ValueError("Invalid page")
        directory = run_path(run_id)
        if directory is None:
            return None
        path = _path(root_path(), "runs", directory.name, "observer", page + ".html")
        with path.open("rb") as stream:
            data = stream.read(600001)
        if len(data) > 600000:
            return None
        parser = PassivePage()
        parser.feed(data.decode("utf-8"))
        return ('<!doctype html><meta charset="utf-8"><style>body{background:#f4edd9;color:#191915;'
                'font:14px Georgia,serif;padding:20px;line-height:1.55;overflow-wrap:anywhere}'
                'h1,h2,h3{font-family:monospace}td,th{border:1px solid #aaa;padding:5px}</style>' + "".join(parser.parts))
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return None
