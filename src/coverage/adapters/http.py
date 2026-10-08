"""Bounded, injectable public HTTP with robots policy; never executes challenges."""
from __future__ import annotations

import codecs
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx

from ..artifacts import canonical_json, utc_now

USER_AGENT = "TTEA-Coverage/1.0 (+public job inventory; no authenticated access)"
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
TRANSIENT = frozenset({408, 425, 500, 502, 503, 504})
SENSITIVE_PATH = re.compile(r"/(?:login|signin|sign-in|auth|captcha|cdn-cgi)/", re.I)


@dataclass(frozen=True)
class HttpPolicy:
    user_agent: str = USER_AGENT
    connect_timeout: float = 10
    read_timeout: float = 20
    min_interval: float = 5
    max_attempts: int = 3
    retry_rate_limits: bool = True
    max_redirects: int = 3
    max_bytes: int = 2 * 1024 * 1024
    max_retry_delay: float = 60
    request_deadline: float = 90

    def __post_init__(self):
        if not self.user_agent or min(self.connect_timeout, self.read_timeout, self.min_interval) <= 0:
            raise ValueError("Positive timeouts and request interval required")
        if not 1 <= self.max_attempts <= 5 or not 0 <= self.max_redirects <= 5 or self.max_bytes < 1:
            raise ValueError("Invalid HTTP bounds")


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    status: int
    text: str
    sha256: str
    size_bytes: int
    content_type: str
    charset: str
    requested_at: str
    completed_at: str
    attempts: int
    etag: str | None = None
    last_modified: str | None = None
    redirects: tuple[str, ...] = ()
    robots: dict = field(default_factory=dict)

    def metadata(self):
        return {key: value for key, value in self.__dict__.items() if key != "text"}

    def evidence(self):
        # Body is acquisition evidence, never a log message or SQLite response blob.
        return {"fetch": self.metadata(), "body": self.text}


class FetchProblem(RuntimeError):
    def __init__(self, code: str, evidence: dict, terminal_state: str | None = None):
        super().__init__(code)
        self.code = code
        self.evidence = evidence
        self.terminal_state = terminal_state


class JsonAudit:
    """Persistent event codes and safe HTTP metadata; no body/cookies/auth headers."""
    def __init__(self, path: Path):
        self.path = path

    def __call__(self, event: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        allowed = {"at", "event", "host", "status", "attempt", "response_sha256", "bytes", "code"}
        row = {key: value for key, value in event.items() if key in allowed}
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(row) + "\n")
            stream.flush()


class RobotsRules:
    """Longest-match Allow/Disallow including * and $; merge equal UA groups."""
    def __init__(self, text: str, agent: str):
        groups = []
        agents, rules = [], []
        delay = None
        for raw in text.splitlines() + ["User-agent: __end__"]:
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if rules or delay is not None:
                    groups.append((agents, rules, delay))
                    agents, rules, delay = [], [], None
                agents.append(value.lower())
            elif key in {"allow", "disallow"} and agents and value:
                rules.append((key == "allow", value))
            elif key == "request-rate" and agents:
                match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", value)
                if match and int(match.group(1)) > 0:
                    delay = max(delay or 0, int(match.group(2)) / int(match.group(1)))
            elif key == "crawl-delay" and agents:
                try:
                    delay = max(delay or 0, float(value))
                except ValueError:
                    pass
        best = -1
        self.rules = []
        self.delay = 0.0
        for names, entries, interval in groups:
            specificity = max((len(name) if name != "*" else 0
                               for name in names if name == "*" or name in agent.lower()), default=-1)
            if specificity < 0 or specificity < best:
                continue
            if specificity > best:
                self.rules, self.delay, best = [], 0.0, specificity
            self.rules.extend(entries)
            self.delay = max(self.delay, interval or 0.0)

    def allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        target = unquote(parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        matches = []
        for allow, pattern in self.rules:
            end = pattern.endswith("$")
            body = pattern[:-1] if end else pattern
            regex = "^" + ".*".join(re.escape(unquote(part)) for part in body.split("*"))
            if re.search(regex + ("$" if end else ""), target):
                matches.append((len(body.replace("*", "")), allow))
        return max(matches, default=(0, True))[1]


def _url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise FetchProblem("unsafe_public_url", {}, "blocked")
    if parsed.port not in {None, 443}:
        raise FetchProblem("unsupported_public_port", {}, "blocked")
    if SENSITIVE_PATH.search(parsed.path):
        raise FetchProblem("authentication_or_challenge_route", {"url": url}, "blocked")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def retry_after(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class PublicHttp:
    def __init__(self, allowed_hosts: set[str], *, policy: HttpPolicy = HttpPolicy(),
                 transport=None, client=None, sleep: Callable = time.sleep,
                 monotonic: Callable = time.monotonic, audit: Callable | None = None):
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self.policy, self.sleep, self.monotonic = policy, sleep, monotonic
        self.audit = audit or JsonAudit(Path(__file__).resolve().parents[3] / "logs" / "coverage_http.log")
        self.client = client or httpx.Client(
            transport=transport, follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(policy.read_timeout, connect=policy.connect_timeout),
            headers={"User-Agent": policy.user_agent, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"})
        self._owns_client = client is None
        self._last_request = {}
        self._robots = {}
        self._lock = threading.RLock()
        self.observations = []  # Only robots summaries; never a whole crawl.

    def close(self):
        if self._owns_client:
            self.client.close()

    def _admit(self, url):
        url = _url(url)
        if urlsplit(url).hostname not in self.allowed_hosts:
            raise FetchProblem("redirect_outside_source", {"url": url}, "blocked")
        return url

    def _pause(self, host, interval):
        remaining = self._last_request.get(host, -float("inf")) + interval - self.monotonic()
        if remaining > 0:
            self.sleep(remaining)
        self._last_request[host] = self.monotonic()

    def _request(self, url, expected_types, *, interval=None):
        requested = utc_now()
        start = self.monotonic()
        last_evidence = {"requested_url": url}
        for attempt in range(1, self.policy.max_attempts + 1):
            host = urlsplit(url).hostname
            self._pause(host, max(self.policy.min_interval, interval or 0))
            self.audit({"at": utc_now(), "event": "http_attempt", "host": host, "attempt": attempt})
            delay = None
            try:
                # Public acquisition never supplies credentials or retains login cookies.
                self.client.cookies.clear()
                with self.client.stream("GET", url, follow_redirects=False,
                                        headers={"User-Agent": self.policy.user_agent}) as response:
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        if self.monotonic() - start > self.policy.request_deadline:
                            raise FetchProblem("response_deadline", last_evidence)
                        if len(raw) + len(chunk) > self.policy.max_bytes:
                            raise FetchProblem("response_too_large", {"status": response.status_code, "url": url})
                        raw.extend(chunk)
                    digest = hashlib.sha256(raw).hexdigest()
                    evidence = {"requested_url": url, "status": response.status_code,
                                "requested_at": requested, "completed_at": utc_now(),
                                "response_sha256": digest, "size_bytes": len(raw), "attempts": attempt}
                    last_evidence = evidence
                    self.audit({"at": utc_now(), "event": "http_response", "host": host,
                                "status": response.status_code, "attempt": attempt,
                                "response_sha256": digest, "bytes": len(raw)})
                    charset_match = re.search(r"charset\s*=\s*[\"']?([\w.-]+)",
                                              response.headers.get("content-type", ""), re.I)
                    charset = charset_match.group(1) if charset_match else "utf-8"
                    if not charset_match:
                        meta = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w.-]+)", raw[:8192], re.I)
                        if meta:
                            charset = meta.group(1).decode("ascii")
                    try:
                        codecs.lookup(charset)
                        body = raw.decode(charset, errors="strict")
                    except (LookupError, UnicodeDecodeError):
                        raise FetchProblem("invalid_response_charset", evidence)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        return response.status_code, response.headers.get("location"), evidence
                    if response.status_code in {401, 403}:
                        raise FetchProblem("access_denied", evidence, "blocked")
                    lower = body[:200000].lower()
                    challenge = ("<title>just a moment" in lower or "cf-chl-" in lower or
                                 "challenge-platform" in lower or
                                 "verify you are human" in lower or "checking your browser" in lower or
                                 ("captcha" in lower and any(p in lower for p in
                                  ("verify you are human", "complete the captcha", "vérifiez que vous"))))
                    if challenge:
                        raise FetchProblem("human_verification_required", evidence, "manual_only")
                    if response.status_code == 429:
                        delay = retry_after(response.headers.get("retry-after"), datetime.now(timezone.utc))
                        evidence["retry_after_seconds"] = delay
                        if not self.policy.retry_rate_limits or delay is None or delay > self.policy.max_retry_delay or attempt == self.policy.max_attempts:
                            raise FetchProblem("rate_limited", evidence, "blocked")
                    elif response.status_code in TRANSIENT:
                        delay = retry_after(response.headers.get("retry-after"), datetime.now(timezone.utc))
                        delay = delay if delay is not None else 2 ** attempt
                        if delay > self.policy.max_retry_delay:
                            raise FetchProblem("retry_deferred", evidence)
                    elif response.status_code not in {200, 404, 410}:
                        raise FetchProblem("unexpected_http_status", evidence)
                    else:
                        if response.status_code == 200 and content_type not in expected_types:
                            raise FetchProblem("unexpected_content_type", {**evidence, "content_type": content_type})
                        return FetchResult(url, url, response.status_code, body, digest, len(raw),
                                           content_type, charset, requested, utc_now(), attempt,
                                           response.headers.get("etag"), response.headers.get("last-modified"))
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                code = "http_timeout" if isinstance(exc, httpx.TimeoutException) else "http_transport_error"
                last_evidence = {"requested_url": url, "code": code, "attempts": attempt, "at": utc_now()}
                self.audit({"at": utc_now(), "event": "http_failure", "host": host, "code": code, "attempt": attempt})
                delay = 2 ** attempt
            if attempt == self.policy.max_attempts or self.monotonic() - start + (delay or 0) > self.policy.request_deadline:
                raise FetchProblem("transient_retries_exhausted", last_evidence)
            self.sleep(delay or 0)
        raise FetchProblem("request_bound_reached", last_evidence)

    def robots(self, url):
        url = self._admit(url)
        parts = urlsplit(url)
        origin = parts.scheme + "://" + parts.netloc
        cached = self._robots.get(origin)
        if cached and self.monotonic() - cached[0] < 3600:
            return cached[1], cached[2]
        robots_url = origin + "/robots.txt"
        result = self._request(robots_url, {"text/plain", "text/x-robots"})
        if not isinstance(result, FetchResult):
            # No alternate-host/route probing when policy discovery redirects.
            raise FetchProblem("robots_redirect_requires_review", result[2], "blocked")
        if result.status in {404, 410}:
            rules = RobotsRules("", self.policy.user_agent)
            summary = {**result.metadata(), "policy": "robots_not_published"}
        else:
            rules = RobotsRules(result.text, self.policy.user_agent)
            summary = {**result.metadata(), "policy": "robots_respected", "rules": result.text}
        if rules.delay > self.policy.max_retry_delay:
            raise FetchProblem("robots_delay_requires_manual_schedule", summary, "blocked")
        self._robots[origin] = (self.monotonic(), rules, summary)
        self.observations.append(summary)
        return rules, summary

    def fetch(self, url: str) -> FetchResult:
        from dataclasses import replace
        with self._lock:
            original = self._admit(url)
            current = original
            redirects = []
            for _ in range(self.policy.max_redirects + 1):
                current = self._admit(current)
                rules, summary = self.robots(current)
                if not rules.allowed(current):
                    raise FetchProblem("robots_disallow", {"url": current, "robots": summary}, "blocked")
                result = self._request(current, HTML_TYPES, interval=rules.delay)
                if isinstance(result, FetchResult):
                    return replace(result, requested_url=original, final_url=current,
                                   redirects=tuple(redirects), robots=summary)
                _, destination, evidence = result
                if not destination:
                    raise FetchProblem("redirect_without_destination", evidence)
                next_url = self._admit(urljoin(current, destination))
                if next_url in redirects or next_url == current:
                    raise FetchProblem("redirect_cycle", evidence)
                redirects.append(current)
                current = next_url
            raise FetchProblem("redirect_limit", {"requested_url": original, "redirects": redirects})
