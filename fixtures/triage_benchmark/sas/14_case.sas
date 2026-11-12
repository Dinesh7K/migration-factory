data sasb_14;
set claims;
where paid_amount > 190;
net_paid = coalesce(paid_amount, 0) * 1.4;
keep policy_id claim_id net_paid claim_date;
run;
