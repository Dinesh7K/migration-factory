"""Triage: retire proposals with evidence. Approve → archive (never delete). Restore anytime."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from factory import registry
from factory.archive.archive import restore_job
from factory.triage.triage import approve_retire

st.title("Triage — what not to migrate")
st.caption("The agent only proposes. A human decides. Retired jobs are archived and restorable.")
st.info("Pending means the job passed the initial evidence checks; it is not proof the migrated output is correct. "
        "Complex or structurally uncertain jobs get an AI explanation and lineage cross-check. If that review is uncertain or disagrees with lineage, the job needs human review.")

proposals = registry.list_jobs(status="proposed_retire")
st.subheader(f"Retire proposals ({len(proposals)})")
for j in proposals:
    with st.container(border=True):
        st.markdown(f"**{j['job_id']}** · {j['risk']} · feeds: {j['feeds_report'] or '—'}")
        st.write(j["reason"])
        ai = j.get("triage_ai") or {}
        if ai.get("summary"):
            st.caption(f"Purpose ({'AI-written' if ai.get('source') == 'llm' else 'from lineage'}): {ai['summary']}")
        if ai.get("escalate"):
            st.warning(f"AI second opinion: this looks regulatory. {ai.get('risk_reason', '')}")
        st.json(j["lineage"], expanded=False)
        if st.button(f"Approve retirement of {j['job_id']}", key=f"ret_{j['job_id']}"):
            approve_retire(j["job_id"], "reviewer")
            st.rerun()

retired = registry.list_jobs(status="retired")
st.subheader(f"Archive ({len(retired)})")
for j in retired:
    cols = st.columns([4, 1])
    cols[0].write(f"**{j['job_id']}** — {j['reason']}")
    if cols[1].button("Restore", key=f"res_{j['job_id']}"):
        restore_job(j["job_id"])
        st.rerun()

st.subheader("All jobs: purpose and column-level lineage")
st.caption("T-SQL is parsed with sqlglot; SAS structure is parsed with tree-sitter-sas and lineage may use an AI estimate. "
           "AI summaries and risk opinions are advisory. Unknown or incomplete lineage is sent for human review.")
for j in registry.list_jobs():
    ai, lin = j.get("triage_ai") or {}, j.get("lineage") or {}
    with st.expander(f"{j['job_id']} · {j['status']} · {ai.get('summary', '')}"):
        st.markdown(f"**Declared risk:** {j['risk']} · **AI opinion:** {ai.get('risk') or 'none'}"
                    + (f" ({ai['risk_reason']})" if ai.get("risk_reason") else ""))
        if "understood" in ai:
            st.markdown("**Complex logic review:** " + ("AI reports a complete explanation" if ai["understood"]
                        else "AI could not confirm the complete logic"))
            if ai.get("uncertainties"):
                st.warning("AI review uncertainties: " + "; ".join(ai["uncertainties"]))
        d = lin.get("difficulty") or {}
        if d:
            st.markdown(f"**Difficulty:** {d['tier']} ({d['score']}/100) · **Route:** {d['route']} · "
                        + "; ".join(d["reasons"]))
        structure = lin.get("language_structure") or {}
        if j.get("lang") == "sas":
            st.caption(f"SAS grammar: {'available' if structure.get('available') else 'unavailable'} · "
                       f"{structure.get('generic_statements', 0)} generic statements · "
                       f"{structure.get('parse_errors', 0)} parse errors")
            if structure.get("generic_statements") or structure.get("parse_errors"):
                st.warning("Some SAS syntax was not fully structured by the grammar; the AI review must account for this.")
        st.markdown("**Known legacy-vs-Spark gaps:** " + (", ".join(lin.get("known_gaps") or []) or "none found"))
        how = {"parser": "parser (exact)", "parser_partial": "parser, partial", "ai_estimated": "AI estimate",
               "none": "not available"}.get(lin.get("source"), "parser (exact)")
        st.markdown(f"**Lineage method:** {how} · **Reads:** {', '.join(lin.get('reads') or []) or 'unknown'}")
        if lin.get("skipped"):
            st.warning("Steps not followed by the parser, so missing from this lineage: " + ", ".join(lin["skipped"]))
        if lin.get("source") == "ai_estimated":
            st.warning("This lineage is an AI estimate for a person to confirm."
                       + (f" Not found in the catalog: {', '.join(lin['unverified'])}." if lin.get("unverified") else
                          " Every table and column it names exists in the catalog."))
        rows = [{"output column": col, "comes from": ", ".join(src) or "(no source column, e.g. a row count)"}
                for col, src in (lin.get("column_lineage") or {}).items()]
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        if lin.get("filters"):
            st.markdown("**Filter and join columns:** " + ", ".join(lin["filters"]))
