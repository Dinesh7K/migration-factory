"""Dashboard: factory KPIs, learning per wave, ROI calculator."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from factory import registry

st.title("Dashboard")
jobs = pd.DataFrame(registry.list_jobs())
if jobs.empty:
    st.info("Run the factory first.")
    st.stop()
audit = pd.DataFrame(registry.audit())

c = st.columns(4)
c[0].metric("Scanned", len(jobs))
c[1].metric("Scope cut by triage", f"{(jobs.status == 'retired').mean():.0%}")
c[2].metric("Reconciled & approved", int((jobs.status == "approved").sum()))
c[3].metric("Bugs auto-fixed", int((audit.event == "fix").sum()) if not audit.empty else 0)

st.subheader("The factory learns: fixer calls per wave")
fixes = audit[audit.event == "fix"].merge(jobs[["job_id", "wave"]], on="job_id") if not audit.empty else pd.DataFrame()
per_wave = (fixes.groupby("wave").size() if not fixes.empty else pd.Series(dtype=int)).reindex(
    sorted(jobs.wave.unique()), fill_value=0)
st.bar_chart(per_wave.rename("fixer calls"))
st.caption("Falling bars = approved fixes reused as rules in later waves.")

st.subheader("Pattern library")
st.dataframe(pd.DataFrame(registry.list_rules()), width="stretch", hide_index=True)

st.subheader("ROI calculator (assumptions — edit them)")
a, b, d = st.columns(3)
n_jobs = a.number_input("Legacy jobs in estate", value=1600, step=100)
manual_days = b.number_input("Manual days per job (migrate + test)", value=3.0, step=0.5)
day_rate = d.number_input("Day rate (£)", value=600, step=50)
cut = (jobs.status == "retired").mean()
review_hours = st.slider("Human review hours per job with MigrateGuard", 0.5, 8.0, 2.0)
manual_cost = n_jobs * manual_days * day_rate
factory_cost = n_jobs * (1 - cut) * (review_hours / 8) * day_rate
st.metric("Estimated saving", f"£{manual_cost - factory_cost:,.0f}",
          help=f"Manual £{manual_cost:,.0f} vs factory £{factory_cost:,.0f}; scope cut {cut:.0%} from triage on the sample")
st.caption("Illustrative only — based on your inputs and the sample's triage rate.")
