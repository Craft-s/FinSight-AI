import httpx
import pytest
from redis.exceptions import ConnectionError

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.services.sec.client import SecClient, validate_cik, validate_sec_url


class Limiter:
    def get(self, *_args, **_kwargs):
        return None

    def set(self, *_args, **_kwargs):
        return True


@pytest.fixture
def sec_settings(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "FinSightUnitTest unit-test@invalid.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("url", [
    "http://data.sec.gov/submissions/CIK0000320193.json",
    "https://localhost/submissions/CIK0000320193.json",
    "https://data.sec.gov.evil.test/submissions/CIK0000320193.json",
    "https://user:pass@data.sec.gov/submissions/CIK0000320193.json",
    "https://data.sec.gov:8080/submissions/CIK0000320193.json",
    "https://data.sec.gov/submissions/../secret",
    "https://data.sec.gov/submissions/CIK0000320193.json?secret=x",
    "https://data.sec.gov:not-a-port/submissions/CIK0000320193.json",
    "https://data.sec.gov:70000/submissions/CIK0000320193.json",
    "https://[invalid-ipv6/submissions/CIK0000320193.json",
    "https://@data.sec.gov/submissions/CIK0000320193.json",
    "https://data.sec.gov\t/submissions/CIK0000320193.json",
    " https://data.sec.gov/submissions/CIK0000320193.json",
    "https://data.sec.gov/submissions/CIK0000320193.json\n",
])
def test_sec_url_allowlist(url):
    with pytest.raises(AppError):
        validate_sec_url(url)


def test_cik_padding():
    assert validate_cik("320193") == "0000320193"
    for bad in ("0", "-1", "../1", "1e2", "１"):
        with pytest.raises(AppError):
            validate_cik(bad)


def test_valid_request_and_no_redirect(sec_settings):
    def handler(request):
        assert request.headers["User-Agent"].startswith("FinSightUnitTest")
        return httpx.Response(200, json={"cik": 320193})
    client = SecClient(redis=Limiter(), transport=httpx.MockTransport(handler))
    raw, fetched = client.fetch("https://data.sec.gov/submissions/CIK0000320193.json")
    assert b"320193" in raw
    assert fetched.tzinfo is not None


def test_403_not_retried(sec_settings):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(403)
    with pytest.raises(AppError) as raised:
        SecClient(redis=Limiter(), transport=httpx.MockTransport(handler)).fetch("https://data.sec.gov/submissions/CIK0000320193.json")
    assert raised.value.code == "SEC_FORBIDDEN"
    assert len(calls) == 1


def test_429_respects_retry_after(sec_settings, monkeypatch):
    delays, calls = [], []
    monkeypatch.setattr("app.services.sec.client.time.sleep", delays.append)
    def handler(_request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "2"}) if len(calls) == 1 else httpx.Response(200, json={"cik": 1})
    SecClient(redis=Limiter(), transport=httpx.MockTransport(handler)).fetch("https://data.sec.gov/submissions/CIK0000320193.json")
    assert delays == [2.0]


def test_oversize_and_redirect_rejected(sec_settings, monkeypatch):
    monkeypatch.setenv("SEC_MAX_BYTES", "10")
    get_settings.cache_clear()
    for response, code in ((httpx.Response(200, content=b"x" * 11), "SOURCE_TOO_LARGE"), (httpx.Response(302, headers={"Location": "http://127.0.0.1"}), "SEC_INVALID_RESPONSE")):
        with pytest.raises(AppError) as raised:
            SecClient(redis=Limiter(), transport=httpx.MockTransport(lambda _r: response)).fetch("https://data.sec.gov/submissions/CIK0000320193.json")
        assert raised.value.code == code


def test_limiter_failure_stops_request(sec_settings):
    class Down:
        def get(self, *_a, **_kw):
            raise ConnectionError("secret connection details")

        def set(self, *_a, **_kw):
            raise ConnectionError("secret connection details")
    with pytest.raises(AppError) as raised:
        SecClient(redis=Down())._limit()
    assert raised.value.code == "RATE_LIMITER_UNAVAILABLE"
    assert "secret" not in raised.value.message


def test_shared_cooldown_stops_followup_request(sec_settings):
    class Cooling(Limiter):
        def get(self, *_a, **_kw):
            return b"forbidden"
    with pytest.raises(AppError) as raised:
        SecClient(redis=Cooling())._limit()
    assert raised.value.code == "SEC_COOLDOWN"


def test_missing_contact_and_dev_only_sqlite(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "")
    get_settings.cache_clear()
    with pytest.raises(AppError) as raised:
        SecClient(redis=Limiter()).fetch("https://data.sec.gov/submissions/CIK0000320193.json")
    assert raised.value.code == "SEC_USER_AGENT_REQUIRED"
    with pytest.raises(ValueError):
        Settings(database_url="sqlite:///anything.db", development=False)
    get_settings.cache_clear()
