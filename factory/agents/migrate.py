"""M7 — the agent loop (the conductor).

    translate → break → run legacy + run new → reconcile → (fail) fix → repeat ≤ MAX_ATTEMPTS

The LLM is allowed to be wrong: nothing reaches `ready_for_review` unless reconciliation passes.
After MAX_ATTEMPTS failures → `needs_human`. Humans approve via approve_job() (or the app).

Fixer:
  llm_fix      LLM reads code + diffs, returns new code + plain-English reason (prompts/fix.md)
  offline_fix  deterministic: tries known patterns from the pattern library (no key needed)
fix() uses llm_fix when a provider is configured and falls back to offline_fix if the LLM is
unreachable or returns no usable code, so the demo never depends on the model being up.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from factory import config, llm, registry
from factory.breaker.breaker import break_data
from factory.patterns import rules
from factory.reconcile.reconcile import ReconResult, reconcile
from factory.runners.legacy import run_legacy
from factory.runners.spark import run_new
from factory.translate.translate import translate


def offline_fix(job: dict, code: str, result: ReconResult) -> tuple[str | None, str, str | None]:
    """Try each known pattern; return (new_code, reason, pattern_name) or (None, reason, None)."""
    for name, (_, description) in rules.PATTERNS.items():
        new_code, n = rules.apply_pattern(name, code)
        if n and new_code != code:
            d = result.field_diffs[0] if result.field_diffs else None
            where = (f" For {d['key']} {d['column']}: legacy={d['legacy']}, new={d['new']}." if d else "")
            return new_code, f"Mismatch explained by known gap '{name}': {description}.{where}", name
    return None, "No known pattern explains the mismatch; needs a human.", None


def _field(answer: str, label: str) -> str:
    """Return the text after `LABEL:` on its line in an LLM answer, or ''."""
    m = re.search(rf"^\W*{label}\W*:\s*(.+)$", answer, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip(" *`") if m else ""


def _candidates(code: str) -> dict[str, str]:
    """Known patterns that actually change this SQL → {pattern_name: rewritten_sql}."""
    out = {}
    for name in rules.PATTERNS:
        try:
            new_code, n = rules.apply_pattern(name, code)
        except Exception:  # SQL the library cannot parse: no candidates from this pattern
            continue
        if n and new_code != code:
            out[name] = new_code
    return out


def _compact(result: ReconResult, n: int = 5) -> str:
    """Reconciliation result as short JSON: totals plus the first few diffs (keeps the prompt small)."""
    d = result.to_dict()
    for k in ("field_diffs", "missing_in_new", "extra_in_new"):
        d[f"{k}_count"], d[k] = len(d[k]), d[k][:n]
    return json.dumps(d, default=str)


def llm_fix(job: dict, code: str, result: ReconResult) -> tuple[str | None, str, str | None]:
    """Ask the LLM to diagnose the mismatch; return (new_code, reason, pattern_name).

    The prompt lists the library patterns that apply to this SQL, each with its rewrite. If the LLM
    picks one, the rewrite comes from the library, not from the LLM's own SQL: that is the exact
    transformation saved as a rule on approval, so the rule reused in later waves is the one
    reconciliation proved here. Otherwise the LLM's SQL is used (reconciliation decides if it is right)."""
    source = (config.ROOT / job["source_path"]).read_text()
    candidates = _candidates(code)
    known = "\n".join(f"- {name}: {rules.PATTERNS[name][1]}\n  Rewritten SQL: {sql}"
                      for name, sql in candidates.items())
    prompt = llm.load_prompt("fix", lang=job["lang"], source=source, code=code, diffs=_compact(result),
                             patterns=known or "(none of the known patterns applies to this SQL)")
    answer = llm.complete(prompt, job_id=job["job_id"], purpose="fix")
    reason = _field(answer, "REASON") or answer.split("```")[0].strip()[:300] or "LLM fix"
    pattern = _field(answer, "PATTERN")
    if pattern in candidates:
        return candidates[pattern], reason, pattern
    new_code = llm.extract_code(answer) if "```" in answer else None
    return new_code, reason, None


def fix(job: dict, code: str, result: ReconResult) -> tuple[str | None, str, str | None]:
    """Propose corrected code: LLM first when configured, deterministic patterns as fallback."""
    mode, out = "offline", None
    if llm.available():
        try:
            out = llm_fix(job, code, result)
            if out[0] and out[0].strip() != code.strip():
                mode = "llm"
            else:
                out = None  # no code, or the same code again: not a fix
        except llm.LLMUnavailable as e:
            print(f"    fixer: LLM unavailable ({e}); using offline patterns")
    if out is None:
        out = offline_fix(job, code, result)
        mode = "offline_fallback" if llm.available() else "offline"
    registry.log(job["job_id"], "fixer", "fix", {"mode": mode, "reason": out[1], "pattern": out[2],
                                                  "code": out[0]})
    return out


def migrate_job(job: dict, tables: dict[str, pd.DataFrame]) -> dict:
    jid = job["job_id"]
    code, confidence, method = translate(job)
    test_tables, added = break_data(job, tables)
    reason, pattern = "", None

    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        try:
            old = run_legacy(job, test_tables)
            new = run_new(code, test_tables)
            result = reconcile(old, new, job["keys"])
        except Exception as e:  # a crash is a failed attempt, not a pass
            result = ReconResult(passed=False, row_count=(0, 0), schema_issues=[f"{type(e).__name__}: {e}"])
            new = None
        registry.log(jid, "reconciler", "reconcile", {"attempt": attempt, "summary": result.summary(),
                                                      "result": result.to_dict()})
        print(f"    attempt {attempt}: {result.summary()}")
        if result.passed:
            if pattern is None:
                confidence = min(0.99, confidence + 0.02)
            (config.LAKEHOUSE / "gold").mkdir(parents=True, exist_ok=True)
            new.to_parquet(config.LAKEHOUSE / "gold" / f"{jid}.parquet", index=False)
            registry.update_job(jid, status="ready_for_review", target_code=code, confidence=confidence,
                                method=method, attempts=attempt, reason=reason or "Passed reconciliation.",
                                fix_pattern=pattern)
            return registry.get_job(jid)
        if attempt == config.MAX_ATTEMPTS:
            break
        new_code, reason, p = fix(job, code, result)
        print(f"    fixer: {reason}")
        if new_code is None:
            break
        code, pattern, confidence = new_code, p or pattern, confidence - 0.1

    registry.update_job(jid, status="needs_human", target_code=code, confidence=max(confidence, 0.1),
                        method=method, attempts=attempt, reason=reason or result.summary(), fix_pattern=pattern)
    return registry.get_job(jid)


def approve_job(job_id: str, reviewer: str = "reviewer") -> dict:
    """Human sign-off. Saves the fix as a rule so later waves reuse it."""
    job = registry.get_job(job_id)
    if job["status"] != "ready_for_review":
        raise ValueError(f"{job_id} is {job['status']}, not ready_for_review")
    registry.update_job(job_id, status="approved")
    registry.log(job_id, reviewer, "approved", {"confidence": job["confidence"]})
    if job.get("fix_pattern"):
        rules.save_rule(job["fix_pattern"], job_id, reviewer)
    return registry.get_job(job_id)


def migrate_wave(wave: int, tables: dict[str, pd.DataFrame]) -> list[dict]:
    done = []
    for job in registry.list_jobs(status="pending"):
        if job["wave"] == wave:
            print(f"  {job['job_id']} ({job['risk']}) — {job['feeds_report'] or 'no report'}")
            done.append(migrate_job(job, tables))
    return done
