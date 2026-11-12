proc sql;
create table sasb_01 as select policy_id, sum(paid_amount) as paid_total from claims where paid_amount > 60 group by policy_id;
quit;
