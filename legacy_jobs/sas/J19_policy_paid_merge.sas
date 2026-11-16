/* J19: claims paid against each policy (SAS DATA-step MERGE, one-to-many, BY policy_id).
   One row per policy, including policies with no claims: number of claims, total paid and
   paid as a share of the annual premium.
   Migration traps:
   - MERGE keeps policies with no matching claim only because of `if in_pol;` (without it,
     MERGE behaves like a full outer join, not an inner join).
   - The sum statement treats a missing paid_amount as 0, and a policy with no claims gets
     total_paid = 0. A SQL LEFT JOIN + SUM gives NULL for those policies instead.
   - Region and premium from the one policy row are carried onto every claim row of it. */
proc sort data=policies out=pol;
  by policy_id;
run;

proc sort data=claims out=clm;
  by policy_id;
run;

data policy_paid;
  merge pol(in=in_pol keep=policy_id region annual_premium)
        clm(in=in_clm keep=policy_id paid_amount);
  by policy_id;
  if in_pol;
  retain n_claims total_paid;
  if first.policy_id then do;
    n_claims = 0;
    total_paid = 0;
  end;
  if in_clm then do;
    n_claims + 1;
    total_paid + paid_amount;
  end;
  if last.policy_id then do;
    paid_to_premium = round(total_paid / annual_premium, 0.0001);
    output;
  end;
  format annual_premium total_paid 15.2 paid_to_premium 10.4;
  keep policy_id region annual_premium n_claims total_paid paid_to_premium;
run;
