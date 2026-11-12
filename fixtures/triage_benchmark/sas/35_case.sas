data sasb_35;
merge claims(in=inc) policies(in=inp);
by policy_id;
if inc and inp;
if first.policy_id then sequence=0;
retain sequence;
sequence+1;
band=ifn(paid_amount>2250, 'LARGE', 'OTHER');
run;
