data sasb_18;
set claims;
where paid_amount > 230;
net_paid = coalesce(paid_amount, 0) * 1.3;
keep policy_id claim_id net_paid claim_date;
run;
