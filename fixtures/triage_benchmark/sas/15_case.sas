data sasb_15;
set claims;
where paid_amount > 200;
net_paid = coalesce(paid_amount, 0) * 1.0;
keep policy_id claim_id net_paid claim_date;
run;
