/* Deliberately uses macro generation and row-order-sensitive SAS semantics. */
%macro summarize(source=claims, output=stress_sas_policy_claims);
    proc sort data=&source out=sorted_claims;
        by policy_id claim_date;
    run;

    data &output;
        set sorted_claims;
        by policy_id;
        retain running_paid 0;
        if first.policy_id then running_paid = 0;
        running_paid + paid_amount;
        if last.policy_id then output;
    run;
%mend summarize;

%summarize(source=claims, output=stress_sas_policy_claims);
