-- J17: Claims per 100 policies, by region. Both sides are integers, so SQL Server truncates the
-- result (integer division). Actuarial claim frequency report.
-- Job contributed by Eesha (wave3-rules branch).
SELECT p.region,
       COUNT(c.claim_id) * 100 / COUNT(DISTINCT p.policy_id) AS claims_per_100
FROM policies AS p
LEFT JOIN claims AS c ON c.policy_id = p.policy_id
GROUP BY p.region
