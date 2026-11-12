data sasb_12;
set claims;
where paid_amount > 170;
net_paid = coalesce(paid_amount, 0) * 1.2;
keep policy_id claim_id net_paid claim_date;
run;
