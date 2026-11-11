-- J12: Payments made against 2022 claims, per policy, rounded to pence. Finance cash report.
SELECT c.policy_id,
       COUNT(*) AS n_payments,
       ROUND(SUM(p.amount), 2) AS total_amount
FROM payments AS p
JOIN claims AS c ON p.claim_id = c.claim_id
WHERE YEAR(c.claim_date) = 2022
GROUP BY c.policy_id
