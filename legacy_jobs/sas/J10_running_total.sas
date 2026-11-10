/* J10: running total of paid amount per policy (SAS RETAIN + BY-group).
   Legacy result: legacy_jobs/expected/J10.csv (produce once in SAS OnDemand for Academics).
   TODO (P0): run in SAS, save expected output, then set enabled: true in jobs.yaml. */
proc sort data=claims; by policy_id claim_date; run;
data running_paid;
  set claims;
  by policy_id;
  retain running_total;
  if first.policy_id then running_total = 0;
  running_total + coalesce(paid_amount, 0);
  if last.policy_id;
  keep policy_id running_total;
run;
