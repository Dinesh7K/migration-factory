-- J04: Claims and amounts paid per year (NULL paid amounts treated as 0). Actuarial report.
SELECT YEAR(claim_date) AS claim_year,
       COUNT(*) AS n_claims,
       SUM(ISNULL(paid_amount, 0)) AS total_paid
FROM claims
GROUP BY YEAR(claim_date)
