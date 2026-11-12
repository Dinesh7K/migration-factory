%macro build_38(threshold=500);
proc sql;
create table sasb_38 as select c.policy_id, count(*) as claim_count, sum(c.paid_amount) as paid_total from claims as c inner join policies as p on c.policy_id=p.policy_id where c.paid_amount > &threshold group by c.policy_id;
quit;
%mend;
%build_38(threshold=1250);
