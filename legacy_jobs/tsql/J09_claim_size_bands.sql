-- J09: Claims bucketed by paid amount (NULL = not yet paid). Actuarial severity report.
SELECT CASE WHEN paid_amount IS NULL THEN 'UNPAID'
            WHEN paid_amount < 1000 THEN 'SMALL'
            WHEN paid_amount < 5000 THEN 'MEDIUM'
            ELSE 'LARGE' END AS band,
       COUNT(*) AS n_claims,
       SUM(ISNULL(paid_amount, 0)) AS total_paid
FROM claims
GROUP BY CASE WHEN paid_amount IS NULL THEN 'UNPAID'
              WHEN paid_amount < 1000 THEN 'SMALL'
              WHEN paid_amount < 5000 THEN 'MEDIUM'
              ELSE 'LARGE' END
