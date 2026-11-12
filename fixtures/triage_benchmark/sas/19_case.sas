data sasb_19;
set claims;
where paid_amount > 240;
net_paid = coalesce(paid_amount, 0) * 1.4;
keep policy_id claim_id net_paid claim_date;
run;
