"""Human review gate: old vs new code, the diff, the reason, then Approve."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from factory import config, registry
from factory.agents.migrate import approve_job

st.title("Review — human sign-off")

queue = registry.list_jobs(status="ready_for_review") + registry.list_jobs(status="needs_human")
if not queue:
    st.success("Nothing waiting for review.")
    st.stop()

job = registry.get_job(st.selectbox("Job", [j["job_id"] for j in queue]))
badge = "🔒 Regulatory — sign-off mandatory" if job["risk"] == "regulatory" else "Internal"
st.markdown(f"**{job['job_id']}** · {badge} · status **{job['status']}** · confidence **{job['confidence']:.2f}** "
            f"· method `{job['method']}` · attempts {job['attempts']}")
st.info(job["reason"])

left, right = st.columns(2)
left.subheader("Legacy")
left.code((config.ROOT / job["source_path"]).read_text(), language="sql")
right.subheader("Migrated (Spark SQL)")
right.code(job["target_code"] or "", language="sql")

st.subheader("Reconciliation attempts")
for a in registry.audit(job["job_id"]):
    if a["event"] == "reconcile":
        st.write(f"Attempt {a['detail']['attempt']}: {a['detail']['summary']}")
        diffs = a["detail"]["result"]["field_diffs"]
        if diffs:
            st.dataframe(diffs, width="stretch")
    elif a["event"] == "edge_cases_added" and a["detail"]:
        st.caption("Breaker added: " + "; ".join(a["detail"]))

if job["status"] == "ready_for_review":
    if st.button("✅ Approve", type="primary"):
        approve_job(job["job_id"], "reviewer")
        st.success("Approved" + (f" — rule saved: {job['fix_pattern']}" if job["fix_pattern"] else ""))
        st.rerun()
elif not job["attempts"]:
    st.warning("Stopped before any reconciliation attempt (at triage or translation) — nothing has been "
               "proven for this job. A person must confirm or rework it (see the reason above). "
               "TODO: edit-and-rerun box.")
else:
    st.warning("Failed after max attempts — a human needs to fix this job. TODO: edit-and-rerun box.")
