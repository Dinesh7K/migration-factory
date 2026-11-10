"""MigrateGuard — Streamlit entry point.  Run: streamlit run app/app.py

TODO (P1, business lead): styling, lineage graph view, EXL-friendly wording.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from factory import registry

st.set_page_config(page_title="MigrateGuard", layout="wide")
st.title("MigrateGuard — AI migration factory")
st.caption("Decides what not to migrate · proves every job down to the field · gets faster every wave")

jobs = pd.DataFrame(registry.list_jobs())
if jobs.empty:
    st.info("No jobs yet. Run `python run.py demo` (or `run.py data` → `triage` → `migrate`).")
    st.stop()

c = st.columns(5)
c[0].metric("Jobs scanned", len(jobs))
c[1].metric("Retired (archived)", int((jobs.status == "retired").sum()))
c[2].metric("Approved", int((jobs.status == "approved").sum()))
c[3].metric("Awaiting review", int(jobs.status.isin(["ready_for_review", "proposed_retire"]).sum()))
c[4].metric("Needs human", int((jobs.status == "needs_human").sum()))

st.subheader("All jobs")
st.dataframe(jobs[["job_id", "wave", "risk", "status", "confidence", "method", "attempts", "feeds_report", "reason"]],
             width="stretch", hide_index=True)
st.write("Use the pages in the sidebar: **Triage** → **Review** → **Dashboard**.")
