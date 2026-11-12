"""MigrateGuard command line.

    python run.py data            build the insurance database
    python run.py triage          register jobs, lineage, retire proposals
    python run.py migrate [WAVE]  run the agent loop on pending jobs (all waves if omitted)
    python run.py approve JOB     human sign-off (saves the fix as a rule)
    python run.py retire JOB      approve a retire proposal (archives, never deletes)
    python run.py restore JOB     bring an archived job back
    python run.py status          show all jobs
    python run.py demo            full scripted demo (resets state)
"""
import sys

from factory import config, registry
from factory.agents.migrate import approve_job, migrate_wave
from factory.archive.archive import restore_job
from factory.data_gen.build_db import build_db, load_tables
from factory.runners.spark import engine_label
from factory.triage.triage import approve_retire, triage


def status() -> None:
    print(f"\n  {'job':4} {'wave':4} {'risk':11} {'status':17} {'conf':5} reason")
    for j in registry.list_jobs():
        conf = f"{j['confidence']:.2f}" if j["confidence"] else "  -  "
        print(f"  {j['job_id']:4} {j['wave']:<4} {j['risk']:11} {j['status']:17} {conf:5} {(j['reason'] or '')[:70]}")
    rules = registry.list_rules()
    if rules:
        print("\n  rules:", ", ".join(f"{r['rule_id']} {r['name']}" for r in rules))


def fixer_calls(wave: int) -> int:
    ids = {j["job_id"] for j in registry.list_jobs() if j["wave"] == wave}
    return sum(1 for a in registry.audit() if a["event"] == "fix" and a["job_id"] in ids)


def demo() -> None:
    registry.reset()
    print(f"\n[0] ENGINES — legacy: {config.LEGACY_MODE} · new: {engine_label()}")
    print("\n[1] DATA — same tables loaded into the legacy and new engines")
    tables = build_db()
    print("\n[2] TRIAGE — what NOT to migrate (proposals only, human decides)")
    for r in triage(tables):
        flag = f"→ {r['status']}: {r['reason']}" if r["status"] != "pending" else f"→ migrate in wave {r['wave']}"
        print(f"  {r['job_id']} complexity={r['complexity']:<3} {flag}")
        gaps = r["lineage"]["known_gaps"]
        print(f"       purpose: {r['triage_ai']['summary']}" + (f"  [exposed to: {', '.join(gaps)}]" if gaps else ""))
        if r["lineage"]["source"] not in ("parser", "none"):
            print(f"       lineage: {r['lineage']['source']} · reads {', '.join(r['lineage']['reads']) or '?'}"
                  + (f" · skipped {', '.join(r['lineage']['skipped'])}" if r["lineage"]["skipped"] else "")
                  + (f" · unverified {', '.join(r['lineage']['unverified'])}" if r["lineage"]["unverified"] else ""))
        if r["triage_ai"]["escalate"]:
            print(f"       AI second opinion: looks regulatory — {r['triage_ai']['risk_reason']}")
    for j in registry.list_jobs(status="proposed_retire"):
        approve_retire(j["job_id"], "reviewer")
        print(f"  reviewer approved retirement of {j['job_id']} → archived (restorable)")

    print("\n[3] WAVE 1 — translate → break → run both → reconcile → fix (≤3)")
    for j in migrate_wave(1, tables):
        if j["status"] == "ready_for_review":
            approve_job(j["job_id"], "reviewer")
            print(f"  reviewer approved {j['job_id']}" + (f" → rule saved ({j['fix_pattern']})" if j["fix_pattern"] else ""))

    print("\n[4] WAVE 2 — learned rules applied up front")
    for j in migrate_wave(2, tables):
        if j["status"] == "ready_for_review":
            approve_job(j["job_id"], "reviewer")
            print(f"  reviewer approved {j['job_id']} (method: {j['method']})")

    print("\n[5] WAVE 3 — harder jobs: new kinds of gap → fixed, proven, saved as new rules")
    for j in migrate_wave(3, tables):
        if j["status"] == "ready_for_review":
            approve_job(j["job_id"], "reviewer")
            print(f"  reviewer approved {j['job_id']}" + (f" → rule saved ({j['fix_pattern']})" if j["fix_pattern"] else ""))

    print("\n[6] WAVE 4 — the new rules applied up front")
    for j in migrate_wave(4, tables):
        if j["status"] == "ready_for_review":
            approve_job(j["job_id"], "reviewer")
            print(f"  reviewer approved {j['job_id']} (method: {j['method']})")

    waves = sorted({j["wave"] for j in registry.list_jobs()})
    print("\n[7] DASHBOARD — fixer calls: " + ", ".join(f"wave {w} = {fixer_calls(w)}" for w in waves))
    status()


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else "demo"
    if cmd == "data":
        build_db()
    elif cmd == "triage":
        triage(load_tables()); status()
    elif cmd == "migrate":
        tables = load_tables()
        waves = [int(argv[2])] if len(argv) > 2 else sorted({j["wave"] for j in registry.list_jobs()})
        for w in waves:
            print(f"\nwave {w}"); migrate_wave(w, tables)
        status()
    elif cmd == "approve":
        approve_job(argv[2]); status()
    elif cmd == "retire":
        approve_retire(argv[2]); status()
    elif cmd == "restore":
        restore_job(argv[2]); status()
    elif cmd == "status":
        status()
    elif cmd == "demo":
        demo()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
