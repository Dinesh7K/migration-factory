data sasb_16;
set claims;
where paid_amount > 210;
net_paid = coalesce(paid_amount, 0) * 1.1;
keep policy_id claim_id net_paid claim_date;
run;
