-- J16: Outstanding reserve (claimed minus paid) on open claims, by region. Finance reserving.
-- Job contributed by Eesha (wave3-rules branch).
SELECT p.region,
       COUNT(*) AS open_claims,
       SUM(c.claimed_amount - ISNULL(c.paid_amount, 0)) AS outstanding
FROM claims AS c
JOIN policies AS p ON p.policy_id = c.policy_id
WHERE c.status IN ('OPEN', 'REOPENED')
GROUP BY p.region
