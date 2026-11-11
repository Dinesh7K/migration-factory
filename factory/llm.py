"""The ONLY place the factory calls an LLM. Every call is written to the audit log.

Providers (LLM_PROVIDER in .env):
  offline  no LLM; callers fall back to deterministic logic (demo runs without a key)
  openai   OpenAI API (OPENAI_API_KEY, OPENAI_MODEL)
  azure    Azure OpenAI (AZURE_OPENAI_* vars)
  ollama   local model served by Ollama through its OpenAI-compatible endpoint
           (OLLAMA_BASE_URL, OLLAMA_MODEL); free, no key, needs `ollama serve` running

Any provider failure (server down, timeout, missing key) is raised as LLMUnavailable so
callers fall back to deterministic logic instead of crashing the demo.
TODO (agent dev): test openai/azure paths with a real key.
"""
from __future__ import annotations

import os

from factory import config, registry


class LLMUnavailable(RuntimeError):
    """Raised when no provider is configured; callers must fall back."""


def available() -> bool:
    return config.LLM_PROVIDER in {"openai", "azure", "ollama"}


def load_prompt(name: str, **values: str) -> str:
    """Read factory/prompts/<name>.md and fill {placeholders}."""
    text = (config.PROMPTS / f"{name}.md").read_text()
    return text.format(**values)


def complete(prompt: str, *, job_id: str | None, purpose: str, system: str | None = None) -> str:
    """Send one prompt, return the text answer, and log both sides."""
    if not available():
        raise LLMUnavailable("LLM_PROVIDER=offline")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]

    try:
        client, model = _client()
        resp = client.chat.completions.create(model=model, messages=messages, temperature=0)
    except LLMUnavailable:
        raise
    except Exception as e:  # server down, timeout, bad key, model not pulled, ...
        registry.log(job_id, "llm", f"llm_{purpose}_error",
                     {"provider": config.LLM_PROVIDER, "prompt": prompt, "error": f"{type(e).__name__}: {e}"})
        raise LLMUnavailable(f"{config.LLM_PROVIDER} call failed: {type(e).__name__}: {e}") from e
    answer = resp.choices[0].message.content or ""
    registry.log(job_id, "llm", f"llm_{purpose}", {"provider": config.LLM_PROVIDER, "model": model,
                                                    "prompt": prompt, "response": answer})
    return answer


def _client():
    """Build the client for the configured provider. Returns (client, model_name)."""
    from openai import AzureOpenAI, OpenAI
    opts = {"timeout": config.LLM_TIMEOUT, "max_retries": 1}
    try:
        if config.LLM_PROVIDER == "openai":
            return OpenAI(api_key=os.environ["OPENAI_API_KEY"], **opts), os.getenv("OPENAI_MODEL", "gpt-4o")
        if config.LLM_PROVIDER == "ollama":
            # Ollama ignores the key, but the client requires a non-empty one.
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            return OpenAI(api_key="ollama", base_url=base_url, **opts), os.getenv("OLLAMA_MODEL", "qwen2.5-coder")
        client = AzureOpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
                             azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                             api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01"), **opts)
        return client, os.environ["AZURE_OPENAI_DEPLOYMENT"]
    except KeyError as e:
        raise LLMUnavailable(f"missing env var {e} for LLM_PROVIDER={config.LLM_PROVIDER}") from e


def extract_code(answer: str) -> str:
    """Pull the first fenced code block out of an LLM answer (or return it as-is)."""
    if "```" not in answer:
        return answer.strip()
    block = answer.split("```", 2)[1]
    return block.split("\n", 1)[1].strip() if "\n" in block else block.strip()
