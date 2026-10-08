"""TN-1 transport regressions. Written for independent certification; NOT RUN."""
import hashlib
import socket

import httpx
import pytest

from src.coverage.adapters.http import FetchProblem, HttpPolicy, JsonAudit, PublicHttp, RobotsRules


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Only MockTransport is permitted")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def transport(handler, tmp_path, **bounds):
    requests, waits = [], []
    clock = [0.0]
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    def wrapped(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
        return handler(request)
    client = PublicHttp({"source.example"}, policy=HttpPolicy(**bounds),
                        transport=httpx.MockTransport(wrapped), sleep=sleep,
                        monotonic=lambda: clock[0], audit=JsonAudit(tmp_path / "http.jsonl"))
    return client, requests, waits


@pytest.mark.parametrize("status,code", [(401, "access_denied"), (403, "access_denied"), (429, "rate_limited")])
def test_access_stop_is_never_exhaustion(tmp_path, status, code):
    client, calls, _ = transport(lambda r: httpx.Response(status, text="Denied"), tmp_path)
    with client.client, pytest.raises(FetchProblem) as error:
        client.fetch("https://source.example/jobs/")
    assert error.value.code == code
    assert error.value.terminal_state == "blocked"
    assert len(calls) == 2


def test_200_challenge_stops(tmp_path):
    client, _, _ = transport(lambda r: httpx.Response(200,
        text="<title>Just a moment...</title>", headers={"content-type": "text/html"}), tmp_path)
    with pytest.raises(FetchProblem) as error:
        client.fetch("https://source.example/jobs/")
    assert error.value.terminal_state == "manual_only"
    client.close()


def test_transient_retry_and_spacing_metadata(tmp_path):
    attempts = []
    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503, headers={"Retry-After": "7"}, text="Unavailable")
        return httpx.Response(200, content="Ã©tÃ©".encode("iso-8859-1"),
            headers={"content-type": "text/html; charset=iso-8859-1", "etag": "fixture-v1"})
    client, calls, waits = transport(handler, tmp_path)
    result = client.fetch("https://source.example/jobs/")
    assert result.text == "Ã©tÃ©" and result.attempts == 2 and result.etag == "fixture-v1"
    assert result.sha256 == hashlib.sha256("Ã©tÃ©".encode("iso-8859-1")).hexdigest()
    assert len(calls) == 3 and 7 in waits and 5 in waits
    assert "body" not in (tmp_path / "http.jsonl").read_text()
    client.close()


def test_timeout_bounded_and_no_terminal_exhaustion(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("fixture timeout", request=request)
    client, calls, _ = transport(timeout, tmp_path, max_attempts=2)
    with pytest.raises(FetchProblem) as error:
        client.fetch("https://source.example/jobs/")
    assert len(calls) == 3
    assert error.value.code == "transient_retries_exhausted"
    assert error.value.terminal_state is None
    client.close()


def test_retry_after_429_and_keejob_immediate_stop(tmp_path):
    count = [0]
    def response(request):
        count[0] += 1
        return httpx.Response(429 if count[0] == 1 else 200, text="<html>ok</html>",
            headers={"retry-after": "8", "content-type": "text/html"})
    client, _, waits = transport(response, tmp_path)
    assert client.fetch("https://source.example/jobs/").status == 200
    assert 8 in waits
    client.close()
    count[0] = 0
    client, calls, _ = transport(response, tmp_path, retry_rate_limits=False)
    with pytest.raises(FetchProblem, match="rate_limited"):
        client.fetch("https://source.example/jobs/")
    assert len(calls) == 2
    client.close()


@pytest.mark.parametrize("headers,body,code", [
    ({"content-type": "application/json"}, b"{}", "unexpected_content_type"),
    ({"content-type": "text/html; charset=utf-8"}, b"\xff", "invalid_response_charset"),
    ({"content-type": "text/html"}, b"x" * 300, "response_too_large"),
])
def test_payload_safety(tmp_path, headers, body, code):
    client, _, _ = transport(lambda r: httpx.Response(200, content=body, headers=headers),
                             tmp_path, max_bytes=200)
    with pytest.raises(FetchProblem, match=code):
        client.fetch("https://source.example/jobs/")
    client.close()


def test_redirect_host_and_cycle_guards(tmp_path):
    client, calls, _ = transport(lambda r: httpx.Response(302,
        headers={"location": "https://outside.example/job/"}), tmp_path)
    with pytest.raises(FetchProblem, match="redirect_outside_source"):
        client.fetch("https://source.example/jobs/")
    assert len(calls) == 2  # Target was never requested.
    client.close()
    client, calls, _ = transport(lambda r: httpx.Response(302,
        headers={"location": "/jobs/"}), tmp_path)
    with pytest.raises(FetchProblem, match="redirect_cycle"):
        client.fetch("https://source.example/jobs/")
    client.close()


def test_robots_wildcards_specificity_and_no_forbidden_fetch(tmp_path):
    rules = RobotsRules("User-agent: *\nDisallow: /*/postuler/\nDisallow: /private/\nAllow: /private/public$\n", "Example/1")
    assert not rules.allowed("https://source.example/123/postuler/")
    assert rules.allowed("https://source.example/private/public")
    assert not rules.allowed("https://source.example/private/public/more")
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /jobs/",
                              headers={"content-type": "text/plain"})
    client = PublicHttp({"source.example"}, transport=httpx.MockTransport(handler),
                        audit=JsonAudit(tmp_path / "robots.jsonl"), sleep=lambda _: None)
    with pytest.raises(FetchProblem, match="robots_disallow"):
        client.fetch("https://source.example/jobs/")
    assert len(calls) == 1
    client.close()
