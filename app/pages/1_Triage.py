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

proposals = registry.list_jobs(status="proposed_retire")
st.subheader(f"Retire proposals ({len(proposals)})")
for j in proposals:
    with st.container(border=True):
        st.markdown(f"**{j['job_id']}** · {j['risk']} · feeds: {j['feeds_report'] or '—'}")
        st.write(j["reason"])
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
