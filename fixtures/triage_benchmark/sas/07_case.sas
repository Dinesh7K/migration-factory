proc sql;
create table sasb_07 as select policy_id, sum(paid_amount) as paid_total from claims where paid_amount > 120 group by policy_id;
quit;
