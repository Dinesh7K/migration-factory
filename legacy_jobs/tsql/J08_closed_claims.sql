-- J08: Settled amount per policy for closed claims. Finance settlement report (regulatory).
SELECT policy_id,
       COUNT(*) AS n_closed,
       SUM(ISNULL(paid_amount, 0)) AS paid_closed
FROM claims
WHERE status LIKE 'CLOS%'
GROUP BY policy_id
