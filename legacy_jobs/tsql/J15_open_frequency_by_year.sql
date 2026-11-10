-- J15: Open/reopened claim frequency per year (whole-number claims per policy, as J07).
SELECT YEAR(claim_date) AS claim_year,
       COUNT(*) AS n_claims,
       COUNT(*) / COUNT(DISTINCT policy_id) AS claims_per_policy
FROM claims
WHERE status LIKE '%OPEN%'
GROUP BY YEAR(claim_date)
