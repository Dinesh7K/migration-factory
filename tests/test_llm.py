"""LLM provider wiring (no network calls)."""
import pytest

from factory import config, llm


def test_gemini_uses_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    client, model = llm._client()
    assert llm.available() and model == "gemini-test"
    assert str(client.base_url) == llm.GEMINI_BASE_URL


def test_gemini_without_key_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(llm.LLMUnavailable, match="GEMINI_API_KEY"):
        llm._client()


def _rate_limit_error():
    from openai import RateLimitError
    e = RateLimitError.__new__(RateLimitError)  # no HTTP response needed: no Retry-After header
    Exception.__init__(e, "429 Too Many Requests")
    e.response = None
    return e


class _FakeClient:
    """Answers like the OpenAI client; raises a 429 for the first `fails` calls."""
    def __init__(self, fails):
        self.fails, self.calls = fails, 0
        self.chat = self
        self.completions = self

    def create(self, **_):
        self.calls += 1
        if self.calls <= self.fails:
            raise _rate_limit_error()
        from types import SimpleNamespace as NS
        return NS(choices=[NS(message=NS(content="OK"))])


def _fake(monkeypatch, tmp_path, fails):
    client = _FakeClient(fails)
    waits = []
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "factory.db")
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "_client", lambda: (client, "fake-model"))
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    return client, waits


def test_rate_limit_is_waited_out_not_a_failure(monkeypatch, tmp_path):
    client, waits = _fake(monkeypatch, tmp_path, fails=2)
    assert llm.complete("hi", job_id="J10", purpose="test") == "OK"
    assert client.calls == 3 and llm.RATE_LIMIT_WAITS[0] in waits and llm.RATE_LIMIT_WAITS[1] in waits


def test_rate_limit_that_never_clears_becomes_unavailable(monkeypatch, tmp_path):
    client, _ = _fake(monkeypatch, tmp_path, fails=99)
    with pytest.raises(llm.LLMUnavailable, match="RateLimitError"):
        llm.complete("hi", job_id="J10", purpose="test")
    assert client.calls == len(llm.RATE_LIMIT_WAITS) + 1
