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
