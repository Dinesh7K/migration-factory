"""The ONLY place the factory calls an LLM. Every call is written to the audit log.

Providers (LLM_PROVIDER in .env):
  offline  no LLM; callers fall back to deterministic logic (demo runs without a key)
  openai   OpenAI API (OPENAI_API_KEY, OPENAI_MODEL)
  azure    Azure OpenAI (AZURE_OPENAI_* vars)

TODO (P0, agent dev): test openai/azure paths with a real key; add retries + timeout.
"""
from __future__ import annotations

import os

from factory import config, registry


class LLMUnavailable(RuntimeError):
    """Raised when no provider is configured; callers must fall back."""


def available() -> bool:
    return config.LLM_PROVIDER in {"openai", "azure"}


def load_prompt(name: str, **values: str) -> str:
    """Read factory/prompts/<name>.md and fill {placeholders}."""
    text = (config.PROMPTS / f"{name}.md").read_text()
    return text.format(**values)


def complete(prompt: str, *, job_id: str | None, purpose: str, system: str | None = None) -> str:
    """Send one prompt, return the text answer, and log both sides."""
    if not available():
        raise LLMUnavailable("LLM_PROVIDER=offline")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]

    if config.LLM_PROVIDER == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        model = os.getenv("OPENAI_MODEL", "gpt-4o")
    else:
        from openai import AzureOpenAI
        client = AzureOpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
                             azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                             api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01"))
        model = os.environ["AZURE_OPENAI_DEPLOYMENT"]

    resp = client.chat.completions.create(model=model, messages=messages, temperature=0)
    answer = resp.choices[0].message.content or ""
    registry.log(job_id, "llm", f"llm_{purpose}", {"model": model, "prompt": prompt, "response": answer})
    return answer


def extract_code(answer: str) -> str:
    """Pull the first fenced code block out of an LLM answer (or return it as-is)."""
    if "```" not in answer:
        return answer.strip()
    block = answer.split("```", 2)[1]
    return block.split("\n", 1)[1].strip() if "\n" in block else block.strip()
