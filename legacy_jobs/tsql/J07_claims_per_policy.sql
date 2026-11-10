-- J07: Claim frequency per line of business. Both counts are integers, so SQL Server returns a
-- whole number (integer division). The pricing report has always shown the truncated figure.
SELECT line_of_business,
       COUNT(*) AS n_claims,
       COUNT(DISTINCT policy_id) AS n_policies,
       COUNT(*) / COUNT(DISTINCT policy_id) AS claims_per_policy
FROM claims
GROUP BY line_of_business
