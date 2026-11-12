-- Deliberately beyond the single-query migration path: intermediate table plus UPDATE.
SELECT policy_id, claim_id, paid_amount, status
INTO #claim_work
FROM claims
WHERE claim_date >= '2022-01-01';

UPDATE #claim_work
SET paid_amount = paid_amount / 2
WHERE status = 'OPEN';

SELECT policy_id, SUM(paid_amount) AS adjusted_paid
FROM #claim_work
GROUP BY policy_id;
