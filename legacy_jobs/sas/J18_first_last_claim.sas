/* J18: first and last claim per policy (SAS BY-group with FIRST./LAST.).
   Per policy: date of the first and last claim, number of claims, and the status of the
   latest claim. Claims on the same date are ordered by claim_id.
   Migration traps: "status of the last row in a group" has no direct SQL aggregate
   (MAX(status) is wrong); it needs a window function with the same tie-break. */
proc sort data=claims out=claims_sorted;
  by policy_id claim_date claim_id;
run;

data policy_claim_span;
  set claims_sorted;
  by policy_id;
  retain first_claim_date n_claims;
  if first.policy_id then do;
    first_claim_date = claim_date;
    n_claims = 0;
  end;
  n_claims + 1;
  if last.policy_id then do;
    last_claim_date = claim_date;
    last_status = status;
    output;
  end;
  format first_claim_date last_claim_date yymmdd10.;
  keep policy_id first_claim_date last_claim_date n_claims last_status;
run;
